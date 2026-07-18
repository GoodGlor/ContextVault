"""Knowledge-gap detection (card #31, design spec §5).

A *knowledge gap* is a question the vault could not answer — a logged query whose
answer was the honest "not in this vault" (``not_in_vault = True``: retrieval was
empty or too weak to ground). This service turns those logged gaps into the admin's
curation to-do list for one repository: similar questions are aggregated (case- and
whitespace-insensitive) and ranked so the most-asked, still-uncovered topics rise to
the top — "N users asked about X, no source covers it" (design spec §5.2).

The loop this feeds: user demand (gaps) → admin writes an Admin Note (#32) → the
vault permanently answers it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import QueryLog

# Group questions that differ only in case or whitespace: lowercase, trim, and
# collapse internal runs of whitespace to a single space. Not semantic clustering —
# a deliberate v1 heuristic that merges obvious re-asks without an embedding pass.
_NORMALIZED_QUESTION = sa.func.regexp_replace(
    sa.func.lower(sa.func.btrim(QueryLog.question)), r"\s+", " ", "g"
)


@dataclass(frozen=True)
class KnowledgeGap:
    """One aggregated gap topic for the admin dashboard.

    ``question`` is a representative original phrasing (gaps are grouped case- and
    whitespace-insensitively). ``ask_count`` is how many times it was asked;
    ``user_count`` is the distinct *known* askers (anonymized/deleted users are not
    counted distinctly). ``last_asked_at`` is the most recent occurrence.
    """

    question: str
    ask_count: int
    user_count: int
    last_asked_at: datetime


async def list_knowledge_gaps(
    session: AsyncSession, repository_id: UUID, *, limit: int | None = None
) -> Sequence[KnowledgeGap]:
    """Ranked knowledge gaps for a repository — most-asked (then most-recent) first."""
    ask_count = sa.func.count().label("ask_count")
    last_asked_at = sa.func.max(QueryLog.created_at).label("last_asked_at")
    stmt = (
        sa.select(
            sa.func.min(QueryLog.question).label("question"),
            ask_count,
            sa.func.count(sa.distinct(QueryLog.user_id)).label("user_count"),
            last_asked_at,
        )
        .where(
            QueryLog.repository_id == repository_id,
            QueryLog.not_in_vault.is_(True),
        )
        .group_by(_NORMALIZED_QUESTION)
        .order_by(ask_count.desc(), last_asked_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
        KnowledgeGap(
            question=row.question,
            ask_count=row.ask_count,
            user_count=row.user_count,
            last_asked_at=row.last_asked_at,
        )
        for row in rows
    ]
