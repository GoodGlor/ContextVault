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

## Provider API-key encryption

Provider API keys are live credentials, so they are **encrypted at rest** and only
ever stored as ciphertext. `core/crypto.py` wraps Fernet (AES-128-CBC + HMAC,
authenticated) behind three helpers:

- `encrypt(plaintext)` / `decrypt(token)` — round-trip a secret to a URL-safe
  ciphertext token and back. Encryption is non-deterministic (random IV per
  message), and a tampered or wrong-key token raises `EncryptionError` rather than
  returning garbage. Keys are decrypted into memory only at call time.
- `mask_key(key)` — a display-safe preview (`sk-…•••4f2a`) for surfacing a stored
  key in the UI/API without ever re-showing it in full.

The master key comes from `ENCRYPTION_KEY` (a Fernet key) in the environment or
secrets — never in the database or the code. It is unset by default: encryption
fails loudly instead of silently storing plaintext, so it must be set before any
provider key is persisted. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Wiring these helpers into per-repository provider configuration lands with the
repo-config card; this card provides the primitive.

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
An `Answer` also carries a **`not_in_vault`** flag — the first-class honest "not in
this vault" signal. It is `True` when the answer grounds nothing in the repository:
either retrieval surfaced no relevant chunks (the provider short-circuits) or the
model, given chunks, cited none of them. Downstream — the query endpoint and the
knowledge-gap dashboard — reads this flag rather than inferring the refusal from an
empty citation list, so a curated vault states it doesn't cover the question
instead of answering from the model's own training data (design spec §4).

### Providers and default selection

`get_llm_provider()` returns the system-default provider, chosen by the
`LLM_PROVIDER` setting (default **`gemini`**), so the RAG loop generates through
the contract and never names a vendor SDK:

```python
from contextvault.llm import get_llm_provider

provider = get_llm_provider()               # honours LLM_PROVIDER (default: gemini)
answer = await provider.answer(question, chunks)
```

All providers share the same behaviour: they lay the retrieved chunks out under
`[1..n]` markers, instruct the model to answer **only** from them, and parse the
`[n]` markers in the reply back into `Citation`s — no vendor-native citation
feature is used, so the citation experience is identical across providers. Empty
`chunks` short-circuit to the honest "not in this vault" answer (`not_in_vault=True`)
without an API call, and an answer that cites none of its sources is flagged the
same way. That numbered-chunk prompt/parse/map machinery lives in one shared module,
[`contextvault.llm.citations`](#numbered-chunk-citation-scheme), which every
provider imports. `get_llm_provider()` currently wires **Gemini**, **OpenAI**, and
**OpenRouter** (selectable via `LLM_PROVIDER`); the Anthropic provider joins the
factory when per-repo routing across providers lands in a later card.

#### Google (Gemini) — default

`GeminiLLMProvider` (via the Google GenAI SDK) is the current default.
Configuration (`.env` / settings): `GEMINI_API_KEY` authenticates the SDK
(falling back to the SDK's own `GEMINI_API_KEY` / `GOOGLE_API_KEY` resolution),
`GEMINI_MODEL` selects the model (default `gemini-2.5-flash`), and
`LLM_MAX_TOKENS` caps the answer length.

#### OpenAI (ChatGPT)

`OpenAILLMProvider` (via the OpenAI SDK's Chat Completions API) is selectable with
`LLM_PROVIDER=openai`. Configuration: `OPENAI_API_KEY` authenticates the SDK
(falling back to the SDK's own `OPENAI_API_KEY` resolution), `OPENAI_MODEL`
selects the model (default `gpt-4o`), and `LLM_MAX_TOKENS` caps the answer length.

#### OpenRouter (OpenAI-compatible gateway)

`OpenRouterLLMProvider` reaches [OpenRouter](https://openrouter.ai) — a single
gateway to hundreds of models — over the OpenAI-compatible wire format, so it
**subclasses `OpenAILLMProvider`** and reuses its request shape and citation
machinery unchanged; only the client is re-aimed at OpenRouter's base URL.
Selectable with `LLM_PROVIDER=openrouter`. Configuration: `OPENROUTER_API_KEY`
authenticates the SDK, `OPENROUTER_MODEL` selects the model — ids are
vendor-namespaced, e.g. `openai/gpt-4o` (the default) or
`anthropic/claude-3.5-sonnet` — `OPENROUTER_BASE_URL` overrides the gateway
endpoint (default `https://openrouter.ai/api/v1`), and `LLM_MAX_TOKENS` caps the
answer length.

#### Anthropic (Claude)

`AnthropicLLMProvider` (via the official Anthropic SDK) is constructed directly
today and joins `get_llm_provider()` when provider routing lands. Configuration:
`ANTHROPIC_API_KEY` authenticates the SDK, `ANTHROPIC_MODEL` selects the Claude
model (default `claude-opus-4-8`), and `LLM_MAX_TOKENS` caps the answer length.

#### Numbered-chunk citation scheme

The one place the citation machinery lives — every provider imports it, so
citations behave identically no matter which vendor generated the answer:

```python
from contextvault.llm.citations import (
    SYSTEM_PROMPT,        # grounding contract: answer only from the numbered sources
    NOT_IN_VAULT,         # the honest refusal text
    not_in_vault_answer,  # the flagged (not_in_vault=True) refusal Answer, no API call
    build_user_message,   # lays chunks out as [1..n] sources + the question
    parse_citations,      # maps the [n] markers in the reply back to source spans
)
```

`build_user_message` numbers the retrieved chunks `[1..n]` in rank order;
`parse_citations` reads the `[n]` markers back out of the model's answer and
resolves each to its exact source span — taking markers in first-appearance
order, collapsing repeats, and dropping any out-of-range (fabricated) marker so a
`Citation` always points at a real retrieved passage. The Anthropic, Gemini,
OpenAI, and OpenRouter providers only wire this scheme to their vendor SDK.

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

## Query API (the full RAG loop)

One endpoint runs the whole loop end-to-end — authenticate, enforce access,
retrieve, generate, cite:

| Method & path | Purpose |
|---|---|
| `POST /repositories/{id}/query` | Ask a question against one repository. Body `{"question": "..."}`; returns the grounded answer with citations and their source documents. |

Any authenticated user may call it, but access is enforced up front: the
repository must exist (`404` otherwise) and the caller must hold an **active
grant** on it (`403` otherwise — the same grant predicate the retrieval query
enforces at the SQL level, surfaced here as an explicit denial rather than an
empty result). An expired grant is treated as no grant.

Past the gate the loop is: embed the question → access-filtered, thresholded
retrieval → generate through the system-default `LLMProvider` (the `get_llm`
dependency; per-repo routing is a later card) → resolve the `[n]` markers to
source spans. The response is:

```json
{
  "answer": "…grounded prose with [1] markers…",
  "not_in_vault": false,
  "citations": [
    {"number": 1, "chunk_id": "…", "source_id": "…", "char_start": 0, "char_end": 42}
  ],
  "sources": [
    {"id": "…", "title": "policy.txt", "original_filename": "policy.txt", "kind": "document"}
  ]
}
```

`sources` lists the distinct documents the citations point at (first-cited
order), so the UI can label and link each `[n]`. When retrieval surfaces nothing
relevant, the honest "not in this vault" behaviour carries through the provider
untouched: `not_in_vault` is `true`, `answer` is the refusal text, and both
`citations` and `sources` are empty — the endpoint never special-cases it.

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
  core/crypto.py     # Fernet encrypt/decrypt + mask for provider keys at rest
  core/tokens.py     # JWT create/decode
  db/                # Base metadata + async engine/session
  api/               # routers (health, auth, sources, query) + deps (auth, embedder, llm)
  models/            # ORM models (users, repositories, sources, chunks, grants)
  retrieval/         # access-filtered vector search + question→chunks service
  llm/               # LLMProvider interface + Answer/Citation schema + Gemini/OpenAI/OpenRouter/Anthropic providers + factory
  services/          # users, first-admin bootstrap
migrations/          # Alembic (env.py + versions/)
alembic.ini
tests/               # pytest suite
docker-compose.yml   # local Postgres + pgvector
```
