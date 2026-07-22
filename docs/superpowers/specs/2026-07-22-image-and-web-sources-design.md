# Image & web-link sources — design

Add two new source **kinds** to ContextVault so an admin can ingest content
beyond uploaded documents and admin notes:

1. **Image sources** — an uploaded image is OCR'd locally; the text found in the
   image becomes a searchable, citable source.
2. **Web-link sources** — an admin pastes a URL; the system fetches that single
   page, extracts its main article text, and ingests it as a source.

Both feed the **existing** parse → chunk → embed → store → cite pipeline
unchanged. Neither is a new retrieval or generation path — each only produces
extracted text, which everything downstream already handles.

## Goals / non-goals

**Goals**
- Ingest images via local OCR (no third-party service), consistent with the
  project's local-embeddings ethos.
- Ingest a single web page's readable text from a URL.
- Reuse the current ingestion status model (PENDING → PROCESSING → DONE/FAILED)
  and citation/passage-view behavior with no downstream changes.

**Non-goals (YAGNI)**
- No vision-LLM image description — OCR extracts *text only*. A wordless image
  yields nothing and is reported as a failure.
- No site crawling — one URL ingests exactly one page (a snapshot).
- No re-fetch / refresh / change-detection for web sources. If a page changes,
  the admin re-adds it.
- No image/thumbnail storage or display — the image's *text* is the source; the
  binary is not persisted.

## Data model

`SourceKind` (`src/contextvault/models/enums.py`) gains two values:

```python
IMAGE = "image"
WEB = "web"
```

`Source` (`src/contextvault/models/source.py`) gains one nullable column:

```python
# The fetched URL for a WEB source; null for every other kind.
source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
```

Images reuse the existing `original_filename` column. All other columns
(`title`, `content`, `status`, `ingest_error`, …) are used as they are today.

**Migration:** one Alembic revision that (a) adds the `image` and `web` values to
the `source_kind` Postgres enum and (b) adds the `source_url` column. Adding enum
values on Postgres uses `ALTER TYPE ... ADD VALUE`, which cannot run inside a
transaction block — the migration must commit the enum additions outside the
default transactional DDL (e.g. `op.execute` with an autocommit connection /
`ALTER TYPE ... ADD VALUE IF NOT EXISTS`). The `SourceResponse` schema gains
`source_url: str | None` so the UI can render web links.

## Image sources (local OCR)

### Upload path
Images go through the **existing** upload endpoint —
`POST /repositories/{id}/sources` — because an image is just another file. The
only handler change: choose the kind by file extension.

```
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
kind = SourceKind.IMAGE if suffix in IMAGE_SUFFIXES else SourceKind.DOCUMENT
```

Everything else (create PENDING source, schedule `run_ingestion`, return
`SourceResponse`) is unchanged.

### Parsing
`parse_document` (`src/contextvault/ingestion/parsing.py`) gains an image
branch registered for each image suffix. It:

1. Opens the bytes with Pillow (already available) to validate the image;
   a bad/corrupt image raises `DocumentParseError("Could not read image file.")`.
2. Runs OCR to get text.
3. If the extracted text is empty or whitespace-only, raises
   `DocumentParseError("No text found in image.")` — so the source ends `FAILED`
   with that message, satisfying the *text-only* contract.
4. Otherwise returns a `ParsedDocument` with a single page-less `TextBlock`
   (`page=None`), exactly like `_parse_txt`.

### OCR engine
Use **`rapidocr-onnxruntime`**:
- Pure-pip (no system `tesseract` binary), so `./dev.sh` / `uv sync` still "just
  works".
- Ships its recognition models in the wheel — no runtime model download.
- Multilingual, matching the multilingual (bge-m3) embedding stance.

The OCR call is wrapped behind a small, injectable callable
(`OcrEngine = Callable[[bytes], str]`) so the parser depends on an abstraction,
not on RapidOCR directly. Tests inject a fake engine; the real engine is
constructed lazily (models load once). OCR is CPU-bound and synchronous — it runs
inside the existing `asyncio.to_thread` boundary used for embedding, or the
parser stage is likewise dispatched off the event loop. (The current pipeline
already calls `parse_document` synchronously inside `ingest_source`; keep that,
since `ingest_source` runs in a background task, not the request.)

### Frontend
The Sources page file picker accepts the image suffixes, and a helper note reads:
*"Images are read with OCR — only text visible in the image is captured."*

## Web-link sources (single page)

### Endpoint
New `POST /repositories/{id}/web-sources`:

```json
{ "url": "https://example.com/article" }
```

Validates the repository exists (404 otherwise) and the URL is a syntactically
valid `http`/`https` URL (422 otherwise). Creates a `Source` with
`kind=WEB`, `source_url=url`, `title=url` (a better title is filled in during
ingestion), `status=PENDING`, then schedules `run_web_ingestion` as a background
task and returns `SourceResponse` immediately.

### Fetch + extract seam
`run_web_ingestion(source_id, *, url, embedder, session_factory)` mirrors
`run_ingestion`:

1. Opens its own session; no-op if the source was deleted.
2. Marks the source `PROCESSING`.
3. **Fetches** the URL with `httpx` under a guard (below).
4. **Extracts** the main article text with `trafilatura` (pip-only; uses the
   already-present `lxml`). If extraction yields empty text, fail with
   `"No readable text found at URL."`.
5. Sets `source.title` to the page's `<title>` (from trafilatura metadata) when
   available, else leaves the URL.
6. Feeds the extracted text through the **same chunk → embed → store** core used
   by `ingest_source` (refactor the shared tail of `ingest_source` into a helper
   `store_parsed(session, source, parsed, embedder)` that both callers use), so
   there is one place that writes chunks.
7. On any failure, records `ingest_error` and sets `FAILED`, identical to the
   upload path.

### Fetch safety (SSRF + resource guards)
A dedicated `fetch_url(url) -> str` helper enforces:
- **Scheme allow-list:** only `http` / `https`.
- **SSRF guard:** resolve the host and reject loopback, private
  (RFC 1918), link-local (`169.254/16`), and other non-public addresses —
  rejecting *every* resolved address, checked again after redirects.
- **Redirect limit** and a **request timeout**.
- **Response-size cap** (stream and abort past the cap) to avoid unbounded
  memory use.
- Non-2xx or non-HTML responses raise a clear error captured as `ingest_error`.

### Frontend
The Sources page gains an **"Add web link"** control: a URL text field + button
that POSTs to `/web-sources` and refreshes the list. Source rows show a kind
badge (`image` / `web`); a `web` row links to its `source_url`.

## Testing (TDD)

**Parsing (`tests/ingestion/`)**
- Image branch with an **injected fake OCR** returning known text → single
  page-less block containing that text.
- Injected OCR returning `""`/whitespace → `DocumentParseError("No text found in
  image.")`.
- Corrupt image bytes → `DocumentParseError("Could not read image file.")`.

**Web fetch (`tests/`)**
- `fetch_url` rejects `file://`/`ftp://` schemes.
- SSRF guard rejects `localhost`, `127.0.0.1`, `10.x`, `192.168.x`,
  `169.254.x` (resolver mocked).
- Response-size cap and timeout paths raise.
- Success path (httpx mocked) → HTML in, extracted text out; `<title>` used.

**API**
- Image upload → source `kind == IMAGE`; non-image upload → `DOCUMENT`.
- Empty-OCR image → source `FAILED` with `"No text found in image."`.
- `POST /web-sources` with a bad repo → 404; malformed URL → 422; valid → 201,
  `kind == WEB`, `source_url` set, background task scheduled.
- `SourceResponse` includes `source_url`.

**Frontend (Vitest)**
- URL form posts and refreshes the list; empty URL disables the button.
- The image OCR helper note renders on the Sources page.

## Dependencies

Add to `pyproject.toml`:
- `rapidocr-onnxruntime` — local OCR.
- `trafilatura` — main-content web extraction.

Both are pure-pip; no new system binaries. `httpx`, `pillow`, and `lxml` are
already present.

## Docs

Update `README.md` (supported source types) and `docs/architecture.md`
(Document parsing / Source API / Ingestion sections) to cover image and web
sources, per the "update docs before PR" memory.

## Rollout / order of work

1. Migration + model/enum + `SourceResponse` field.
2. Image OCR parser (+ injectable engine) with tests; wire `kind` in upload
   handler.
3. Refactor `ingest_source` tail into `store_parsed`; add `run_web_ingestion`
   + `fetch_url` guard + trafilatura extraction, with tests.
4. `POST /web-sources` endpoint + tests.
5. Frontend: image note + accepted types; web-link form + kind badges.
6. Docs.

Image and web are independently shippable and could be split into two
implementation cards if preferred; the shared groundwork (migration, model)
lands first either way.
