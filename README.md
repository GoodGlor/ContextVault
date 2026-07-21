# ContextVault

> An admin-curated, NotebookLM-style RAG assistant with per-user access control and
> per-repository model choice.

ContextVault lets an admin build trusted knowledge repositories ("vaults") by
ingesting documents and writing Admin Notes, then lets users ask natural-language
questions and get **grounded, cited** answers — scoped strictly to the repositories
each user has been granted. When an answer isn't in a vault, the system says so
honestly and logs the gap for the admin to close. Each repository chooses its own
LLM, so different corpora can answer with different models.

## Features

- **Grounded, cited answers** — every response is generated from retrieved passages
  and carries numbered `[n]` citations back to the exact source spans.
- **Honest "not in this vault"** — when retrieval finds nothing relevant, the system
  refuses to fabricate and records the question as a knowledge gap.
- **Per-user access control** — users ↔ repositories is a many-to-many grant table,
  optionally time-boxed, and **hard-filtered at the SQL level** on every retrieval.
- **Per-repository model choice** — each vault stores its own provider / model / API
  key (encrypted at rest); there is no shared default.
- **Multi-provider generation** — Anthropic, OpenAI, Google (Gemini), and OpenRouter.
- **Curation flywheel** — a ranked knowledge-gap dashboard feeds Admin Notes (first-class,
  verified sources), and usage analytics show what's working.
- **Admin web UI + REST API** — a React SPA over a documented FastAPI backend, with
  admin surfaces for repositories, sources, users/grants, and insights.
- **Local, multilingual embeddings** — sentence-transformers (bge-m3), so no documents
  or queries leave for a third-party embedding service.

## Tech stack

- **Backend:** FastAPI · SQLAlchemy (async) + Alembic · Postgres + pgvector · Argon2 +
  JWT · sentence-transformers.
- **Frontend:** React + TypeScript + Vite (single-page app).
- **Tooling:** uv · ruff · mypy · pytest · Vitest + Testing Library · GitHub Actions CI.

## Quick start

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Python 3.12+ (uv provisions it),
and Docker + Docker Compose (for the local Postgres + pgvector database).

```bash
uv sync            # install backend dependencies (creates .venv)
cp .env.example .env
./dev.sh           # bring up the whole stack, then open http://localhost:5173
```

`./dev.sh` ensures an `ENCRYPTION_KEY` in `.env`, starts the database, applies
migrations, seeds an admin with a **known** password, and launches the backend
(`:8000`) and frontend (`:5173`) together. `Ctrl+C` stops both.

- **App:** http://localhost:5173 — login `admin` / `adminpass123`
  (override with `ADMIN_USER=me ADMIN_PASS=secret ./dev.sh`)
- **API docs (Swagger):** http://localhost:8000/docs

<details>
<summary>Run the pieces manually</summary>

```bash
docker compose up -d                       # Postgres + pgvector
uv run alembic upgrade head                # apply migrations
uv run uvicorn contextvault.main:app --reload   # API at :8000 (docs at /docs)

cd frontend && npm install && npm run dev  # SPA at :5173, proxies /api -> :8000
```

Seed the first admin (idempotent) with
`uv run python -m contextvault.cli create-admin --username admin --password '<strong>'`.
Alembic: `upgrade head` / `downgrade base` / `revision -m "…"`.
</details>

## Configuration

Settings come from the environment or `.env` (see [`.env.example`](.env.example)). The
essentials:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async Postgres connection string (matches `docker compose`). |
| `SECRET_KEY` | JWT signing secret — set a strong value (≥ 32 bytes) in production. |
| `ENCRYPTION_KEY` | Fernet key used to encrypt provider API keys at rest — **required** before any key is stored. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | Local embedding model and its vector width (must match). |

Per-repository LLM provider/model/key are configured at runtime through the admin UI,
not the environment.

## How it works

An admin creates a repository, configures its LLM, and ingests sources; the pipeline
**parses → chunks → embeds → stores** each document as vectors. A user asks a question
against a repository they've been granted: retrieval runs an **access-filtered** vector
search (hard-joined to active grants at the SQL level), the repository's configured LLM
generates an answer **only from the retrieved passages**, and the response carries `[n]`
citations. If nothing relevant is found, the answer is an honest "not in this vault" and
the question is logged as a knowledge gap for the admin to close with an Admin Note.

**Full reference:** [`docs/architecture.md`](docs/architecture.md) documents every
subsystem and endpoint (auth, invitations, grants, encryption, embeddings, ingestion,
retrieval, generation, the query loop, and the React SPA). Intended behavior lives in the
[design spec](docs/superpowers/specs/2026-07-07-contextvault-design.md); the live API
reference is at `/docs`.

## Project layout

```
src/contextvault/
  main.py            # FastAPI app factory + entrypoint
  cli.py             # `python -m contextvault.cli` (create-admin bootstrap)
  core/              # config, security (Argon2), crypto (Fernet), JWT tokens
  db/                # Base metadata + async engine/session
  api/               # routers (auth, invitations, users, repositories, grants, sources,
                     #   query, knowledge_gaps, analytics, health) + deps
  models/            # ORM models (users, repositories, sources, chunks, grants, query_log)
  retrieval/         # access-filtered vector search + question→chunks service
  llm/               # provider interface + Answer/Citation schema + providers + factory
  services/          # users, bootstrap, grants, invitations, ingestion, knowledge_gaps, analytics
migrations/          # Alembic (env.py + versions/)
dev.sh               # one-command local stack
tests/               # pytest suite
frontend/            # React + Vite + TS single-page app (see docs/architecture.md)
docker-compose.yml   # local Postgres + pgvector
```

## Development

Every change must keep **both** gates green — the backend suite and (for UI work) the
frontend suite. CI runs them as two jobs in `.github/workflows/ci.yml`.

```bash
# Backend
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy && uv run pytest        # Postgres up + migrated for DB-backed tests

# Frontend (from frontend/)
npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
```

Tests are isolated from your local `.env`: settings come only from real environment
variables and code defaults during the suite (`tests/conftest.py` sets
`CONTEXTVAULT_ENV_FILE=""`), so a local override can never change a test's result.

## Contributing

Work is tracked as cards on the **ContextVault** GitHub Projects board (each card is a
1:1 GitHub issue). The workflow for a change:

1. **Branch from fresh `main`:** `git fetch && git checkout main && git pull --ff-only`,
   then `git checkout -b feat/<slug>`.
2. **Test-first (TDD):** write a failing test, make it pass minimally, then refactor.
3. **Keep both gates green** — the commands under [Development](#development).
4. **Update the docs in the same change** — README / `docs/` must match the code (project
   *status*, though, lives on the board, not here).
5. **Open a PR against `main`**, reference the card with `Refs #<n>`, and **squash-merge**
   once CI is green.

Conventional commit prefixes (`feat:` / `fix:` / `docs:` / `chore:`) are used throughout.

## License

No license has been declared for this project yet, so it is under **exclusive
copyright** by default — see GitHub's [licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).
To make the terms of use explicit, add a `LICENSE` file (e.g. MIT, Apache-2.0) and
update this section.
