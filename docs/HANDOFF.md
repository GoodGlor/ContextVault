# ContextVault — Session Handoff

- **Last updated:** 2026-07-22
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

**The product backlog is complete — the board is 44 Done / 0 remaining.** ContextVault
is a full-stack, admin-curated RAG assistant: a FastAPI + Postgres/pgvector backend and
a React/Vite SPA, both feature-complete. This session finished the **admin UI epic**
(cards **#37–#40**: repositories + LLM config, sources + ingestion status, users +
grants, and the insights cockpit), then closed the remaining gaps it surfaced —
**#89** (rename/delete repositories), **#90** (user-facing cited-passage view), and
**#91** (deflaked a grant test). It also added a one-command dev runner (`./dev.sh`) and
reorganized the docs (a neat `README.md` + a detailed `docs/architecture.md`). No work is
in flight; the tree is a clean, synced `main`. **There is no next card** — see
*Candidate future work* below for ideas, but nothing is queued.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (synced with origin, clean) |
| `main` HEAD | `7788a12` — fix(test): deterministic expired-grant test (card #91) (#96) |
| Last merged PR | **#96** — card #91; before it #95 (#90), #94 (#89) |
| In flight | none |

**Clean state.** Working tree clean; `main` even with `origin/main`. Feature branches are
deleted on merge (local + remote). Older merged local branches may linger; prune at
leisure (`git branch --merged main`).

---

## Done recently (this session)

Each card: TDD (RED→GREEN), both gates green (backend + frontend), its own squash-merged
PR with green CI, card ticked honestly and moved to Done.

**Admin UI epic (#37–#40)** — built on the SPA foundation, all admin-only and guarded by
`RequireAuth requireAdmin`, linked from the header nav:
- **#37** — `/admin/repositories`: list all repos + create + per-repo LLM config editor
  (write-only, masked key). Added backend `POST /repositories` and `GET /admin/repositories`.
- **#38** — `/admin/sources`: upload a document, auto-poll ingestion status, delete. (Backend already existed.)
- **#39** — `/admin/users`: invite (one-time token), accounts (reset password / confirm-gated delete), and per-repo access grants. Added backend `GET /users`.
- **#40** — `/admin/insights`: knowledge-gap dashboard → inline Admin Note editor, plus analytics overview. (Backend already existed.)

**Follow-up cards:**
- **#89** — repository **rename** (`PATCH /repositories/{id}`) and **delete**
  (`DELETE /repositories/{id}`, confirmation-gated; sources/chunks/grants cascade), with UI.
- **#90** — **user-facing source content** (`GET /repositories/{id}/sources/{source_id}`,
  active-grant-gated) + a "View passage" button on cited sources. Promoted the active-grant
  predicate to `grant_service.has_active_grant` (shared by the query + content endpoints).
- **#91** — deflaked `test_expired_grant_denies_visibility` (1-hour-past expiry, no clock race).

**Tooling & docs (no card):**
- `./dev.sh` — one command brings up db + migrations + a seeded admin + backend + frontend.
- `README.md` slimmed to a neat standard front page; the deep per-subsystem reference moved
  to `docs/architecture.md` (linked). README also gained Features / Tech stack / Contributing / License.

---

## Candidate future work (not carded)

Nothing is queued. If work resumes, natural next steps surfaced during the epic:
- **Token refresh / session renewal** — there is no refresh endpoint; a JWT simply expires
  and bounces the user to login.
- **Citation passage precision** — `GET …/sources/{id}` returns the *whole* source content;
  a chunk/char-span-scoped variant would let the UI highlight the exact cited span.
- **Admin repo-list search/pagination** — `GET /admin/repositories` returns everything.

Create a card (see *Working rules*) before starting any of these.

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

---

## Working rules & gotchas (project conventions)

- **Board discipline:** cards are issues 1:1. Backlog/Ready → In progress at start,
  → In review when the PR opens, → Done after merge. Assign issues/PRs `--assignee @me`.
  PRs reference cards with `Refs #N` (no closing verb). Tick checkboxes **honestly**.
  (Use the `work-on-card` skill — it owns the board mechanics + IDs.)
- **Read the card's *comments*, not just the body** — a human comment can refine scope.
- **Backend DoD (all green):** `uv run ruff check src tests`, `uv run ruff format --check src tests`,
  `uv run mypy`, `uv run pytest`. Runs in CI against a pgvector Postgres.
- **Frontend DoD (all green, from `frontend/`):** `npm run lint`, `npm run format:check`,
  `npm run typecheck`, `npm test`, `npm run build`. Runs as the CI `frontend` job (Node 22).
- **TDD:** RED (fails for the right reason) → GREEN (minimal) → full gate. Update docs
  (README / `docs/`) in the **same PR** — hard rule. **No** "Implementation status" checklist
  in the README (the board is the source of truth).
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b feat/<N>-<slug>`. Conflict-check with
  `git merge-tree --write-tree origin/main HEAD` before opening a PR.
- **Merge policy (this owner's standing directive):** open the PR, run the full gate +
  watch CI green, then **squash-merge to `main` and move the card to Done autonomously**
  (overrides work-on-card's default "stop at In review").
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope; reuse shared enums via
  `create_type=False`. Autogenerate with `uv run alembic revision --autogenerate` and
  verify up→down→up.
- Commit trailer: `Co-authored-by: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# One command — db + migrations + seeded admin + backend + frontend
./dev.sh
# App: http://localhost:5173 (admin / adminpass123) · API docs: http://localhost:8000/docs

# Or the gates by hand:
docker compose up -d && uv run alembic upgrade head
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest
cd frontend && npm install && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
```

See `README.md` for a quick start and `docs/architecture.md` for the full subsystem/endpoint reference.

---

## History

- #91 Deflake expired-grant test — PR #96 (`7788a12`). #90 User-facing source content — PR #95 (`55df0e0`). #89 Repo rename/delete — PR #94 (`93d27de`). *(this session)*
- Docs: neat README + `docs/architecture.md` split — PR #93; README Contributing/License — #92; README overview/TOC — #88. `dev.sh` one-command runner — #83.
- Admin UI epic: #40 Insights — PR #87. #39 Users & grants — PR #86. #38 Sources — PR #85. #37 Repositories — PR #84. *(this session)*
- Frontend foundation: #36 User query UI — PR #81. #35 Auth UI — PR #80. #34 Scaffolding — PR #79.
- Backend: foundation (FastAPI + pgvector + Argon2/JWT auth + admin bootstrap), ingestion pipeline, local embeddings, access-filtered retrieval, providers (Gemini/OpenAI/OpenRouter/Anthropic), numbered-chunk citations, `not_in_vault`, per-repo LLM config, encrypted keys, invitations, grants, query logging, knowledge gaps, analytics, Admin Notes. See `git log` and the board for detail.
