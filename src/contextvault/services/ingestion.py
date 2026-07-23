"""Ingestion pipeline orchestration: parse → chunk → embed → store.

Ties together the parse stage (card #9), the chunk stage (card #10) and the
``EmbeddingProvider`` (card #8) to turn an uploaded document into stored,
embedded ``Chunk``s (design spec §7). A source carries its own ingestion
``status`` (pending → processing → done/failed); any failure is captured on the
source as ``ingest_error`` so it is recorded, never silent.

``ingest_source`` is the awaitable core. ``run_ingestion`` is the thin seam a
request handler schedules via FastAPI ``BackgroundTasks``: it opens its own
session (the request's is gone by the time it runs) and delegates.
"""

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.db.session import SessionLocal
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.ingestion import (
    IMAGE_SUFFIXES,
    ParsedDocument,
    chunk_document,
    file_suffix,
    parse_document,
    parsed_from_text,
)
from contextvault.llm.ocr import transcribe_image
from contextvault.models import Chunk, Repository, Source, SourceStatus
from contextvault.services import providers as provider_service
from contextvault.services.web_source import extract_web_text, fetch_html

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def ingest_source(
    session: AsyncSession,
    source: Source,
    *,
    filename: str,
    data: bytes,
    embedder: EmbeddingProvider,
) -> None:
    """Run the full pipeline for ``source`` and commit the result.

    Parses ``data``, chunks it, embeds every chunk, and replaces any prior
    chunks for the source (so re-ingest is idempotent). On success the source is
    marked ``DONE`` with its extracted text stored; on any error it is marked
    ``FAILED`` with the error captured in ``ingest_error`` — the failure is
    persisted rather than raised, so a caller inspects ``source.status``.
    """
    source_id = source.id

    source.status = SourceStatus.PROCESSING
    source.ingest_error = None
    await session.commit()

    try:
        if file_suffix(filename) in IMAGE_SUFFIXES:
            parsed = await _ocr_image(session, source, data)
        else:
            parsed = parse_document(filename, data)
        await store_parsed(session, source, parsed, embedder)
    except Exception as exc:
        await _record_failure(session, source_id, exc)


async def _ocr_image(session: AsyncSession, source: Source, data: bytes) -> ParsedDocument:
    """Transcribe an image source with the repository's configured vision model.

    Images are read by the repo's own LLM (Cyrillic-capable, unlike the old local OCR)
    using the provider's global key. Requires the repo to be answerable — a model
    picked whose provider has a verified key — otherwise the source fails with a clear,
    actionable message. Empty transcriptions are a failure too, not a silent success.
    """
    repo = await session.get(Repository, source.repository_id)
    if repo is None or not await provider_service.repo_is_answerable(session, repo):
        raise ValueError(
            "This repository has no usable model for reading images. Pick a model whose "
            "provider has a verified API key, then re-upload."
        )
    assert repo.llm_provider is not None and repo.llm_model is not None
    provider, model = repo.llm_provider.value, repo.llm_model
    key = await provider_service.get_provider_key(session, repo.llm_provider)
    assert key is not None
    # Release the pooled DB connection before the slow vision call: loading the repo
    # and key opened a read transaction that would otherwise stay pinned across the
    # whole OCR (and the embed that follows), so bulk image uploads exhaust the pool
    # and every other request times out (QueuePool limit reached). ``expire_on_commit``
    # is off, so ``source``/``repo`` stay usable afterward.
    await session.commit()
    text = await transcribe_image(provider, key, model, image=data)
    if not text.strip():
        raise ValueError("No text found in image.")
    return parsed_from_text(text)


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
    # Embedding may be slow and is synchronous; run it off the event loop.
    vectors = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])

    # Idempotent re-ingest: drop any chunks from a previous run first.
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


async def run_ingestion(
    source_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    embedder: EmbeddingProvider,
    session_factory: SessionFactory = SessionLocal,
) -> None:
    """Background-task seam: open a fresh session and ingest ``source_id``.

    Suitable for ``BackgroundTasks.add_task`` — the request-scoped session is
    already closed by the time this runs, so it opens its own. A no-op if the
    source has since been deleted. ``session_factory`` is injectable for tests.
    """
    async with session_factory() as session:
        source = await session.get(Source, source_id)
        if source is None:
            return
        await ingest_source(session, source, filename=filename, data=data, embedder=embedder)


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
            text, title = await asyncio.to_thread(extract_web_text, html)
            if not text.strip():
                raise ValueError("No readable text found at URL.")
            if title:
                source.title = title
            await store_parsed(session, source, parsed_from_text(text), embedder)
        except Exception as exc:
            await _record_failure(session, source_id, exc)
