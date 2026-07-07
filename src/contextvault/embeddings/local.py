"""Local, free, multilingual embedding provider.

Backed by a sentence-transformers model (the bge-m3 / multilingual-e5 family)
that runs on the server, so document text never leaves the machine and there is
no per-call cost. The model is loaded lazily on first use and reused thereafter;
loading is isolated behind ``_load_sentence_transformer`` so the heavy import
(torch) happens only when an embedding is actually needed — and so tests can
substitute a fake without installing it.
"""

from collections.abc import Sequence
from typing import Any


def _load_sentence_transformer(model_name: str) -> Any:
    """Import sentence-transformers lazily and load ``model_name``."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


class LocalEmbeddingProvider:
    """``EmbeddingProvider`` backed by a local sentence-transformers model.

    ``dimension`` is supplied by configuration and must match the model's own
    output width (which in turn must match the pgvector column). The check runs
    when the model first loads so a misconfiguration fails loudly rather than
    silently writing wrong-width vectors.
    """

    def __init__(self, *, model_name: str, dimension: int) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._model: Any | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_model(self) -> Any:
        if self._model is None:
            model = _load_sentence_transformer(self._model_name)
            actual = model.get_sentence_embedding_dimension()
            if actual != self._dimension:
                raise ValueError(
                    f"Model {self._model_name!r} produces {actual}-dim vectors, but "
                    f"configured embedding_dim is {self._dimension}. Set EMBEDDING_DIM "
                    "(and the pgvector column) to match the model, then re-embed."
                )
            self._model = model
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        # Normalize so cosine similarity reduces to a dot product downstream.
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in row] for row in vectors]
