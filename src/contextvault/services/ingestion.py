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
from contextvault.ingestion import chunk_document, parse_document
from contextvault.models import Chunk, Source, SourceStatus

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
        parsed = parse_document(filename, data)
        chunks = chunk_document(parsed)
        # Embedding may be slow and is synchronous; run it off the event loop.
        vectors = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])

        # Idempotent re-ingest: drop any chunks from a previous run first.
        await session.execute(sa.delete(Chunk).where(Chunk.source_id == source_id))
        session.add_all(
            [
                Chunk(
                    source_id=source_id,
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
        source.content = parsed.text
        source.status = SourceStatus.DONE
        source.ingest_error = None
        await session.commit()
    except Exception as exc:
        # Discard any partial writes, then record the failure on the source.
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
