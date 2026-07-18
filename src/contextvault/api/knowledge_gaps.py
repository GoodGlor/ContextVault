"""Admin knowledge-gap dashboard endpoint (card #31, design spec §5).

Surfaces the questions a repository could not answer — ranked, aggregated — as the
admin's curation to-do list. Reads the query log written by #30; the admin acts on a
gap by writing an Admin Note (#32), which permanently closes it.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import require_admin
from contextvault.db.session import get_session
from contextvault.models import Repository, User
from contextvault.services import knowledge_gaps as gap_service

router = APIRouter(tags=["knowledge-gaps"])


class KnowledgeGapResponse(BaseModel):
    """One aggregated gap topic: a representative question and its demand signal."""

    question: str
    ask_count: int
    user_count: int
    last_asked_at: datetime


@router.get("/repositories/{repository_id}/knowledge-gaps")
async def list_knowledge_gaps(
    repository_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeGapResponse]:
    """Ranked knowledge gaps for a repository (admin-only): questions the vault could
    not answer, aggregated case/whitespace-insensitively, most-asked first."""
    if await session.get(Repository, repository_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    gaps = await gap_service.list_knowledge_gaps(session, repository_id, limit=limit)
    return [
        KnowledgeGapResponse(
            question=g.question,
            ask_count=g.ask_count,
            user_count=g.user_count,
            last_asked_at=g.last_asked_at,
        )
        for g in gaps
    ]
