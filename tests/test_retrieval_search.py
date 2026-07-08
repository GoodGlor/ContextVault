"""DB-backed tests for access-filtered pgvector search (card #13).

The retrieval query is the system's core access boundary: a user may only ever
retrieve chunks from a repository they hold an *active* grant for, and that is
enforced in the SQL itself (design spec §4/§6). These tests exercise both the
ranking (top-k by cosine similarity, with scores + source offsets) and the
access filter (no grant / expired grant / other repo → nothing).
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.models import Chunk, Grant, Repository, Role, Source, SourceKind
from contextvault.retrieval import RetrievedChunk, search_chunks
from contextvault.services import users as user_service


def _embedding(a: float, b: float) -> list[float]:
    """A unit-ish vector living in the first two dims, padded to the model width."""
    dim = get_settings().embedding_dim
    return [a, b, *([0.0] * (dim - 2))]


async def _user(session: AsyncSession, name: str) -> uuid.UUID:
    user = await user_service.create_user(session, username=name, password="pw", role=Role.USER)
    return user.id


async def _repo(session: AsyncSession, name: str = "Vault") -> Repository:
    repo = Repository(name=name)
    session.add(repo)
    await session.flush()
    return repo


async def _source(session: AsyncSession, repo: Repository) -> Source:
    source = Source(repository_id=repo.id, kind=SourceKind.DOCUMENT, title="doc.txt")
    session.add(source)
    await session.flush()
    return source


async def _chunk(
    session: AsyncSession,
    source: Source,
    *,
    ordinal: int,
    content: str,
    embedding: Sequence[float] | None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> Chunk:
    chunk = Chunk(
        source_id=source.id,
        repository_id=source.repository_id,
        ordinal=ordinal,
        content=content,
        char_start=char_start,
        char_end=char_end,
        embedding=list(embedding) if embedding is not None else None,
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def _grant(
    session: AsyncSession,
    user_id: uuid.UUID,
    repo: Repository,
    *,
    expires_at: datetime | None = None,
) -> None:
    session.add(Grant(user_id=user_id, repository_id=repo.id, expires_at=expires_at))
    await session.flush()


async def test_returns_top_k_ranked_with_scores_and_offsets(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "reader")
    await _grant(db_session, user_id, repo)
    source = await _source(db_session, repo)

    # near is identical to the query; mid is 45°; far is orthogonal.
    await _chunk(
        db_session,
        source,
        ordinal=0,
        content="near",
        embedding=_embedding(1.0, 0.0),
        char_start=0,
        char_end=4,
    )
    await _chunk(db_session, source, ordinal=1, content="mid", embedding=_embedding(1.0, 1.0))
    await _chunk(db_session, source, ordinal=2, content="far", embedding=_embedding(0.0, 1.0))

    results = await search_chunks(
        db_session,
        user_id=user_id,
        repository_id=repo.id,
        query_embedding=_embedding(1.0, 0.0),
        k=2,
    )

    assert [r.content for r in results] == ["near", "mid"]  # top-2, closest first
    assert all(isinstance(r, RetrievedChunk) for r in results)
    assert results[0].score > results[1].score  # higher similarity ranks first
    assert results[0].score == 1.0  # identical vector → cosine similarity 1
    assert results[0].char_start == 0 and results[0].char_end == 4  # offsets carried through


async def test_no_grant_returns_nothing(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "stranger")  # deliberately ungranted
    source = await _source(db_session, repo)
    await _chunk(db_session, source, ordinal=0, content="secret", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session, user_id=user_id, repository_id=repo.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert results == []


async def test_expired_grant_returns_nothing(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "lapsed")
    await _grant(db_session, user_id, repo, expires_at=datetime.now(UTC) - timedelta(days=1))
    source = await _source(db_session, repo)
    await _chunk(db_session, source, ordinal=0, content="secret", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session, user_id=user_id, repository_id=repo.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert results == []


async def test_future_expiry_still_active(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "timeboxed")
    await _grant(db_session, user_id, repo, expires_at=datetime.now(UTC) + timedelta(days=1))
    source = await _source(db_session, repo)
    await _chunk(db_session, source, ordinal=0, content="visible", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session, user_id=user_id, repository_id=repo.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert [r.content for r in results] == ["visible"]


async def test_grant_to_other_repo_does_not_leak(db_session: AsyncSession) -> None:
    granted = await _repo(db_session, "Granted")
    other = await _repo(db_session, "Other")
    user_id = await _user(db_session, "reader")
    await _grant(db_session, user_id, granted)  # grant is for `granted`, not `other`
    other_source = await _source(db_session, other)
    await _chunk(
        db_session, other_source, ordinal=0, content="leak", embedding=_embedding(1.0, 0.0)
    )

    # Query the repo the user is NOT granted, even though they hold *some* grant.
    results = await search_chunks(
        db_session, user_id=user_id, repository_id=other.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert results == []


async def test_null_embedding_chunks_excluded(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "reader")
    await _grant(db_session, user_id, repo)
    source = await _source(db_session, repo)
    await _chunk(db_session, source, ordinal=0, content="unembedded", embedding=None)
    await _chunk(db_session, source, ordinal=1, content="embedded", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session, user_id=user_id, repository_id=repo.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert [r.content for r in results] == ["embedded"]


async def test_k_limits_result_count(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "reader")
    await _grant(db_session, user_id, repo)
    source = await _source(db_session, repo)
    for i in range(5):
        await _chunk(db_session, source, ordinal=i, content=f"c{i}", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session,
        user_id=user_id,
        repository_id=repo.id,
        query_embedding=_embedding(1.0, 0.0),
        k=3,
    )
    assert len(results) == 3


async def test_k_defaults_to_setting(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    user_id = await _user(db_session, "reader")
    await _grant(db_session, user_id, repo)
    source = await _source(db_session, repo)
    for i in range(get_settings().retrieval_top_k + 3):
        await _chunk(db_session, source, ordinal=i, content=f"c{i}", embedding=_embedding(1.0, 0.0))

    results = await search_chunks(
        db_session, user_id=user_id, repository_id=repo.id, query_embedding=_embedding(1.0, 0.0)
    )
    assert len(results) == get_settings().retrieval_top_k
