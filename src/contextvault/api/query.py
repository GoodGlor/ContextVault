"""The query endpoint — the full RAG loop behind one route (card #19).

`POST /repositories/{id}/query` is where everything built so far meets the user:
authenticate, enforce the repository grant, retrieve the access-filtered chunks,
generate a grounded answer through the configured provider, and return the answer
with its citations resolved to real source documents (design spec §4/§6).

Two access checks stand in front of generation. The repository must exist (404),
and the caller must hold an *active* grant on it (403) — the same predicate the
retrieval query enforces at the SQL level, surfaced here as an explicit,
first-class denial rather than an empty result. Beyond that gate the honest
"not in this vault" behaviour (card #18) carries through untouched: weak or empty
retrieval flows into the provider, which returns the flagged refusal, so the
endpoint never special-cases it.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import RepoLLMBuilder, get_current_user, get_embedder, get_llm_builder
from contextvault.db.session import get_session
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.llm import Citation
from contextvault.models import Grant, Repository, Source, SourceKind, User
from contextvault.retrieval import retrieve
from contextvault.services.query_log import log_query

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    """A user's question against one repository."""

    question: str = Field(min_length=1)


class CitationResponse(BaseModel):
    """One ``[n]`` citation resolved to its source span, for the UI to jump to."""

    model_config = ConfigDict(from_attributes=True)

    number: int
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    char_start: int | None
    char_end: int | None


class SourceReferenceResponse(BaseModel):
    """A cited source document, so the client can label and link each citation.

    ``verified`` marks an **Admin Note** — a human-authored answer (card #32) — so
    the UI can show a *Verified* badge; ``author`` is the admin's nickname it is
    cited to (null once that admin is deleted, or for uploaded documents).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_filename: str | None
    kind: SourceKind
    verified: bool
    author: str | None


class QueryResponse(BaseModel):
    """The RAG result: the answer, its honesty flag, citations, and their sources."""

    answer: str
    not_in_vault: bool
    citations: list[CitationResponse]
    sources: list[SourceReferenceResponse]


async def _has_active_grant(
    session: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> bool:
    """True when the user holds a grant on the repository that has not expired."""
    stmt = (
        select(Grant.id)
        .where(
            Grant.user_id == user_id,
            Grant.repository_id == repository_id,
            or_(Grant.expires_at.is_(None), Grant.expires_at > func.now()),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def _cited_sources(
    session: AsyncSession, citations: list[Citation]
) -> list[SourceReferenceResponse]:
    """Load the distinct sources the citations point at, in first-cited order.

    Admin Notes (card #32) are flagged ``verified`` and attributed to their author's
    nickname, so the UI can render a *Verified* badge and "by <admin>".
    """
    ordered_ids: list[uuid.UUID] = []
    for citation in citations:
        if citation.source_id not in ordered_ids:
            ordered_ids.append(citation.source_id)
    if not ordered_ids:
        return []

    rows = (await session.execute(select(Source).where(Source.id.in_(ordered_ids)))).scalars().all()
    by_id = {source.id: source for source in rows}

    # Resolve author nicknames for the cited Admin Notes in one query.
    author_ids = {s.created_by for s in rows if s.kind is SourceKind.ADMIN_NOTE and s.created_by}
    authors: dict[uuid.UUID, str] = {}
    if author_ids:
        author_rows = (
            (await session.execute(select(User).where(User.id.in_(author_ids)))).scalars().all()
        )
        authors = {u.id: u.username for u in author_rows}

    references: list[SourceReferenceResponse] = []
    for source_id in ordered_ids:
        source = by_id.get(source_id)
        if source is None:
            continue
        verified = source.kind is SourceKind.ADMIN_NOTE
        author = authors.get(source.created_by) if verified and source.created_by else None
        references.append(
            SourceReferenceResponse(
                id=source.id,
                title=source.title,
                original_filename=source.original_filename,
                kind=source.kind,
                verified=verified,
                author=author,
            )
        )
    return references


@router.post("/repositories/{repository_id}/query")
async def query_repository(
    repository_id: uuid.UUID,
    payload: QueryRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    embedder: EmbeddingProvider = Depends(get_embedder),
    build_provider: RepoLLMBuilder = Depends(get_llm_builder),
) -> QueryResponse:
    """Answer a question against one repository: retrieve → generate → cite.

    Requires an active grant on the repository; without one the caller is denied
    (403) rather than shown an empty result. Retrieval is access-filtered and
    thresholded, so an out-of-corpus question yields no chunks and the provider
    returns the honest "not in this vault" answer.
    """
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if not await _has_active_grant(session, user.id, repository_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No access to this repository"
        )
    # A repository must have its LLM configured before it can answer (card #24,
    # design spec §3: no system default). Generation then routes to that per-repo
    # provider (card #25): the request's provider is built from this repository's
    # stored provider/model/key, never a process-wide default.
    if not repo.llm_configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repository has no LLM configured; an admin must configure a "
                "provider, model, and API key before it can answer."
            ),
        )
    provider = build_provider(repo)

    result = await retrieve(
        session,
        question=payload.question,
        repository_id=repository_id,
        user_id=user.id,
        embedder=embedder,
    )
    answer = await provider.answer(payload.question, result.chunks)

    # Log the query — the raw material for the gap dashboard (#31) and analytics
    # (#33): who asked, against which repo, the retrieval signal, and whether the
    # answer was grounded. Persisted before returning so nothing is lost.
    await log_query(
        session,
        user_id=user.id,
        repository_id=repository_id,
        question=payload.question,
        top_score=result.top_score,
        chunk_count=len(result.chunks),
        not_in_vault=answer.not_in_vault,
    )
    await session.commit()

    return QueryResponse(
        answer=answer.text,
        not_in_vault=answer.not_in_vault,
        citations=[CitationResponse.model_validate(c) for c in answer.citations],
        sources=await _cited_sources(session, answer.citations),
    )
