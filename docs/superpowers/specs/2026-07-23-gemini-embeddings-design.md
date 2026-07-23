# Replace local embeddings with Gemini API — design

- **Date:** 2026-07-23
- **Status:** Approved, ready to implement
- **Feature:** Swap the local sentence-transformers embedder for Gemini's embedding
  API, and remove the local (torch) path entirely.

## Goal

Embed chunks and queries through Google's Gemini embedding API instead of a local
`sentence-transformers` model. Remove `LocalEmbeddingProvider`, `torch`, and
`sentence-transformers` from the project so no ML model runs on the machine.

## Background / motivation

The local embedder (`BAAI/bge-m3` via `sentence-transformers`) runs a torch model that,
on Apple Silicon, executes on the Metal (MPS) GPU. Concurrent embedding calls from the
background-ingestion thread pool crashed the process with a native segfault and, twice,
rebooted the developer's Mac (verified from a SIGSEGV panic report: `AGXMetal13_3` →
`at::native::mps::handle_binary_op`, multiple threads on the `metal gpu stream`). A
serialization lock fixed the crash, but the local model remains a heavy dependency
(multi-GB download, GPU/CPU load, thread-safety hazard).

`bge-m3` is an excellent multilingual embedder, so this change is **not** about retrieval
quality — it is about removing local ML compute from the machine. The user explicitly
chose to replace local embeddings entirely with Gemini (they have a Gemini provider key).

Key facts that shaped the design:

- **Embeddings are global.** All repositories share one vector space, one model, one
  dimension (pgvector column, currently 1024). So the choice of embedder is deployment-wide,
  independent of each repository's *chat* provider.
- **Only some providers offer embeddings.** OpenAI and Gemini do; **Anthropic and
  OpenRouter do not.** This design commits to Gemini.
- **Gemini keys already live in `ProviderSetting`** (global, Fernet-encrypted), resolved
  today by the image-OCR path (`services/providers.get_provider_key`).
- `google-genai` 2.10.0 (already a dependency for OCR) supports batch `embed_content`,
  a configurable `output_dimensionality`, and asymmetric `task_type`.

## Decisions (from brainstorming)

1. **No-key state:** hard-fail with a clear message (HTTP 409). No local fallback.
2. **Existing data:** wipe & re-ingest manually — no migration tooling. Existing bge-m3
   vectors are cleared; the user re-adds sources.
3. **Dimension:** keep the pgvector column at **1024** by requesting
   `output_dimensionality=1024` from Gemini. No DB schema migration.

## Design

### 1. `GeminiEmbeddingProvider` (`src/contextvault/embeddings/gemini.py`)

A synchronous provider (a network call) constructed with `api_key`, `model`, `dimension`.

```python
def embed(self, texts: Sequence[str], *, task: EmbedTask = "document") -> list[list[float]]:
    ...
```

- Calls `client.models.embed_content(model=..., contents=<batch>,
  config=EmbedContentConfig(output_dimensionality=self._dimension, task_type=...))`.
- `task` maps to Gemini's asymmetric task types: `"document"` → `RETRIEVAL_DOCUMENT`,
  `"query"` → `RETRIEVAL_QUERY`. This is a small retrieval-quality win over the current
  symmetric embedding.
- **L2-normalizes** every returned vector. Gemini does not normalize when
  `output_dimensionality` is below the model's native size, and retrieval treats cosine
  similarity as a dot product (`normalize_embeddings=True` today), so normalization must
  happen here.
- **Batches** `contents` into groups (default 100) to respect the API's per-request
  batch limit; concatenates results in order.
- Wraps any SDK/network failure in a clean provider error so ingestion records one tidy
  `ingest_error` (mirrors `llm/ocr.py`'s `OCRError` pattern).
- **No lock needed** — Gemini calls are stateless HTTP and safe to run concurrently, so
  bulk ingestion embeds in parallel again (the torch thread-safety hazard is gone).

### 2. Key resolution — `get_embedder` becomes async (`api/deps.py`)

`get_embedder` currently returns a process-wide `LocalEmbeddingProvider` singleton with no
key. It becomes an **async** dependency that:

- resolves the **Gemini** key from `ProviderSetting` via
  `provider_service.get_provider_key(session, LLMProviderName.GEMINI)`,
- **raises `409`** with a clear, actionable message when no key is set
  ("Configure a verified Gemini API key to enable embeddings"),
- constructs and returns a `GeminiEmbeddingProvider` (decrypted key held in memory only).

The upload endpoints already capture the injected embedder and hand it to the background
task (`run_ingestion`), so the built provider (with its key) flows to background ingestion
unchanged. The query endpoint resolves it the same way. Both upload and query therefore
hard-fail with 409 when no Gemini key exists — the global dependency, made explicit.

`get_ingestion_session_factory` and the background-task wiring are unchanged.

### 3. Call-site + config changes

- `services/ingestion.store_parsed` embeds with `task="document"`; `retrieval/service`
  embeds the question with `task="query"`. Both keep running off the event loop via
  `asyncio.to_thread`.
- `core/config`: `embedding_model` default → `"gemini-embedding-001"`; `embedding_dim`
  stays **1024**.

### 4. Remove the local path

- Delete `src/contextvault/embeddings/local.py` (and `LocalEmbeddingProvider`), the
  `get_embedding_provider` factory in `embeddings/__init__.py`, and the process-wide
  serialization lock (it exists only to guard torch).
- Remove `sentence-transformers` and `torch` from `pyproject.toml`; `uv lock`.
  `pillow-heif`, `google-genai`, and the rest of OCR's stack stay.
- Delete the now-obsolete `embedder-not-thread-safe` auto-memory note.

### 5. Existing data

No migration code. Existing chunks were embedded by bge-m3 and are incompatible; they must
be cleared. The user re-adds sources through the normal upload flow. A one-liner to wipe:

```sql
TRUNCATE chunks;
```

(Sources may be left in place and re-uploaded, or also cleared — the user's choice.)

## Interfaces / boundaries

- `EmbeddingProvider` protocol (`embeddings/base.py`) gains the `task` keyword on `embed`
  (default `"document"` keeps existing callers working). It stays the single seam every
  consumer depends on; only the implementation and key-resolution change.
- The Gemini key never leaves `provider_service` + the provider instance's frame — same
  boundary the OCR path already respects.

## Error handling

- No Gemini key → `409` at the API boundary (upload, web-source, admin-note, query).
- Gemini call failure during ingestion → caught in `ingest_source`, recorded as
  `ingest_error`, source marked `FAILED` (existing behavior, new error source).
- Gemini call failure during query → surfaces as a request error (retrieval has no
  "partial" state); acceptable.

## Testing

- New `test_embeddings.py`: `GeminiEmbeddingProvider` against a **fake genai client** —
  assert batching (grouping + order preserved), `output_dimensionality` passed,
  L2-normalization, `task_type` mapping, and error wrapping. No network, no torch.
- `api/deps` test: `get_embedder` raises `409` when no Gemini key; returns a provider when
  a key exists.
- Update `test_ingestion_pipeline.py` / retrieval tests to the new async `get_embedder`
  and the `task` argument; the existing `FakeEmbedder` gains a `task` keyword.
- Add a "no Gemini key → 409" path test at the API layer.

## Out of scope / YAGNI

- OpenAI or multi-provider embeddings (Gemini only).
- A re-embed-from-stored-content migration command (chosen: wipe & re-ingest).
- Changing the pgvector column dimension (kept at 1024).
- Caching/rate-limit tuning for the embedding API (add later if needed).
