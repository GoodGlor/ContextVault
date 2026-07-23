"""Embeddings via Google's Gemini embedding API.

Replaces the removed local sentence-transformers model: document/image text and
queries are embedded by Gemini using the global Gemini provider key (the same key the
OCR path uses). No ML model runs on the host — a stateless HTTPS call — so concurrent
ingestion no longer contends on a GPU. Vectors are L2-normalized here because retrieval
treats cosine similarity as a dot product and Gemini does not normalize when a
non-native ``output_dimensionality`` is requested.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from google.genai import types

if TYPE_CHECKING:
    from google import genai

EmbedTask = Literal["document", "query"]

# Gemini's asymmetric retrieval task types: documents and queries embed differently.
_TASK_TYPES: dict[str, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}

# Cap on inputs per ``embed_content`` call; batch under it.
_BATCH_SIZE = 100


class EmbeddingError(Exception):
    """A Gemini embedding request failed (bad key, network, quota, etc.)."""


def _genai_client(api_key: str) -> genai.Client:
    """Build a Gemini client (lazy import; monkeypatched in tests)."""
    from google import genai

    return genai.Client(api_key=api_key)


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


class GeminiEmbeddingProvider:
    """``EmbeddingProvider`` backed by Gemini's embedding API."""

    def __init__(self, *, api_key: str, model_name: str, dimension: int) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str], *, task: EmbedTask = "document") -> list[list[float]]:
        if not texts:
            return []
        config = types.EmbedContentConfig(
            task_type=_TASK_TYPES[task],
            output_dimensionality=self._dimension,
        )
        vectors: list[list[float]] = []
        try:
            client = _genai_client(self._api_key)
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = list(texts[start : start + _BATCH_SIZE])
                response = client.models.embed_content(
                    model=self._model_name,
                    contents=batch,  # type: ignore[arg-type]
                    config=config,
                )
                vectors.extend(_l2_normalize(e.values or []) for e in response.embeddings or [])
        except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes a clean error
            raise EmbeddingError(f"Could not embed text: {exc}") from exc
        return vectors
