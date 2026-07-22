# Image & Web-Link Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin ingest two new kinds of source — images (via local OCR) and single web pages (via URL fetch) — through the existing parse→chunk→embed→store→cite pipeline.

**Architecture:** Both new kinds only produce *extracted text*; nothing downstream (chunking, embedding, retrieval, citations) changes. Images ride the existing upload endpoint with a new parser branch. Web links get a new endpoint plus a background fetch seam that mirrors `run_ingestion`. A shared `store_parsed` helper is the single place that writes chunks.

**Tech Stack:** FastAPI · SQLAlchemy async + Alembic · Postgres/pgvector · Pillow + `rapidocr-onnxruntime` (OCR) · `httpx` + `trafilatura` (web) · React + TypeScript + Vitest · pytest.

## Global Constraints

- Python 3.12; backend deps managed with `uv` — add libraries via `uv add`, run tools via `uv run`.
- New source kinds are text-only: an image or page that yields no text ends `FAILED` with a captured `ingest_error`, never a silent success (design spec §7).
- Local-first: OCR runs locally (no third-party). Only the web-link feature makes an outbound request, and it is SSRF-guarded.
- One URL ingests exactly one page — no crawling, no refresh.
- Every code change is TDD: failing test first, minimal implementation, green, commit.
- The image binary is **not** persisted; only its extracted text (in `Source.content`) is stored.
- Alembic head to build on is `550f1a28b886`.

---

### Task 1: Data model — enum values, `source_url` column, migration

**Files:**
- Modify: `src/contextvault/models/enums.py`
- Modify: `src/contextvault/models/source.py`
- Modify: `src/contextvault/api/sources.py` (SourceResponse)
- Create: `migrations/versions/a1b2c3d4e5f6_image_web_sources.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `SourceKind.IMAGE == "image"`, `SourceKind.WEB == "web"`; `Source.source_url: str | None`; `SourceResponse.source_url: str | None`.

- [ ] **Step 1: Write the failing test**

In `tests/test_models.py` add:

```python
from contextvault.models import SourceKind


def test_source_kinds_include_image_and_web() -> None:
    assert SourceKind.IMAGE == "image"
    assert SourceKind.WEB == "web"


def test_source_has_optional_source_url() -> None:
    from contextvault.models import Source

    src = Source(repository_id=None, kind=SourceKind.WEB, title="t", source_url="https://x.test")
    assert src.source_url == "https://x.test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -k "image_and_web or source_url" -v`
Expected: FAIL (`AttributeError: IMAGE` / unexpected `source_url` kwarg).

- [ ] **Step 3: Add the enum values**

In `src/contextvault/models/enums.py`, extend `SourceKind`:

```python
class SourceKind(enum.StrEnum):
    """Kind of ingested source: a document, an admin note, an image, or a web page."""

    DOCUMENT = "document"
    ADMIN_NOTE = "admin_note"
    IMAGE = "image"
    WEB = "web"
```

- [ ] **Step 4: Add the column**

In `src/contextvault/models/source.py`, after the `original_filename` column add:

```python
    # The fetched URL for a WEB source; null for every other kind.
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
```

- [ ] **Step 5: Add `source_url` to the API schema**

In `src/contextvault/api/sources.py`, in `class SourceResponse`, add after `original_filename`:

```python
    source_url: str | None
```

- [ ] **Step 6: Write the migration**

Create `migrations/versions/a1b2c3d4e5f6_image_web_sources.py`:

```python
"""image & web source kinds + source_url

Revision ID: a1b2c3d4e5f6
Revises: 550f1a28b886
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "550f1a28b886"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so commit
    # first and use IF NOT EXISTS so the migration is safely re-runnable.
    op.execute("COMMIT")
    op.execute("ALTER TYPE source_kind ADD VALUE IF NOT EXISTS 'image'")
    op.execute("ALTER TYPE source_kind ADD VALUE IF NOT EXISTS 'web'")
    op.add_column("sources", sa.Column("source_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    # Postgres cannot drop a single enum value; only the column is reversible.
    op.drop_column("sources", "source_url")
```

- [ ] **Step 7: Apply the migration**

Run: `uv run alembic upgrade head`
Expected: revision `a1b2c3d4e5f6` applied, no error.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -k "image_and_web or source_url" -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/contextvault/models/enums.py src/contextvault/models/source.py \
  src/contextvault/api/sources.py migrations/versions/a1b2c3d4e5f6_image_web_sources.py \
  tests/test_models.py
git commit -m "feat: image/web source kinds + source_url column"
```

---

### Task 2: Image OCR parser

**Files:**
- Create: `src/contextvault/ingestion/ocr.py`
- Modify: `src/contextvault/ingestion/parsing.py`
- Modify: `src/contextvault/ingestion/__init__.py`
- Modify: `pyproject.toml` (add `rapidocr-onnxruntime`, `pillow`)
- Test: `tests/test_parsing.py`

**Interfaces:**
- Produces: `ocr_image(image) -> str` (in `contextvault.ingestion.ocr`); `parse_document` now handles `.png .jpg .jpeg .webp .tiff .bmp` → single page-less block; `parsed_from_text(text: str) -> ParsedDocument` (public, in `parsing`, used by Task 5).
- Consumes: `DocumentParseError`, `_blocks_from_segments` from `parsing`.

- [ ] **Step 1: Add the OCR dependency**

Run: `uv add rapidocr-onnxruntime pillow`
Expected: both resolve and land in `pyproject.toml` / `uv.lock`.

- [ ] **Step 2: Write the failing tests**

In `tests/test_parsing.py` add:

```python
import pytest
from PIL import Image


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 16), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_parse_image_returns_ocr_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "Hello world")
    parsed = parse_document("scan.png", _png_bytes())
    assert parsed.text == "Hello world"
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page is None
    _assert_contiguous(parsed)


def test_parse_image_without_text_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "   ")
    with pytest.raises(DocumentParseError, match="No text found in image"):
        parse_document("blank.png", _png_bytes())


def test_parse_corrupt_image_fails() -> None:
    with pytest.raises(DocumentParseError, match="Could not read image file"):
        parse_document("broken.png", b"this is not an image")


def test_parsed_from_text_single_block() -> None:
    from contextvault.ingestion.parsing import parsed_from_text

    parsed = parsed_from_text("some page text")
    assert parsed.text == "some page text"
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -k "image or parsed_from_text" -v`
Expected: FAIL (`ModuleNotFoundError: contextvault.ingestion.ocr` / unsupported type `.png`).

- [ ] **Step 4: Create the OCR wrapper**

Create `src/contextvault/ingestion/ocr.py`:

```python
"""Local OCR via RapidOCR, isolated behind one function.

The parser depends on ``ocr_image`` — a small abstraction — not on the vendor,
so the heavy engine loads lazily (once) and tests swap it with a fake.
"""

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image


@lru_cache(maxsize=1)
def _engine() -> object:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def ocr_image(image: "Image") -> str:
    """Return the text RapidOCR reads from ``image``; empty string if none."""
    import numpy as np

    result, _elapsed = _engine()(np.array(image.convert("RGB")))  # type: ignore[operator]
    if not result:
        return ""
    return "\n".join(line[1] for line in result)
```

- [ ] **Step 5: Add the image branch to the parser**

In `src/contextvault/ingestion/parsing.py`, add a public text helper and the image parser, and register the suffixes:

```python
def parsed_from_text(text: str) -> ParsedDocument:
    """Wrap ready-made text (e.g. an extracted web page) as a single page-less block."""
    return _blocks_from_segments([(text, None)])


def _parse_image(data: bytes) -> ParsedDocument:
    from PIL import Image, UnidentifiedImageError

    from contextvault.ingestion.ocr import ocr_image

    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentParseError("Could not read image file.") from exc

    text = ocr_image(image)
    if not text.strip():
        raise DocumentParseError("No text found in image.")
    return _blocks_from_segments([(text, None)])
```

Then extend `_PARSERS`:

```python
_PARSERS = {
    ".txt": _parse_txt,
    ".docx": _parse_docx,
    ".pdf": _parse_pdf,
    ".png": _parse_image,
    ".jpg": _parse_image,
    ".jpeg": _parse_image,
    ".webp": _parse_image,
    ".tiff": _parse_image,
    ".bmp": _parse_image,
}
```

- [ ] **Step 6: Export `parsed_from_text`**

In `src/contextvault/ingestion/__init__.py`, add `parsed_from_text` to the import from `parsing` and to `__all__`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsing.py -k "image or parsed_from_text" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/contextvault/ingestion/ocr.py src/contextvault/ingestion/parsing.py \
  src/contextvault/ingestion/__init__.py tests/test_parsing.py pyproject.toml uv.lock
git commit -m "feat: OCR image parser (RapidOCR), text-only contract"
```

---

### Task 3: Wire image kind into the upload handler

**Files:**
- Modify: `src/contextvault/api/sources.py` (`upload_source`)
- Test: `tests/test_sources_api.py`

**Interfaces:**
- Consumes: `SourceKind.IMAGE`, image suffix set.
- Produces: an upload with an image extension creates a source with `kind == "image"`; other extensions stay `document`.

- [ ] **Step 1: Write the failing test**

In `tests/test_sources_api.py` add (mirror the file-upload style already used there; adjust the client/auth fixtures to match the file's existing helpers):

```python
def test_image_upload_sets_image_kind(admin_client, repository_id) -> None:
    resp = admin_client.post(
        f"/repositories/{repository_id}/sources",
        files={"file": ("diagram.png", b"\x89PNG\r\n", "image/png")},
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "image"


def test_document_upload_sets_document_kind(admin_client, repository_id) -> None:
    resp = admin_client.post(
        f"/repositories/{repository_id}/sources",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "document"
```

> Note: ingestion runs as a background task; these assert only the created source's `kind`, which is set synchronously in the handler. If the file's suite disables background tasks or fakes the embedder, follow that existing pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources_api.py -k "image_kind or document_kind" -v`
Expected: FAIL (kind is `document` for the PNG).

- [ ] **Step 3: Choose the kind by extension in the handler**

In `src/contextvault/api/sources.py`, add near the top-level constants:

```python
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
```

In `upload_source`, after `filename = file.filename or "untitled"`, replace the hard-coded `kind=SourceKind.DOCUMENT` with:

```python
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = SourceKind.IMAGE if suffix in _IMAGE_SUFFIXES else SourceKind.DOCUMENT
    source = Source(
        repository_id=repository_id,
        kind=kind,
        title=filename,
        original_filename=filename,
        status=SourceStatus.PENDING,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources_api.py -k "image_kind or document_kind" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/sources.py tests/test_sources_api.py
git commit -m "feat: tag image uploads with the image source kind"
```

---

### Task 4: URL fetch safety + web text extraction

**Files:**
- Create: `src/contextvault/services/web_source.py`
- Modify: `pyproject.toml` (add `trafilatura`)
- Test: `tests/test_web_source.py`

**Interfaces:**
- Produces: `WebFetchError(Exception)`; `fetch_html(url: str, *, transport: httpx.BaseTransport | None = None) -> str`; `extract_web_text(html: str) -> tuple[str, str | None]` returning `(text, title_or_none)`.

- [ ] **Step 1: Add the extraction dependency**

Run: `uv add trafilatura`
Expected: resolves into `pyproject.toml` / `uv.lock`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web_source.py`:

```python
import httpx
import pytest

from contextvault.services.web_source import (
    WebFetchError,
    extract_web_text,
    fetch_html,
)


def test_fetch_rejects_non_http_scheme() -> None:
    with pytest.raises(WebFetchError, match="http"):
        fetch_html("file:///etc/passwd")


def test_fetch_rejects_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "contextvault.services.web_source.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(WebFetchError, match="non-public"):
        fetch_html("http://localhost/")


def test_fetch_returns_body_for_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "contextvault.services.web_source.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>Hi</body></html>"
        )
    )
    body = fetch_html("http://example.com/", transport=transport)
    assert "Hi" in body


def test_fetch_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "contextvault.services.web_source.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr("contextvault.services.web_source._MAX_BYTES", 8)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html"}, text="x" * 100
        )
    )
    with pytest.raises(WebFetchError, match="size cap"):
        fetch_html("http://example.com/", transport=transport)


def test_extract_web_text_pulls_main_content_and_title() -> None:
    html = (
        "<html><head><title>My Page</title></head>"
        "<body><article><p>The important sentence here.</p></article></body></html>"
    )
    text, title = extract_web_text(html)
    assert "important sentence" in text
    assert title == "My Page"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_source.py -v`
Expected: FAIL (`ModuleNotFoundError: contextvault.services.web_source`).

- [ ] **Step 4: Implement the module**

Create `src/contextvault/services/web_source.py`:

```python
"""Fetch a single public web page's readable text, with SSRF and size guards.

``fetch_html`` refuses non-``http(s)`` schemes and any host that resolves to a
non-public address (loopback/private/link-local/…), re-checking every redirect
hop, and streams under a byte cap. ``extract_web_text`` pulls the main article
text (and title) with trafilatura.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

_MAX_BYTES = 5 * 1024 * 1024
_TIMEOUT = 15.0
_MAX_HOPS = 5


class WebFetchError(Exception):
    """A URL could not be fetched (bad scheme, blocked host, HTTP error, too big)."""


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebFetchError(f"Could not resolve host {host!r}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise WebFetchError("Refusing to fetch a non-public address.")


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError("Only http and https URLs are supported.")
    if not parsed.hostname:
        raise WebFetchError("URL has no host.")
    _assert_public_host(parsed.hostname)


def fetch_html(url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """Fetch ``url`` and return its decoded HTML, enforcing all guards."""
    current = url
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False, transport=transport) as client:
        for _ in range(_MAX_HOPS + 1):
            _validate_url(current)
            request = client.build_request("GET", current)
            resp = client.send(request, stream=True)
            try:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise WebFetchError("Redirect without a location.")
                    current = str(httpx.URL(current).join(location))
                    continue
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise WebFetchError(f"Fetch failed: {exc}") from exc
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype and "text/" not in ctype:
                    raise WebFetchError(f"Unsupported content type: {ctype!r}.")
                total = 0
                parts: list[bytes] = []
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise WebFetchError("Response exceeds the size cap.")
                    parts.append(chunk)
                return b"".join(parts).decode(resp.encoding or "utf-8", errors="replace")
            finally:
                resp.close()
    raise WebFetchError("Too many redirects.")


def extract_web_text(html: str) -> tuple[str, str | None]:
    """Return ``(main_text, title_or_none)`` extracted from ``html``."""
    import trafilatura

    text = trafilatura.extract(html) or ""
    metadata = trafilatura.extract_metadata(html)
    title = metadata.title if metadata is not None else None
    return text, title
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_source.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/services/web_source.py tests/test_web_source.py pyproject.toml uv.lock
git commit -m "feat: SSRF-guarded URL fetch + trafilatura text extraction"
```

---

### Task 5: `store_parsed` refactor + `run_web_ingestion` background seam

**Files:**
- Modify: `src/contextvault/services/ingestion.py`
- Test: `tests/test_ingestion_pipeline.py`

**Interfaces:**
- Produces: `store_parsed(session, source, parsed, embedder) -> None` (shared chunk→embed→store→mark-done tail); `run_web_ingestion(source_id, *, url, embedder, session_factory=SessionLocal) -> None`.
- Consumes: `fetch_html`, `extract_web_text` (Task 4); `parsed_from_text` (Task 2).

- [ ] **Step 1: Write the failing test**

In `tests/test_ingestion_pipeline.py` add (the file already defines `FakeEmbedder`, `_fixed_factory`, and the `db_session` fixture — reuse them):

```python
from contextvault.services.ingestion import run_web_ingestion


async def test_run_web_ingestion_stores_extracted_text(db_session, monkeypatch) -> None:
    repo = Repository(name="Vault")
    db_session.add(repo)
    await db_session.flush()
    source = Source(
        repository_id=repo.id, kind=SourceKind.WEB, title="https://x.test",
        source_url="https://x.test",
    )
    db_session.add(source)
    await db_session.flush()

    monkeypatch.setattr(
        "contextvault.services.ingestion.fetch_html", lambda url: "<html/>"
    )
    monkeypatch.setattr(
        "contextvault.services.ingestion.extract_web_text",
        lambda html: ("Extracted body text.", "Nice Title"),
    )
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await run_web_ingestion(
        source.id, url="https://x.test", embedder=embedder,
        session_factory=_fixed_factory(db_session),
    )

    refreshed = await db_session.get(Source, source.id)
    assert refreshed.status is SourceStatus.DONE
    assert refreshed.title == "Nice Title"
    assert refreshed.content == "Extracted body text."
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Chunk).where(Chunk.source_id == source.id)
    )
    assert count >= 1


async def test_run_web_ingestion_empty_text_fails(db_session, monkeypatch) -> None:
    repo = Repository(name="Vault2")
    db_session.add(repo)
    await db_session.flush()
    source = Source(
        repository_id=repo.id, kind=SourceKind.WEB, title="https://y.test",
        source_url="https://y.test",
    )
    db_session.add(source)
    await db_session.flush()

    monkeypatch.setattr("contextvault.services.ingestion.fetch_html", lambda url: "<html/>")
    monkeypatch.setattr(
        "contextvault.services.ingestion.extract_web_text", lambda html: ("   ", None)
    )

    await run_web_ingestion(
        source.id, url="https://y.test", embedder=FakeEmbedder(get_settings().embedding_dim),
        session_factory=_fixed_factory(db_session),
    )

    refreshed = await db_session.get(Source, source.id)
    assert refreshed.status is SourceStatus.FAILED
    assert "No readable text" in (refreshed.ingest_error or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingestion_pipeline.py -k "web_ingestion" -v`
Expected: FAIL (`cannot import name 'run_web_ingestion'`).

- [ ] **Step 3: Refactor the shared tail into `store_parsed`**

In `src/contextvault/services/ingestion.py`, add imports at the top:

```python
from contextvault.ingestion import chunk_document, parse_document, parsed_from_text
from contextvault.services.web_source import extract_web_text, fetch_html
```

Add the helper:

```python
async def store_parsed(
    session: AsyncSession,
    source: Source,
    parsed: object,
    embedder: EmbeddingProvider,
) -> None:
    """Chunk → embed → replace-chunks → mark DONE for an already-parsed source.

    The single writer of chunks, shared by document/image ingestion and web
    ingestion. ``parsed`` is a ``ParsedDocument``. Commits on success.
    """
    chunks = chunk_document(parsed)  # type: ignore[arg-type]
    vectors = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])
    await session.execute(sa.delete(Chunk).where(Chunk.source_id == source.id))
    session.add_all(
        [
            Chunk(
                source_id=source.id,
                repository_id=source.repository_id,
                ordinal=chunk.ordinal,
                content=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
    )
    source.content = parsed.text  # type: ignore[attr-defined]
    source.status = SourceStatus.DONE
    source.ingest_error = None
    await session.commit()


async def _record_failure(session: AsyncSession, source_id: uuid.UUID, exc: Exception) -> None:
    """Roll back partial writes and persist the failure on the source."""
    await session.rollback()
    failed = await session.get(Source, source_id)
    if failed is not None:
        failed.status = SourceStatus.FAILED
        failed.ingest_error = f"{type(exc).__name__}: {exc}"
        await session.commit()
```

Then rewrite the body of `ingest_source` (keep its signature/docstring) so its `try` uses the helpers:

```python
    source.status = SourceStatus.PROCESSING
    source.ingest_error = None
    await session.commit()

    try:
        parsed = parse_document(filename, data)
        await store_parsed(session, source, parsed, embedder)
    except Exception as exc:
        await _record_failure(session, source_id, exc)
```

- [ ] **Step 4: Add `run_web_ingestion`**

Append to `src/contextvault/services/ingestion.py`:

```python
async def run_web_ingestion(
    source_id: uuid.UUID,
    *,
    url: str,
    embedder: EmbeddingProvider,
    session_factory: SessionFactory = SessionLocal,
) -> None:
    """Background-task seam: fetch ``url``, extract its text, and ingest ``source_id``.

    Mirrors :func:`run_ingestion` for web-link sources — opens its own session,
    marks the source PROCESSING, fetches + extracts (off the event loop), and
    stores via :func:`store_parsed`. Any failure is captured on the source.
    """
    async with session_factory() as session:
        source = await session.get(Source, source_id)
        if source is None:
            return

        source.status = SourceStatus.PROCESSING
        source.ingest_error = None
        await session.commit()

        try:
            html = await asyncio.to_thread(fetch_html, url)
            text, title = extract_web_text(html)
            if not text.strip():
                raise ValueError("No readable text found at URL.")
            if title:
                source.title = title
            await store_parsed(session, source, parsed_from_text(text), embedder)
        except Exception as exc:
            await _record_failure(session, source_id, exc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ingestion_pipeline.py -v`
Expected: PASS (both new tests and the pre-existing pipeline tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/services/ingestion.py tests/test_ingestion_pipeline.py
git commit -m "feat: web ingestion seam + shared store_parsed helper"
```

---

### Task 6: `POST /web-sources` endpoint

**Files:**
- Modify: `src/contextvault/api/sources.py`
- Test: `tests/test_sources_api.py`

**Interfaces:**
- Consumes: `run_web_ingestion` (Task 5), `SourceKind.WEB`.
- Produces: `POST /repositories/{id}/web-sources` accepting `{ "url": "..." }` → 201 `SourceResponse` with `kind == "web"`, `source_url` set; 404 unknown repo; 422 malformed URL.

- [ ] **Step 1: Write the failing test**

In `tests/test_sources_api.py` add:

```python
def test_add_web_source_creates_web_source(admin_client, repository_id) -> None:
    resp = admin_client.post(
        f"/repositories/{repository_id}/web-sources",
        json={"url": "https://example.com/article"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "web"
    assert body["source_url"] == "https://example.com/article"
    assert body["status"] == "pending"


def test_add_web_source_rejects_bad_url(admin_client, repository_id) -> None:
    resp = admin_client.post(
        f"/repositories/{repository_id}/web-sources", json={"url": "not a url"}
    )
    assert resp.status_code == 422


def test_add_web_source_unknown_repo_404(admin_client) -> None:
    import uuid

    resp = admin_client.post(
        f"/repositories/{uuid.uuid4()}/web-sources", json={"url": "https://example.com"}
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources_api.py -k "web_source" -v`
Expected: FAIL (404 route not found → 405/404 mismatch).

- [ ] **Step 3: Implement the endpoint**

In `src/contextvault/api/sources.py`, update the import from the ingestion service to include the web seam:

```python
from contextvault.services.ingestion import SessionFactory, run_ingestion, run_web_ingestion
```

Add near the other request models, using Pydantic's URL validation:

```python
from pydantic import AnyHttpUrl


class WebSourceRequest(BaseModel):
    """A URL to fetch and ingest as a single web-page source."""

    url: AnyHttpUrl
```

Add the route (place it after `upload_source`):

```python
@router.post(
    "/repositories/{repository_id}/web-sources",
    status_code=status.HTTP_201_CREATED,
)
async def add_web_source(
    repository_id: uuid.UUID,
    payload: WebSourceRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    embedder: EmbeddingProvider = Depends(get_embedder),
    session_factory: SessionFactory = Depends(get_ingestion_session_factory),
) -> SourceResponse:
    """Add a single web page as a source: fetch + extract run in the background."""
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    url = str(payload.url)
    source = Source(
        repository_id=repository_id,
        kind=SourceKind.WEB,
        title=url,
        source_url=url,
        status=SourceStatus.PENDING,
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)

    background_tasks.add_task(
        run_web_ingestion,
        source.id,
        url=url,
        embedder=embedder,
        session_factory=session_factory,
    )
    return SourceResponse.model_validate(source)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources_api.py -k "web_source" -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/api/sources.py tests/test_sources_api.py
git commit -m "feat: POST /web-sources endpoint"
```

---

### Task 7: Frontend — image note, web-link form, kind badges

**Files:**
- Modify: `frontend/src/api/sources.ts`
- Modify: `frontend/src/pages/AdminSourcesPage.tsx`
- Test: `frontend/src/pages/AdminSourcesPage.test.tsx`

**Interfaces:**
- Consumes: `POST /repositories/{id}/web-sources`.
- Produces: `addWebSource(repositoryId, url) -> Promise<Source>`; UI shows an OCR helper note, a web-link form, and per-kind badges.

- [ ] **Step 1: Extend the API client types**

In `frontend/src/api/sources.ts`:

- Change the kind union to:
  ```ts
  export type SourceKind = "document" | "admin_note" | "image" | "web";
  ```
- Add `source_url: string | null;` to the `Source` interface (after `original_filename`).
- Add:
  ```ts
  /** Add a single web page as a source; ingestion runs in the background. */
  export function addWebSource(repositoryId: string, url: string): Promise<Source> {
    return api.post<Source>(`/repositories/${repositoryId}/web-sources`, { url });
  }
  ```

- [ ] **Step 2: Write the failing tests**

In `frontend/src/pages/AdminSourcesPage.test.tsx` add tests following the file's existing render/mocking pattern (mock `../api/sources`):

```tsx
it("renders the OCR helper note", async () => {
  // ...render with at least one repository mocked...
  expect(
    await screen.findByText(/only text visible in the image is captured/i),
  ).toBeInTheDocument();
});

it("submits a web link and appends the created source", async () => {
  const addWebSource = vi.mocked(sourcesApi.addWebSource);
  addWebSource.mockResolvedValue({
    id: "w1", repository_id: "r1", kind: "web", title: "https://x.test",
    original_filename: null, source_url: "https://x.test", status: "pending",
    ingest_error: null, created_at: "2026-07-22T00:00:00Z",
  });
  // ...render, type https://x.test into the "Web link" field, click "Add link"...
  await waitFor(() => expect(addWebSource).toHaveBeenCalledWith("r1", "https://x.test"));
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/AdminSourcesPage.test.tsx`
Expected: FAIL (no helper note / no web-link field).

- [ ] **Step 4: Update the page**

In `frontend/src/pages/AdminSourcesPage.tsx`:

- Import `addWebSource`:
  ```ts
  import { addWebSource, deleteSource, isIngesting, listSources, uploadSource, type Source } from "../api/sources";
  ```
- Add web-link state near the file-upload state:
  ```ts
  const [webUrl, setWebUrl] = useState("");
  const [addingWeb, setAddingWeb] = useState(false);
  const [webError, setWebError] = useState<string | null>(null);
  ```
- Add the submit handler next to `onUpload`:
  ```ts
  const onAddWeb = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || webUrl.trim() === "") return;
    setAddingWeb(true);
    setWebError(null);
    try {
      const created = await addWebSource(selected, webUrl.trim());
      setSources((prev) => [...(prev ?? []), created]);
      setWebUrl("");
    } catch (err) {
      setWebError(errorMessage(err, "Could not add the link."));
    } finally {
      setAddingWeb(false);
    }
  };
  ```
- Add `accept` + the helper note to the upload form's file input:
  ```tsx
  <input
    id="source-file"
    type="file"
    ref={fileInput}
    onChange={onFileChange}
    accept=".txt,.pdf,.docx,.png,.jpg,.jpeg,.webp,.tiff,.bmp"
  />
  <p className="hint">Images are read with OCR — only text visible in the image is captured.</p>
  ```
- Add the web-link form right after the upload form:
  ```tsx
  <form className="source-web" onSubmit={onAddWeb}>
    <label htmlFor="source-url">Web link</label>
    <input
      id="source-url"
      type="url"
      placeholder="https://example.com/article"
      value={webUrl}
      onChange={(e) => setWebUrl(e.target.value)}
    />
    <button type="submit" disabled={addingWeb || webUrl.trim() === ""}>
      Add link
    </button>
    {webError !== null && <p className="error">{webError}</p>}
  </form>
  ```
- Show the kind badge and link web rows, inside the `.source-item` `<li>` (before the status badge):
  ```tsx
  <span className={`badge kind-${s.kind}`}>{s.kind}</span>
  {s.kind === "web" && s.source_url !== null ? (
    <a className="source-title" href={s.source_url} target="_blank" rel="noreferrer">
      {s.title}
    </a>
  ) : (
    <span className="source-title">{s.title}</span>
  )}
  ```
  (Replace the existing bare `<span className="source-title">{s.title}</span>` with this conditional.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/AdminSourcesPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/sources.ts frontend/src/pages/AdminSourcesPage.tsx \
  frontend/src/pages/AdminSourcesPage.test.tsx
git commit -m "feat: image OCR note, web-link form, source kind badges"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

**Interfaces:** none (docs only). Required by the "update docs before PR" memory.

- [ ] **Step 1: Update the README**

In `README.md`, in the Features list, extend the source description to note images (local OCR, text-only) and web links (single page) as ingestible source types.

- [ ] **Step 2: Update the architecture doc**

In `docs/architecture.md`:
- **Document parsing** section: list the image suffixes and that OCR extracts text only (empty → `FAILED` with `"No text found in image."`); mention `rapidocr-onnxruntime`.
- **Source API (admin)** section: document `POST /repositories/{id}/web-sources` (body `{ "url": ... }`, 201/404/422), and that image uploads reuse the existing upload endpoint but are tagged `kind=image`.
- **Ingestion pipeline** section: note `run_web_ingestion` (fetch → extract → `store_parsed`) and the SSRF/size guards in `web_source.fetch_html`.
- Add `source_url` to the documented `SourceResponse` fields.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/architecture.md
git commit -m "docs: image (OCR) & web-link sources"
```

---

## Self-review notes

- **Spec coverage:** data model + migration (T1) ✓; image OCR + text-only contract (T2, T3) ✓; web fetch safety + extraction (T4); background seam (T5); endpoint (T6); frontend note/form/badges (T7); docs (T8) ✓. Every spec section maps to a task.
- **Type consistency:** `store_parsed`, `run_web_ingestion`, `fetch_html`, `extract_web_text`, `parsed_from_text`, `addWebSource`, `SourceResponse.source_url` are defined once and consumed with the same signatures downstream.
- **Deps:** `rapidocr-onnxruntime`, `pillow`, `trafilatura` added via `uv add`; `httpx`/`lxml` already present.
- **Test isolation:** OCR and network are always mocked/monkeypatched — the suite stays offline and deterministic.
```
