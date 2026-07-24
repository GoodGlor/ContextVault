# Custom (OpenAI-compatible) LLM Provider — Local / Self-Hosted Chat — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning
**Feature:** Let a deployment point ContextVault's chat / report / OCR models at an
LLM the customer runs themselves — Ollama, vLLM, LM Studio, TGI, LocalAI, or any
server that speaks the OpenAI `/v1` API — by adding a single **"Custom
(OpenAI-compatible)"** provider with one **global** endpoint URL and an optional
API key. Embeddings stay on Gemini in this phase.

This is **Phase 1** of a three-phase "connect your own LLM" initiative. It is the
low-risk plumbing that Phase 2 (fully air-gapped local embeddings) builds on, and
it is independently sellable as *"run your own chat model — your questions never
hit a cloud LLM."*

---

## 1. Problem & goal

The provider layer hardcodes **four** cloud vendors (`gemini`, `openai`,
`openrouter`, `anthropic`) as a `StrEnum`. Keys are stored globally, one per
vendor, Fernet-encrypted. There is **no place to store a custom endpoint URL**
(except OpenRouter's single hardcoded base URL), and every key is **mandatory**
(`min_length=1`). So a customer with a local OpenAI-compatible server has nowhere
to enter its address, and keyless local servers (the common Ollama case) are
rejected outright.

**Goal:** an admin can configure one **Custom (OpenAI-compatible)** endpoint
(base URL + optional key), verify it, and select its models per repository —
exactly like the existing cloud providers — so chat answering, report generation
(NL→SQL), and image OCR all run against the customer's own model.

**The load-bearing caveat (must be stated in-product and in docs):** Phase 1
makes the *chat / report / OCR* model local. It does **not** make the deployment
air-gapped — **ingestion still calls Gemini to embed documents**, so document
text still leaves the network at index time. Removing that Gemini coupling is
Phase 2. Do not describe Phase 1 as "air-gapped" or "nothing leaves your
network."

## 2. Decisions locked with the owner

| Decision | Choice |
|---|---|
| Endpoint model | **New `custom` provider** ("Custom (OpenAI-compatible)"), alongside the four existing vendors — existing providers untouched |
| Endpoint scope | **Global** — one base URL stored on the custom provider row, shared by every repo that selects it |
| API key | **Optional** for the custom provider (keyless local servers allowed); still Fernet-encrypted when present |
| Model selection | **Per repository** (unchanged mechanic): a repo picks `(custom, model)`; model dropdown populated from the endpoint's `/v1/models`, with **manual text entry fallback** |
| Embeddings | **Unchanged** — Gemini only, 1024-dim, existing `chunks` table. Phase 2 concern |
| Scope of change | Backend + frontend; **one Alembic migration** (add `base_url` column) |
| i18n | All new/changed strings in **EN + UK** from the start |

## 3. Data model

**Enum** — `src/contextvault/models/enums.py`
Add one value to `LLMProviderName`:
```python
CUSTOM = "custom"
```
Value must match the factory key in `llm/__init__.py` and the frontend
`LLMProvider` union.

**`provider_settings` table** — `src/contextvault/models/provider_setting.py`
- Add nullable column `base_url: Mapped[str | None]` (Text, nullable). Only the
  `custom` row populates it; cloud rows leave it `NULL`.
- `api_key_encrypted` is currently `nullable=False`; change to
  `Mapped[str | None]` **nullable** (keyless custom). Cloud providers still
  require a key (enforced in the service/API layer, not the column).
- The `provider` uniqueness (`unique=True`, one row per vendor) is **unchanged** —
  one global custom row, matching the "global endpoint" decision.

**Migration** — new Alembic revision (head is currently `b8c2d5e7f901`):
- **`ALTER TYPE llm_provider ADD VALUE 'custom'`** — the provider column is a
  Postgres `ENUM` named `llm_provider`; the new value must be added to the type.
  Note: `ADD VALUE` cannot run inside a transaction block on older PG, and a
  freshly added enum value cannot be *used* in the same transaction — keep it in
  its own migration step / use `op.execute` with autocommit as the project's other
  enum migrations do (check an existing enum-altering revision for the pattern).
- `ADD COLUMN base_url TEXT NULL` on `provider_settings`.
- `ALTER COLUMN api_key_encrypted DROP NOT NULL` (confirmed currently NOT NULL).
- Downgrade reverses the column changes. (Postgres cannot drop a single enum
  value; document that the `custom` enum value is left in place on downgrade —
  harmless, unused.)

## 4. Base-URL resolution (single source of truth)

Today base URL is resolved ad hoc: `providers.py:_base_url_for()` returns
OpenRouter's URL else `None`, and OCR/textgen re-inject OpenRouter's base
internally. This phase makes `providers.py` the **one resolver**:

- Extend `_base_url_for(session, provider)` → returns:
  - the stored `custom` row's `base_url` when `provider == CUSTOM`,
  - `settings.openrouter_base_url` when `provider == OPENROUTER`,
  - `None` otherwise.
- Add a public `get_provider_base_url(session, provider) -> str | None` used by
  every call site that needs it (`deps.build_repo_llm`, `services/reports.py`,
  `services/ingestion.py`).

## 5. Provider dispatch — the five sites

The OpenRouter path (OpenAI client + custom `base_url`) is the template. Each of
the five dispatch sites gains a `custom` branch:

| Site | File | Change |
|---|---|---|
| Answer factory | `src/contextvault/llm/__init__.py` | `get_llm_provider(name, *, api_key, model, base_url=None)` — add `base_url` param; `custom` → `OpenAILLMProvider(api_key=api_key or "sk-noauth", model=model, base_url=base_url)` |
| Answer client | `src/contextvault/llm/openai.py` | Add `base_url: str \| None = None` to `__init__`; pass to `AsyncOpenAI(api_key=..., base_url=base_url)` (no-op when `None`) |
| Model list | `src/contextvault/llm/models.py` | `custom` branch → OpenAI-compatible `GET /v1/models` with `base_url` (reuse the existing OpenAI-compatible helper path) |
| OCR | `src/contextvault/llm/ocr.py` | `custom` branch → `_ocr_openai_compatible(..., base_url=base_url)` |
| Text gen | `src/contextvault/llm/textgen.py` | `custom` branch → `_generate_openai_compatible(..., base_url=base_url)` |

**Call-site plumbing:**
- `deps.build_repo_llm` (`src/contextvault/api/deps.py`) — fetch `base_url` via
  `get_provider_base_url(session, repo.llm_provider)` and pass it into
  `get_llm_provider(...)`.
- `services/reports.py` — replace the inline `openrouter_base_url if ...` with
  `get_provider_base_url(session, repo.llm_provider)`.
- `services/ingestion.py` — resolve `base_url` via the same helper and pass it to
  `transcribe_image(...)` (today it passes none and relies on OCR's internal
  OpenRouter injection; the `custom` case needs the resolved URL threaded in).

**Keyless auth:** OpenAI-compatible servers require *some* non-empty string in the
`Authorization` header even when they ignore it. When the stored custom key is
absent, pass a harmless placeholder (`"sk-noauth"`) to the client. Never persist
the placeholder; it exists only at client-construction time.

## 6. Verification & key gating

- `ProviderKeyRequest` (`src/contextvault/api/providers.py`) — for `custom`,
  **`base_url` required, `api_key` optional**; for cloud providers, key required
  as today. Validate accordingly (a model validator keyed on `provider`).
- `services/providers.set_provider_key` — the verify-then-store contract holds,
  but for `custom` it stores `base_url` and an **optional** key. Verification for
  custom = `list_models(CUSTOM, api_key or placeholder, base_url=base_url)`
  succeeds (i.e. the endpoint answers `/v1/models`); stamp `verified_at`.
- `services/providers.get_provider_key` — unchanged for cloud; for `custom` may
  return `None` (keyless), which callers tolerate via the placeholder.
- **Embeddings coupling stays:** `deps.get_embedder` still 409s without a verified
  Gemini key. This is intended in Phase 1 and documented as the reason Phase 2
  exists. A repo may chat via `custom` but the *deployment* still needs a Gemini
  key to ingest. Surface this clearly in the UI copy (see §7).

## 7. Frontend

- `frontend/src/api/repositories.ts` — add `"custom"` to the `LLMProvider` union
  and a `LLM_PROVIDERS` entry `{ value: "custom", label: <i18n> }`.
- `frontend/src/api/providers.ts` — `ProviderStatus` gains `base_url?: string | null`;
  `setProviderKey` payload gains optional `base_url` and makes `api_key` optional.
- `frontend/src/pages/AdminProvidersPage.tsx` — the `custom` `ProviderRow` renders:
  - a **Base URL** text input (required; placeholder `http://localhost:11434/v1`),
  - an **optional** API key password input (labeled optional),
  - Verify / Save / Remove,
  - an inline note: *"Chat runs on your server. Document embedding still uses
    Gemini in this version."* (i18n).
- `frontend/src/pages/AdminRepositoriesPage.tsx` — when provider `custom` is
  selected and verified, the model `<select>` loads from `listModels("custom")`;
  if the endpoint returns no models (server without `/v1/models`), fall back to a
  **free-text model input**.
- **i18n:** add EN + UK strings for the provider label, base-URL field + helper,
  optional-key hint, the embeddings note, and the manual-model-entry fallback.

## 8. Testing strategy (TDD)

Backend (pytest, async):
- Enum/model: `custom` persists with `base_url` and null key; migration up/down.
- `get_provider_base_url`: returns stored URL for custom, OpenRouter's for
  openrouter, `None` for others.
- Factory: `get_llm_provider("custom", api_key=None, model="m", base_url="http://x/v1")`
  builds an OpenAI client whose `base_url` is `http://x/v1` and whose key is the
  placeholder.
- Answer path: `OpenAILLMProvider(base_url=...)` end-to-end `answer()` against a
  mocked custom endpoint returns an `Answer`.
- `list_models("custom", ..., base_url=...)` hits `/v1/models`; OCR and textgen
  custom branches pass `base_url` through (assert on the mocked client's
  `base_url`).
- Verify: keyless custom verifies via `/v1/models` and stamps `verified_at`;
  `ProviderKeyRequest` rejects custom without `base_url`, accepts custom without
  key, still requires key for cloud providers.
- `deps.build_repo_llm` threads the resolved `base_url` into the factory.

Frontend (vitest):
- Providers panel renders the base-URL field + optional-key hint + embeddings note
  for `custom`; save sends `base_url`.
- Repo config: selecting `custom` loads models; empty model list → free-text input
  appears and its value is saved as `llm_model`.

Full suite + lint + typecheck + build green before PR. CI does not run e2e; note
any provider e2e selector updates as a follow-up.

## 9. Rollout / risk

- **One additive migration** (nullable column + relax a NOT NULL). Existing rows
  and existing repos are unaffected; no data backfill.
- Cloud providers are untouched — the `custom` branch is purely additive in all
  five dispatch sites.
- Biggest risk is a half-threaded `base_url` (a site still hardcoding an endpoint):
  mitigated by routing **all** resolution through `get_provider_base_url` and
  asserting `base_url` in each site's test.
- Security: `base_url` is not a secret (no encryption needed). The optional key is
  still Fernet-encrypted. **SSRF note:** the custom base URL is admin-supplied and
  is fetched server-side (verify + every call). Admins are trusted, but record
  SSRF hardening (block link-local/metadata ranges) as a follow-up — out of scope
  here, consistent with the existing `services/web_source.py` posture.

## 10. Out of scope (this phase)

- Local / self-hosted **embeddings** and the per-dimension vector tables → Phase 2.
- **Per-repository** endpoints (repo A → server 1, repo B → server 2) — Phase 1 is
  one global endpoint.
- **Ollama-native** UX (auto-detect `:11434`, model pull) → Phase 3.
- SSRF DNS-rebinding hardening (shared with `web_source.py`; separate effort).

## 11. Future phases (direction on record — not built here)

**Phase 2 — Fully air-gapped: local embeddings + separate vector storage.**
- Add an OpenAI-compatible **embedding provider** beside `embeddings/gemini.py`;
  make `deps.get_embedder` dispatch on a configured embedding provider instead of
  hardwiring Gemini, and drop the mandatory-Gemini 409 when a local embedder is
  configured.
- **Separation is driven by the embedding model's dimension, not the chat model.**
  A repo can already run local chat (Phase 1) while embedding via Gemini (1024,
  existing table). Separation only applies when a repo uses a local embedding
  model with a **non-1024 dimension**, which pgvector cannot store in the fixed
  1024 `chunks.embedding` column or index.
- **Storage decision (locked):** *separate vector table per dimension, same
  Postgres.* An **embedding-space registry** — one vector table per distinct
  dimension (e.g. `chunk_vectors_768`, `chunk_vectors_384`), each with its own
  HNSW cosine index. Each **repository records the embedding space it uses**
  (provider + model + dimension); retrieval routes to the matching table. Existing
  Gemini repos keep today's `chunks` table (1024) with **no data migration**. The
  grant + `repository_id` isolation from `retrieval/search.py` still applies on top
  of whichever table is queried.
- Re-embedding flow when a repo's embedding space changes (re-index its sources
  into the new table).

**Phase 3 — Ollama-native polish.**
- Auto-detect `http://localhost:11434/v1`; list installed models; optional model
  pull. UX sugar over the Phase 1 custom provider (Ollama already speaks the
  OpenAI API).
