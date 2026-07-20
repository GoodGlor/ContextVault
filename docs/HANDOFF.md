# ContextVault — Session Handoff

- **Last updated:** 2026-07-20 09:46 EEST
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

**The backend is feature-complete and the frontend epic is underway.** This session
stood up the **React SPA** and merged the first three UI cards — **#34 scaffolding,
#35 auth UI, #36 user query UI** — each via its own squash-merged PR with green CI.
The app now under `frontend/` (Vite + React + TypeScript) covers login / accept-invite /
forced-change and the core **ask-a-repo → cited-answer** experience. The board is
**37 Done / 4 Backlog**, and the 4 remaining are all **admin UI (#37–#40)**. No work is
in flight; the tree is a clean, synced `main`. **Next:** card **#37** (admin: repository
management + LLM config UI) — the backend it consumes is fully in place and documented.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (synced with origin, clean) |
| `main` HEAD | `323be38` — feat(frontend): user query UI (card #36) (#81) |
| Last merged PR | **#81** — card #36; before it #80 (#35), #79 (#34) this session |
| In flight | none |

**Clean state.** Working tree clean; `main` even with `origin/main`. Feature branches
`feat/34…`–`feat/36…` were deleted on merge (local + remote). Older merged local
branches from earlier cards remain; prune at leisure (`git branch --merged main`).

---

## Done recently (this session — frontend epic)

Each card: TDD (RED→GREEN), full frontend gate green + backend re-verified untouched,
own squash-merged PR, CI green, card ticked honestly and moved to Done. Highlights:

### Card #34 — Frontend scaffolding ✅ merged (PR #79, `847f6c0`)
Introduced the React SPA under **`frontend/`** (Vite 6 + React 18 + TS strict). Typed
`fetch` client (`src/api/client.ts`: `/api` prefix, JWT bearer, `ApiError` with backend
`detail`, 401 → session clear); `AuthProvider`/`useAuth` (`src/auth/`) holding a session
decoded from the JWT and persisted to `localStorage`; `RequireAuth`/`RequireSession`
route guards mirroring the backend forced-change bounce; base `Layout`. **17 tests**
(Vitest + Testing Library). Added a **separate `frontend` CI job** (Node 22:
lint/format/typecheck/test/build) to `.github/workflows/ci.yml`.

### Card #35 — Auth UI ✅ merged (PR #80, `a1daed9`)
Accept-invite screen (`/accept-invite?token=…` → `POST /invitations/accept` → **auto
sign-in** with the chosen password, since accept returns no token); confirm-password with
match validation on both new-password screens; login links to accept-invite. Token
handling: **no refresh endpoint exists**, so the client drops an **expired** token on
load and clears the session on any 401. **24 tests** (+7).

### Card #36 — User query UI ✅ merged (PR #81, `323be38`)
The app's home page (`/`) is now the query experience: granted-repo picker
(`GET /repositories`), ask form (`POST /repositories/{id}/query`), running conversation.
Inline `[n]` markers render as **clickable citation chips** (`parseAnswer` splits marker
from text) that highlight + scroll to the matching **Sources** entry (title, **Verified**
badge + author for Admin Notes, char span). Explicit **not-in-vault** callout; no-grants
empty state. **33 tests** (+9).

---

## Next up

### Card #37 — Admin: repository management + LLM config UI [Backlog]
First of the **admin UI** epic (#37–#40), built on the same SPA foundation. Backend is
complete and documented; the UI has a full API surface:
- **LLM config:** `PUT /repositories/{id}/llm-config` (provider/model/api_key) and
  `GET /repositories/{id}/llm-config` (returns the key **masked**, `configured` flag) —
  the key is write-only, never re-shown; an unconfigured repo can't answer (409 on query).
- **Repositories:** note there is currently **no create/list-all/rename repository
  endpoint** — `GET /repositories` returns only the *caller's granted* repos, not an
  admin's full list, and there is no `POST /repositories`. If #37 needs repo CRUD, that
  is a **backend gap** to raise as a card first (don't invent client calls to routes that
  don't exist — see the #36 pattern of scoping honestly to the real API).
- **Admin gating:** guard admin routes/pages with `RequireAuth requireAdmin`, and use
  the `admin` role already decoded from the JWT in `useAuth().session.role`.

The other admin cards: **#38** source upload + ingestion status (`POST /repositories/{id}/sources`,
`GET …/sources`, `GET /sources/{id}` status), **#39** user management + grants
(`/users`, `/repositories/{id}/grants`, invitations), **#40** knowledge-gap dashboard +
analytics + Admin-Notes editor (`/repositories/{id}/knowledge-gaps`, `/analytics`,
`POST /repositories/{id}/admin-notes`). Sequence with the user.

---

## Open known issues / gotchas

- **Frontend tooling versions are aligned deliberately:** vitest **3** with vite **6**
  (vitest 2 pulls a nested vite 5 → a dual-vite type clash). Keep them in step on upgrades.
- **Node 25's experimental `localStorage` global is non-functional and shadows jsdom's.**
  The test setup (`frontend/src/test/setup.ts`) installs an in-memory `Storage`; keep it.
- **No user-facing source-content endpoint.** `GET /sources/{id}` is **admin-only and
  metadata-only** — the query UI's citation click-through highlights the source
  *reference* (title/author/char span), not the raw passage. Rendering the passage text
  needs a **new backend card** (a user-scoped source-content route).
- **No repository CRUD / admin repo-list endpoint** (see Next up) — a likely backend gap
  for card #37.
- **~~OpenRouter test fails locally~~ — FIXED by #76.** Bare `uv run pytest` is green
  regardless of a local `.env`.
- **`ENCRYPTION_KEY` required** before persisting or using any provider key. Generate:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Tests get a per-run key from `conftest`; CI sets one in `.github/workflows/ci.yml`.
- **Forced-change enforcement lives in `get_current_user`** — any new authenticated
  backend endpoint that must be blockable by the bounce should depend on it.
- **CI warning (cosmetic):** `astral-sh/setup-uv@v6` runs on the deprecated Node 20.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; bring it up
  with `docker compose up -d` + `uv run alembic upgrade head`. (The persistent "1 skipped".)

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
  Commit `frontend/package-lock.json` (CI uses `npm ci`); `node_modules`/`dist` are gitignored.
- **TDD:** RED (fails for the right reason) → GREEN (minimal) → full gate. Update docs
  (README/`docs/`) in the **same PR** — hard rule. **No** "Implementation status" checklist
  in the README (the board is the source of truth).
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b feat/<N>-<slug>`. Conflict-check with
  `git merge-tree --write-tree origin/main HEAD` before opening a PR.
- **Merge policy (this owner's standing directive):** open the PR, run the full gate +
  watch CI green, then **squash-merge to `main` and move the card to Done autonomously**
  (overrides work-on-card's default "stop at In review"). Confirmed for this session's work.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope; reuse shared enums via
  `create_type=False`. Autogenerate with `uv run alembic revision --autogenerate` and
  verify up→down→up.
- Commit trailer: `Co-authored-by: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# Backend
docker compose up -d                 # Postgres + pgvector
uv run alembic upgrade head          # migrate
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy && uv run pytest         # green regardless of your local .env (card #76)

# Frontend
cd frontend
npm install
npm run dev                          # http://localhost:5173, proxies /api -> :8000
npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
```

---

## History

- #36 User query UI — PR #81 (`323be38`). #35 Auth UI — PR #80 (`a1daed9`). #34 Frontend scaffolding — PR #79 (`847f6c0`). *(this session)*
- #76 Isolate tests from local `.env` — PR #77 (`be09354`). #33 Query analytics — PR #75 (`af690ec`). #32 Admin Notes as sources — PR #74 (`b96dd4f`).
- #31 Knowledge-gap dashboard — PR #73 (`b70a68d`). #30 Query logging — PR #72 (`6f3affa`). #29 Access grants — PR #71 (`2805c26`). #28 Delete/anonymize user — PR #70 (`577436a`).
- #27 Temp-password recovery + forced-change — PR #69 (`8ce6665`). #26 Invite links — PR #68 (`8aea201`). #25 Provider routing — PR #67 (`637cc2d`). #24 Per-repo LLM config — PR #65/#66.
- #23 Encrypted API-key storage — PR #64. #22 OpenRouter — PR #63. #20 OpenAI — PR #62. #19 Query endpoint — PR #61.
- Earlier: foundation (FastAPI + pgvector + Argon2/JWT auth + admin bootstrap), ingestion pipeline, local embeddings, access-filtered retrieval, Gemini & Anthropic providers, numbered-chunk citations, `not_in_vault`, GitHub Actions CI. See `git log` and the board for detail.
