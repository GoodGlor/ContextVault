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
- [x] JWT login + auth dependency
- [x] Role-based authorization (admin vs user)
- [x] First-admin bootstrap (seed/CLI)
- [x] `EmbeddingProvider` interface + local multilingual model

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

Retrieval embeds text with a pluggable `EmbeddingProvider` (`embed(texts) -> vectors`,
plus a `dimension`). v1 ships one local, free implementation
(`LocalEmbeddingProvider`) backed by a multilingual sentence-transformers model, so
document text never leaves the server and there is no per-call cost:

```python
from contextvault.embeddings import get_embedding_provider

provider = get_embedding_provider()      # config-driven, cached
vectors = provider.embed(["hello", "привіт"])   # each len == provider.dimension
```

The model is loaded lazily on first use, then reused. The default is
[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) — multilingual (Russian /
Ukrainian / English), 1024-dim, and needs no query/passage prefixes.

**Swapping the model is a config change** (`EMBEDDING_MODEL` + `EMBEDDING_DIM`).
The two must agree: on first load the provider checks the model's real width against
`EMBEDDING_DIM` and raises if they differ. Because vectors are stored in a fixed-width
pgvector column, changing the model (or its dimension) means a schema migration and
re-embedding existing chunks. To use the `multilingual-e5` family instead, set
`EMBEDDING_MODEL=intfloat/multilingual-e5-large` (also 1024-dim); note e5 expects
`query:` / `passage:` prefixes for best results.

Because the real model is large, the automated tests use a fake and skip the
model-download test by default. Run it explicitly with:

```bash
RUN_EMBEDDING_MODEL_TESTS=1 uv run pytest tests/test_embeddings.py
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
