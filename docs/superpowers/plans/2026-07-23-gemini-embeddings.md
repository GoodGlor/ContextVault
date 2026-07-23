# Gemini Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed chunks and queries through Google's Gemini embedding API and remove the local `sentence-transformers`/`torch` embedder entirely.

**Architecture:** A new `GeminiEmbeddingProvider` implements the existing `EmbeddingProvider` protocol; `get_embedder` resolves the global Gemini key (hard-fails 409 without it) and builds the provider, which the upload/query paths already inject. Documents and queries embed with Gemini's asymmetric `task_type`. The pgvector column stays 1024 via `output_dimensionality=1024`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, pgvector, `google-genai` 2.10.0 (already used for OCR), pytest.

## Global Constraints

- Embedding dimension stays **1024** (`output_dimensionality=1024`); no DB schema migration.
- Embedding model: **`gemini-embedding-001`**.
- No Gemini key → **HTTP 409** with a clear message. No local fallback.
- Vectors are **L2-normalized** in the provider (retrieval treats cosine as dot product).
- `google-genai` response shape: `response.embeddings` is a list; each item has `.values`.
- Gemini key is resolved via `provider_service.get_provider_key(session, LLMProviderName.GEMINI)` and never leaves the provider/service frame.
- Run tests with `.venv/bin/python -m pytest`. DB-backed tests skip when Postgres is unreachable — that is expected, not a failure.

---

### Task 1: `GeminiEmbeddingProvider` + protocol `task` param + config default

**Files:**
- Modify: `src/contextvault/embeddings/base.py`
- Modify: `src/contextvault/core/config.py:33` (`embedding_model` default)
- Create: `src/contextvault/embeddings/gemini.py`
- Test: `tests/test_embeddings.py` (rewrite)

**Interfaces:**
- Produces: `GeminiEmbeddingProvider(*, api_key: str, model_name: str, dimension: int)` with `.dimension: int` and `.embed(texts: Sequence[str], *, task: EmbedTask = "document") -> list[list[float]]`.
- Produces: `EmbedTask = Literal["document", "query"]`, `EmbeddingError(Exception)` — both exported from `contextvault.embeddings.gemini`.
- Produces: `EmbeddingProvider.embed` protocol now accepts `*, task: str = "document"`.

- [ ] **Step 1: Add the `task` keyword to the protocol**

In `src/contextvault/embeddings/base.py`, replace the `embed` method signature:

```python
    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        """Embed ``texts`` into vectors, one per input, each of ``dimension``.

        ``task`` is ``"document"`` for stored content and ``"query"`` for a search
        query — providers that support asymmetric retrieval embeddings use it.
        """
        ...
```

- [ ] **Step 2: Point the config default at Gemini**

In `src/contextvault/core/config.py`, change the `embedding_model` default (line ~33):

```python
    embedding_model: str = "gemini-embedding-001"
```

Leave `embedding_dim: int = 1024` unchanged.

- [ ] **Step 3: Write the failing provider tests**

Replace the entire contents of `tests/test_embeddings.py` with:

```python
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
    provider = GeminiEmbeddingProvider(api_key="k", model_name="gemini-embedding-001", dimension=1024)
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_embeddings.py -q`
Expected: FAIL — `ImportError` / `GeminiEmbeddingProvider` not defined.

- [ ] **Step 5: Implement `GeminiEmbeddingProvider`**

Create `src/contextvault/embeddings/gemini.py`:

```python
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
from typing import Literal

from google.genai import types

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


def _genai_client(api_key: str):  # -> genai.Client
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
                    model=self._model_name, contents=batch, config=config
                )
                vectors.extend(_l2_normalize(e.values) for e in response.embeddings)
        except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes a clean error
            raise EmbeddingError(f"Could not embed text: {exc}") from exc
        return vectors
```

- [ ] **Step 6: Export the provider**

In `src/contextvault/embeddings/__init__.py`, add the import and export (leave the local imports for now — Task 4 removes them):

```python
from contextvault.embeddings.gemini import GeminiEmbeddingProvider
```

Add `"GeminiEmbeddingProvider"` to `__all__`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_embeddings.py -q`
Expected: PASS (7 passed).

- [ ] **Step 8: Commit**

```bash
git add src/contextvault/embeddings/base.py src/contextvault/embeddings/gemini.py \
  src/contextvault/embeddings/__init__.py src/contextvault/core/config.py tests/test_embeddings.py
git commit -m "feat(embeddings): Gemini embedding provider with asymmetric task types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `get_embedder` resolves the Gemini key (409 without it)

**Files:**
- Modify: `src/contextvault/api/deps.py:88-99` (`_default_embedder` / `get_embedder`)
- Test: `tests/test_embedder_dependency.py` (create)

**Interfaces:**
- Consumes: `GeminiEmbeddingProvider` (Task 1), `provider_service.get_provider_key`.
- Produces: `async def get_embedder(session: AsyncSession = Depends(get_session)) -> EmbeddingProvider`, raising `HTTPException(409)` when no Gemini key is verified.

- [ ] **Step 1: Write the failing dependency tests**

Create `tests/test_embedder_dependency.py`:

```python
"""``get_embedder`` resolves the global Gemini key or hard-fails 409."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import get_embedder
from contextvault.core.crypto import encrypt
from contextvault.embeddings.gemini import GeminiEmbeddingProvider
from contextvault.models import LLMProviderName, ProviderSetting


async def test_get_embedder_raises_409_without_gemini_key(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_embedder(session=db_session)
    assert exc.value.status_code == 409
    assert "Gemini" in exc.value.detail


async def test_get_embedder_builds_provider_with_key(db_session: AsyncSession) -> None:
    db_session.add(
        ProviderSetting(
            provider=LLMProviderName.GEMINI,
            api_key_encrypted=encrypt("secret-key"),
            verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.flush()

    provider = await get_embedder(session=db_session)
    assert isinstance(provider, GeminiEmbeddingProvider)
    assert provider.dimension == 1024
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_embedder_dependency.py -q`
Expected: FAIL (`get_embedder` is sync / returns local provider; `TypeError` or wrong type).

- [ ] **Step 3: Rewrite `get_embedder` (and delete `_default_embedder`)**

In `src/contextvault/api/deps.py`, remove the `@lru_cache _default_embedder` function and replace `get_embedder` with:

```python
async def get_embedder(session: AsyncSession = Depends(get_session)) -> EmbeddingProvider:
    """Build the Gemini embedder from the global provider key, or 409.

    Embeddings are global (one vector space for every repository), so the deployment
    must have a verified Gemini key — even for repositories that chat through another
    provider. Without it, ingestion and querying cannot embed, so fail fast with a
    clear, actionable message rather than deep in the pipeline.
    """
    settings = get_settings()
    key = await provider_service.get_provider_key(session, LLMProviderName.GEMINI)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Configure a verified Gemini API key to enable embeddings: embeddings "
                "for every repository are generated by Gemini."
            ),
        )
    return GeminiEmbeddingProvider(
        api_key=key, model_name=settings.embedding_model, dimension=settings.embedding_dim
    )
```

Update the imports at the top of `deps.py`: remove `from functools import lru_cache` and the `LocalEmbeddingProvider` import; add `from contextvault.embeddings.gemini import GeminiEmbeddingProvider` and `from contextvault.models import LLMProviderName` (extend the existing `models` import). `EmbeddingProvider`, `provider_service`, `get_settings`, `HTTPException`, `status` are already imported.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_embedder_dependency.py -q`
Expected: PASS (2 passed, or skipped if no DB).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/deps.py tests/test_embedder_dependency.py
git commit -m "feat(embeddings): resolve Gemini key in get_embedder, 409 when absent

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread `task` through ingestion + retrieval; update fake embedders

**Files:**
- Modify: `src/contextvault/services/ingestion.py:108` (`store_parsed`)
- Modify: `src/contextvault/retrieval/service.py:65`
- Modify: `tests/test_ingestion_pipeline.py:32`, `tests/test_query_api.py:41`, `tests/test_sources_api.py`, `tests/test_retrieval_service.py:29` (fake embedders accept `task`)

**Interfaces:**
- Consumes: `EmbeddingProvider.embed(..., task=...)` (Task 1).

- [ ] **Step 1: Update the fake embedders to accept `task`**

In each of `tests/test_ingestion_pipeline.py`, `tests/test_query_api.py`, `tests/test_sources_api.py`, change the `FakeEmbedder.embed` signature from `def embed(self, texts: Sequence[str])` to:

```python
    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
```

In `tests/test_retrieval_service.py`, update `_FakeEmbedder.embed` the same way (keep its existing body):

```python
    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
```

- [ ] **Step 2: Pass `task="document"` from ingestion**

In `src/contextvault/services/ingestion.py`, in `store_parsed`, change the embed call (line ~108) from:

```python
    vectors = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])
```

to:

```python
    texts = [c.text for c in chunks]
    vectors = await asyncio.to_thread(lambda: embedder.embed(texts, task="document"))
```

- [ ] **Step 3: Pass `task="query"` from retrieval**

In `src/contextvault/retrieval/service.py`, change the embed call (line ~65) from:

```python
    vectors = await asyncio.to_thread(embedder.embed, [question])
```

to:

```python
    vectors = await asyncio.to_thread(lambda: embedder.embed([question], task="query"))
```

Update the adjacent comment to reflect that `embed` is a network call, not a local CPU model:

```python
    # ``embed`` is a synchronous network call; keep the event loop free.
```

- [ ] **Step 4: Run the affected suites**

Run: `.venv/bin/python -m pytest tests/test_ingestion_pipeline.py tests/test_retrieval_service.py tests/test_query_api.py tests/test_sources_api.py -q`
Expected: PASS (or skip when no DB). No `TypeError` about an unexpected `task` argument.

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/services/ingestion.py src/contextvault/retrieval/service.py \
  tests/test_ingestion_pipeline.py tests/test_query_api.py tests/test_sources_api.py \
  tests/test_retrieval_service.py
git commit -m "feat(embeddings): embed documents and queries with Gemini task types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Remove the local path and drop torch/sentence-transformers

**Files:**
- Delete: `src/contextvault/embeddings/local.py`
- Modify: `src/contextvault/embeddings/__init__.py`
- Modify: `pyproject.toml` (remove `torch`, `sentence-transformers`)
- Regenerate: `uv.lock`
- Delete: `/Users/chaplin/.claude/projects/-Users-chaplin-Python-MyPets-ContextVault/memory/embedder-not-thread-safe.md`
- Modify: `/Users/chaplin/.claude/projects/-Users-chaplin-Python-MyPets-ContextVault/memory/MEMORY.md`

**Interfaces:**
- Removes: `LocalEmbeddingProvider`, `get_embedding_provider` (verified unused outside tests, which no longer import them after Tasks 1–3).

- [ ] **Step 1: Confirm nothing still imports the local path**

Run: `grep -rn "LocalEmbeddingProvider\|get_embedding_provider\|embeddings.local" src/ tests/`
Expected: no matches. If any remain, fix them before deleting.

- [ ] **Step 2: Delete the local module and rewrite the package init**

Delete `src/contextvault/embeddings/local.py`. Replace `src/contextvault/embeddings/__init__.py` with:

```python
"""Pluggable embedding layer (design spec §7).

Embeddings are generated by Gemini (``GeminiEmbeddingProvider``); the ``EmbeddingProvider``
protocol is the seam ingestion and retrieval depend on. The active provider is built
per-request from the global Gemini key in ``api.deps.get_embedder``.
"""

from contextvault.embeddings.base import EmbeddingProvider
from contextvault.embeddings.gemini import GeminiEmbeddingProvider

__all__ = ["EmbeddingProvider", "GeminiEmbeddingProvider"]
```

- [ ] **Step 3: Drop the heavy dependencies**

In `pyproject.toml`, remove the `torch` and `sentence-transformers` entries from `dependencies`. Then:

Run: `uv lock`
Expected: lockfile updates, removing `torch`, `sentence-transformers`, and their now-orphaned transitive deps.

- [ ] **Step 4: Delete the obsolete memory note**

Delete `/Users/chaplin/.claude/projects/-Users-chaplin-Python-MyPets-ContextVault/memory/embedder-not-thread-safe.md` and remove its line from `MEMORY.md` (the `- [Embedder not thread-safe]...` bullet). The torch thread-safety hazard no longer exists once the local model is gone.

- [ ] **Step 5: Run the full suite + lint + type check**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (DB-backed tests may skip when Postgres is down; no failures, no import errors).

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/mypy src/contextvault/embeddings src/contextvault/api/deps.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(embeddings): remove local torch embedder and its deps

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: API-layer 409 test + docs

**Files:**
- Test: `tests/test_sources_api.py` (add one test that does NOT override `get_embedder`)
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: the real `get_embedder` 409 behavior (Task 2).

- [ ] **Step 1: Write the failing API-layer 409 test**

In `tests/test_sources_api.py`, add a test that builds its own app overriding `get_session` and `get_ingestion_session_factory` **but not** `get_embedder`, seeds no Gemini key, then uploads a document and asserts 409. It reuses the module's existing helpers (`_token`, `_repo`, `_auth`, `_fixed_factory`) and already-imported names (`create_app`, `ASGITransport`, `AsyncClient`, `get_session`, `Role`):

```python
async def test_upload_without_gemini_key_returns_409(db_session: AsyncSession) -> None:
    app = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_ingestion_session_factory] = lambda: _fixed_factory(db_session)
    # get_embedder intentionally NOT overridden — exercise the real 409 path.

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = await _token(c, db_session, Role.ADMIN)
        repo = await _repo(db_session)
        resp = await c.post(
            f"/repositories/{repo.id}/sources",
            headers=_auth(token),
            files={"file": ("doc.txt", b"hello world", "text/plain")},
        )
    assert resp.status_code == 409
    assert "Gemini" in resp.json()["detail"]
```

`get_embedder` is resolved as a route dependency before the handler body runs, so the 409 fires for a plain document upload — no Gemini key seeded means `get_provider_key` returns `None`.

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sources_api.py::test_upload_without_gemini_key_returns_409 -q`
Expected: PASS (or skip when no DB).

- [ ] **Step 3: Update the handoff doc**

In `docs/HANDOFF.md`, update the TL;DR/state to note: embeddings now run through Gemini (`gemini-embedding-001`, 1024-dim); the local `sentence-transformers`/`torch` embedder was removed; a verified Gemini provider key is now required for ingestion and query (409 otherwise); existing data must be re-ingested (old bge-m3 vectors are incompatible — `TRUNCATE chunks`).

- [ ] **Step 4: Run the full suite one more time**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS / skips only.

- [ ] **Step 5: Commit**

```bash
git add tests/test_sources_api.py docs/HANDOFF.md
git commit -m "test(embeddings): API 409 without Gemini key; docs: handoff update

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- The two pre-existing crash fixes are already in the working tree at plan start: the DB-pool fix in `services/ingestion.py` (`_ocr_image` commits before the slow OCR call) and its test in `test_ingestion_pipeline.py`. **Keep both.** The torch serialization lock in `embeddings/local.py` is deleted by Task 4 along with the file — that is intended.
- Do not change `embedding_dim` from 1024. Do not add an Alembic migration.
- `test_sources_api.py` and `test_query_api.py` override `get_embedder` with a `FakeEmbedder`, so they never hit the 409 path — only Task 5's new test does.
