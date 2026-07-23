# ContextVault — Session Handoff

- **Last updated:** 2026-07-23 12:32 EEST (Gemini embeddings merged as #112)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete.

**Latest work — Gemini embeddings replace the local torch embedder — MERGED as #112 (squash
`045d363`, branch deleted; built via superpowers SDD, not a board card).** Embeddings now
run through the Gemini API (`gemini-embedding-001`, 1024-dim, asymmetric `task`:
`RETRIEVAL_DOCUMENT` for ingestion, `RETRIEVAL_QUERY` for search) instead of a local
`sentence-transformers`/bge-m3/torch model — no more GPU/CPU inference in-process. The local
embedder (`embeddings/local.py`) and its deps (`sentence-transformers`, `torch`) are
**removed entirely**. *Why this happened:* the local torch model ran on the Apple-Silicon
Metal GPU and, under concurrent bulk-upload ingestion, segfaulted the process and **rebooted
the owner's Mac** (verified from a `AGXMetal` SIGSEGV panic report). Moving to Gemini (a
stateless HTTPS call) removes that failure class; a DB connection-pool-exhaustion bug on the
same bulk-upload path was fixed in the same branch.
- **A verified Gemini provider key is now required for both ingestion and query.**
  `get_embedder` resolves the key from `provider_settings` (the global per-provider key
  store from the previous session) and **409s** ("Gemini API key required...") when no
  verified Gemini key is stored — fails fast before any upload or query work happens,
  same pattern as the existing vision-OCR 409.
- **Existing data must be re-ingested.** Old bge-m3 vectors are **not compatible** with
  Gemini's embedding space — mixing them would silently corrupt retrieval (nearest-neighbor
  search across two unrelated vector spaces). Before using this on an existing DB:
  `TRUNCATE chunks;` then re-upload/re-ingest every source. No Alembic migration was added
  (`embedding_dim` stays 1024, so the column shape is unchanged — only the vector *values*
  are incompatible).

Verified: backend **363✓** (adds an end-to-end Gemini test; ruff + `ruff format --check` +
mypy clean). CI green on #112.

**Pending owner step (required before use — not code):** **`TRUNCATE chunks`** and re-ingest
every source. Old bge-m3 vectors live in a different space and will poison retrieval if mixed
with Gemini vectors (no Alembic migration — `embedding_dim` stays 1024, only vector *values*
are incompatible). Also set a **verified Gemini provider key** in the Providers tab, or every
ingestion/query 409s.

**Previous session** shipped global provider keys + LLM-vision OCR (#111) — see *History*
for #105–#110 before that. The one older open follow-up is SSRF DNS-rebinding hardening of
the URL fetcher (from #100) — safe as-is (admin-only), card it + `/security-review` before
non-admin exposure. *(A carried-over owner note from the previous handoff — dev-instance
cleanup: delete all repos except `NGU payments`, confirm the list first — may already be
done; verify.)*

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (in sync with `origin/main`, tree clean) |
| `main` HEAD | `045d363` (#112, Gemini embeddings replace the local torch embedder) |
| In flight | nothing — #112 merged, branch deleted |
| CI | green on #112 (backend ruff/format/mypy/pytest **363✓**; frontend ✓) |
| Local infra | `contextvault-db` (pgvector pg16) container — had exited (255) at the Mac reboot; **restarted** (`docker start contextvault-db`), up + migrated |

**Migration note:** `d4f1a2b7c9e0` creates `provider_settings` and **drops
`repositories.api_key_encrypted`** — old per-repo keys are *not* migrated; re-enter each
provider's key once in the new Providers tab. Round-trips (down/up) cleanly.

**Data note (no migration, but action required):** the Gemini-embeddings branch changes
what a chunk's vector *means*, not its column shape — no Alembic migration was added. Any
`chunks` rows embedded with the old local bge-m3 model are vectors in a different space and
will poison nearest-neighbor search if mixed with Gemini vectors. Before relying on this
branch against an existing DB: `TRUNCATE chunks;` then re-ingest every source (re-upload
documents / re-add web sources) so all chunks are embedded with Gemini.

---

## Done recently (this session)

### Gemini embeddings replace the local torch embedder — merged as #112 (squash `045d363`; built via superpowers SDD, not a board card)

**Motivation:** the local `sentence-transformers`/bge-m3/torch embedder ran on the
Apple-Silicon Metal GPU; concurrent embed calls from the ingestion thread pool under
bulk image upload segfaulted the process and **rebooted the owner's Mac** (diagnosed from
a `AGXMetal13_3` → `at::native::mps::handle_binary_op` SIGSEGV panic report). Removing torch
kills that failure class. Two same-path crash fixes were folded into this branch first:
(a) DB **connection-pool exhaustion** — `_ocr_image` held a pooled connection across the
slow LLM-vision OCR call; now commits before it to release the connection; (b) an interim
torch serialization lock (deleted along with `local.py`). Replaced the embedder with the
Gemini embedding API:
- **`GeminiEmbeddingProvider`** (`embeddings/gemini.py`) implements the existing `Embedder`
  protocol against `gemini-embedding-001` (1024-dim, matching `embedding_dim`), with
  asymmetric `task` — `RETRIEVAL_DOCUMENT` when embedding ingested chunks, `RETRIEVAL_QUERY`
  when embedding a search query — fails loud (raises) on an empty/malformed API response
  rather than silently returning zero vectors.
- **`get_embedder` (api/deps.py)** resolves the Gemini key from `provider_settings` (the
  global per-provider key store from the previous session) and raises **409** ("Gemini API
  key required...") when no verified Gemini key exists. It is a route dependency, so both
  the upload endpoint and the query endpoint fail fast before doing any work.
- **Local embedder removed.** `embeddings/local.py` and its torch serialization lock are
  deleted; `sentence-transformers` and `torch` dropped as dependencies (`pyproject.toml`,
  `uv.lock`); the stale `[[tool.mypy.overrides]]` for `sentence_transformers.*` removed too.
- **Regression caught mid-plan:** the query endpoint's new 409 broke two password-recovery
  bounce-probe tests that expected a 404 (the 409 from the embedder dependency fired first).
  Fixed by seeding a verified Gemini key in those tests so the probe reaches the actual
  404 handler being tested.

Tests: new `test_embedder_dependency.py` (409 without a key, resolves with one); reworked
`test_embeddings.py` for the Gemini provider (batching/order, L2-normalization, task types,
fail-loud on empty/None responses); `test_sources_api.py` gained
`test_upload_without_gemini_key_returns_409` (real 409 path through the API); new
**`test_gemini_embeddings_e2e.py`** — full upload→ingest→query loop through the *real*
`GeminiEmbeddingProvider` (monkeypatches only the genai SDK boundary, seeds a Gemini key,
asserts both `RETRIEVAL_DOCUMENT` and `RETRIEVAL_QUERY` flow). Docs updated (README's stale
"nothing leaves the machine" privacy claim corrected; `docs/architecture.md` embeddings
section + a code sample that imported the deleted `get_embedding_provider`). Backend
**363✓**, mypy/ruff/format clean.

**Process note:** built spec → plan → subagent-driven TDD (per-task + final whole-branch
review). Specs/plans under `docs/superpowers/`.

**Action required before use against existing data:** see the *Data note* under Repo &
branch state above — `TRUNCATE chunks` and re-ingest, and set a verified Gemini key.

### Global provider keys + LLM-vision OCR — branch `feat/global-provider-keys` (merged as #111)

**Bug:** Ukrainian/Cyrillic image uploads ingested as gibberish (local RapidOCR dict is
Chinese+English only). **Fix + reshape:**
- **Global provider keys.** `ProviderSetting` model + migration `d4f1a2b7c9e0` (drops the
  per-repo key); `services/providers.py` (verify-then-store, decrypt, answerability);
  `api/providers.py` (`GET/PUT/DELETE /admin/providers`, verify on save → 400 on bad key);
  frontend `AdminProvidersPage` + nav tab + `api/providers.ts`.
- **Repo picks a model.** `LLMConfigRequest`={provider, model} (no key); `set_llm_config`
  requires a verified provider; `list-models` uses the global key; `build_repo_llm`/query
  resolve the key from `provider_settings` (build is now async). `RepoConfigPanel` reworked:
  provider select (only verified enabled), model auto-loads, no key input; empty-state hint.
- **LLM-vision OCR.** `llm/ocr.py` `transcribe_image` (gemini/openai/anthropic/openrouter,
  `OCRError`, HEIC→JPEG); ingestion routes images through the repo's model; 409 fail-fast on
  image upload to an unanswerable repo. RapidOCR + `ingestion/ocr.py` removed.

Tests: new `test_providers_api`, `test_llm_ocr`; reworked `test_repositories_api`,
`test_sources_api` (image-OCR-via-model + blocked-409), `test_models`, query/notes/logging
(seed a verified `ProviderSetting`); frontend new `AdminProvidersPage.test`, reworked
`AdminRepositoriesPage.test`; e2e `providers.spec.ts` (replaces `llm-config.spec.ts`),
`admin`/`sources` updated. Backend 354✓, frontend 68✓, e2e 4✓.

### LLM config panel redesign — single model dropdown + optional key — squash-merged (#109)

Fixes the config panel (`RepoConfigPanel` in `AdminRepositoriesPage.tsx`): a configured
repo could not change its model because the API-key field was `required`, and the model was
a free-text `<input>` plus a separate select. Now:
- **Model is one field** — a single `<select>` showing the current model and the loaded
  alternatives (the free-text `model-{id}` input is gone).
- **Auto-load on open** — when the selected provider already has a relevant stored key, the
  model list is fetched automatically (stored key), current model preselected.
- **Key optional** — the key field only appears when there's no relevant stored key (new repo,
  or a switched provider); an already-keyed provider shows **"Replace key"** instead. Saving a
  model/provider change no longer requires re-entering the key.
- **Backend:** `LLMConfigRequest.api_key` is now optional; `set_llm_config` keeps the stored
  key when the key is omitted, and 400s only when no key exists at all.

Tests: backend `test_repositories_api` (requires-key-when-none-stored 400, update-model-without-key
keeps key) → 341✓; frontend `AdminRepositoriesPage.test` (unconfigured flow, configured
change-without-key, Replace-key) → 65✓; new e2e `llm-config.spec.ts` (configure → change model
without re-entering key, PUT carries no `api_key`) → e2e **4✓**.

### Multi-file upload on the admin Sources page — squash-merged

The document picker took one file at a time. Now `<input multiple>` + upload every
selected file concurrently via `Promise.allSettled` (one failure doesn't sink the rest;
successes append, failures summarised). Each file already becomes its own background-ingested
source, so **no backend change**. Labels/button reflect the count ("Upload N files"); EN + UK
strings added; e2e `sources.spec.ts` label updated ("Document" → "Documents"). Frontend only.

### Chat + memory on the query page — squash-merged

The query page was one-shot Q&A; now it's a real chat **with memory** (user chose the
"chat + memory" scope over visual-only). Frontend: `QueryPage` renders question/answer as
right/left bubbles with a bottom composer (Enter sends, Shift+Enter newline), auto-scroll,
and a "thinking" placeholder; each ask sends the running `history`; switching repository
starts a fresh conversation. `QueryTurn` now renders the two bubbles (its citation→source
highlight + passage view unchanged). Backend: `QueryRequest` gains an optional bounded
`history` (`MAX_HISTORY_TURNS = 10`); `LLMProvider.answer` + shared `build_user_message`
thread it into a "Conversation so far" preamble; `SYSTEM_PROMPT` gains a line — use history
only to interpret the question, answer ONLY from numbered sources, never treat a prior answer
as a source. Retrieval is contextualised for terse follow-ups by prepending the previous
question to the embedding query (answered/logged question stays raw). EN + UK strings added.
Tests: backend 340✓ (citations + query-api history threading), frontend 63✓ (follow-up sends
history, repo change clears it), e2e 2✓.

### Chat e2e — squash-merged

Closed the gap left above: a Playwright spec (`e2e/query.spec.ts`) drives the chat in a real
browser against the real stack (real login, repo creation, grant, granted-repo listing) and
intercepts only the browser's `/query` call — the one piece that would otherwise need a live,
non-deterministic LLM — fulfilling it with a canned grounded answer. It asserts the exchange
renders as user/assistant **bubbles** and that a **follow-up carries the running `history`**
(first request `history: []`; second carries the first Q&A). Test-only; no source change. e2e
now **3✓**. Backend memory threading remains covered by pytest.

### Model-picker UX + green CI + drop dead provider-key env fallbacks — squash-merged

Three related fixes in one PR (branch `fix/model-picker-ux-and-ci`):

- **Model dropdown made visible (the "Gemini does not work" report).** Root cause was
  *not* Gemini: the list-models backend is correct — verified against the live Gemini API
  (56 models, 41 with `generateContent`). The frontend pushed results into a `<datalist>`,
  which renders **no visible change**, so a successful load looked like nothing happened.
  Replaced with a real `<select>` dropdown that appears once models load, plus a
  "Loaded N models" confirmation; selecting one fills the still-free-text Model input.
  New i18n keys `repositories.chooseModel` / `chooseModelPlaceholder` / `modelsLoaded`
  (EN + UK). `AdminRepositoriesPage.test.tsx` updated to assert the `<select>` + pick-fills-input.
- **CI green again (red since #101).** Prettier flagged 3 unformatted files
  (`AdminRepositoriesPage.tsx/.test.tsx`, `AdminUsersPage.tsx`) — `npm run format`. And
  masked behind that early failure, `tsc` couldn't find `process` in `vite.config.ts`
  (the `VITE_PROXY_TARGET` override) → added `@types/node` + `"types":["node"]` in
  `tsconfig.node.json`. Full suite now passes (ruff/mypy/pytest 334✓, vitest 60✓, build, e2e 2✓).
- **Removed dead process-wide provider keys.** `build_repo_llm` always passes the repo's
  own decrypted key ("never a process-wide default"), so the four `*_api_key` settings and
  their `or settings.X_api_key` fallbacks were unreachable. Dropped the config fields, the
  provider fallbacks, and the `.env.example` entries. Local `.env` left untouched (gitignored;
  `extra="ignore"` means leftover key lines are harmless — safe to delete by hand).

---

## Next up

**The three-feature request (A #101, B #102, C #103) is fully shipped. No feature work is
queued.** New i18n keys should be added to *both* `src/i18n/locales/en.json` and `uk.json`
(en/uk key sets must match, except UK's extra `_few`/`_many` plural forms); any new
user-facing string must go through `t()`, or it will render only in English.

The concrete follow-up surfaced by the #100 code review:

- **SSRF DNS-rebinding / TOCTOU hardening** (`src/contextvault/services/web_source.py`).
  The guard resolves the host with `getaddrinfo`, but httpx **re-resolves at connect**, so
  the connection isn't pinned to the validated IP — attacker-controlled DNS with a short
  TTL could point a validated host at `127.0.0.1`/`169.254.169.254` on the second lookup.
  **Acceptable to ship now:** the feature is admin-only and every redirect hop is
  re-validated. **Before non-admin exposure:** harden (resolve once, then connect to the
  validated literal IP with the original host as SNI/Host, e.g. a custom httpx transport)
  and run `/security-review` on the fetch path. Worth a card.

Other minor follow-ups (nice-to-have, not blocking):
- Pin `requires-python` ≥ 3.12.4 (or normalize `ipv4_mapped`) so IPv4-mapped IPv6
  loopback/metadata addresses can't slip the SSRF classifier on older CPython patches.
- Content-type filter accepts any `text/*`, not just HTML (currently harmless — trafilatura
  degrades gracefully).

Older candidate work (still not carded): token refresh/session renewal; char-span-scoped
citation passages; admin repo-list search/pagination. Create a card before starting any.

---

## Open known issues / gotchas

- **Frontend tooling versions are aligned deliberately:** vitest **3** with vite **6**
  (vitest 2 pulls a nested vite 5 → a dual-vite type clash). Keep them in step on upgrades.
- **Node 25's experimental `localStorage` global is non-functional and shadows jsdom's.**
  The test setup (`frontend/src/test/setup.ts`) installs an in-memory `Storage`; keep it.
- **`ENCRYPTION_KEY` required** before persisting or using any provider key. Generate:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  (`./dev.sh` auto-generates one into `.env` on first run.) Tests get a per-run key from `conftest`.
- **Forced-change enforcement lives in `get_current_user`** — any new authenticated backend
  endpoint that must be blockable by the bounce should depend on it.
- **CI warning (cosmetic):** `astral-sh/setup-uv@v6` runs on the deprecated Node 20.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; bring it up with
  `docker compose up -d` + `uv run alembic upgrade head`. (The persistent "1 skipped".)
- **OCR/web ingestion is mocked in tests** — the suite never runs RapidOCR or hits the
  network. To exercise them for real, use the e2e spec against `./dev.sh` (below).

---

## Working rules & gotchas (project conventions)

- **Board discipline:** cards are issues 1:1. Backlog/Ready → In progress at start,
  → In review when the PR opens, → Done after merge. Assign issues/PRs `--assignee @me`.
  PRs reference cards with `Refs #N`. Tick checkboxes **honestly**. (Use `work-on-card`.)
  *(This session's feature shipped outside the board via superpowers; the DNS-rebinding
  follow-up should be carded.)*
- **Verify the FULL CI-parity gate before pushing — CI checks more than the obvious.**
  Backend CI runs `ruff check src tests`, **`ruff format --check src tests`**, **`mypy`
  (no args → includes `tests/`)**, `alembic upgrade head`, `pytest`. Running only
  `ruff check` or `mypy src` locally **will miss** format diffs and test-only type errors.
- **`git add -p`/partial staging bit twice this session:** a verified fix stayed
  uncommitted and CI kept failing on the old file. **After committing, run
  `git status --porcelain` and confirm it's empty**, and re-run the gate on the committed
  state, before declaring green.
- **Backend DoD (all green):** the five CI steps above.
- **Frontend DoD (all green, from `frontend/`):** `npm run lint`, `npm run format:check`,
  `npm run typecheck`, `npm test`, `npm run build`. Node 22.
- **E2e (Playwright):** `frontend/e2e/*.spec.ts` drive the **real running stack** — bring
  it up with `./dev.sh` first, then `cd frontend && npm run test:e2e`. Not part of the CI
  jobs; run manually.
- **TDD:** RED → GREEN (minimal) → full gate. Update docs (README / `docs/`) in the **same
  PR** — hard rule. **No** "Implementation status" checklist in the README.
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b feat/<slug>`. Note: PRs are **squash-merged**, so after a merge your
  local `main` may diverge from a squashed `origin/main` — `git reset --hard origin/main`.
- **Merge policy (owner's standing directive):** open the PR, run the full gate + watch CI
  green, then squash-merge to `main` and move the card to Done autonomously.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope. Postgres enum value
  additions use `ALTER TYPE … ADD VALUE IF NOT EXISTS` **outside** the txn (`op.execute("COMMIT")`).
- Commit trailer: `Co-authored-by: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# One command — db + migrations + seeded admin + backend + frontend
./dev.sh
# App: http://localhost:5173 (admin / adminpass123) · API docs: http://localhost:8000/docs

# Backend gate (CI parity — note format --check and bare mypy):
docker compose up -d && uv run alembic upgrade head
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest

# Frontend gate + e2e (stack must be up for e2e):
cd frontend && npm install && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
cd frontend && npm run test:e2e   # Playwright, against ./dev.sh
```

See `README.md` for a quick start and `docs/architecture.md` for the full subsystem/endpoint reference.

---

## History

- **This session (owner requests, not board cards):** #109 LLM config redesign (single model
  dropdown + optional key), #108 chat e2e, #107 chat with memory, #106 multi-file upload,
  #105 visible model dropdown + green CI + drop dead `*_api_key` env fallbacks — all detailed
  under *Done recently* until they age out. Earlier: **#104** copy invite-link button (admin
  Users; clipboard copy of the accept-invite URL). **#103** EN/UK i18n via react-i18next,
  Ukrainian default (~150 strings, `contextvault.locale`). **#102** dynamic LLM model-list
  endpoint (`POST /repositories/{id}/llm-models`, `llm/models.py`). **#101** HEIC/HEIF image
  support (`pillow-heif`, `.heic`/`.heif` in `IMAGE_SUFFIXES`).
- **#100 Image (OCR) & web-link sources** — squash `2934091` (14 commits; spec+plan under
  `docs/superpowers/`). Local OCR (RapidOCR), SSRF-guarded web fetch (trafilatura), shared
  `store_parsed`, Playwright e2e. *(built via superpowers, not a board card)*
- #98 Visual polish + Playwright e2e — PR #99. #97 handoff refresh.
- #91 Deflake expired-grant test — PR #96. #90 User-facing source content — PR #95. #89 Repo rename/delete — PR #94.
- Docs: neat README + `docs/architecture.md` split — PR #93; Contributing/License — #92; overview/TOC — #88. `dev.sh` — #83.
- Admin UI epic: #40 Insights — PR #87. #39 Users & grants — PR #86. #38 Sources — PR #85. #37 Repositories — PR #84.
- Frontend foundation: #36 Query UI — PR #81. #35 Auth UI — PR #80. #34 Scaffolding — PR #79.
- Backend: FastAPI + pgvector + Argon2/JWT auth + admin bootstrap, ingestion pipeline, local embeddings, access-filtered retrieval, providers (Gemini/OpenAI/OpenRouter/Anthropic), numbered-chunk citations, `not_in_vault`, per-repo LLM config, encrypted keys, invitations, grants, query logging, knowledge gaps, analytics, Admin Notes. See `git log` and the board.
