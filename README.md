# ContextVault

An admin-curated, NotebookLM-style RAG assistant with per-user access control and
per-repository model choice. See the design spec in
[`docs/superpowers/specs/2026-07-07-contextvault-design.md`](docs/superpowers/specs/2026-07-07-contextvault-design.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python dependency manager)
- Python 3.12+ (uv will provision it via `.python-version`)
- Docker + Docker Compose (for the local Postgres + pgvector database)

## Setup

```bash
# Install dependencies (creates .venv)
uv sync

# Copy the example environment file
cp .env.example .env
```

## Run

```bash
# Start Postgres + pgvector
docker compose up -d

# Run the API (http://127.0.0.1:8000, docs at /docs)
uv run uvicorn contextvault.main:app --reload

# Health check
curl http://127.0.0.1:8000/health   # -> {"status":"ok"}
```

## Database migrations

Migrations use Alembic (async). With the database running (`docker compose up -d`):

```bash
uv run alembic upgrade head     # apply migrations
uv run alembic downgrade base   # roll back
uv run alembic revision -m "add X"   # new migration
```

## Access boundary

The access boundary is expressed in SQL: `chunks.repository_id` is denormalized so
the permission filter (`join grants`) and the vector similarity search run as a
single query (design spec §4/§6).

## Authentication

`POST /auth/login` takes `{"username", "password"}` and returns a JWT:

```json
{ "access_token": "…", "token_type": "bearer", "must_change_password": false }
```

Send it as `Authorization: Bearer <token>` on protected routes. The
`get_current_user` dependency resolves the token to a user (401 otherwise); build
role guards with `require_role(...)` / `require_admin` (403 when the role is
insufficient). Tune `ACCESS_TOKEN_EXPIRE_MINUTES` and set a strong `SECRET_KEY`
(≥ 32 bytes) in production.

## First-admin bootstrap

Onboarding is invite-based, but the first admin has no one to invite them. Seed one
(idempotent — a no-op once any admin exists):

```bash
# credentials via flags…
uv run python -m contextvault.cli create-admin --username admin --password '<strong>'

# …or via the environment (INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD)
uv run python -m contextvault.cli create-admin
```

## Embeddings

Text is embedded through a pluggable `EmbeddingProvider`. v1 ships one local, free
implementation backed by a multilingual sentence-transformers model, so document
text never leaves the server. The default is
[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (multilingual, 1024-dim).

Swapping the model is a config change via `EMBEDDING_MODEL` and `EMBEDDING_DIM` —
the two must match the model's output width and the pgvector column.

## Quality checks (Definition of Done)

These are the commands every task's Definition of Done refers to:

```bash
uv run ruff check src tests           # lint
uv run ruff format --check src tests  # formatting
uv run mypy                           # strict type check
uv run pytest                         # tests
```

## Project layout

```
src/contextvault/
  main.py            # FastAPI app factory + entrypoint
  cli.py             # `python -m contextvault.cli` (create-admin bootstrap)
  core/config.py     # Settings (pydantic-settings, .env)
  core/security.py   # Argon2 password hashing
  core/tokens.py     # JWT create/decode
  db/                # Base metadata + async engine/session
  api/               # routers (health, auth) + deps (get_current_user, require_admin)
  models/            # ORM models (users, repositories, sources, chunks, grants)
  services/          # users, first-admin bootstrap
migrations/          # Alembic (env.py + versions/)
alembic.ini
tests/               # pytest suite
docker-compose.yml   # local Postgres + pgvector
```
