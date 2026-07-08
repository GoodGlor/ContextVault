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

## Document parsing

Uploads are turned into text by `parse_document(filename, data)`, the first stage
of the ingestion pipeline. It supports **PDF, DOCX, and TXT** and returns a
`ParsedDocument` — the full text plus positioned `TextBlock`s that tile it exactly.
Each block records its character span and, for PDFs, its 1-based page, so a citation
can later map a character offset back to its source passage.

```python
from contextvault.ingestion import parse_document

doc = parse_document("report.pdf", data)   # data: bytes
doc.text                                    # full extracted text
doc.blocks[0].page, doc.blocks[0].start     # position info for citations
```

Unsupported types raise `UnsupportedDocumentError`; corrupt or invalid files of a
supported type raise `DocumentParseError`.

## Chunking

The next stage, `chunk_document(parsed)`, slices a `ParsedDocument` into overlapping
`TextChunk`s sized for retrieval. Chunking is character-based, so every chunk keeps
the exact span it came from — `parsed.text[chunk.char_start:chunk.char_end] ==
chunk.text` — which is what later lets a citation jump to the highlighted passage. It
also carries the distinct source `pages` that span touches (empty for page-less
sources), derived from the parsed blocks.

```python
from contextvault.ingestion import chunk_document

chunks = chunk_document(doc)                 # or size=/overlap= to override
chunks[0].char_start, chunks[0].char_end     # span into doc.text, for citations
chunks[0].pages                              # e.g. (1, 2) for a chunk crossing a page break
```

Windows advance by `size - overlap` and the final window ends exactly at the end of
the text, so chunks tile it with no redundant tail. Size and overlap default to the
`chunk_size` / `chunk_overlap` settings (1000 / 150 characters) and must satisfy
`0 <= overlap < size`.

## Ingestion pipeline

`ingest_source(session, source, filename=…, data=…, embedder=…)` orchestrates the
full pipeline — **parse → chunk → embed → store** — for one source. It parses the
uploaded bytes, chunks the text, embeds every chunk with the given
[`EmbeddingProvider`](#embeddings), and stores the results as `Chunk` rows (each with
its char offsets and vector). It is **idempotent**: a re-ingest deletes the source's
prior chunks before writing the new ones.

Each `Source` tracks its own ingestion `status` — `pending → processing → done` on
success, or `failed` on error, with the message captured in `ingest_error`. Failures
are recorded on the source rather than raised, so they are never silent; a caller
inspects `source.status`.

```python
from contextvault.services.ingestion import ingest_source, run_ingestion

await ingest_source(session, source, filename="report.pdf", data=data, embedder=embedder)
source.status        # SourceStatus.DONE (or .FAILED, with source.ingest_error set)
```

Because ingestion is slower than a request should block on, `run_ingestion(source_id,
…)` is the seam a handler schedules via FastAPI `BackgroundTasks`: it opens its own
session (the request's is already closed by the time it runs) and delegates to
`ingest_source`. The embedding call runs off the event loop so it doesn't block other
requests.

## Retrieval (access-filtered vector search)

`search_chunks(session, user_id=…, repository_id=…, query_embedding=…)` is the
RAG loop's core access boundary. It runs the similarity search and the permission
check as a **single SQL query**: `chunks` is joined to `grants`, so a user only
ever retrieves from a repository they hold an **active (non-expired)** grant on —
the boundary lives in the query, not in app code layered on top (design spec §4/§6).

```python
from contextvault.retrieval import search_chunks

hits = search_chunks(
    session, user_id=user.id, repository_id=repo.id, query_embedding=vector, k=5
)
hits[0].content, hits[0].score            # passage text + cosine similarity (higher = closer)
hits[0].char_start, hits[0].char_end      # source offsets, for citation highlighting
```

Results are the top-k chunks ordered by cosine similarity (`k` defaults to the
`retrieval_top_k` setting). Chunks without an embedding are skipped. Similarity is
cosine, backed by an **HNSW ANN index** on `chunks.embedding` with
`vector_cosine_ops` (migration `b6be69ab221b`). This is the raw query layer.

### Retrieval service (question → relevant chunks)

`retrieve(session, question=…, repository_id=…, user_id=…, embedder=…)` is the
service the RAG loop calls. It embeds the question with the system-wide provider,
runs `search_chunks`, then keeps only hits whose cosine similarity clears a
**relevance threshold** (`min_score`, defaulting to the `retrieval_min_score`
setting). Filtering weak matches is what makes the honest "not in this vault"
answer and the knowledge-gap dashboard possible (design spec §4/§5).

```python
from contextvault.embeddings import get_embedding_provider
from contextvault.retrieval import retrieve

result = await retrieve(
    session,
    question="How do I rotate the signing key?",
    repository_id=repo.id,
    user_id=user.id,
    embedder=get_embedding_provider(),
)
result.chunks          # relevant hits, ranked closest first (empty → "not in this vault")
result.has_results     # True when at least one chunk cleared the threshold
result.top_score       # best similarity among *retrievable* chunks, or None
```

`top_score` distinguishes a **knowledge gap** (chunks exist but none relevant
enough — `top_score` set, `chunks` empty) from an **empty or inaccessible vault**
(`top_score` is `None`). The query endpoint builds on top of this.

## Generation (LLM provider interface)

Answers are generated through a pluggable `LLMProvider`, so any vendor —
Anthropic, OpenAI/OpenRouter, Google — sits behind one contract and the RAG loop
never depends on a vendor SDK (design spec §4/§7). This is the interface only;
concrete providers arrive in later cards.

```python
from contextvault.llm import Answer, Citation, LLMProvider

# every provider implements:
async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer: ...
```

An `Answer` is the answer `text` plus a list of `Citation`s. Because only Claude
has native citations, the scheme is **provider-agnostic**: the retrieved chunks
are numbered `[1..n]`, the model is told to cite those numbers, and each
`Citation` maps a marker back to its exact source span:

```python
Citation(number=1, chunk_id=…, source_id=…, char_start=120, char_end=170)
```

`char_start`/`char_end` are `None` when the cited chunk had no positional offsets.
An `Answer` with text but **no citations** is the honest "not in this vault"
response: when `chunks` is empty, a provider states the repository doesn't cover
the question instead of answering from the model's own training data (design spec
§4).

### Anthropic (Claude) provider

`AnthropicLLMProvider` is the first concrete provider, backed by Claude through
the official Anthropic SDK. It lays the retrieved chunks out under `[1..n]`
markers, tells the model to answer **only** from them, and parses the `[n]`
markers in the reply back into `Citation`s — Claude's native-citation feature is
deliberately unused so the citation experience is identical across every provider.
When `chunks` is empty it returns the honest "not in this vault" answer directly,
without spending an API call.

```python
from contextvault.llm.anthropic import AnthropicLLMProvider

provider = AnthropicLLMProvider()          # model + key from settings
answer = await provider.answer(question, chunks)
```

Configuration (`.env` / settings): `ANTHROPIC_API_KEY` authenticates the SDK,
`ANTHROPIC_MODEL` selects the Claude model (default `claude-opus-4-8`), and
`LLM_MAX_TOKENS` caps the answer length. The provider carries a self-contained
version of the numbered-chunk prompt/parse/map; a later card generalises that
scheme into a shared module the OpenAI/Google providers reuse.

## Source API (admin)

Admin-only endpoints manage a repository's sources and expose ingestion status. All
require an admin bearer token; non-admins get `403`.

| Method & path | Purpose |
|---|---|
| `POST /repositories/{id}/sources` | Upload a document (multipart `file`). Creates the source `pending` and schedules background ingestion; returns `201` with the source. |
| `GET /repositories/{id}/sources` | List a repository's sources (oldest first). |
| `GET /sources/{id}` | Fetch one source, including `status` and `ingest_error`. |
| `DELETE /sources/{id}` | Delete a source; its chunks cascade away. |

Upload returns immediately with `status: "pending"` — ingestion runs in the
background, so poll `GET /sources/{id}` to watch it move to `done` (or `failed`, with
`ingest_error` set). The embedding provider is injected via a dependency
(`get_embedder`), defaulting to the local model.

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
  retrieval/         # access-filtered vector search + question→chunks service
  llm/               # LLMProvider interface + Answer/Citation schema + Anthropic provider
  services/          # users, first-admin bootstrap
migrations/          # Alembic (env.py + versions/)
alembic.ini
tests/               # pytest suite
docker-compose.yml   # local Postgres + pgvector
```
