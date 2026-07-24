# Database-Backed Reports — Design Spec

**Date:** 2026-07-23
**Status:** Approved by owner (this session)
**Feature:** Connect an external SQL database to a repository; granted users request
reports in natural language ("prepare a report from 1 Jan to 31 Mar for Kyiv");
the LLM generates guardrailed SQL; results render as a PDF with a chart and a
stats table; reports can repeat on a nightly schedule.

---

## 1. Problem & goal

ContextVault today answers questions from *unstructured* knowledge (documents,
notes, web pages) via RAG. Structured questions — date ranges, per-city filters,
aggregates, trends — cannot be answered from embedded text chunks; they need real
SQL against live data. The owner wants users to ask for such reports in plain
language and receive a downloadable document with statistics and graphs.

**Explicit non-goal:** ingesting database rows into the RAG index. Report data
stays structured and is never chunked/embedded. (A snapshot-and-ingest DB source
was considered and set aside; see §10 Alternatives.)

## 2. Decisions locked with the owner

| Decision | Choice |
|---|---|
| Who writes the SQL | **The LLM generates it, behind strict layered checks** (not admin templates, not unchecked text-to-SQL) |
| Databases in slice 1 | **PostgreSQL + MySQL** |
| Output format in slice 1 | **PDF** (DOCX/PPTX later) |
| Who can generate reports | **Any granted user** of the repository |
| Report history visibility | **Own reports only; admins see all** (incl. generated SQL, for audit) |
| Scheduling | **In slice 1**: nightly re-run of a frozen, validated query |
| Admin UI location | **Per-repository "Database" tab** (connection belongs to a repo) |
| Report artifact | Stored on the report row (Postgres `BYTEA`) + downloadable; **not** fed into RAG |

## 3. Architecture overview

```
Admin (Database tab)                       User (Reports tab)
  connect Postgres/MySQL                     "report 1 Jan–31 Mar for Kyiv"
  test connection                                   │
  introspect schema                                 ▼
  allow-list tables/columns          LLM (repo's provider) → {sql, chart_spec}
  + optional descriptions                           │
        │                                  Guardrail stack (§5)
        ▼                                           │
  database_connections  ──────────────►  execute on read-only conn
   (creds encrypted)                                │
                                         matplotlib chart + stats table
                                                    │
                                         PDF (Unicode font) → generated_reports
                                                    │
                                   history (own; admin sees all) / download
                                                    │
                              optional: freeze as report_schedules (nightly)
```

Report generation runs as a background task with status polling — the same
pattern as source ingestion (`PENDING → PROCESSING → DONE | FAILED`, frontend
polls every 2 s).

## 4. Data model (3 new tables, Alembic migrations)

### `database_connections`
- `id` UUID PK, timestamps (existing mixins)
- `repository_id` FK → repositories, `ON DELETE CASCADE`
- `db_type` enum: `postgres | mysql`
- `host`, `port`, `database`, `username`
- `password_encrypted` — encrypted with `core/crypto` (same as provider keys)
- `exposed_schema` JSONB — the allow-list, captured/edited by the admin:
  `[{"table": "orders", "description": "...", "columns": [{"name": "city", "description": "..."}]}]`
- `created_by` FK → users, `ON DELETE SET NULL`
- One connection per repository in slice 1 (UNIQUE on `repository_id`).

### `generated_reports`
- `id` UUID PK, timestamps
- `repository_id` FK CASCADE, `connection_id` FK CASCADE
- `requested_by` FK → users CASCADE (history is per-user)
- `prompt` — the user's NL request, verbatim
- `generated_sql` — the final validated SQL (audit trail; admin-visible)
- `chart_spec` JSONB — `{chart_type, x_column, y_column, title}`
- `status` enum: `pending | processing | done | failed`; `error` text
- `pdf_data` BYTEA nullable — the artifact; `pdf_filename`
- `schedule_id` FK → report_schedules SET NULL — set when a run came from a schedule

### `report_schedules`
- `id` UUID PK, timestamps
- `repository_id` FK CASCADE, `connection_id` FK CASCADE
- `owner_id` FK → users CASCADE — receives the nightly report in their history
- `prompt` — original NL request (display only)
- `frozen_sql`, `frozen_chart_spec` JSONB — the validated artifacts, re-executed verbatim
- `run_at_time` TIME (server local; e.g. 01:00), `enabled` bool
- `last_run_at`, `last_error`

**Freeze-the-SQL rationale:** nightly runs re-execute the already-validated SQL —
no LLM call, no nondeterminism, no per-night token cost. Rolling windows still
work because the LLM is instructed to express relative ranges as SQL
(`CURRENT_DATE - INTERVAL '30 days'`), which stays rolling; literal ranges stay
fixed, as they should.

## 5. The guardrail stack (defense in depth — all five layers, always)

1. **DB-level least privilege.** Admins are told (docs + UI copy) to supply a
   read-only role. The connection additionally sets the session read-only where
   the engine supports it and never commits.
2. **Parse, don't regex.** `sqlglot` parses the generated SQL; reject anything
   that is not exactly **one `SELECT`** statement — no DDL/DML, no multiple
   statements, no writing CTEs, and a deny-list of dangerous functions
   (`pg_sleep`, `pg_read_file`, `copy`, `load_file`, `sleep`, `benchmark`, …).
3. **Schema allow-list.** Every table/column referenced must appear in
   `exposed_schema`. The LLM prompt contains *only* the exposed schema (+
   admin descriptions); validation independently re-checks the AST against it.
4. **Resource ceilings.** Inject/enforce a row `LIMIT` (default 10 000), a
   statement timeout (Postgres `SET LOCAL statement_timeout`; MySQL
   `MAX_EXECUTION_TIME` hint), run inside a `READ ONLY` transaction that is
   always rolled back.
5. **Transparency.** `generated_sql` is stored on every report; admins can see
   it in the history. A wrong number is auditable, never a black box.

**Self-repair loop:** if validation or execution fails, the error is fed back to
the LLM for at most **2 retries**; every attempt passes the full stack. After
that the report fails with a readable message.

## 6. Report generation flow

1. `POST /repositories/{id}/reports` `{prompt}` (grant-gated) → creates
   `generated_reports` row (`pending`), schedules background task, returns row.
2. Background task: load connection + exposed schema → structured-output LLM
   call (repo's configured provider/model) returning `{sql, chart_spec}` →
   guardrails → execute → render → store PDF → `done` (or `failed` + error).
3. Frontend Reports tab polls `GET /repositories/{id}/reports` (own reports;
   `?all=true` admin-only) until terminal, then offers
   `GET /repositories/{id}/reports/{report_id}/download`.
4. "Repeat nightly at …" on a `done` report → `POST /repositories/{id}/report-schedules`
   freezes `{generated_sql, chart_spec}` into a schedule.

### Rendering
- **Chart:** matplotlib (`Agg` backend), one chart per report in slice 1
  (`bar | line | pie | none` per `chart_spec`), rendered to PNG in memory.
- **Stats table:** the result rows (capped for display), plus simple aggregates
  where numeric (count, sum, mean of the y column).
- **PDF:** `fpdf2` with a **bundled DejaVu Sans** font registered for both
  matplotlib and fpdf2 — Cyrillic must render correctly (□□□ boxes are a known
  default-font failure). Report prose (titles, labels) is written in the
  language of the user's request.

## 7. Scheduler

- In-process asyncio loop started in the FastAPI lifespan; wakes every 60 s,
  finds `enabled` schedules whose `run_at_time` has passed without a run today,
  and executes each as a normal report generation (owner = schedule owner,
  `schedule_id` set). No new infrastructure.
- Failures set `last_error` and do not disable the schedule; next night retries.
- Single-process assumption (matches current deployment). If the app ever runs
  multi-worker, add a DB advisory lock around the scheduler tick.

## 8. API surface (slice 1)

| Route | Who | Purpose |
|---|---|---|
| `PUT /repositories/{id}/database` | admin | create/replace connection (tests it first) |
| `GET /repositories/{id}/database` | admin | connection status, masked creds, exposed schema |
| `DELETE /repositories/{id}/database` | admin | remove connection (reports/schedules cascade) |
| `POST /repositories/{id}/database/introspect` | admin | list live tables/columns for allow-listing |
| `POST /repositories/{id}/reports` | granted user | request a report (NL prompt) |
| `GET /repositories/{id}/reports` | granted user | own history; `?all=true` for admins |
| `GET /repositories/{id}/reports/{rid}/download` | owner or admin | the PDF |
| `POST /repositories/{id}/report-schedules` | owner of source report | freeze a nightly schedule |
| `GET /repositories/{id}/report-schedules` | owner sees own; admin all | list |
| `PATCH /report-schedules/{sid}` | owner or admin | enable/disable/change time |
| `DELETE /report-schedules/{sid}` | owner or admin | remove |

Password is write-only: accepted on `PUT`, returned only masked.

## 9. Frontend (slice 1)

- **Admin "Database" tab** (per repo): connection form + "Test connection",
  introspection-driven allow-list editor with per-table/column description
  fields, masked credential display.
- **"Reports" tab** (all granted users): prompt box → generating state (poll) →
  result card (chart preview optional; download button; error message on
  failure) → history list → "Repeat nightly at …" action; admins additionally
  see all users' reports and the generated SQL.
- i18n: all new UI strings in EN + UK from the start.

## 10. Alternatives considered

- **Snapshot & ingest DB rows into RAG** — rejected for this goal: embeddings
  cannot do date-range/city filtering or aggregation; embedding transactional
  tables is costly and useless. (May return later as a *separate* "DB as
  knowledge source" feature, scoped to an admin-defined query.)
- **Admin-defined report templates, LLM fills parameters** — safer but less
  flexible; owner explicitly chose LLM-generated SQL behind strict checks.
- **Unchecked text-to-SQL** — rejected: untrusted numbers in a document meant to
  be trusted.

## 11. Dependencies & operational notes

- New backend deps: `aiomysql` (MySQL driver), `sqlglot` (SQL AST validation),
  `matplotlib` (charts), `fpdf2` (PDF) + bundled DejaVu Sans TTF.
- **SSRF posture is intentionally different from web sources:** DB hosts are
  *expected* to be internal/private — the public-only guard from
  `services/web_source.py` must NOT be applied. The boundary is: only admins
  can add connections; credentials encrypted at rest; read-only role.
- **Prerequisite before shipping:** rotate the exposed `ENCRYPTION_KEY` (and the
  two provider API keys) — DB credentials must not be stored under the burned
  key. Rotation invalidates stored provider keys; re-enter them in Providers.
- Known limitation (documented, accepted): data access is per-repository — any
  granted user can query anything the read-only role exposes. Row-level
  restrictions are the admin's job via the DB role / allow-list.

## 12. Testing strategy

- Unit: SQL guardrails (accept/reject matrix — multi-statement, DML, DDL,
  disallowed tables/columns/functions, LIMIT injection), chart-spec validation,
  schedule due-time logic, freeze semantics.
- Service: report generation happy path + self-repair loop + failure recording,
  against the test Postgres (as its own "external" DB).
- API: authz matrix (admin vs granted vs stranger; own-vs-all history), masked
  password, cascade deletes.
- Frontend: vitest for tabs/polling states; e2e later, consistent with repo
  practice (CI does not run e2e).
- MySQL driver path: unit-tested via dialect abstraction; live-MySQL testing is
  manual (no MySQL service in CI in slice 1 — noted, not hidden).

## 13. Out of scope (slice 1)

DOCX/PPTX export; multiple charts per report; multiple connections per repo;
MongoDB; email delivery of scheduled reports; retention/cleanup of old PDFs;
per-user row-level data restrictions.
