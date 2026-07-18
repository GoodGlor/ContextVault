"""Query-logging service (card #30, design spec §5).

``log_query`` persists one ``QueryLog`` row per answered question. It is called from
the query endpoint after retrieval + generation, capturing the retrieval signal
(``top_score``, ``chunk_count``) and whether the answer was grounded
(``not_in_vault``). Downstream cards read these rows: the knowledge-gap dashboard
(#31) and usage analytics (#33).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import QueryLog


async def log_query(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    repository_id: uuid.UUID,
    question: str,
    top_score: float | None,
    chunk_count: int,
    not_in_vault: bool,
) -> QueryLog:
    """Record a query and its retrieval outcome. Adds + flushes; the caller commits."""
    entry = QueryLog(
        user_id=user_id,
        repository_id=repository_id,
        question=question,
        top_score=top_score,
        chunk_count=chunk_count,
        not_in_vault=not_in_vault,
    )
    session.add(entry)
    await session.flush()
    return entry
