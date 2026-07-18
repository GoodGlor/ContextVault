# ContextVault — Session Handoff

- **Last updated:** 2026-07-18 09:16 EEST
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

**The backend is feature-complete.** This session merged the entire remaining
backend epic — **#28 delete/anonymize user, #29 access grants, #30 query logging,
#31 knowledge-gap dashboard, #32 Admin Notes (the curation flywheel), #33 query
analytics** — plus a self-authored hardening card **#76 (test `.env` isolation)**.
All seven are on `main`, each via its own squash-merged PR with green CI. The board
is **34 Done / 7 Backlog**, and the 7 remaining are all **React frontend (#34–#40)**.
No feature work is in flight; the tree is a clean, synced `main`. **Next:** card
**#34** (frontend scaffolding) — the first of the UI epic; the backend it consumes is
entirely in place.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (synced with origin, clean) |
| `main` HEAD | `be09354` — test: isolate tests from local .env (card #76) (#77) |
| Last merged PR | **#77** — card #76; before it #70–#75 (cards #28–#33) all merged, CI green |
| In flight | none |

**Clean state.** Working tree clean; `main` even with `origin/main`. Feature branches
`feat/28…`–`feat/33…` and `feat/76…` were deleted on merge (local + remote). Older
merged local branches from earlier cards remain; prune at leisure
(`git branch --merged main`).

---

## Done recently (this session)

Each card: TDD (RED→GREEN), full DoD gate green, own squash-merged PR, CI green,
card ticked honestly and moved to Done. Details on the issues/PRs; highlights:

### Card #28 — Delete / anonymize user ✅ merged (PR #70, `577436a`)
Admin-only `DELETE /users/{id}`, confirmation-gated (echo the username). Grants
cascade; admin-authored sources detach (`created_by = NULL`). **Last admin cannot be
deleted** (409). Relied on existing FK constraints — no migration.

### Card #29 — Access grants ✅ merged (PR #71, `2805c26`)
Admin grant/revoke with optional expiry + per-repo grant list; user `GET /repositories`
picker (active grants only). New `services/grants.py`; reused the existing Grant model
+ the active-grant predicate already enforced by retrieval. No migration.

### Card #30 — Query logging ✅ merged (PR #72, `6f3affa`)
`query_logs` table (**migration `550f1a28b886`**) + a write in the query endpoint
(now commits). Captures user/repo/question/`top_score`/`chunk_count`/`not_in_vault`.
**`user_id` FK `ON DELETE SET NULL`** — closes #28's forward reference (deleting a user
anonymizes past questions). Only *answered* queries log; pre-gate 4xx don't.

### Card #31 — Knowledge-gap dashboard ✅ merged (PR #73, `b70a68d`)
`GET /repositories/{id}/knowledge-gaps` (admin). Gaps = `not_in_vault` logs, aggregated
case/whitespace-insensitively, ranked by demand. Read-only over `query_logs`.

### Card #32 — Admin Notes as sources ✅ merged (PR #74, `b96dd4f`)
`POST /repositories/{id}/admin-notes` — an admin answer becomes an `admin_note`
source, ingested through the **same** parse→chunk→embed pipeline (body as `.txt`),
then retrievable and **cited Verified, attributed to the admin**. Query response
`sources` gained `verified` + `author`. End-to-end test proves the whole flywheel.
No migration (`SourceKind.ADMIN_NOTE` + `created_by` pre-existed).

### Card #33 — Query analytics ✅ merged (PR #75, `af690ec`)
One composite `GET /analytics` (admin): totals + answered/gap rate, per-repo volume,
top questions, active known users, daily answered-vs-gap series. `services/analytics.py`.
Extracted the shared `normalized_question` SQL helper into `services/query_log.py`
(used by #31 and #33). No migration.

### Card #76 — Isolate tests from the developer's local `.env` ✅ merged (PR #77, `be09354`)
**Self-authored.** `Settings.env_file` now resolves from `CONTEXTVAULT_ENV_FILE`
(default `.env`; empty disables). `conftest.py` sets it empty, so the suite reads only
real env vars + code defaults. **`uv run pytest` is now green with no override** even
with a local `.env` — retires the long-standing OpenRouter-default gotcha.

---

## Next up

### Card #34 — Frontend scaffolding (React, routing, API client, auth) [Backlog]
First of the frontend epic (#34–#40). The backend is complete and stable, so the UI
has a full, documented API surface to build against:
- **Auth:** `POST /auth/login` (JWT), `POST /auth/change-password`, forced-change bounce
  (`403 Password change required…`) — the UI must handle that gate (#27/#35).
- **Onboarding:** `POST /invitations` (admin), `POST /invitations/accept` (public).
- **User flow:** `GET /repositories` (granted picker) → `POST /repositories/{id}/query`
  (answer + citations + sources, each with `verified`/`author`).
- **Admin:** sources (`/repositories/{id}/sources`, `/admin-notes`), grants
  (`/repositories/{id}/grants`), LLM config (`/llm-config`), knowledge-gaps, `/analytics`.
- README documents every one of these with request/response shapes.
Frontend cards get the **`frontend`** label. Pick the sequencing with the user.

---

## Open known issues / gotchas

- **~~OpenRouter test fails locally~~ — FIXED by #76.** A bare `uv run pytest` is now
  green regardless of a local `.env` (settings ignore `.env` in tests). No override needed.
- **`ENCRYPTION_KEY` required** before persisting or *using* any provider key (routing
  decrypts at query time). Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Tests get a per-run key from `conftest`; CI sets one in `.github/workflows/ci.yml`.
- **Enum-reuse in migrations.** A new table referencing an existing PG enum (e.g.
  `user_role`) needs `postgresql.ENUM(..., create_type=False)` and its `downgrade()`
  must NOT drop the shared type. (`query_logs` #30 added no enum, so this didn't bite it.)
- **Forced-change enforcement lives in `get_current_user`.** Any new authenticated
  endpoint that must be blockable by the `must_change_password` bounce should depend on
  `get_current_user` (or `require_admin`/`require_role`) — not `get_authenticated_user`.
- **CI warning (cosmetic):** `astral-sh/setup-uv@v6` runs on the deprecated Node 20.
- DB-backed tests **skip** (not fail) when Postgres is unreachable; bring it up with
  `docker compose up -d` + `uv run alembic upgrade head`. (The persistent "1 skipped".)

---

## Working rules & gotchas (project conventions)

- **Board discipline:** cards are issues 1:1. Backlog/Ready → In progress at start,
  → In review when the PR opens, → Done after merge. Assign issues/PRs `--assignee @me`.
  PRs reference cards with `Refs #N` (no closing verb). Tick checkboxes **honestly**.
  (Use the `work-on-card` skill — it owns the board mechanics + IDs.)
- **Read the card's *comments*, not just the body** — a human comment can refine scope.
- **DoD gate (all green):** `uv run ruff check src tests`, `uv run ruff format --check src tests`,
  `uv run mypy`, `uv run pytest`. Also runs in **GitHub Actions CI** against a pgvector Postgres.
- **TDD:** RED (fails for the right reason) → GREEN (minimal) → full gate. Update docs
  (README/`docs/`) in the **same PR** — hard rule. **No** "Implementation status" checklist
  in the README (the board is the source of truth).
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b feat/<N>-<slug>`. Conflict-check with
  `git merge-tree --write-tree origin/main HEAD` before opening a PR.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope; reuse shared enums via
  `create_type=False`. Autogenerate with `uv run alembic revision --autogenerate` and
  verify up→down→up.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
docker compose up -d                 # Postgres + pgvector
uv run alembic upgrade head          # migrate
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest                        # green regardless of your local .env (card #76)
```

---

## History

- #76 Isolate tests from local `.env` — PR #77 (`be09354`). *(this session)*
- #33 Query analytics — PR #75 (`af690ec`). #32 Admin Notes as sources — PR #74 (`b96dd4f`). *(this session)*
- #31 Knowledge-gap dashboard — PR #73 (`b70a68d`). #30 Query logging — PR #72 (`6f3affa`). *(this session)*
- #29 Access grants — PR #71 (`2805c26`). #28 Delete/anonymize user — PR #70 (`577436a`). *(this session)*
- #27 Temp-password recovery + forced-change — PR #69 (`8ce6665`). #26 Invite links — PR #68 (`8aea201`).
- #25 Provider routing — PR #67 (`637cc2d`). #24 Per-repo LLM config — PR #65 (`b5e95a3`); CI ENCRYPTION_KEY — PR #66 (`53a7073`).
- #23 Encrypted API-key storage — PR #64 (`a1f0898`). #22 OpenRouter — PR #63 (`35f8cbc`). #20 OpenAI — PR #62 (`b7143d0`). #19 Query endpoint — PR #61 (`7e67867`).
- Earlier: foundation (FastAPI + pgvector + Argon2/JWT auth + admin bootstrap), ingestion pipeline, local embeddings, access-filtered retrieval, Gemini & Anthropic providers, numbered-chunk citations, `not_in_vault`, GitHub Actions CI. See `git log` and the board for detail.
