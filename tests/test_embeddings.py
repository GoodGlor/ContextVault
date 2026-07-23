"""Tests for the Gemini-backed embedding provider.

A fake genai client stands in for the network, so the suite runs offline and never
touches torch or a real API key.
"""

from collections.abc import Sequence

import pytest

import contextvault.embeddings.gemini as gemini_mod
from contextvault.embeddings import EmbeddingProvider, GeminiEmbeddingProvider
from contextvault.embeddings.gemini import EmbeddingError


class _FakeEmbedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, embeddings: list[_FakeEmbedding]) -> None:
        self.embeddings = embeddings


class _FakeModels:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    def embed_content(self, *, model: str, contents: Sequence[str], config) -> _FakeResponse:
        self._recorder.setdefault("calls", []).append(
            {"model": model, "contents": list(contents), "config": config}
        )
        # Return one 3-vector per input, non-unit so normalization is observable.
        return _FakeResponse([_FakeEmbedding([3.0, 0.0, 4.0]) for _ in contents])


class _FakeClient:
    def __init__(self, recorder: dict) -> None:
        self.models = _FakeModels(recorder)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> dict:
    rec: dict = {}
    monkeypatch.setattr(gemini_mod, "_genai_client", lambda api_key: _FakeClient(rec))
    return rec


def test_provider_satisfies_protocol() -> None:
    provider: EmbeddingProvider = GeminiEmbeddingProvider(
        api_key="k", model_name="gemini-embedding-001", dimension=1024
    )
    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimension == 1024


def test_embed_normalizes_vectors(recorder: dict) -> None:
    provider = GeminiEmbeddingProvider(api_key="k", model_name="m", dimension=1024)
    vectors = provider.embed(["hello", "привіт"])
    assert len(vectors) == 2
    # [3,0,4] has norm 5 → normalized to [0.6, 0, 0.8]
    assert vectors[0] == pytest.approx([0.6, 0.0, 0.8])


def test_embed_passes_dimension_and_document_task(recorder: dict) -> None:
    provider = GeminiEmbeddingProvider(
        api_key="k", model_name="gemini-embedding-001", dimension=1024
    )
    provider.embed(["a"])
    config = recorder["calls"][0]["config"]
    assert config.output_dimensionality == 1024
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert recorder["calls"][0]["model"] == "gemini-embedding-001"


def test_embed_query_task(recorder: dict) -> None:
    provider = GeminiEmbeddingProvider(api_key="k", model_name="m", dimension=1024)
    provider.embed(["q"], task="query")
    assert recorder["calls"][0]["config"].task_type == "RETRIEVAL_QUERY"


def test_embed_batches_and_preserves_order(recorder: dict) -> None:
    provider = GeminiEmbeddingProvider(api_key="k", model_name="m", dimension=1024)
    texts = [str(i) for i in range(250)]  # > 2 batches of 100
    vectors = provider.embed(texts)
    assert len(vectors) == 250
    calls = recorder["calls"]
    assert [len(c["contents"]) for c in calls] == [100, 100, 50]


def test_embed_empty_returns_empty(recorder: dict) -> None:
    provider = GeminiEmbeddingProvider(api_key="k", model_name="m", dimension=1024)
    assert provider.embed([]) == []
    assert "calls" not in recorder


def test_embed_wraps_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        @property
        def models(self):
            raise RuntimeError("network down")

    monkeypatch.setattr(gemini_mod, "_genai_client", lambda api_key: _Boom())
    provider = GeminiEmbeddingProvider(api_key="k", model_name="m", dimension=1024)
    with pytest.raises(EmbeddingError, match="network down"):
        provider.embed(["a"])
