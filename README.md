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
  db/                # Base metadata + async engine/session
  api/               # routers (health, …)
  models/            # ORM models (added in later phases)
  services/          # business logic (added in later phases)
migrations/          # Alembic (env.py + versions/)
alembic.ini
tests/               # pytest suite
docker-compose.yml   # local Postgres + pgvector
```
