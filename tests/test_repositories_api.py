"""Integration tests for the admin per-repo LLM-config API (card #24).

An admin sets a repository's provider / model / API key; the key is encrypted at
rest (card #23) and only ever returned masked (design spec §3/§8). Driven with
httpx.AsyncClient over the async ``db_session`` fixture, mirroring
test_sources_api's real-auth pattern (a JWT minted per role via /auth/login).
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.crypto import decrypt
from contextvault.db.session import get_session
from contextvault.main import create_app
from contextvault.models import Repository, Role
from contextvault.services import users as user_service

_KEY = "sk-proj-abcdefghijklmnop4f2a"


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session
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


def _config(provider: str = "openai", model: str = "gpt-4o", api_key: str = _KEY) -> dict[str, str]:
    return {"provider": provider, "model": model, "api_key": api_key}


async def test_set_config_requires_authentication(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    resp = await client.put(f"/repositories/{repo.id}/llm-config", json=_config())
    assert resp.status_code == 401


async def test_set_config_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.USER)
    resp = await client.put(
        f"/repositories/{repo.id}/llm-config", json=_config(), headers=_auth(token)
    )
    assert resp.status_code == 403


async def test_admin_sets_config_and_key_is_masked_never_returned_in_full(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.put(
        f"/repositories/{repo.id}/llm-config", json=_config(), headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o"
    assert body["configured"] is True
    # The masked key keeps prefix/suffix but never leaks the full secret.
    assert body["api_key_masked"] == "sk-…•••4f2a"
    assert _KEY not in resp.text

    # Stored as ciphertext, not plaintext — encrypt-at-rest (card #23/#24).
    await db_session.refresh(repo)
    assert repo.api_key_encrypted is not None
    assert repo.api_key_encrypted != _KEY
    assert decrypt(repo.api_key_encrypted) == _KEY


async def test_update_config_overwrites_previous(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)

    await client.put(f"/repositories/{repo.id}/llm-config", json=_config(), headers=_auth(token))
    resp = await client.put(
        f"/repositories/{repo.id}/llm-config",
        json=_config(provider="gemini", model="gemini-2.5-flash", api_key="AIzaSyNEWKEY99xy"),
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["model"] == "gemini-2.5-flash"

    await db_session.refresh(repo)
    assert repo.api_key_encrypted is not None
    assert decrypt(repo.api_key_encrypted) == "AIzaSyNEWKEY99xy"


async def test_get_config_returns_masked_key(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    await client.put(f"/repositories/{repo.id}/llm-config", json=_config(), headers=_auth(token))

    resp = await client.get(f"/repositories/{repo.id}/llm-config", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["provider"] == "openai"
    assert body["api_key_masked"] == "sk-…•••4f2a"
    assert _KEY not in resp.text


async def test_get_config_unconfigured_repo(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.get(f"/repositories/{repo.id}/llm-config", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["provider"] is None
    assert body["model"] is None
    assert body["api_key_masked"] is None


async def test_get_config_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.USER)
    resp = await client.get(f"/repositories/{repo.id}/llm-config", headers=_auth(token))
    assert resp.status_code == 403


async def test_set_config_unknown_repository_404(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put(
        f"/repositories/{uuid.uuid4()}/llm-config", json=_config(), headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_set_config_rejects_unknown_provider(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put(
        f"/repositories/{repo.id}/llm-config",
        json=_config(provider="not-a-provider"),
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_set_config_rejects_blank_key(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put(
        f"/repositories/{repo.id}/llm-config",
        json=_config(api_key=""),
        headers=_auth(token),
    )
    assert resp.status_code == 422
