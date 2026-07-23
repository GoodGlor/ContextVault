"""End-to-end tests for the ingestion pipeline (parse→chunk→embed→store).

DB-backed: they use the ``db_session`` fixture and skip when no migrated
database is reachable (see conftest). The embedder is faked — a deterministic
vector per text of the configured width — so the suite stays fast and offline.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.crypto import encrypt
from contextvault.models import (
    Chunk,
    LLMProviderName,
    ProviderSetting,
    Repository,
    Source,
    SourceKind,
    SourceStatus,
)
from contextvault.services.ingestion import ingest_source, run_ingestion, run_web_ingestion


class FakeEmbedder:
    """Deterministic ``EmbeddingProvider``: one fixed-width vector per text."""

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension
        self.calls: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.1] * self._dimension for _ in texts]


class BoomEmbedder:
    """An embedder that always fails, to exercise the failure path."""

    @property
    def dimension(self) -> int:
        return get_settings().embedding_dim

    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        raise RuntimeError("embed exploded")


def _fixed_factory(session: AsyncSession):  # type: ignore[no-untyped-def]
    """A session factory that always yields ``session`` without closing it."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


async def _make_source(session: AsyncSession) -> tuple[Repository, Source]:
    repo = Repository(name="Vault")
    session.add(repo)
    await session.flush()
    source = Source(
        repository_id=repo.id,
        kind=SourceKind.DOCUMENT,
        title="Doc",
        original_filename="doc.txt",
    )
    session.add(source)
    await session.flush()
    return repo, source


async def _chunks_for(session: AsyncSession, source: Source) -> list[Chunk]:
    result = await session.execute(
        sa.select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.ordinal)
    )
    return list(result.scalars().all())


async def test_source_defaults_to_pending(db_session: AsyncSession) -> None:
    _, source = await _make_source(db_session)
    refreshed = await db_session.get(Source, source.id)
    assert refreshed is not None
    assert refreshed.status is SourceStatus.PENDING


async def test_ingest_populates_chunks_and_marks_done(db_session: AsyncSession) -> None:
    repo, source = await _make_source(db_session)
    text = "Sentence number one. " * 200  # long enough for several chunks
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await ingest_source(
        db_session, source, filename="doc.txt", data=text.encode(), embedder=embedder
    )

    assert source.status is SourceStatus.DONE
    assert source.ingest_error is None
    assert source.content == text
    assert embedder.calls  # the embedder was actually used

    chunks = await _chunks_for(db_session, source)
    assert len(chunks) >= 2
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.repository_id == repo.id
        assert chunk.embedding is not None
        assert len(list(chunk.embedding)) == embedder.dimension
        # Offsets slice the chunk text back out of the source — citation-ready.
        assert text[chunk.char_start : chunk.char_end] == chunk.content


async def _make_answerable_image_source(session: AsyncSession) -> Source:
    """A repo with a verified provider key and model, plus a pending image source."""
    repo = Repository(
        name="Vault",
        llm_provider=LLMProviderName.ANTHROPIC,
        llm_model="claude-x",
    )
    session.add(repo)
    session.add(
        ProviderSetting(
            provider=LLMProviderName.ANTHROPIC,
            api_key_encrypted=encrypt("secret-key"),
            verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await session.flush()
    source = Source(
        repository_id=repo.id,
        kind=SourceKind.IMAGE,
        title="photo.heic",
        original_filename="photo.heic",
    )
    session.add(source)
    await session.flush()
    return source


async def test_image_ocr_releases_db_connection_during_transcription(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Image OCR must not pin a pooled DB connection across the slow vision call.

    Regression for the QueuePool exhaustion under bulk image upload: ``_ocr_image``
    used to hold the read transaction opened while loading the repo/key open across
    ``transcribe_image``, so N concurrent image ingestions pinned N connections for
    the whole OCR + embed. The connection must be released before the slow call.
    """
    source = await _make_answerable_image_source(db_session)

    in_transaction_during_ocr: dict[str, bool] = {}

    async def fake_transcribe(
        provider: str, api_key: str, model: str, *, image: bytes, base_url: str | None = None
    ) -> str:
        in_transaction_during_ocr["value"] = db_session.in_transaction()
        return "transcribed page text"

    monkeypatch.setattr("contextvault.services.ingestion.transcribe_image", fake_transcribe)
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await ingest_source(
        db_session, source, filename="photo.heic", data=b"rawimagebytes", embedder=embedder
    )

    assert source.status is SourceStatus.DONE
    assert in_transaction_during_ocr.get("value") is False, (
        "the DB connection must be released before the slow OCR call, not held across it"
    )


async def test_embedding_failure_is_recorded(db_session: AsyncSession) -> None:
    _, source = await _make_source(db_session)

    await ingest_source(
        db_session, source, filename="doc.txt", data=b"hello", embedder=BoomEmbedder()
    )

    assert source.status is SourceStatus.FAILED
    assert source.ingest_error is not None
    assert "embed exploded" in source.ingest_error
    assert await _chunks_for(db_session, source) == []


async def test_parse_failure_is_recorded(db_session: AsyncSession) -> None:
    _, source = await _make_source(db_session)
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await ingest_source(db_session, source, filename="doc.xyz", data=b"anything", embedder=embedder)

    assert source.status is SourceStatus.FAILED
    assert source.ingest_error  # the UnsupportedDocumentError was captured
    assert await _chunks_for(db_session, source) == []


async def test_reingest_replaces_prior_chunks(db_session: AsyncSession) -> None:
    _, source = await _make_source(db_session)
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await ingest_source(
        db_session, source, filename="doc.txt", data=b"first body " * 200, embedder=embedder
    )
    first_ids = {c.id for c in await _chunks_for(db_session, source)}
    assert first_ids

    await ingest_source(
        db_session, source, filename="doc.txt", data=b"short second", embedder=embedder
    )
    second = await _chunks_for(db_session, source)
    second_ids = {c.id for c in second}

    assert source.status is SourceStatus.DONE
    assert first_ids.isdisjoint(second_ids)  # prior chunks were replaced, not appended
    assert len(second) == 1
    assert second[0].content == "short second"


async def test_run_ingestion_missing_source_is_noop(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await run_ingestion(
        uuid.uuid4(),
        filename="x.txt",
        data=b"hi",
        embedder=embedder,
        session_factory=_fixed_factory(db_session),
    )

    count = (await db_session.execute(sa.select(sa.func.count()).select_from(Chunk))).scalar_one()
    assert count == 0


async def test_run_ingestion_delegates_to_pipeline(db_session: AsyncSession) -> None:
    _, source = await _make_source(db_session)
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await run_ingestion(
        source.id,
        filename="doc.txt",
        data=b"body text here",
        embedder=embedder,
        session_factory=_fixed_factory(db_session),
    )

    refreshed = await db_session.get(Source, source.id)
    assert refreshed is not None
    assert refreshed.status is SourceStatus.DONE
    assert len(await _chunks_for(db_session, source)) == 1


async def test_run_web_ingestion_stores_extracted_text(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repository(name="Vault")
    db_session.add(repo)
    await db_session.flush()
    source = Source(
        repository_id=repo.id,
        kind=SourceKind.WEB,
        title="https://x.test",
        source_url="https://x.test",
    )
    db_session.add(source)
    await db_session.flush()

    monkeypatch.setattr("contextvault.services.ingestion.fetch_html", lambda url: "<html/>")
    monkeypatch.setattr(
        "contextvault.services.ingestion.extract_web_text",
        lambda html: ("Extracted body text.", "Nice Title"),
    )
    embedder = FakeEmbedder(get_settings().embedding_dim)

    await run_web_ingestion(
        source.id,
        url="https://x.test",
        embedder=embedder,
        session_factory=_fixed_factory(db_session),
    )

    refreshed = await db_session.get(Source, source.id)
    assert refreshed is not None
    assert refreshed.status is SourceStatus.DONE
    assert refreshed.title == "Nice Title"
    assert refreshed.content == "Extracted body text."
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Chunk).where(Chunk.source_id == source.id)
    )
    assert count is not None and count >= 1


async def test_run_web_ingestion_empty_text_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repository(name="Vault2")
    db_session.add(repo)
    await db_session.flush()
    source = Source(
        repository_id=repo.id,
        kind=SourceKind.WEB,
        title="https://y.test",
        source_url="https://y.test",
    )
    db_session.add(source)
    await db_session.flush()

    monkeypatch.setattr("contextvault.services.ingestion.fetch_html", lambda url: "<html/>")
    monkeypatch.setattr(
        "contextvault.services.ingestion.extract_web_text", lambda html: ("   ", None)
    )

    await run_web_ingestion(
        source.id,
        url="https://y.test",
        embedder=FakeEmbedder(get_settings().embedding_dim),
        session_factory=_fixed_factory(db_session),
    )

    refreshed = await db_session.get(Source, source.id)
    assert refreshed is not None
    assert refreshed.status is SourceStatus.FAILED
    assert "No readable text" in (refreshed.ingest_error or "")
