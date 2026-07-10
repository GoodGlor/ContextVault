# ContextVault — Session Handoff

- **Last updated:** 2026-07-10 10:40 EEST
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

Backend RAG core is complete end-to-end and multi-provider. Card **#24 (per-repo
LLM config)** just merged: repositories now carry their own provider/model/
encrypted key, admin `llm-config` endpoints set/read it (keys masked, encrypted
at rest), and the query endpoint returns **409** if a repo isn't configured. A
small **CI hardening PR (#66)** is open and green — awaiting a human merge. No
feature work in flight. **Next:** card **#25** (provider routing at query time) —
the card that finally *uses* #24's stored config to route generation to each
repo's own provider (today generation still goes through the system-default
`get_llm` seam).

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `ci/encryption-key-env` (pushed, even with origin) |
| Branch HEAD | `44e5f1b` — ci: set ENCRYPTION_KEY in the CI env |
| `main` HEAD | `b5e95a3` — per-repo LLM config (card #24) (#65) |
| Open PR | **#66** `ci: set ENCRYPTION_KEY in CI env` — CI green, **awaiting human merge** |

**Not on `main` right now** — the working branch is `ci/encryption-key-env`. After
#66 merges, `git checkout main && git pull` before starting #25. Nothing else
unpushed. `docs/HANDOFF.md` is **untracked** (this file — commit it, see below).

---

## Done recently (this session)

### Card #24 — Per-repo LLM config model + endpoints (masked keys) ✅ merged
- **PR #65**, merge commit `b5e95a3`. Card → **Done**; all checkboxes ticked (all genuinely satisfied).
- **What landed:**
  - `models/repository.py` — new `llm_provider` (native enum: `gemini`/`openai`/`openrouter`/`anthropic`), `llm_model`, `api_key_encrypted` columns (all nullable) + an `llm_configured` property (true only when all three set).
  - `models/enums.py` — `LLMProviderName` StrEnum; values match the `get_llm_provider` factory keys so #25 routing is a direct lookup.
  - Migration `ed2189c1cf01` — adds the columns; manages the enum type explicitly (`CREATE`/`DROP TYPE`), so up→down→up round-trips cleanly.
  - `api/repositories.py` (new router, registered in `main.py`) — admin-only `PUT`/`GET /repositories/{id}/llm-config`. Key **encrypted on write** (`core/crypto.encrypt`), only ever returned **masked** (`mask_key` → `sk-…•••4f2a`), never in full. Masking decrypts in memory just long enough to keep prefix/suffix.
  - `api/query.py` — rejects an unconfigured repo with **409** (after the grant check, so config state isn't leaked to unauthorized callers).
  - Tests: `test_models.py` (columns + predicate), new `test_repositories_api.py` (auth, mask, ciphertext-at-rest round-trip, 404, 422), `test_query_api.py` (new 409 path; existing tests updated so `_repo` configures the LLM).
  - README — new "Repository LLM configuration (admin)" section + 409 note in the Query API section.
- **Scope boundary (locked):** #24 = config model + endpoints + *reject* unconfigured. **Routing generation to the per-repo provider is #25** — query still uses `get_llm`. Documented in `query.py` and README.
- **Verification:** full DoD gate green; **CI green** on the PR and the post-merge `main` run.

### CI hardening — ENCRYPTION_KEY in CI env (PR #66, open)
- `.github/workflows/ci.yml` — added a valid, throwaway **Fernet** `ENCRYPTION_KEY` to the `env:` block so the crypto suite runs against a *set* key (mirrors deployment) instead of `conftest`'s per-run default.
- **Gotcha captured:** `conftest.py` uses `os.environ.setdefault("ENCRYPTION_KEY", …)`, so any CI-provided value is **kept as-is** — it MUST be a real Fernet key or `test_crypto` round-trip tests break. That's why the value is a genuine key, not a placeholder.
- One-line CI-config change, no app/test code touched. **CI green on PR #66. Awaiting human merge.**

---

## Next up

### Card #25 — Provider routing at query time [Backlog]
The direct consumer of #24. Locked context so it isn't re-litigated:
- **Goal:** route generation to each repository's *configured* provider instead of the system-default `get_llm` seam. `#24` already stores `repo.llm_provider` / `repo.llm_model` / `repo.api_key_encrypted` and gates unconfigured repos with 409.
- **Seam to change:** `api/query.py` currently does `provider: LLMProvider = Depends(get_llm)`. #25 should build the provider *per request* from the repo's config: decrypt `api_key_encrypted` in memory, pass provider/model/api_key into `get_llm_provider(...)`.
- **Factory work:** `llm/__init__.py:get_llm_provider(name)` today takes only a name and constructs with settings defaults. Extend it (or add a builder) to accept `api_key` / `model` — each provider `__init__` already accepts `api_key=`, `model=`, `max_tokens=` kwargs (see `llm/openai.py`, `gemini.py`, `anthropic.py`, `openrouter.py`).
- **Anthropic:** valid stored provider but NOT yet wired into `get_llm_provider` (raises `ValueError`). #25 (or a sibling) must add the `anthropic` branch to the factory so a repo configured for Anthropic can actually answer.
- **Tests:** assert the query path uses the repo's provider/model/key (not the global default). The existing `RecordingProvider` override pattern in `test_query_api.py` is the seam to assert against.

Other near-term backlog (all Backlog, no priority set): #26–#29 user management, curation (#30–#33), React frontend (#34–#40), plus #37 (admin repo-management + LLM config UI — the frontend for #24). No card is in Ready/In progress — pick the next one deliberately with the user.

---

## Open known issues / gotchas

- **OpenRouter test fails LOCALLY only.** `test_llm_openrouter.py::test_default_model_is_openrouter_namespaced` asserts the default `openai/gpt-4o`, but the maintainer's local `.env` sets `OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free`, which pydantic-settings loads into `get_settings()` and bleeds into the test. **Reproduces on clean `main`; unrelated to any recent card.** CI has no `.env`, so it's green there. To run the full suite green locally: `OPENROUTER_MODEL=openai/gpt-4o uv run pytest`. Real fix (a separate card): isolate settings from `.env` in tests. This is a **test-isolation gap**, not a product bug.
- **`.env` `LLM_PROVIDER=gemini`** — the local OpenRouter key won't be used until this is `openrouter`. `.env` is local only, not committed.
- **`ENCRYPTION_KEY` unset by default** (required before persisting any provider key). Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Tests get a per-run key from `conftest`; CI sets one via PR #66.
- **CI warning (cosmetic):** `astral-sh/setup-uv@v6` runs on the deprecated Node 20. Harmless; a future one-line action bump clears it.
- DB-backed tests **skip** (not fail) when Postgres is unreachable; bring it up with `docker compose up -d` + `uv run alembic upgrade head`.

---

## Working rules & gotchas (project conventions)

- **Board discipline:** cards are issues 1:1. Move Backlog/Ready → In progress at start, → In review when PR opens, → Done only after a human merges. Assign issues/PRs `--assignee @me`. PRs reference cards with `Refs #N` (no closing verb — the board decides final state). Tick issue checkboxes **honestly**, never blanket.
- **DoD gate (all must be green):** `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, `uv run pytest`. Also runs in **GitHub Actions CI** (`.github/workflows/ci.yml`) on every PR + push to main, with a pgvector Postgres service.
- **TDD:** RED (failing test that fails for the right reason) → GREEN (minimal) → full gate. Update docs (README/`docs/`) in the same PR — hard rule. **Do NOT** add an "Implementation status" checklist to the README (the board is the source of truth).
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then `git checkout -b feat/<N>-<slug>`. Conflict-check with `git merge-tree --write-tree origin/main HEAD` before opening a PR.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope — autogenerated style is fine; keep enum types explicitly created/dropped.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
docker compose up -d                 # Postgres + pgvector
uv run alembic upgrade head          # migrate
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
OPENROUTER_MODEL=openai/gpt-4o uv run pytest   # override neutralizes the local .env gotcha
```

---

## History

- #24 Per-repo LLM config model + endpoints — PR #65 (`b5e95a3`). *(this session)*
- CI: ENCRYPTION_KEY in CI env — PR #66 (`44e5f1b`, open). *(this session)*
- #23 Encrypted API-key storage — PR #64 (`a1f0898`).
- #22 OpenRouter LLMProvider — PR #63 (`35f8cbc`).
- #20 OpenAI (ChatGPT) LLMProvider — PR #62 (`b7143d0`).
- #19 Query endpoint (full RAG loop) — PR #61 (`7e67867`).
- #18 `not_in_vault` signal — `3ed262e`. #17 numbered-chunk citations — `e4a5c82`.
- Earlier: foundation (FastAPI + pgvector + Argon2/JWT auth + admin bootstrap), ingestion pipeline, local embeddings, access-filtered retrieval, Gemini & Anthropic providers, GitHub Actions CI. See `git log` and the board for detail.
