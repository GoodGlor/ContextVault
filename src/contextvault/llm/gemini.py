"""The Google (Gemini) ``LLMProvider`` — generation via the Google GenAI SDK.

Implements the provider-agnostic ``answer`` contract (card #15) against Gemini,
enforcing the same two defining behaviours as the Anthropic provider:

- **Grounded, numbered-chunk answers.** The retrieved chunks are laid out under
  ``[1..n]`` markers and the system instruction tells the model to answer *only*
  from them and cite those numbers. The ``[n]`` markers the model emits are
  parsed back into ``Citation`` objects pointing at the exact source span, so the
  citation experience is identical across every provider — no reliance on any
  vendor-native citation feature.
- **Honest "not in this vault".** With no relevant chunks the provider
  short-circuits and returns the honest answer without spending an API call.

The prompt-build / marker-parse helpers here mirror the Anthropic provider's;
card #17 unifies both into one shared numbered-chunk scheme. The model defaults
to ``gemini-2.5-flash`` and is configurable (constructor arg or ``gemini_model``
setting), per the card.
"""

import re
from collections.abc import Sequence

from google import genai
from google.genai import types

from contextvault.core.config import get_settings
from contextvault.llm.base import Answer, Citation
from contextvault.retrieval import RetrievedChunk

# Grounding contract handed to the model: answer only from the numbered sources,
# cite them by number, and be honest when they fall short.
SYSTEM_PROMPT = (
    "You are ContextVault's retrieval assistant. Answer the user's question using "
    "ONLY the numbered sources provided in their message. Never use outside or "
    "prior knowledge.\n\n"
    "Cite every claim with the bracketed number of the source it draws from — "
    "e.g. [1], or [2] — citing multiple sources where they apply.\n\n"
    "If the sources do not contain the answer, say plainly that the answer is not "
    "in this vault. Do not answer from your own knowledge, and do not invent "
    "citations."
)

# Returned verbatim when retrieval found nothing relevant (no chunks) — the
# honest "not in this vault" answer, produced without consulting the model.
NOT_IN_VAULT = "I don't have anything on that in this repository."

# A citation marker in the model's answer text: ``[1]``, ``[2]``, …
_MARKER = re.compile(r"\[(\d+)\]")


def _format_sources(chunks: Sequence[RetrievedChunk]) -> str:
    """Lay the chunks out as ``[n] <content>`` blocks, numbered from 1."""
    return "\n\n".join(f"[{i}] {chunk.content}" for i, chunk in enumerate(chunks, start=1))


def _build_user_message(question: str, chunks: Sequence[RetrievedChunk]) -> str:
    return f"Sources:\n{_format_sources(chunks)}\n\nQuestion: {question}"


def _parse_citations(text: str, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
    """Map the ``[n]`` markers in ``text`` back to their source chunks.

    Markers are taken in first-appearance order; repeats collapse to one citation
    and markers outside ``1..len(chunks)`` (a fabricated or mis-numbered ``[n]``)
    are dropped, so a citation always resolves to a real retrieved passage.
    """
    citations: dict[int, Citation] = {}
    order: list[int] = []
    for match in _MARKER.finditer(text):
        number = int(match.group(1))
        if number in citations or not 1 <= number <= len(chunks):
            continue
        chunk = chunks[number - 1]
        citations[number] = Citation(
            number=number,
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
        )
        order.append(number)
    return [citations[number] for number in order]


class GeminiLLMProvider:
    """``LLMProvider`` backed by Gemini via the Google GenAI SDK."""

    def __init__(
        self,
        *,
        client: genai.Client | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Configure the provider.

        ``client`` injects a ``genai.Client`` (mainly for tests); otherwise one is
        built from ``api_key`` (falling back to the ``gemini_api_key`` setting,
        then the SDK's own ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` resolution).
        ``model`` and ``max_tokens`` default to the ``gemini_model`` /
        ``llm_max_tokens`` settings.
        """
        settings = get_settings()
        self._model = model or settings.gemini_model
        self._max_tokens = max_tokens or settings.llm_max_tokens
        self._client = client or genai.Client(api_key=api_key or settings.gemini_api_key)

    async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer:
        """Generate a grounded, cited answer to ``question`` from ``chunks``.

        With no chunks, returns the honest "not in this vault" answer without an
        API call. Otherwise numbers the chunks, asks Gemini to answer only from
        them, and resolves the ``[n]`` markers in the reply to ``Citation``s.
        """
        if not chunks:
            return Answer(text=NOT_IN_VAULT, citations=[])

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=_build_user_message(question, chunks),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=self._max_tokens,
            ),
        )
        # ``response.text`` concatenates the text parts; it is None when the model
        # returned no text (e.g. a safety-blocked response).
        text = (response.text or "").strip()
        return Answer(text=text, citations=_parse_citations(text, chunks))
