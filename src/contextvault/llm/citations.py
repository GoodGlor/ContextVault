"""The shared numbered-chunk citation scheme (card #17): prompt → parse → map.

Only Claude offers native citations, so ContextVault builds its own scheme that
works identically across *every* provider. This module is the one place that
machinery lives; the Anthropic, Gemini, and future OpenAI/OpenRouter providers
all import it, so citations behave the same no matter which vendor generated the
answer:

- **prompt** — ``build_user_message`` lays the retrieved chunks out under
  ``[1..n]`` markers, and ``SYSTEM_PROMPT`` tells the model to answer *only* from
  those numbered sources and cite them by number.
- **parse → map** — ``parse_citations`` reads the ``[n]`` markers back out of the
  model's answer and resolves each to the exact source span (document + character
  offsets) it points at, so the UI can jump to and highlight the passage.

``NOT_IN_VAULT`` is the honest answer a provider returns, without an API call,
when retrieval surfaced nothing relevant.
"""

import re
from collections.abc import Sequence

from contextvault.llm.base import Citation
from contextvault.retrieval import RetrievedChunk

# Grounding contract handed to the model. Kept blunt on purpose: answer only from
# the numbered sources, cite them by number, and be honest when they fall short.
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


def format_sources(chunks: Sequence[RetrievedChunk]) -> str:
    """Lay the chunks out as ``[n] <content>`` blocks, numbered from 1."""
    return "\n\n".join(f"[{i}] {chunk.content}" for i, chunk in enumerate(chunks, start=1))


def build_user_message(question: str, chunks: Sequence[RetrievedChunk]) -> str:
    """Compose the user turn: the numbered sources followed by the question."""
    return f"Sources:\n{format_sources(chunks)}\n\nQuestion: {question}"


def parse_citations(text: str, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
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
