# ContextVault — Session Handoff

- **Last updated:** 2026-07-24 (workspace-sidebar + unified Data page, task 8/8 — pending whole-branch review)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`. *(Recent feature work has shipped outside the board via superpowers SDD.)*

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete.

**In flight — workspace-sidebar redesign, branch `redesign/workspace-sidebar` (not yet
merged).** Frontend-only: top header nav → left sidebar with grouped nav + a repository
switcher that's the single source of the current repo (`RepositoryContext`); Sources +
Database admin pages merged into one tabbed **Data** page (`/admin/sources` and
`/admin/database` now redirect there). Built via superpowers SDD (8 tasks); this is the
final task (full gate + docs) — next is the whole-branch review, then a PR. Details
under *Done recently*.

**Merged this session — Database-backed reports — #116 (squash `1eb528e`).** An admin connects a
read-only Postgres/MySQL database to a repository (encrypted credentials, allow-listed
tables/columns); a granted user asks in natural language ("report from 1 Jan to 31 Mar for
Kyiv"); the repo's LLM writes SQL that passes a **5-layer guardrail**; it runs read-only +
statement-timed-out; the result renders to a **Cyrillic-safe PDF** (chart + stats); per-user
history + **nightly schedules** that re-run frozen SQL. Built via superpowers SDD (14 tasks,
per-task review + final whole-branch review). Backend **484✓**, frontend **93✓**, CI green.
Details under *Done recently*.

**⚠️ Two owner actions now gate real use of the reports feature:**
1. **Rotate the three exposed `.env` secrets** — `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and
   `ENCRYPTION_KEY` were exposed in a screenshot earlier. Rotating `ENCRYPTION_KEY` invalidates
   the provider keys encrypted in `provider_settings` (re-enter them in the Providers tab) **and**
   any reporting-DB passwords (re-enter connections). Do this **before** creating real database
   connections, so credentials aren't stored under a burned key and then lost on the next rotation.
2. **MySQL is beta / untested live** — the connector + guardrails are dialect-abstracted and
   unit-tested through that abstraction, but no CI service spins up a real MySQL and the MySQL path
   has never run against an actual server. Postgres is the only dialect verified end-to-end.

**Owner note (from #112, still applies to existing data):** old bge-m3 vectors are incompatible
with Gemini's embedding space — `TRUNCATE chunks;` + re-ingest before trusting retrieval on a
pre-Gemini DB, and set a verified Gemini provider key or every ingest/query 409s.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `redesign/workspace-sidebar` (local, not yet pushed) — frontend workspace-sidebar + unified Data page, built via superpowers SDD (8 tasks); this is task 8/8 (final gate + docs), next step is the whole-branch review before opening a PR |
| `main` HEAD | `1eb528e` (#116, database-backed reports) |
| In flight | `redesign/workspace-sidebar`, not yet pushed/PR'd — pending whole-branch review |
| Parked | `wip/passage-toggle` (off an older `main`) — a prior session's passage view/hide toggle, **never reviewed/merged**; carries stale HANDOFF edits. Rebase, review, PR-or-drop. |
| CI | green on #116 |
| Local infra | `contextvault-db` (pgvector pg16) up + migrated (head `b8c2d5e7f901`) |
| Migration head | `b8c2d5e7f901` (db-reports: `database_connections`, `generated_reports`, `report_schedules`; enums `database_type`, `report_status`) |

Recent merged PRs: **#116** database-backed reports (`1eb528e`) · **#115** admin-note grounding +
auto-grant creator (`c6f1e3a`) · **#114** persisted conversations + gap rejection (`3ec75c6`).

---

## Done recently (this session)

### Workspace sidebar + unified Data page — branch `redesign/workspace-sidebar` (superpowers SDD, 8 tasks; not yet merged — whole-branch review is next)

Frontend-only redesign: the old top header nav is gone, replaced by a left **sidebar**
(`src/components/Sidebar.tsx`) grouped into *Workspace* (Ask, Reports — everyone),
*Manage this repo* (Data, Providers, Insights — admin-only), and *Admin* (Repositories,
Users — admin-only). A **repository switcher** at the top of the sidebar is now the
single source of the current repo: a new `RepositoryContext`/`RepositoryProvider`
(`src/repository/`) owns `currentRepoId` (persisted to `localStorage`) and every
repo-scoped page (Ask, Reports, Data, Insights) reads it via `useCurrentRepository()`
instead of holding its own repo picker. The former **Sources** and **Database** admin
pages are merged into one **Data** page (`/admin/data`, tabs `?tab=documents|database`,
extracted as `SourcesPanel`/`DatabasePanel`); `/admin/sources` and `/admin/database` are
now `<Navigate>` redirects to `/admin/data`, so old bookmarks/links keep working. The
merged nav label is a one-string i18n change (`nav.data` / `data.title` in both
`en.json`/`uk.json`); the now-dead `nav.sources`, `nav.database`, `query.repository`,
`reports.repository`, `reports.errorLoadRepos` keys were deleted from both locales
(verified zero references first). Full frontend gate green (lint/format/typecheck/test/
build) with pristine test output (no `act(...)` warnings). **Follow-up, not a CI
blocker:** CI does not run the Playwright e2e specs, but any spec that navigated via the
old top-header links (rather than the sidebar) will need selector updates before it can
pass again — check `frontend/e2e/` next time e2e is run.

### Database-backed reports — merged as #116 (squash `1eb528e`; superpowers SDD, 14 tasks + final review)

Spec → plan under `docs/superpowers/` (`2026-07-23-db-reports-design.md` / `-db-reports.md`); each
task built TDD (RED→GREEN) with its own commit and an independent per-task review; a final
whole-branch review caught a Critical bug the per-task reviews missed. What shipped:

- **Reporting-DB connections.** `DatabaseConnection` model + `services/report_db.py` (per-call
  async engine; connection test + `information_schema` introspection; **no SSRF guard by design** —
  internal DB hosts are the point, boundary is admin-only + encrypted creds). Admin **Database**
  tab: connect, introspect, edit an allow-list of exposed tables/columns (with descriptions). Only
  allow-listed schema is shown to the LLM or queryable. `PUT`/`GET`/`PATCH .../database` +
  `POST .../database/introspect`, all admin-only; passwords Fernet-encrypted, never returned.
- **NL → guardrailed SQL → PDF.** `report_llm.py` prompts the repo's configured LLM with the
  allow-listed schema only, demanding a strict single-JSON contract (SQL + chart spec). The
  **guardrail** (`services/sql_guardrails.py`, `sqlglot` AST) enforces: exactly one `SELECT`; no
  DDL/DML/multi-statement; allow-listed tables **and columns** only; **no `SELECT *`** (would leak
  excluded columns); no schema-qualified tables; no `pg_`/`lo_`/`dblink` function family; no
  `SELECT INTO`; injected `LIMIT`. Execution (`services/report_execution.py`) runs in a
  rolled-back READ ONLY transaction with a statement timeout. The orchestrator
  (`services/reports.py`) feeds guardrail/execution errors back to the LLM for up to 2 self-repair
  retries. Rendering (`services/report_render.py`) → matplotlib chart + Unicode-safe `fpdf2` PDF
  (DejaVu font, so Cyrillic renders — verified via pypdf text round-trip).
- **Per-user history + PDF download.** `GeneratedReport` rows are per-requesting-user;
  `GET .../reports` returns the caller's own (admin `?all=true` sees all + the audit-trail
  `generated_sql`); `GET .../reports/{id}/download` streams the stored PDF bytes; owner-or-admin
  only, **404 (never 403)** for another user's report so existence isn't leaked; PDF bytes never
  appear in a JSON body.
- **Nightly schedules.** A schedule *freezes* a `DONE` report's already-validated SQL + chart spec;
  the scheduler (`services/report_scheduler.py`, an in-process asyncio loop started **only** in the
  FastAPI lifespan) re-executes the frozen SQL verbatim at `run_at_time` with **no further LLM
  call**. Single-process assumption (add a DB advisory lock if ever multi-worker). `report-schedules`
  API: freeze/list/toggle/delete.
- **Frontend.** `ReportsPage` (any authenticated user): pick a granted repo, request a report,
  2s-poll while generating, download the PDF, see failures inline, freeze a done report into a
  nightly schedule + manage schedules. `/reports` route + nav visible to all (unlike the admin-only
  Database tab). `api.getBlob` added to the client for binary download.
- **Final whole-branch review caught + fixed a Critical:** `SELECT *` bypassed the *column*
  allow-list (`exp.Star` ≠ `exp.Column`), leaking deliberately-excluded columns (PII) into PDFs.
  Fixed (reject projection `Star`, preserve `COUNT(*)`; verified via the real validator). Also
  folded in: bool excluded from numeric PDF stats, and doc/comment accuracy. **Deferred, non-blocking
  minors are listed in `.superpowers/sdd/progress.md`** (git-ignored) — see *Next up* for the two
  worth tracking.
- **CI-only test flake fixed post-open:** `client.test.ts` asserted `toBeInstanceOf(Blob)`; CI's
  fetch returns a Blob from a different JS realm than the test's global `Blob`, so the identity
  check failed in CI (passed locally). Now asserts `.type`/`.size` (realm-agnostic).

*Older completed work (#114, #115, #105–#112 etc.) demoted to History.*

---

## Next up

1. **Rotate the three exposed `.env` secrets** (see TL;DR) — owner action, now the top item because
   database-backed reports store reporting-DB passwords under `ENCRYPTION_KEY`. Settle the key
   **before** creating real connections; re-enter provider keys (Providers tab) and any DB
   connections afterward.
2. **Follow-ups for the reports feature** (each a candidate card):
   - **No retention/cleanup of old PDFs.** `GeneratedReport.pdf_data` (bytea) accumulates forever —
     no TTL/size-cap/purge. A busy nightly schedule grows the table unbounded; needs a policy before
     real use.
   - **MySQL never run live** (beta) — add a CI MySQL service or hand-smoke-test before relying on it.
   - **Frozen schedules aren't re-validated against a later-narrowed allow-list** (spec-accepted):
     if an admin removes a now-sensitive column from the allow-list, existing schedules keep running
     the old frozen SQL. Bounded (owner already saw the report when they froze it); worth a doc note
     and, if tightening, a re-validate-on-run pass.
   - **Revoked-grant users can still download their own past reports** (`get`/`download`/`delete`
     gate on owner-or-admin, not active grant — unlike `create`/`list`). Consistent-with-create would
     re-check the grant.
   - **No per-user row-level restrictions** (repo-level grants only) and **no DOCX/PPTX export**
     (PDF only) — both real gaps if the trust model or output needs grow.
3. **Re-tune `retrieval_min_score` for Gemini embeddings (worth a card).** With Gemini even
   loosely-related chunks score ~0.7, so the current `0.3` threshold (tuned for bge-m3) barely
   filters. Flagged since #112.
4. **Decide the fate of `wip/passage-toggle`** — parked passage view/hide toggle (frontend, green
   locally an earlier session, never reviewed). Rebase onto current `main`, review, PR or drop.
5. **SSRF DNS-rebinding / TOCTOU hardening** (`services/web_source.py`) — open from #100.
   `getaddrinfo` validates the host but httpx re-resolves at connect; not pinned to the validated
   IP. Safe as-is (admin-only, redirects re-validated); harden + `/security-review` before non-admin
   exposure.

---

## Open known issues / gotchas

- **UUID primary keys are populated on *flush*, not at construction.** `UUIDPrimaryKeyMixin` uses
  `default=uuid.uuid4`, applied at INSERT. If you need a new row's `id` for an FK (e.g. grant the
  creator on a just-created repo; link a report to a connection), `await session.flush()` first —
  else the FK goes in NULL.
- **The SQL guardrail is column-level, not just table-level** — `sql_guardrails.validate_sql` is
  the *only* column-visibility boundary (the read-only DB role blocks writes, not reads). Any change
  there is security-critical: err toward reject, and remember `exp.Star` (`SELECT *`, `t.*`) is not
  an `exp.Column`, so it needs its own check (this bit the Critical in #116).
- **`toBeInstanceOf(Blob)` (and other cross-realm `instanceof`) is flaky in vitest CI** — CI's fetch
  returns a Blob from a different realm than the test's global `Blob`; assert on `.type`/`.size`/
  duck-typed shape instead.
- **Don't query `db_session` directly right after an API call that triggers background work**
  (ingestion OR report generation, both use `run_*` against the test session) — a direct
  `db_session.execute(...)` afterward **hangs**. Verify through the API instead.
- **In tests, TRUNCATE from the fixture deadlocks a same-Postgres reporting connection** — the
  `db_session` fixture now clears with per-table `DELETE` (reversed `sorted_tables`) instead of
  `TRUNCATE … CASCADE`, because TRUNCATE's ACCESS EXCLUSIVE lock (held for the whole test) blocked
  `report_execution`'s independent connection to the same test DB. Isolation is unchanged (still
  inside the rolled-back outer txn); it's dynamic (new tables auto-covered).
- **Stale `.mypy_cache` produces spurious `attr-defined` errors** on `contextvault.services`
  submodule imports. `rm -rf .mypy_cache && uv run mypy` — a fresh run is authoritative.
- **`f"...".encode("utf-8")` trips ruff UP012** (string-literal encode with a redundant arg) even
  though `variable.encode("utf-8")` does not. Use `.encode()`.
- **Conversation history is server-authoritative** — the `/query` request body no longer accepts a
  `history` field.
- **matplotlib/fpdf2 need an explicit Unicode font for Cyrillic** — DejaVu Sans is registered for
  both in `report_render.py`; core fonts render □□□. Set the Agg backend before importing pyplot.
- **`ENCRYPTION_KEY` required** before persisting/using any provider key or DB password
  (`./dev.sh` auto-generates one into `.env`; tests get a per-run key from `conftest`).
- **Frontend tooling:** vitest **3** with vite **6**; Node 25's experimental `localStorage` global
  is shadowed by the in-memory `Storage` in `frontend/src/test/setup.ts`. New i18n keys go in **both**
  `en.json` and `uk.json`.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; `docker compose up -d`
  + `uv run alembic upgrade head`.
- **e2e is not run by CI** (no Playwright in `.github/workflows`); run manually against `./dev.sh`.
  The `:8000` port can conflict — `export BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001 && ./dev.sh`.

---

## Working rules & gotchas (project conventions)

- **Verify the FULL CI-parity gate before pushing.** Backend CI runs `ruff check src tests`,
  **`ruff format --check src tests`**, **`mypy`** (bare → includes `tests/`), `alembic upgrade head`,
  `pytest`. `ruff check` ≠ `ruff format --check`.
- **After committing, confirm `git status --porcelain` is empty** and re-run the gate on the
  committed state before declaring green.
- **Frontend DoD (from `frontend/`):** `npm run lint`, `npm run format:check`, `npm run typecheck`,
  `npm test`, `npm run build`. New i18n keys in **both** locales.
- **TDD:** RED → GREEN (minimal) → full gate. Update docs in the **same PR** — hard rule.
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b <slug>`. PRs are **squash-merged**, so after a merge local `main` may diverge —
  `git reset --hard origin/main`.
- **Merge policy (owner's standing directive):** open the PR, run the full gate + watch CI green,
  then squash-merge. *(This session the owner asked to confirm before each merge — respect that
  until told otherwise; the owner merged #116 themselves.)*
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

- **This session:** #116 database-backed reports (superpowers SDD, 14 tasks + final whole-branch
  review that caught a Critical `SELECT *` allow-list bypass) — detailed under *Done recently*.
- **#115** admin-note grounding (ingest `title\n\ncontent` so terse gap answers are groundable) +
  auto-grant the repo-creating admin (`flush` before `grant_access` — UUID PK populates on flush).
- **#114** persisted conversations (`conversations`/`conversation_turns`, server-authoritative
  `/query` history, GET/DELETE conversation, QueryPage hydrate + Clear) + admin knowledge-gap
  rejection (`gap_rejections`, reject-with-reason, rejected list). Includes the auth reload-logout
  fix (wire `configureApi` synchronously in render, not in a `useEffect`).
- **#112** Gemini API embeddings replace local torch/bge-m3 (1024-dim asymmetric; Gemini key now
  required, 409 otherwise). Existing data needs `TRUNCATE chunks` + re-ingest.
- **#111** Global provider keys (`ProviderSetting` + migration `d4f1a2b7c9e0`) + LLM-vision OCR
  (`llm/ocr.py`, fixes Cyrillic images).
- **#109** LLM config panel redesign. **#108** chat e2e. **#107** chat with memory (client-held,
  since made server-authoritative by #114). **#106** multi-file upload. **#105** visible model
  dropdown + drop dead `*_api_key` fallbacks.
- **#100–#104** image(OCR)/web sources, HEIC support, dynamic model-list endpoint, EN/UK i18n,
  copy-invite-link. Earlier: admin UI epic (#37–#40), frontend foundation (#34–#36), backend core
  (auth, ingestion, retrieval, providers, citations, not_in_vault, invitations, grants, query
  logging, knowledge gaps, analytics, Admin Notes). See `git log` and the board.
