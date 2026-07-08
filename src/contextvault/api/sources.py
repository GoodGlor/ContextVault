"""Admin source management: upload documents, view ingestion status, list/delete.

Admin-facing endpoints (design spec §3). Uploading a document creates a
``Source`` (status ``pending``) and schedules the ingestion pipeline (card #11)
as a FastAPI background task, so the request returns immediately while
parse→chunk→embed→store runs behind it. Status is then observable on the source.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import get_embedder, get_ingestion_session_factory, require_admin
from contextvault.db.session import get_session
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.models import Repository, Source, SourceKind, SourceStatus, User
from contextvault.services.ingestion import SessionFactory, run_ingestion

router = APIRouter(tags=["sources"])


class SourceResponse(BaseModel):
    """A source with its ingestion state, for the admin UI."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    kind: SourceKind
    title: str
    original_filename: str | None
    status: SourceStatus
    ingest_error: str | None
    created_at: datetime


@router.post(
    "/repositories/{repository_id}/sources",
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    repository_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    embedder: EmbeddingProvider = Depends(get_embedder),
    session_factory: SessionFactory = Depends(get_ingestion_session_factory),
) -> SourceResponse:
    """Upload a document to a repository and kick off ingestion in the background."""
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    data = await file.read()
    filename = file.filename or "untitled"
    source = Source(
        repository_id=repository_id,
        kind=SourceKind.DOCUMENT,
        title=filename,
        original_filename=filename,
        status=SourceStatus.PENDING,
    )
    session.add(source)
    await session.commit()
    # Load the server-generated columns (id, created_at) before serializing.
    await session.refresh(source)

    background_tasks.add_task(
        run_ingestion,
        source.id,
        filename=filename,
        data=data,
        embedder=embedder,
        session_factory=session_factory,
    )
    return SourceResponse.model_validate(source)


@router.get("/repositories/{repository_id}/sources")
async def list_sources(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[SourceResponse]:
    """List the sources in a repository, oldest first."""
    result = await session.execute(
        select(Source).where(Source.repository_id == repository_id).order_by(Source.created_at)
    )
    return [SourceResponse.model_validate(s) for s in result.scalars().all()]


@router.get("/sources/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    """Fetch a single source, including its ingestion status."""
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return SourceResponse.model_validate(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a source; its chunks cascade away with it."""
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    await session.delete(source)
    await session.commit()
