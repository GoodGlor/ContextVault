"""The ``LLMProvider`` abstraction — provider-agnostic grounded generation.

Generation is pluggable (design spec §4/§7). Different vendors — Anthropic,
OpenAI/OpenRouter, Google — sit behind this one interface so the RAG loop and
the per-repo provider routing depend only on the contract, never on a vendor
SDK. This card defines the contract only; the concrete providers are cards #16
and #20–#22.

The contract carries the project's two defining behaviors:

- **Provider-agnostic citations.** Only Claude has native citations, so instead
  of relying on any vendor feature the retrieved chunks are numbered ``[1..n]``
  and the model is told to cite those numbers. ``answer`` returns a ``Citation``
  per marker, mapping ``[n]`` back to the exact source passage (document +
  character span) the UI can jump to. The prompt/parse/map machinery that turns
  a model's ``[n]`` markers into these citations is the shared
  ``contextvault.llm.citations`` scheme (card #17); here we fix the shape the
  providers return.
- **Honest "not in this vault".** When retrieval surfaced nothing relevant
  (``chunks`` empty), a provider must say the answer isn't in the repository
  rather than answering from the model's own training data — an ``Answer`` with
  text but no citations.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from contextvault.retrieval import RetrievedChunk


@dataclass(frozen=True)
class Citation:
    """One ``[n]`` citation: the marker and the source span it points to.

    ``number`` is the 1-based marker the model emits in the answer text
    (``[1]``, ``[2]``, …). The remaining fields identify the exact passage that
    grounds it — the source document and the character span within it — so the
    UI can jump to and highlight it. ``char_start``/``char_end`` are ``None``
    when the cited chunk carried no positional offsets (some parsed formats have
    none); the citation still resolves to its source.
    """

    number: int
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    char_start: int | None
    char_end: int | None


@dataclass(frozen=True)
class Answer:
    """A grounded generation result: the answer text plus its citations.

    ``citations`` lists the markers referenced in ``text``, in marker order —
    one per ``[n]`` the model used. An ``Answer`` with text but an empty
    ``citations`` list is the honest "not in this vault" answer: retrieval found
    nothing relevant, so nothing is cited.
    """

    text: str
    citations: list[Citation]


@runtime_checkable
class LLMProvider(Protocol):
    """Generates a grounded, cited answer from a question and its chunks."""

    async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer:
        """Answer ``question`` grounded in ``chunks``, citing them by number.

        ``chunks`` are the retrieved passages, ranked most relevant first; the
        provider numbers them ``[1..n]`` in that order, instructs the model to
        cite those markers, and returns each used marker as a ``Citation``
        resolved to its source span. When ``chunks`` is empty the provider must
        return the honest "not in this vault" answer — text explaining the
        repository doesn't cover the question, and no citations — rather than
        answering from the model's own knowledge.
        """
        ...
