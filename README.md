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

## Implementation status

Foundation phase (design spec §9.1), tracked as it lands:

- [x] FastAPI skeleton + config + Docker Compose (Postgres + pgvector)
- [x] Async SQLAlchemy engine/session + Alembic (async) + pgvector extension migration
- [x] Core schema: users, repositories, sources, chunks(+vector), grants
- [x] Argon2 password hashing + user model
- [ ] JWT login + auth dependency
- [ ] Role-based authorization (admin vs user)
- [ ] First-admin bootstrap (seed/CLI)

The access boundary is expressed in SQL: `chunks.repository_id` is denormalized so
the permission filter (`join grants`) and the vector similarity search run as a
single query (design spec §4/§6).

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
  core/config.py     # Settings (pydantic-settings, .env)
  core/security.py   # Argon2 password hashing
  db/                # Base metadata + async engine/session
  api/               # routers (health, …)
  models/            # ORM models (users, repositories, sources, chunks, grants)
  services/          # business logic (added in later phases)
migrations/          # Alembic (env.py + versions/)
alembic.ini
tests/               # pytest suite
docker-compose.yml   # local Postgres + pgvector
```
