"""The Anthropic (Claude) ``LLMProvider`` — the first concrete generator.

Implements the provider-agnostic ``answer`` contract (card #15) against the
official Anthropic SDK. Two of the project's defining behaviours are enforced
here rather than left to the model's goodwill:

- **Grounded, numbered-chunk answers.** The retrieved chunks are laid out under
  ``[1..n]`` markers and the system prompt tells the model to answer *only* from
  them and cite those numbers. The ``[n]`` markers the model emits are parsed
  back into ``Citation`` objects pointing at the exact source span, so the
  citation experience is identical across every provider — Claude's own
  native-citation feature is deliberately unused. That prompt/parse/map machinery
  is the shared ``contextvault.llm.citations`` scheme (card #17); this provider
  only wires it to the Anthropic SDK.
- **Honest "not in this vault".** When retrieval surfaced nothing relevant the
  provider short-circuits — it returns the honest answer without spending an API
  call, so the model is never even given the chance to answer from training data.

The model defaults to ``claude-opus-4-8`` and is configurable (constructor arg or
``anthropic_model`` setting), per the card.
"""

from collections.abc import Sequence

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock

from contextvault.core.config import get_settings
from contextvault.llm.base import Answer
from contextvault.llm.citations import (
    NOT_IN_VAULT,
    SYSTEM_PROMPT,
    build_user_message,
    parse_citations,
)
from contextvault.retrieval import RetrievedChunk


class AnthropicLLMProvider:
    """``LLMProvider`` backed by Claude via the Anthropic SDK."""

    def __init__(
        self,
        *,
        client: AsyncAnthropic | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Configure the provider.

        ``client`` injects an ``AsyncAnthropic`` (mainly for tests); otherwise one
        is built from ``api_key`` (falling back to the ``anthropic_api_key``
        setting, then the SDK's own ``ANTHROPIC_API_KEY`` resolution). ``model``
        and ``max_tokens`` default to the ``anthropic_model`` / ``llm_max_tokens``
        settings.
        """
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._max_tokens = max_tokens or settings.llm_max_tokens
        self._client = client or AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)

    async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer:
        """Generate a grounded, cited answer to ``question`` from ``chunks``.

        With no chunks, returns the honest "not in this vault" answer without an
        API call. Otherwise numbers the chunks, asks Claude to answer only from
        them, and resolves the ``[n]`` markers in the reply to ``Citation``s.
        """
        if not chunks:
            return Answer(text=NOT_IN_VAULT, citations=[])

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(question, chunks)}],
        )
        text = "".join(
            block.text for block in message.content if isinstance(block, TextBlock)
        ).strip()
        return Answer(text=text, citations=parse_citations(text, chunks))
