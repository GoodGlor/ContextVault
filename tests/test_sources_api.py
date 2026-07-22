"""Integration tests for the admin source-management API (card #12).

Driven with httpx.AsyncClient in the same event loop as the async ``db_session``
fixture (see test_auth). The embedder and the ingestion session factory are
overridden so an upload runs the *real* background ingestion inside the test
transaction — proving upload → ingest → chunks end-to-end, offline and fast.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import get_embedder, get_ingestion_session_factory
from contextvault.core.config import get_settings
from contextvault.db.session import get_session
from contextvault.main import create_app
from contextvault.models import Chunk, Repository, Role, Source
from contextvault.services import users as user_service


class FakeEmbedder:
    """Deterministic embedder of the configured width, so vectors fit pgvector."""

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * self._dimension for _ in texts]


def _fixed_factory(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder(get_settings().embedding_dim)
    app.dependency_overrides[get_ingestion_session_factory] = lambda: _fixed_factory(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _token(client: AsyncClient, db_session: AsyncSession, role: Role) -> str:
    username = f"{role.value}user"
    await user_service.create_user(db_session, username=username, password="pw", role=role)
    resp = await client.post("/auth/login", json={"username": username, "password": "pw"})
    return str(resp.json()["access_token"])


async def _repo(db_session: AsyncSession) -> Repository:
    repo = Repository(name="Vault")
    db_session.add(repo)
    await db_session.flush()
    return repo


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_upload_requires_authentication(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    resp = await client.post(
        f"/repositories/{repo.id}/sources", files={"file": ("n.txt", b"hi", "text/plain")}
    )
    assert resp.status_code == 401


async def test_upload_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.USER)
    resp = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("n.txt", b"hi", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 403


async def test_upload_creates_source_and_ingests(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("policy.txt", b"Some document body to ingest.", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "policy.txt"
    assert body["original_filename"] == "policy.txt"
    assert body["kind"] == "document"
    # The response is built before the background task runs, so it reads pending.
    assert body["status"] == "pending"
    source_id = body["id"]

    # Background ingestion has completed by the time the response returns.
    status_resp = await client.get(f"/sources/{source_id}", headers=_auth(token))
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "done"

    count = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(Chunk).where(Chunk.source_id == source_id)
        )
    ).scalar_one()
    assert count >= 1


async def test_upload_records_failure_for_unsupported_type(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("archive.zip", b"not a document", "application/zip")},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    source_id = resp.json()["id"]

    status_resp = await client.get(f"/sources/{source_id}", headers=_auth(token))
    body = status_resp.json()
    assert body["status"] == "failed"
    assert body["ingest_error"]


async def test_image_upload_sets_image_kind(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("diagram.png", b"\x89PNG\r\n", "image/png")},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "image"


async def test_document_upload_sets_document_kind(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.json()["kind"] == "document"


async def test_upload_unknown_repository_404(db_session: AsyncSession, client: AsyncClient) -> None:
    import uuid

    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.post(
        f"/repositories/{uuid.uuid4()}/sources",
        files={"file": ("n.txt", b"hi", "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 404


async def test_list_sources(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    for name in ("a.txt", "b.txt"):
        await client.post(
            f"/repositories/{repo.id}/sources",
            files={"file": (name, b"body", "text/plain")},
            headers=_auth(token),
        )

    resp = await client.get(f"/repositories/{repo.id}/sources", headers=_auth(token))
    assert resp.status_code == 200
    titles = sorted(s["title"] for s in resp.json())
    assert titles == ["a.txt", "b.txt"]


async def test_list_sources_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.USER)
    resp = await client.get(f"/repositories/{repo.id}/sources", headers=_auth(token))
    assert resp.status_code == 403


async def test_get_unknown_source_404(db_session: AsyncSession, client: AsyncClient) -> None:
    import uuid

    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.get(f"/sources/{uuid.uuid4()}", headers=_auth(token))
    assert resp.status_code == 404


async def test_delete_source(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    up = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("gone.txt", b"body", "text/plain")},
        headers=_auth(token),
    )
    source_id = up.json()["id"]

    resp = await client.delete(f"/sources/{source_id}", headers=_auth(token))
    assert resp.status_code == 204
    assert await db_session.get(Source, source_id) is None
    gone = await client.get(f"/sources/{source_id}", headers=_auth(token))
    assert gone.status_code == 404


async def test_delete_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    admin = await _token(client, db_session, Role.ADMIN)
    up = await client.post(
        f"/repositories/{repo.id}/sources",
        files={"file": ("keep.txt", b"body", "text/plain")},
        headers=_auth(admin),
    )
    source_id = up.json()["id"]

    user = await _token(client, db_session, Role.USER)
    resp = await client.delete(f"/sources/{source_id}", headers=_auth(user))
    assert resp.status_code == 403
    assert await db_session.get(Source, source_id) is not None
