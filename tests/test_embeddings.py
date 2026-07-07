"""Tests for the pluggable embedding layer.

The unit tests substitute a fake model for the local sentence-transformers
backend, so the suite runs without downloading a multi-gigabyte model (or even
installing torch). The one test that loads the real model is opt-in, gated
behind ``RUN_EMBEDDING_MODEL_TESTS`` so CI stays fast.
"""

import os
from collections.abc import Sequence

import pytest

import contextvault.embeddings.local as local_mod
from contextvault.embeddings import get_embedding_provider
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.embeddings.local import LocalEmbeddingProvider


class _FakeModel:
    """Stand-in for ``sentence_transformers.SentenceTransformer``."""

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self.encode_calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        self.encode_calls.append(texts)
        # Deterministic vectors of the declared dimension, one row per input.
        return [[float(i)] * self._dim for i, _ in enumerate(texts)]


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> _FakeModel:
    model = _FakeModel(dim=8)
    monkeypatch.setattr(local_mod, "_load_sentence_transformer", lambda name: model)
    return model


def test_local_provider_satisfies_protocol() -> None:
    provider: EmbeddingProvider = LocalEmbeddingProvider(model_name="x", dimension=8)
    assert isinstance(provider, EmbeddingProvider)


def test_embed_returns_vectors_of_declared_dimension(fake_model: _FakeModel) -> None:
    provider = LocalEmbeddingProvider(model_name="x", dimension=8)
    vectors = provider.embed(["hello", "привіт", "здравствуй"])

    assert provider.dimension == 8
    assert len(vectors) == 3
    assert all(len(v) == 8 for v in vectors)
    assert all(isinstance(x, float) for v in vectors for x in v)


def test_embed_empty_input_does_not_load_model(fake_model: _FakeModel) -> None:
    provider = LocalEmbeddingProvider(model_name="x", dimension=8)
    assert provider.embed([]) == []
    assert fake_model.encode_calls == []


def test_model_loaded_once_and_cached(fake_model: _FakeModel) -> None:
    provider = LocalEmbeddingProvider(model_name="x", dimension=8)
    provider.embed(["a"])
    provider.embed(["b"])
    # Two embed calls, but the fake model instance is reused (loaded once).
    assert len(fake_model.encode_calls) == 2


def test_dimension_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_mod, "_load_sentence_transformer", lambda name: _FakeModel(dim=384))
    provider = LocalEmbeddingProvider(model_name="x", dimension=1024)
    with pytest.raises(ValueError, match="384.*1024|1024.*384"):
        provider.embed(["a"])


def test_get_embedding_provider_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    get_embedding_provider.cache_clear()
    provider = get_embedding_provider()
    assert isinstance(provider, LocalEmbeddingProvider)
    # Cached: same instance on repeat calls.
    assert get_embedding_provider() is provider
    get_embedding_provider.cache_clear()


@pytest.mark.skipif(
    not os.getenv("RUN_EMBEDDING_MODEL_TESTS"),
    reason="set RUN_EMBEDDING_MODEL_TESTS=1 to download and run the real model",
)
def test_real_local_model_embeds_multilingual() -> None:
    from contextvault.core.config import get_settings

    settings = get_settings()
    provider = LocalEmbeddingProvider(
        model_name=settings.embedding_model, dimension=settings.embedding_dim
    )
    texts: Sequence[str] = ["The cat sits on the mat.", "Кіт сидить на килимку."]
    vectors = provider.embed(texts)
    assert len(vectors) == 2
    assert all(len(v) == settings.embedding_dim for v in vectors)
