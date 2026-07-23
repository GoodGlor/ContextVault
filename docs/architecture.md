# ContextVault — Architecture & reference

Detailed reference for ContextVault's backend and frontend, split out of the
[README](../README.md) to keep the front page lean. It documents each subsystem's
behavior and API. The authoritative *intended* behavior is the
[design spec](superpowers/specs/2026-07-07-contextvault-design.md); the live,
interactive API reference is at `/docs` (Swagger) when the server is running.

## Contents

- [Authentication](#authentication)
- [First-admin bootstrap](#first-admin-bootstrap)
- [Invitations (onboarding)](#invitations-onboarding)
- [Provider API-key encryption](#provider-api-key-encryption)
- [Embeddings](#embeddings)
- [Document parsing](#document-parsing)
- [Chunking](#chunking)
- [Ingestion pipeline](#ingestion-pipeline)
- [Access grants (admin)](#access-grants-admin)
- [Retrieval (access-filtered vector search)](#retrieval-access-filtered-vector-search)
- [Generation (LLM provider interface)](#generation-llm-provider-interface)
- [Source API (admin)](#source-api-admin)
- [Repository management (admin)](#repository-management-admin)
- [Repository LLM configuration (admin)](#repository-llm-configuration-admin)
- [Query API (the full RAG loop)](#query-api-the-full-rag-loop)
- [Frontend (React SPA)](#frontend-react-spa)

## Authentication

`POST /auth/login` takes `{"username", "password"}` and returns a JWT:

```json
{ "access_token": "…", "token_type": "bearer", "must_change_password": false }
```

Send it as `Authorization: Bearer <token>` on protected routes. Two auth
dependencies resolve the token to a user (401 otherwise): `get_authenticated_user`
does authentication only, while `get_current_user` adds the forced-password-change
bounce (below) and is what every normal endpoint uses. Build role guards with
`require_role(...)` / `require_admin` (they chain off `get_current_user`, so they
enforce the bounce too; 403 when the role is insufficient). Tune
`ACCESS_TOKEN_EXPIRE_MINUTES` and set a strong `SECRET_KEY` (≥ 32 bytes) in
production.

### Listing users

- `GET /users` (**admin-only**) lists every account — `id`, `username`, `role`,
  `must_change_password`, `created_at` — oldest first. It never includes the
  password hash. This is the enumeration the admin user-management UI drives
  (reset / delete, and picking grant recipients).

### Password recovery & forced change

Account recovery is admin-issued (design spec §2):

- `POST /users/{id}/reset-password` (**admin-only**) issues a **random temporary
  password**, returned **once** in plaintext (only its hash is stored), and sets
  the user's `must_change_password` flag. The admin hands the temp password over
  and never learns the user's eventual real password.

  ```json
  { "temporary_password": "…", "must_change_password": true }
  ```

- **Enforcement:** while `must_change_password` is set, `get_current_user` bounces
  the user with `403 Password change required before continuing` — so *every*
  normal endpoint is blocked at the single auth chokepoint until the password is
  changed. `login` (unauthenticated) and `change-password` are the only reachable
  routes.

- `POST /auth/change-password` (**authenticated**, the escape hatch) takes
  `{"current_password", "new_password"}` (new password ≥ 8 chars), verifies the
  current password, sets the new one, and **clears the flag**. It depends on
  `get_authenticated_user`, so a bounced user can still reach it; it returns a
  fresh token whose `must_change_password` is now false.

An optional expiry on the temporary password is out of scope for this card.

### Deleting a user

Removing a user is destructive, so it is **confirmation-gated** and preserves
analytics signal by anonymizing rather than erasing (design spec §2):

- `DELETE /users/{id}` (**admin-only**) permanently deletes a user. The request
  body must **echo the target's username** — `{"confirm_username": "<name>"}` — or
  the call is a `400` no-op. Success returns `204 No Content`.

- **Cascade vs. detach.** The user's access grants are **cascade-deleted** (their
  access vanishes with the account), while their contributions are **detached**:
  admin-authored sources keep existing with `created_by = NULL` ("by a deleted
  user") instead of being deleted, so curation/analytics signal survives. This is
  enforced at the database (`grants.user_id ON DELETE CASCADE`,
  `sources.created_by ON DELETE SET NULL`).

- **Last-admin guard.** Deleting the **last remaining admin** is refused with
  `409 Conflict`, so the system can never be locked out of its bootstrap invariant.

> The user's **past questions are anonymized too** (spec §2 "detach past
> questions"): the `query_logs.user_id` FK is `ON DELETE SET NULL`, so this same
> delete detaches their logged queries ("asked by a deleted user") instead of
> erasing them — the analytics signal survives the account (see *Query logging*).

## First-admin bootstrap

Onboarding is invite-based, but the first admin has no one to invite them. Seed one
(idempotent — a no-op once any admin exists):

```bash
# credentials via flags…
uv run python -m contextvault.cli create-admin --username admin --password '<strong>'

# …or via the environment (INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD)
uv run python -m contextvault.cli create-admin
```

## Invitations (onboarding)

New accounts are created by invite, so an admin never sees or handles a user's
password (design spec §2). The flow is two endpoints:

- `POST /invitations` (**admin-only**) issues a single-use, expiring invite for a
  new `username` (optional `role`, default `user`; optional `expires_in_hours`,
  default `INVITE_EXPIRY_HOURS` = 72). It returns the raw token **once** — for the
  invite link — plus the username, role, and `expires_at`. A username that already
  has an account is rejected (409).

  ```json
  { "token": "…", "username": "alice", "role": "user", "expires_at": "…" }
  ```

- `POST /invitations/accept` (**public**) redeems a token: `{"token", "password"}`
  (password ≥ 8 chars). It creates the account with the user's own chosen password
  (`must_change_password` stays false — distinct from the temp-password recovery
  flow) and marks the invite spent. Returns the new `{id, username, role}` (201).

The token is high-entropy and stored **only as a SHA-256 hash**
(`core/invite_tokens.py`), so a leaked database never yields a usable invite — the
raw token lives solely in the link handed out once (unlike provider keys, which are
reversibly encrypted because they must be recovered; an invite is only ever
compared). An invite is valid while unexpired **and** unaccepted; reused, expired,
and unknown tokens all return one uniform `400 Invalid or expired invitation`, so a
caller cannot probe which tokens exist.

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

Text is embedded through a pluggable `EmbeddingProvider`. The shipped implementation,
`GeminiEmbeddingProvider`, calls Google's Gemini embedding API
(`gemini-embedding-001`, multilingual, 1024-dim via `output_dimensionality`) instead of
running a model locally — document and query text is sent to the configured Gemini
provider (consistent with how image OCR already sends content to the provider). It
uses Gemini's asymmetric task types (`RETRIEVAL_DOCUMENT` for ingested text,
`RETRIEVAL_QUERY` for questions) and L2-normalizes the returned vectors so retrieval's
cosine similarity is a plain dot product. Embeddings are global — one vector space for
every repository — so a verified global Gemini provider key is required; ingestion and
query both fail fast (409) without one.

Swapping the model is a config change via `EMBEDDING_MODEL` and `EMBEDDING_DIM` —
the two must match the model's requested output width and the pgvector column.

## Document parsing

Uploads are turned into text by `parse_document(filename, data)`, the first stage
of the ingestion pipeline. It supports **PDF, DOCX, TXT, and images** and returns a
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

### Images (local OCR)

Files with an image suffix — `.png .jpg .jpeg .webp .tiff .bmp` (`IMAGE_SUFFIXES`)
— are routed to the image parser instead of a document parser. It decodes the file
with Pillow, then reads text out of it with **local** OCR
([`rapidocr-onnxruntime`](https://github.com/RapidAI/RapidOCR), lazily loaded once
and cached) via `ocr_image` — no third-party OCR service ever sees the image. OCR
is **text-only**: it produces one page-less block of recognized text, with no
layout/position info beyond that. An unreadable image file raises
`DocumentParseError("Could not read image file.")`; an image with no recognizable
text raises `DocumentParseError("No text found in image.")`, which the ingestion
pipeline (below) turns into a `FAILED` source with that message in `ingest_error`
rather than storing an empty source.

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

`ingest_source` and `run_web_ingestion` (below) share a single writer,
`store_parsed(session, source, parsed, embedder)` — chunk → embed → replace-chunks →
mark `DONE` — so document/image ingestion and web ingestion converge on identical
storage behavior once they have a `ParsedDocument`.

### Web-link sources

`run_web_ingestion(source_id, url=…, embedder=…)` is the background-task seam for a
web-link source (mirrors `run_ingestion`): it marks the source `PROCESSING`, fetches
the page and extracts its main text off the event loop, then hands the result to
`store_parsed`. A page with no extractable text fails the source with `"No readable
text found at URL."`; on success, a title recovered from the page's metadata
replaces the source's title (the URL).

The fetch itself, `web_source.fetch_html(url)`, guards against fetching an
untrusted or hostile URL:

- **Scheme:** only `http`/`https` are accepted.
- **SSRF:** every hostname — including on each redirect hop — is resolved and
  rejected if any resolved address is private, loopback, link-local, reserved,
  multicast, or unspecified, so a URL can't be used to reach internal
  infrastructure.
- **Redirects:** followed manually (not via the HTTP client) up to 5 hops, each
  re-validated the same way; more hops raise `"Too many redirects."`.
- **Content type:** non-HTML/text responses are rejected.
- **Size cap:** the body is streamed and capped at 5 MiB; exceeding it raises
  `"Response exceeds the size cap."`.
- **Timeout:** a 15-second request timeout.

`web_source.extract_web_text(html)` then pulls the main article text (and title)
out of the fetched HTML with [trafilatura](https://trafilatura.readthedocs.io/),
discarding boilerplate (nav, ads, footers).

## Access grants (admin)

Access is a per-user, per-repository **grant** (many-to-many), optionally
time-boxed with an `expires_at` (design spec §6). An admin manages grants; every
retrieval is hard-filtered to the caller's **active** (non-expired) grants.

| Endpoint | Behavior |
|---|---|
| `POST /repositories/{id}/grants` | **Admin-only.** Grant a user access: body `{"user_id": "…", "expires_at": "<ISO-8601>"\|null}`. **Idempotent** — re-granting the same pair updates the expiry (never a duplicate). `404` if the repo or user is unknown. |
| `DELETE /repositories/{id}/grants/{user_id}` | **Admin-only.** Revoke a grant. `204` on success, `404` if no such grant. |
| `GET /repositories/{id}/grants` | **Admin-only.** List every grant on a repository (including expired ones — the audit view). |
| `GET /repositories` | **Any authenticated user.** The caller's repo picker: the repositories they hold an **active** grant on — never others', never expired ones. |

A grant with `expires_at` in the past grants nothing: the repository disappears
from `GET /repositories` and retrieval/query deny access with `403`. The
active-grant rule (`expires_at IS NULL OR expires_at > now()`) is the same one the
retrieval query and the query endpoint enforce, so management and enforcement never
drift.

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

The embedder is resolved per-request by the `get_embedder` FastAPI dependency, which
reads the global Gemini key from the database and raises `409` if none is configured
(there is no standalone factory to call outside a request):

```python
from fastapi import Depends
from contextvault.api.deps import get_embedder
from contextvault.embeddings import EmbeddingProvider
from contextvault.retrieval import retrieve

async def ask(
    ...,
    embedder: EmbeddingProvider = Depends(get_embedder),
):
    result = await retrieve(
        session,
        question="How do I rotate the signing key?",
        repository_id=repo.id,
        user_id=user.id,
        embedder=embedder,
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

`get_llm_provider()` builds a provider behind the shared contract, so callers
never name a vendor SDK. Called bare it returns the system-default provider chosen
by the `LLM_PROVIDER` setting (default **`gemini`**); called with a name — and
optionally an `api_key` and `model` — it builds that specific provider, which is
how per-repo routing constructs each repository's own LLM (see *Repository LLM
configuration*). Omitted `api_key`/`model` fall back to the settings defaults:

```python
from contextvault.llm import get_llm_provider

provider = get_llm_provider()               # honours LLM_PROVIDER (default: gemini)
answer = await provider.answer(question, chunks)

# per-repo routing: a specific provider built from stored config
repo_provider = get_llm_provider("openai", api_key=key, model="gpt-4o")
```

All providers share the same behaviour: they lay the retrieved chunks out under
`[1..n]` markers, instruct the model to answer **only** from them, and parse the
`[n]` markers in the reply back into `Citation`s — no vendor-native citation
feature is used, so the citation experience is identical across providers. Empty
`chunks` short-circuit to the honest "not in this vault" answer (`not_in_vault=True`)
without an API call, and an answer that cites none of its sources is flagged the
same way. That numbered-chunk prompt/parse/map machinery lives in one shared module,
[`contextvault.llm.citations`](#numbered-chunk-citation-scheme), which every
provider imports. `get_llm_provider()` wires all four providers — **Gemini**,
**OpenAI**, **OpenRouter**, and **Anthropic** — selectable by name (or, for the
system default, via `LLM_PROVIDER`).

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

`AnthropicLLMProvider` (via the official Anthropic SDK) is selectable through
`get_llm_provider("anthropic")` — the factory wire-up that lets a repository
configured for Anthropic route to it. Configuration: `ANTHROPIC_API_KEY`
authenticates the SDK, `ANTHROPIC_MODEL` selects the Claude model (default
`claude-opus-4-8`), and `LLM_MAX_TOKENS` caps the answer length.

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
| `POST /repositories/{id}/sources` | Upload a file (multipart `file`). Creates the source `pending` and schedules background ingestion; returns `201` with the source. This one endpoint handles both documents and images — a filename whose suffix is one of `IMAGE_SUFFIXES` is tagged `kind: "image"` and routed to OCR (see *Document parsing*) instead of the document parsers; there is no separate image-upload route. |
| `POST /repositories/{id}/web-sources` | Add a single web page as a source: body `{"url": "https://..."}`. Creates a `kind: "web"` source `pending` (`title` and `source_url` set to the URL, `title` later replaced by the page's own title if one is found) and schedules a background fetch + extract; returns `201` with the source. `404` if the repository is unknown; `422` if `url` is not a well-formed `http`/`https` URL. |
| `POST /repositories/{id}/admin-notes` | Write an **Admin Note** (JSON `{"title": "...", "content": "..."}`). Creates an `admin_note` source attributed to the author and schedules ingestion; returns `201`. |
| `GET /repositories/{id}/sources` | List a repository's sources (oldest first). |
| `GET /sources/{id}` | Fetch one source, including `status` and `ingest_error`. |
| `DELETE /sources/{id}` | Delete a source; its chunks cascade away. |

`SourceResponse` (the shape returned by the endpoints above) carries: `id`,
`repository_id`, `kind` (`document` / `admin_note` / `image` / `web`), `title`,
`original_filename` (the uploaded filename; `None` for admin notes and web
sources), `source_url` (the fetched URL for a `web` source; `None` otherwise),
`status`, `ingest_error`, and `created_at`.

One endpoint here is **not** admin-only — it is the user-facing counterpart that lets a
reader open a cited passage (card #90):

| Method & path | Purpose |
|---|---|
| `GET /repositories/{id}/sources/{source_id}` | **Any authenticated user.** Return a source's `title`, `kind`, and `content` (its passage text). Gated by an **active grant** on the repository (`403` without — the same rule retrieval enforces); `404` if the source is not in that repository. |

Upload returns immediately with `status: "pending"` — ingestion runs in the
background, so poll `GET /sources/{id}` to watch it move to `done` (or `failed`, with
`ingest_error` set). The embedding provider is injected via a dependency
(`get_embedder`), defaulting to the local model.

### Admin Notes (the curation flywheel)

An **Admin Note** is an admin-authored answer that becomes a first-class source
(design spec §5). It closes the knowledge-gap loop: the admin reads a gap (see
*Knowledge-gap dashboard*), writes the answer — typically titling the note with the
gap's question — and the note is ingested (chunk → embed → store) through the **same
pipeline as uploads** (its body handled as plain text). From then on the next user
who asks that question gets the answer automatically.

A cited Admin Note is marked in the query response's `sources`: `kind` is
`admin_note`, `verified` is `true` (the *Verified* badge), and `author` is the
admin's nickname it is cited to (uploaded documents are `verified: false`,
`author: null`). If the authoring admin is later deleted, the note survives with
`author: null` — `created_by` is `ON DELETE SET NULL` (see *Deleting a user*).

## Repository management (admin)

Admins create repositories and see all of them; regular users only ever see the
repositories they've been granted (design spec §3/§6). These endpoints are
admin-only (`403` for non-admins):

| Method & path | Purpose |
|---|---|
| `POST /repositories` | Create a repository. Body `{"name": "...", "description": "..."?}`; returns the repo with a `configured` flag (always `false` at creation). |
| `GET /admin/repositories` | List **every** repository with its `configured` state — distinct from `GET /repositories`, which is scoped to the caller's granted repos. |
| `PATCH /repositories/{id}` | Update `name` and/or `description`. Only the fields present are applied; an explicit `description: null` clears it, an omitted field is left unchanged; a blank `name` is `422`. |
| `DELETE /repositories/{id}` | Delete a repository, **confirmation-gated** by echoing its name (`{"confirm_name": "…"}`, else `400`). Its sources, chunks, and grants cascade away with it (`ON DELETE CASCADE`). `204` on success. |

A freshly created repository is unconfigured and cannot answer until an admin sets
its LLM (below). `GET /admin/repositories` never includes key material — the key
lives only behind the per-repo `llm-config` route, always masked.

## Repository LLM configuration (admin)

Each repository chooses its own LLM — there is no system default, so a repository
must be configured before it can answer (design spec §3). Admin-only endpoints
set and read that config; non-admins get `403`.

| Method & path | Purpose |
|---|---|
| `PUT /repositories/{id}/llm-config` | Set (or replace) the repo's `provider`, `model`, and `api_key`. Returns the config with the key **masked**. |
| `GET /repositories/{id}/llm-config` | Read the repo's config (`configured` flag; key masked; `null` fields when unconfigured). |

`provider` is one of `gemini` / `openai` / `openrouter` / `anthropic`. The
`api_key` is **encrypted at rest** on write (Fernet, see *Provider API-key
encryption*) and **never returned in full** — responses carry only
`api_key_masked` (`sk-…•••4f2a`), produced by decrypting in memory just long
enough to keep the prefix/suffix:

```json
{"provider": "openai", "model": "gpt-4o", "api_key_masked": "sk-…•••4f2a", "configured": true}
```

Setting the config requires `ENCRYPTION_KEY` to be present (encryption fails
loudly rather than storing plaintext). At query time generation **routes to this
stored config**: the endpoint decrypts the key in memory and builds the
repository's own provider/model through `get_llm_provider(...)`, so each
repository answers with the LLM it was configured for — never a shared default.

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
empty result). An expired grant is treated as no grant. The repository must also
have its **LLM configured** (`409` otherwise, with a message to configure a
provider, model, and key — see *Repository LLM configuration*).

Past the gate the loop is: embed the question → access-filtered, thresholded
retrieval → generate through the repository's **own configured provider** (built
per request from its stored provider/model/decrypted key via the `get_llm_builder`
dependency) → resolve the `[n]` markers to source spans. The response is:

```json
{
  "answer": "…grounded prose with [1] markers…",
  "not_in_vault": false,
  "citations": [
    {"number": 1, "chunk_id": "…", "source_id": "…", "char_start": 0, "char_end": 42}
  ],
  "sources": [
    {"id": "…", "title": "policy.txt", "original_filename": "policy.txt", "kind": "document", "verified": false, "author": null}
  ]
}
```

`sources` lists the distinct documents the citations point at (first-cited
order), so the UI can label and link each `[n]`. When retrieval surfaces nothing
relevant, the honest "not in this vault" behaviour carries through the provider
untouched: `not_in_vault` is `true`, `answer` is the refusal text, and both
`citations` and `sources` are empty — the endpoint never special-cases it.

### Conversation memory (persisted, server-authoritative)

Conversation history is **persisted per (user, repository)** and **owned by the
server**, not the client: the request body carries only `{"question": "..."}` — a
client never sends a `history` field. `POST /repositories/{id}/query` loads this
user's saved thread for the repository, threads the most recent
`MAX_HISTORY_TURNS` (10) `(question, answer)` pairs into the prompt (and folds the
prior question into the retrieval query, so a follow-up like "what about X"
retrieves in context), then appends the new exchange — question, answer,
`not_in_vault`, and the exact `citations`/`sources` it was served with — as one
`ConversationTurn`. There is exactly one conversation per user per repository,
created lazily on first use.

| Method & path | Purpose |
|---|---|
| `GET /repositories/{id}/conversation` | This user's saved conversation for the repository, oldest turn first (empty `turns: []` if none yet). Each turn mirrors the query response shape exactly, so a page reload rebuilds the thread verbatim — including its citations and sources. |
| `DELETE /repositories/{id}/conversation` | Delete this user's saved conversation for the repository (the "Clear conversation" action). `204` on success. |

Both endpoints require the same **active grant** as `/query` (`404` unknown repo,
`403` no access). Conversations and their turns are per-user and cascade away with
the user or the repository (`ON DELETE CASCADE`).

### Query logging

Every answered query writes one `query_logs` row (design spec §5) — the raw
material for the knowledge-gap dashboard and usage analytics. Each row records who
asked (`user_id`), the repository, the question text, the retrieval signal
(`top_score` — best similarity among retrievable chunks; `chunk_count` — how many
cleared the relevance threshold), whether the answer was grounded (`not_in_vault`),
and when (`created_at`). Only *answered* queries are logged: requests rejected at a
pre-generation gate (`404`/`403`/`409`) never reach the log.

`user_id` is `ON DELETE SET NULL`: deleting a user (see *Deleting a user*)
anonymizes their past questions to "asked by a deleted user" rather than erasing
them, so the analytics signal survives the account. `repository_id` cascades — a
repository's history dies with the repository.

### Knowledge-gap dashboard

The gaps a repository couldn't answer become the admin's curation to-do list
(design spec §5). A **gap** is a logged query whose answer was the honest "not in
this vault" (`not_in_vault = true` — retrieval was empty or too weak to ground).

| Endpoint | Behavior |
|---|---|
| `GET /repositories/{id}/knowledge-gaps` | **Admin-only.** Ranked, aggregated gaps for the repository. `?limit=` (1–200, default 50). `404` if the repo is unknown. |
| `POST /repositories/{id}/knowledge-gaps/reject` | **Admin-only.** Reject a gap question with a required `reason`: body `{"question": "...", "reason": "..."}` (both non-empty). Upserted on `(repository_id, normalized_question)`, so rejecting the same topic again just replaces the reason. Returns `201` with the rejection (`question`, `reason`, `rejected_by`, `rejected_at`). `404` if the repo is unknown. |
| `GET /repositories/{id}/knowledge-gaps/rejected` | **Admin-only.** Rejected gaps for the repository, newest first. |

Similar questions are aggregated **case- and whitespace-insensitively** (a v1
heuristic — lowercase, trim, collapse whitespace; not semantic clustering), so
re-asks of the same topic merge into one row. Each row carries a representative
question, `ask_count` (times asked), `user_count` (distinct known askers), and
`last_asked_at`, ranked most-asked then most-recent — "N users asked about X, no
source covers it." An admin closes a gap one of two ways: **answer** it by writing
an Admin Note (a source), after which the next user who asks gets the answer
automatically; or **reject** it — decide it's out of scope, with a required
written reason (`GapRejection`, keyed by the same normalized question the
aggregation uses). Either way the topic drops out of the active gap list — a
rejected question is excluded at the same SQL query that aggregates gaps — and
survives its authoring admin being deleted (`rejected_by` goes `null`, matching
Admin Notes).

### Query analytics

Usage insight for the admin, aggregated from the query log (design spec §5.4):

| Endpoint | Behavior |
|---|---|
| `GET /analytics` | **Admin-only.** One composite summary. `?top_limit=` (1–100, default 10) bounds the questions/users lists. |

The response carries everything the dashboard needs in a single call:

- `total_queries`, `answered`, `not_in_vault`, and `not_in_vault_rate` — the overall answered-vs-gap picture;
- `per_repository` — query + gap counts per repo, busiest first ("which repos are active");
- `top_questions` — most-asked questions (aggregated case/whitespace-insensitively, the same rule as the gap dashboard);
- `active_users` — the most active **known** users (anonymized/deleted askers are excluded — "who's using what");
- `by_day` — a daily time series of `total` vs `not_in_vault`, for the answered-vs-gap rate over time.

## Frontend (React SPA)

The web UI lives in [`frontend/`](frontend/) — a Vite + React + TypeScript single-page
app that talks to this backend over the REST/JSON API using JWT bearer auth. Card #34
scaffolds the foundation (routing, API client, auth context, protected routes, base
layout); card #35 adds the auth screens; card #36 adds the user query experience;
card #37 begins the admin surface (repository management + LLM config).

```bash
cd frontend
npm install          # install dependencies
npm run dev          # dev server at http://localhost:5173 (proxies /api -> :8000)
```

Run the backend (`uvicorn …` above) alongside `npm run dev`: the dev server proxies
every `/api/*` request to `http://localhost:8000`, so the SPA and API share an origin
in development (no CORS).

**Architecture.**

- **API client** (`src/api/client.ts`) — a typed `fetch` wrapper that prefixes `/api`,
  attaches the bearer token, JSON-encodes bodies, and turns any non-2xx into a typed
  `ApiError` carrying the backend's `detail`. A `401` clears the session (expired token
  → back to login).
- **Auth** (`src/auth/`) — `AuthProvider` holds the session (token + claims decoded from
  the JWT + username), persists it to `localStorage`, and exposes `login` / `logout` /
  `changePassword` via the `useAuth()` hook. `RequireAuth` gates routes on a session
  (and optionally an admin role); it mirrors the backend's forced-password-change bounce
  by redirecting flagged users to `/change-password`.
- **Routing** (`src/App.tsx`) — public auth screens (`/login`, `/accept-invite`,
  `/change-password`) sit outside the protected `Layout` shell that wraps the app's
  authenticated pages.

**Auth screens** (card #35, `src/pages/`):

- **Login** (`/login`) — username + password → `POST /auth/login`; a
  `must_change_password` response routes the user to the change-password screen.
- **Accept invite** (`/accept-invite?token=…`) — a new user chooses a password
  (`POST /invitations/accept`) and is signed straight in with it, landing authenticated.
- **Change password** (`/change-password`) — reachable voluntarily or via the forced-change
  bounce; both new-password screens require a matching confirmation.

**Token handling.** There is no refresh endpoint — a JWT simply expires. The client
drops an expired token on load (no dead session mounts the app shell) and clears the
session on any `401`, bouncing the user back to `/login`.

**Query experience** (card #36, the app's home page `/`):

- **Repo picker** — `GET /repositories` lists only the repos the user holds an active
  grant on; a user with none is told so instead of shown an empty box.
- **Ask + cited answer** — `POST /repositories/{id}/query` returns the answer, its
  `[n]` citations, and the cited sources. The answer's inline `[n]` markers render as
  clickable chips (`parseAnswer` splits them from text) that highlight and scroll to the
  matching entry in the **Sources** panel. Each source shows its title, a **Verified**
  badge + author for Admin Notes, and the citation's character span.
- **View passage** — each source has a "View passage" button that loads its raw text on
  demand from the user-scoped content endpoint (`GET /repositories/{id}/sources/{source_id}`,
  card #90) and shows it inline. Access is gated by the caller's active grant (403 otherwise).
- **Not in this vault** — when the response's `not_in_vault` is set, the turn shows an
  explicit callout rather than dressing up the refusal as an answer.
- **Persisted conversation** — on selecting a repository the page restores the saved
  thread from the server (`GET …/conversation`) instead of starting blank, so a reload
  never loses history; a **Clear conversation** button deletes it (`DELETE …/conversation`).
  The client never sends history in the query request — the server is authoritative.

**Admin surface** (cards #37–#40, admin-only pages, linked from the header nav for admins):

- **Repositories** (`/admin/repositories`) — lists **all** repositories
  (`GET /admin/repositories`) with a Configured / Not configured badge; a form creates
  a new one (`POST /repositories`). Each repo expands to an **LLM config** editor that
  reads the current config (`GET …/llm-config`, key shown only masked) and sets a new
  `provider` / `model` / `api_key` (`PUT …/llm-config`). The key field is write-only —
  it is never pre-filled, and the saved key comes back masked. Each repo can also be
  **renamed** (`PATCH /repositories/{id}`) or **deleted** (`DELETE /repositories/{id}`,
  confirmation-gated by echoing its name).
- **Sources** (`/admin/sources`) — pick a repository, then **upload** a document
  (`POST …/sources`, multipart) and watch it ingest: the list (`GET …/sources`) shows
  each source's status (`pending` → `processing` → `done` / `failed`) and **auto-polls**
  while anything is still ingesting, stopping once every source is terminal. A failed
  source shows its ingestion error; each can be **deleted** (`DELETE /sources/{id}`).

- **Users & access** (`/admin/users`) — three panels over the account and grant
  APIs. *Invite* issues an onboarding invite (`POST /invitations`) and reveals the
  one-time token to hand over. *Accounts* lists every user (`GET /users`) with a
  "Password change owed" badge, a **reset-password** action (`POST …/reset-password`,
  showing the temp password once) and a **confirmation-gated delete** (type the
  username, then `DELETE /users/{id}`). *Repository access* picks a repo and manages
  its grants (`GET`/`POST /repositories/{id}/grants`, `DELETE …/grants/{user_id}`) with
  an optional expiry.

- **Insights** (`/admin/insights`) — the curation cockpit. A **knowledge-gap
  dashboard** (`GET …/knowledge-gaps`) ranks the questions a repo couldn't answer;
  "Answer this gap" opens an inline **Admin Note** editor prefilled with the question
  (`POST …/admin-notes`), which closes the gap once ingested. Alongside it, "Reject"
  opens a form requiring a reason (`POST …/knowledge-gaps/reject`) — either action
  removes the gap from the active list. A **Rejected gaps** list
  (`GET …/knowledge-gaps/rejected`) shows what was rejected, why, by whom, and when.
  An **analytics** panel (`GET /analytics`) shows totals + gap rate, per-repository
  volume, top questions, most-active users, and a by-day series.

All admin routes are guarded by `RequireAuth requireAdmin`, so non-admins are bounced home.

**Frontend Definition of Done** (run from `frontend/`):

```bash
npm run lint          # eslint
npm run format:check  # prettier
npm run typecheck     # tsc --noEmit
npm test              # vitest
npm run build         # tsc -b && vite build
```

CI runs these as a separate `frontend` job (see `.github/workflows/ci.yml`).

