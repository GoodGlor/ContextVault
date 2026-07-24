# ContextVault — Session Handoff

- **Last updated:** 2026-07-24 20:00 EEST (custom OpenAI-compatible LLM provider — merged #118)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`. *(Recent feature work has shipped outside the board via superpowers SDD.)*

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete. **Nothing is in flight — `main` is clean, pushed,
and there are no open PRs.**

**Merged this session — custom (OpenAI-compatible) LLM provider, Phase 1 — #118 (squash `52196b1`).**
A new **`custom`** provider ("Custom, OpenAI-compatible") lets a deployment point chat / report /
OCR at a self-hosted model (Ollama, vLLM, LM Studio, TGI, LocalAI) via **one global endpoint** —
a nullable `base_url` column on `provider_settings` + an **optional** API key (keyless local
servers allowed; a `sk-noauth` placeholder is used only at client-construction time, never
persisted). It reuses the OpenAI Chat Completions wire path (like OpenRouter). One service seam,
`provider_service.get_call_credentials(session, provider) -> (api_key_or_placeholder, base_url)`,
resolves credentials at **every** call site (chat via `deps.build_repo_llm`, reports, OCR/ingestion,
model-list endpoint). Per-repo model selection with **free-text entry** for arbitrary local ids.
EN+UK i18n. Built via superpowers SDD (11 tasks + gate-fix + whole-branch review on opus = merge-as-is).
Full local gate green — backend **498✓** (ruff/format/mypy/alembic/pytest), frontend **109✓** (lint/
format/typecheck/test/build). Details under *Done recently*.

**⚠️ LOAD-BEARING CAVEAT — Phase 1 is NOT air-gapped.** Ingestion still calls **Gemini** to embed
documents (`deps.get_embedder` unchanged), so document text still leaves the network at index time.
A repo can chat via `custom`, but the *deployment* still needs a verified Gemini key to ingest.
Removing that coupling (local embeddings + per-dimension vector tables) is **Phase 2** — see the
spec's §11. Do not describe Phase 1 as "air-gapped" or "nothing leaves your network."

**⚠️ Two owner actions still gate real use of the database-backed reports feature (#116):**
1. **Rotate the three exposed `.env` secrets** — `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and
   `ENCRYPTION_KEY` were exposed in a screenshot earlier. Rotating `ENCRYPTION_KEY` invalidates
   the provider keys encrypted in `provider_settings` (re-enter them in the Providers tab) **and**
   any reporting-DB passwords (re-enter connections). Do this **before** creating real database
   connections, so credentials aren't stored under a burned key and then lost on the next rotation.
2. **MySQL is beta / untested live** — the reporting-DB connector + guardrails are dialect-abstracted
   and unit-tested through that abstraction, but no CI service spins up a real MySQL and the MySQL
   path has never run against an actual server. Postgres is the only dialect verified end-to-end.

**Owner note (from #112, still applies to existing data):** old bge-m3 vectors are incompatible
with Gemini's embedding space — `TRUNCATE chunks;` + re-ingest before trusting retrieval on a
pre-Gemini DB, and set a verified Gemini provider key or every ingest/query 409s.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (clean, in sync with `origin/main`) |
| `main` HEAD | `52196b1` (#118, custom OpenAI-compatible provider) |
| In flight | **nothing** — no open PRs; `feat/custom-openai-compatible-provider` merged + local branch deleted (remote branch still on GitHub — safe to delete) |
| Parked | `wip/passage-toggle` (off an older `main`) — a prior session's passage view/hide toggle, **never reviewed/merged**. Rebase, review, PR-or-drop. |
| CI | green on #118 (merged); local CI-parity gate green pre-merge (backend 498✓, frontend 109✓) |
| Local infra | `contextvault-db` (pgvector pg16) up + migrated (head `c1d2e3f40506`) |
| Migration head | **`c1d2e3f40506`** (#118 — `custom` enum value + `base_url` column + nullable key). Prev head `b8c2d5e7f901`. |

Recent merged PRs: **#118** custom OpenAI-compatible provider (`52196b1`) · **#117** workspace-sidebar
redesign (`c4ec625`) · **#116** database-backed reports (`1eb528e`).

---

## Done recently (this session)

### Custom (OpenAI-compatible) LLM provider — Phase 1 — merged as #118 (squash `52196b1`; superpowers SDD, 11 tasks + gate-fix + whole-branch review on opus)

Spec → plan under `docs/superpowers/` (`2026-07-24-custom-openai-compatible-provider.md` ×2; committed
`7f8aa3a` spec / `da7c0d0` plan / `fb23cf1` plan-fix). Additive backend + frontend, one Alembic
migration. What shipped (commit-by-commit, `3266b19`..`0efa41c`):

- **Data layer (`3266b19`).** `LLMProviderName.CUSTOM = "custom"`; nullable `base_url` (Text, not a
  secret — never encrypted) on `provider_settings`; `api_key_encrypted` relaxed to nullable. Migration
  `c1d2e3f40506` follows the repo's enum pattern (`op.execute("COMMIT")` before `ALTER TYPE ... ADD
  VALUE`, like `a1b2c3d4e5f6`); downgrade leaves the enum value (PG can't drop one).
- **Service seam (`20d3bc8`).** `get_provider_base_url`, `get_call_credentials -> (key_or_placeholder,
  base_url)`, `NOAUTH_PLACEHOLDER = "sk-noauth"` (never persisted), keyless verify/store; `get_provider_key`
  made NULL-safe (no `decrypt(None)` crash). `_base_url_for` → static `_static_base_url` + async resolver.
- **Answer path + dispatch (`4ff0438`, `9614d15`).** `base_url` threaded through `OpenAILLMProvider` +
  `get_llm_provider` (custom → OpenAI client at `base_url`); `custom` branch in model-listing (rename
  `_list_openrouter` → `_list_openai_compatible`, behavior-preserving), OCR, textgen.
- **All call sites (`fe87bce`).** deps/reports/ingestion/model-list endpoint all route through
  `get_call_credentials`; dropped the `assert key is not None` (keyless legit). Two deliberate,
  plan-mandated behavior changes: reports gates on `repo_is_answerable` (now needs `llm_selected`);
  `list_llm_models` gates on *verified* not *key-present*.
- **API + frontend (`18471be`, `68847ae`, `580ba37`, `f61a9ea`).** PUT `/admin/providers/{p}` takes
  optional key + `base_url` (custom requires base_url, cloud still requires key); status returns
  `base_url` unmasked. Providers page renders the custom row (base URL, optional key, **not-air-gapped
  embeddings note**); repo config uses a **free-text model input + datalist** so an empty `/v1/models`
  is never a dead end. EN+UK i18n (`86eb16c` prettier follow-up).
- **Docs (`1ad07f5`) + gate-fix (`0efa41c`).** `docs/architecture.md` custom-provider subsection + the
  Gemini-embeddings caveat; fixed 2 stale docstrings (deps `build_repo_llm`, providers "four→five").
  Gate-fix: the per-task runs missed `ruff format --check` + `mypy` — fixed 4 format files + 8 mypy
  annotations on test helpers (mechanical, no logic change).
- **Whole-branch review (opus): Ready to merge = YES**, zero Critical/Important. The one real risk
  (a half-threaded `base_url` leaving a site on the SDK default endpoint) verified absent at all sites.
- **Open follow-ups (non-blocking, accepted):** (a) **SSRF hardening** for the admin-supplied `base_url`
  (fetched server-side at verify + every call) — deferred, consistent with `web_source.py` posture; (b)
  provider **e2e** selector updates (CI skips e2e); (c) cosmetic minors logged in `.superpowers/sdd/progress.md`
  (reports error message wording; `noModels` also shows for custom empty list; "Remove key" on custom
  removes the whole endpoint). Phase 2 (local embeddings) / Phase 3 (Ollama-native UX) per spec §11.

*Workspace-sidebar redesign (#117), database-backed reports (#116) and older work under History.*

---

## Next up

1. **Custom-provider follow-ups (#118, each a candidate card, all non-blocking):** (a) **SSRF hardening**
   for the admin-supplied `base_url` (fetched server-side at verify + every call) — deferred, shares the
   `web_source.py` posture below; (b) provider **e2e** selector updates (CI skips e2e); (c) cosmetic
   polish in `.superpowers/sdd/progress.md` — tighten the reports "no verified key" message when the real
   gap is a missing model; consider suppressing `noModels` for custom; "Remove key" on the custom row
   removes the whole endpoint (label imprecise). **Phase 2** (local embeddings + per-dimension vector
   tables → true air-gap) / **Phase 3** (Ollama-native UX) per spec §11.
2. **Rotate the three exposed `.env` secrets** (see the ⚠️ owner-actions block in the TL;DR above) —
   owner action, because database-backed reports store reporting-DB passwords under `ENCRYPTION_KEY`.
   Settle the key **before** creating real connections; re-enter provider keys (Providers tab) and any
   DB connections afterward.
3. **Follow-ups for the reports feature (#116)** (each a candidate card):
   - **No retention/cleanup of old PDFs.** `GeneratedReport.pdf_data` (bytea) accumulates forever —
     no TTL/size-cap/purge. A busy nightly schedule grows the table unbounded; needs a policy.
   - **MySQL never run live** (beta) — add a CI MySQL service or hand-smoke-test before relying on it.
   - **Frozen schedules aren't re-validated against a later-narrowed allow-list** (spec-accepted):
     removing a now-sensitive column doesn't stop existing schedules from running the old frozen SQL.
   - **Revoked-grant users can still download their own past reports** (`get`/`download`/`delete`
     gate on owner-or-admin, not active grant — unlike `create`/`list`).
   - **No per-user row-level restrictions** (repo-level grants only) and **no DOCX/PPTX export**.
4. **Redesign follow-ups (#117)** — e2e selector updates for the sidebar nav (before next e2e run) and
   the two minor a11y-polish items. Small; do when convenient.
5. **Re-tune `retrieval_min_score` for Gemini embeddings (worth a card).** With Gemini even
   loosely-related chunks score ~0.7, so the current `0.3` threshold (tuned for bge-m3) barely
   filters. Flagged since #112.
6. **Decide the fate of `wip/passage-toggle`** — parked passage view/hide toggle, never reviewed.
   Rebase onto current `main`, review, PR or drop.
7. **SSRF DNS-rebinding / TOCTOU hardening** (`services/web_source.py`) — open from #100; **now also
   applies to the custom provider's admin-supplied `base_url`** (fetched server-side at verify + every
   call). Harden both + `/security-review` before non-admin exposure.
   `getaddrinfo` validates the host but httpx re-resolves at connect; not pinned to the validated IP.
   Safe as-is (admin-only, redirects re-validated); harden + `/security-review` before non-admin use.

---

## Open known issues / gotchas

- **All LLM credential resolution goes through one seam: `provider_service.get_call_credentials(session,
  provider) -> (api_key_or_placeholder, base_url)`.** Never re-fetch a key or hardcode a base URL at a
  call site — a half-threaded `base_url` silently sends `custom` traffic to OpenAI's default endpoint.
  `custom` may be **keyless**: the seam substitutes `NOAUTH_PLACEHOLDER = "sk-noauth"` at client
  construction only (never persisted), and `get_provider_key` returns `None` for a NULL stored key (do
  not `decrypt(None)`). A repo is "answerable" on `verified_at` alone (key optional for custom).
- **`custom` is NOT air-gapped (Phase 1).** Ingestion still requires a verified **Gemini** key to embed
  (`deps.get_embedder` unchanged). The Providers UI says so; keep that framing in any copy/docs.
- **Adding an `llm_provider` enum value needs the COMMIT-first migration pattern** — `op.execute("COMMIT")`
  before `ALTER TYPE llm_provider ADD VALUE IF NOT EXISTS '…'` (see `c1d2e3f40506` / `a1b2c3d4e5f6`).
  Postgres can't drop an enum value, so downgrades leave it in place.
- **The repository is one shared, route-scoped context now.** `src/repository/RepositoryContext` is
  the single source of `currentRepoId`; repo-scoped pages must read `useCurrentRepository()`, not add
  their own picker. The provider calls `useLocation()`, so it must stay mounted **under a Router**
  (it's a route element in `App.tsx` — fine). Workspace routes see the granted list, `/admin/*` sees
  all (admins) — if you add a new repo-scoped page, put it under the right path or the scope will be
  wrong.
- **The SQL guardrail is column-level, not just table-level** — `sql_guardrails.validate_sql` is the
  *only* column-visibility boundary (the read-only DB role blocks writes, not reads). Any change there
  is security-critical: err toward reject, and remember `exp.Star` (`SELECT *`, `t.*`) is not an
  `exp.Column`, so it needs its own check (this bit the Critical in #116).
- **`toBeInstanceOf(Blob)` (and other cross-realm `instanceof`) is flaky in vitest CI** — CI's fetch
  returns a Blob from a different realm than the test's global `Blob`; assert on `.type`/`.size`/
  duck-typed shape instead.
- **UUID primary keys populate on *flush*, not construction.** `UUIDPrimaryKeyMixin` uses
  `default=uuid.uuid4` at INSERT. If you need a new row's `id` for an FK, `await session.flush()` first
  or the FK goes in NULL.
- **Don't query `db_session` directly right after an API call that triggers background work**
  (ingestion OR report generation) — a direct `db_session.execute(...)` afterward **hangs**. Verify
  through the API instead.
- **In tests, TRUNCATE from the fixture deadlocks a same-Postgres reporting connection** — the
  `db_session` fixture clears with per-table `DELETE` (reversed `sorted_tables`), not `TRUNCATE …
  CASCADE`, because TRUNCATE's ACCESS EXCLUSIVE lock blocked `report_execution`'s independent
  connection to the same test DB.
- **matplotlib/fpdf2 need an explicit Unicode font for Cyrillic** — DejaVu Sans is registered for both
  in `report_render.py`; core fonts render □□□. Set the Agg backend before importing pyplot.
- **Stale `.mypy_cache` produces spurious `attr-defined` errors** on `contextvault.services` submodule
  imports. `rm -rf .mypy_cache && uv run mypy` is authoritative.
- **`f"...".encode("utf-8")` trips ruff UP012** (redundant arg) though `variable.encode("utf-8")` does
  not. Use `.encode()`.
- **`ENCRYPTION_KEY` required** before persisting/using any provider key or DB password (`./dev.sh`
  auto-generates one into `.env`; tests get a per-run key from `conftest`).
- **Frontend tooling:** vitest **3** with vite **6**; Node 25's experimental `localStorage` global is
  shadowed by the in-memory `Storage` in `frontend/src/test/setup.ts`. New i18n keys go in **both**
  `en.json` and `uk.json`.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; `docker compose up -d` +
  `uv run alembic upgrade head`.
- **e2e is not run by CI** (no Playwright in `.github/workflows`); run manually against `./dev.sh`.
  Port `:8000` can conflict — `export BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001 && ./dev.sh`.

---

## Working rules & gotchas (project conventions)

- **Verify the FULL CI-parity gate before pushing.** Backend CI runs `ruff check src tests`,
  **`ruff format --check src tests`**, **`mypy`** (bare → includes `tests/`), `alembic upgrade head`,
  `pytest`. `ruff check` ≠ `ruff format --check`. Frontend CI runs `npm run lint`, `format:check`,
  `typecheck`, `test`, `build`. A local Postgres on `:5432` matching the `.env` `DATABASE_URL` lets you
  run the whole backend job locally (done this session — 498 passed).
- **After committing, confirm `git status --porcelain` is empty** and re-run the gate on the committed
  state before declaring green.
- **TDD:** RED → GREEN (minimal) → full gate. Update docs in the **same PR** — hard rule (owner memory).
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b <slug>`. PRs are **squash-merged**, so after a merge local `main` may diverge —
  `git reset --hard origin/main` (or just `git pull --ff-only` once synced, as this session).
- **Merge policy (owner's standing directive):** open the PR, run the full gate + watch CI green, then
  the **owner** merges. Precedent (#117): owner confirmed the merge path (chose PR), CI watched green,
  owner merged themselves. Respect confirm-before-merge until told otherwise.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# One command — db + migrations + seeded admin + backend + frontend
./dev.sh
# App: http://localhost:5173 (admin / adminpass123) · API docs: http://localhost:8000/docs
# Port conflict? export BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001 && ./dev.sh

# Backend gate (CI parity — note format --check and bare mypy):
docker compose up -d && uv run alembic upgrade head
uv run ruff check src tests && uv run ruff format --check src tests && rm -rf .mypy_cache && uv run mypy && uv run pytest

# Frontend gate + e2e (stack must be up for e2e):
cd frontend && npm install && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
cd frontend && npm run test:e2e   # Playwright, against ./dev.sh — NOT in CI
```

See `README.md` for quick start and `docs/architecture.md` for the subsystem/endpoint reference.

---

## History

- **#118** custom (OpenAI-compatible) LLM provider — Phase 1 (`52196b1`): global `base_url` + optional
  key on `provider_settings`, one `get_call_credentials` seam, `custom` branch at all five dispatch
  sites, per-repo free-text model, EN+UK i18n, migration `c1d2e3f40506`. superpowers SDD (11 tasks +
  gate-fix + opus whole-branch review = merge-as-is). NOT air-gapped (Gemini still embeds → Phase 2).
  Detailed under *Done recently*.
- **#117** workspace-sidebar redesign (frontend-only): header → grouped sidebar, one route-scoped
  `RepositoryContext` switcher, Sources+Database → tabbed Data page with redirects. superpowers SDD,
  8 tasks + opus whole-branch review + fix pass. Detailed under *Done recently*.
- **#116** database-backed reports: admin connects a read-only Postgres/MySQL DB to a repo (encrypted
  creds, allow-listed tables/columns); NL request → repo's LLM writes SQL → **5-layer guardrail**
  (`sqlglot` AST: single-SELECT, allow-list tables+columns, no `SELECT *`, no dangerous funcs, LIMIT)
  → read-only + statement-timed execution → Cyrillic-safe `fpdf2` PDF (chart+stats) → per-user history
  + nightly frozen-SQL schedules. superpowers SDD (14 tasks); final whole-branch review caught a
  Critical `SELECT *` column-allow-list bypass (fixed). Deferred minors in `.superpowers/sdd/` (git-ignored).
- **#115** admin-note grounding (ingest `title\n\ncontent`) + auto-grant the repo-creating admin
  (`flush` before `grant_access`). **#114** persisted conversations + admin knowledge-gap rejection.
- **#112** Gemini API embeddings replace local bge-m3 (1024-dim; Gemini key now required). Existing
  data needs `TRUNCATE chunks` + re-ingest. **#111** global provider keys + LLM-vision OCR.
- **#105–#109** LLM config panel, chat e2e, chat-with-memory (later server-authoritative via #114),
  multi-file upload, visible model dropdown.
- **#100–#104** image(OCR)/web sources, HEIC, dynamic model-list endpoint, EN/UK i18n, copy-invite-link.
  Earlier: admin UI epic (#37–#40), frontend foundation (#34–#36), backend core (auth, ingestion,
  retrieval, providers, citations, not_in_vault, invitations, grants, query logging, knowledge gaps,
  analytics, Admin Notes). See `git log` and the board.
