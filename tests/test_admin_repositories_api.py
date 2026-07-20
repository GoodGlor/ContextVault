"""Integration tests for admin repository management (card #37).

Admins create repositories and list *all* of them (each with its LLM-config
state) so the admin UI can drive create → configure. Unlike ``GET /repositories``
(the user's granted picker), ``GET /admin/repositories`` is admin-only and returns
every repository. Mirrors test_repositories_api's real-auth httpx harness.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.crypto import encrypt
from contextvault.db.session import get_session
from contextvault.main import create_app
from contextvault.models import LLMProviderName, Repository, Role
from contextvault.services import users as user_service


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- create -----------------------------------------------------------------


async def test_create_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post("/repositories", json={"name": "Handbook"})
    assert resp.status_code == 401


async def test_create_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    token = await _token(client, db_session, Role.USER)
    resp = await client.post("/repositories", json={"name": "Handbook"}, headers=_auth(token))
    assert resp.status_code == 403


async def test_admin_creates_repository(db_session: AsyncSession, client: AsyncClient) -> None:
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.post(
        "/repositories",
        json={"name": "Handbook", "description": "the company handbook"},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Handbook"
    assert body["description"] == "the company handbook"
    assert "id" in body
    # A freshly created repo has no LLM config yet.
    assert body["configured"] is False
    # Persisted.
    repo = await db_session.get(Repository, uuid.UUID(body["id"]))
    assert repo is not None and repo.name == "Handbook"


async def test_create_defaults_description_to_null(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.post("/repositories", json={"name": "Runbook"}, headers=_auth(token))
    assert resp.status_code == 201
    assert resp.json()["description"] is None


async def test_create_rejects_blank_name(db_session: AsyncSession, client: AsyncClient) -> None:
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.post("/repositories", json={"name": ""}, headers=_auth(token))
    assert resp.status_code == 422


# --- admin list-all ---------------------------------------------------------


async def test_list_all_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/admin/repositories")
    assert resp.status_code == 401


async def test_list_all_forbidden_for_non_admin(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    token = await _token(client, db_session, Role.USER)
    resp = await client.get("/admin/repositories", headers=_auth(token))
    assert resp.status_code == 403


async def test_list_all_returns_every_repo_with_config_state(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    unconfigured = Repository(name="Empty")
    configured = Repository(
        name="Ready",
        description="ops",
        llm_provider=LLMProviderName.OPENAI,
        llm_model="gpt-4o",
        api_key_encrypted=encrypt("sk-proj-secret"),
    )
    db_session.add_all([unconfigured, configured])
    await db_session.flush()
    token = await _token(client, db_session, Role.ADMIN)

    resp = await client.get("/admin/repositories", headers=_auth(token))
    assert resp.status_code == 200
    by_name = {r["name"]: r for r in resp.json()}
    assert set(by_name) == {"Empty", "Ready"}
    assert by_name["Empty"]["configured"] is False
    assert by_name["Ready"]["configured"] is True
    assert by_name["Ready"]["description"] == "ops"
    # The list never leaks key material.
    assert "sk-proj-secret" not in resp.text
