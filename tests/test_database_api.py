"""Admin database-connection API (Task 9, DB-reports spec).

An admin points a repository at a read-only external SQL database: ``PUT``
live-tests the connection before storing it, the password is never returned by
any route, the exposed-schema allow-list can be edited without re-testing, and
``introspect`` reads live tables/columns from the *stored* connection.

Liveness (``test_connection`` / ``introspect_schema``) is monkeypatched by
attribute string — never imported by name — so the suite never dials a real
external database and pytest never mistakes the ``test_``-named import for a
test function.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.db.session import get_session
from contextvault.main import create_app
from contextvault.models import Repository, Role, User
from contextvault.services import users as user_service
from contextvault.services.report_db import DBConnectionError


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _use_test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _user(db_session: AsyncSession, role: Role, username: str) -> User:
    return await user_service.create_user(db_session, username=username, password="pw", role=role)


async def _token(client: AsyncClient, username: str) -> str:
    resp = await client.post("/auth/login", json={"username": username, "password": "pw"})
    return str(resp.json()["access_token"])


async def _repo(db_session: AsyncSession) -> Repository:
    repo = Repository(name="Reporting Vault")
    db_session.add(repo)
    await db_session.flush()
    return repo


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "db_type": "postgres",
        "host": "reporting.internal",
        "port": 5432,
        "database": "sales",
        "username": "reporter",
        "password": "s3cret",
    }
    body.update(overrides)
    return body


def _assert_no_password_leak(resp_json: object) -> None:
    assert "password" not in str(resp_json)
    assert "s3cret" not in str(resp_json)


# --------------------------------------------------------------------------- #
# PUT — live-test, encrypt, mask, upsert
# --------------------------------------------------------------------------- #


async def test_put_tests_connection_encrypts_and_masks(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_test_connection(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin1")
    token = await _token(client, "admin1")

    resp = await client.put(
        f"/repositories/{repo.id}/database",
        json=_payload(),
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_password_leak(body)
    assert body["host"] == "reporting.internal"
    assert body["db_type"] == "postgres"
    assert body["username"] == "reporter"
    first_id = body["id"]
    assert len(calls) == 1
    assert calls[0]["password"] == "s3cret"

    got = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert got.status_code == 200
    got_body = got.json()
    _assert_no_password_leak(got_body)
    assert got_body["username"] == "reporter"

    # A second PUT (new host) upserts — one row per repo, same id.
    resp2 = await client.put(
        f"/repositories/{repo.id}/database",
        json=_payload(host="reporting2.internal"),
        headers=_auth(token),
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["id"] == first_id
    assert body2["host"] == "reporting2.internal"


async def test_put_update_keeps_password_when_omitted(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_passwords: list[object] = []

    async def fake_test_connection(**kwargs: object) -> None:
        seen_passwords.append(kwargs["password"])

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin2")
    token = await _token(client, "admin2")

    first = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert first.status_code == 200

    # Update without a password (empty string) — must keep the stored one, so
    # the live re-test is called with the original secret, not "".
    second = await client.put(
        f"/repositories/{repo.id}/database",
        json=_payload(host="new-host.internal", password=""),
        headers=_auth(token),
    )
    assert second.status_code == 200
    assert seen_passwords == ["s3cret", "s3cret"]
    _assert_no_password_leak(second.json())


async def test_put_update_keeps_exposed_schema_when_omitted(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin_schema_keep")
    token = await _token(client, "admin_schema_keep")

    put_resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert put_resp.status_code == 200

    allow_list = [
        {
            "table": "orders",
            "description": "Customer orders",
            "columns": [{"name": "id", "description": "Primary key"}],
        }
    ]
    patch_resp = await client.patch(
        f"/repositories/{repo.id}/database/schema",
        json={"exposed_schema": allow_list},
        headers=_auth(token),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["exposed_schema"] == allow_list

    # Second PUT fixes the host but omits exposed_schema entirely — the
    # curated allow-list set via PATCH must survive, not be wiped to [].
    put_resp2 = await client.put(
        f"/repositories/{repo.id}/database",
        json=_payload(host="reporting3.internal"),
        headers=_auth(token),
    )
    assert put_resp2.status_code == 200
    assert put_resp2.json()["exposed_schema"] == allow_list

    got = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["exposed_schema"] == allow_list


async def test_put_rejects_unreachable_database(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        raise DBConnectionError("no route to host")

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin3")
    token = await _token(client, "admin3")

    resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert resp.status_code == 400
    assert "no route to host" in resp.json()["detail"]

    # Nothing was stored — GET still 404s.
    got = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert got.status_code == 404


# --------------------------------------------------------------------------- #
# Authorization / 404s
# --------------------------------------------------------------------------- #


async def test_requires_admin(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    async def fake_introspect_schema(**kwargs: object) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)
    monkeypatch.setattr("contextvault.api.database.introspect_schema", fake_introspect_schema)

    repo = await _repo(db_session)
    await _user(db_session, Role.USER, "regular")
    token = await _token(client, "regular")

    put_resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert put_resp.status_code == 403

    get_resp = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert get_resp.status_code == 403

    patch_resp = await client.patch(
        f"/repositories/{repo.id}/database/schema",
        json={"exposed_schema": []},
        headers=_auth(token),
    )
    assert patch_resp.status_code == 403

    delete_resp = await client.delete(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert delete_resp.status_code == 403

    introspect_resp = await client.post(
        f"/repositories/{repo.id}/database/introspect", headers=_auth(token)
    )
    assert introspect_resp.status_code == 403


async def test_unknown_repo_404(db_session: AsyncSession, client: AsyncClient) -> None:
    await _user(db_session, Role.ADMIN, "admin4")
    token = await _token(client, "admin4")
    ghost = uuid.uuid4()

    put_resp = await client.put(
        f"/repositories/{ghost}/database", json=_payload(), headers=_auth(token)
    )
    assert put_resp.status_code == 404

    get_resp = await client.get(f"/repositories/{ghost}/database", headers=_auth(token))
    assert get_resp.status_code == 404

    patch_resp = await client.patch(
        f"/repositories/{ghost}/database/schema",
        json={"exposed_schema": []},
        headers=_auth(token),
    )
    assert patch_resp.status_code == 404

    delete_resp = await client.delete(f"/repositories/{ghost}/database", headers=_auth(token))
    assert delete_resp.status_code == 404

    introspect_resp = await client.post(
        f"/repositories/{ghost}/database/introspect", headers=_auth(token)
    )
    assert introspect_resp.status_code == 404


# --------------------------------------------------------------------------- #
# PATCH schema — allow-list edits, no re-test
# --------------------------------------------------------------------------- #


async def test_patch_schema_saves_allow_list(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin5")
    token = await _token(client, "admin5")

    put_resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert put_resp.status_code == 200

    allow_list = [
        {
            "table": "orders",
            "description": "Customer orders",
            "columns": [
                {"name": "id", "description": "Primary key"},
                {"name": "total", "description": "Order total in cents"},
            ],
        }
    ]
    patch_resp = await client.patch(
        f"/repositories/{repo.id}/database/schema",
        json={"exposed_schema": allow_list},
        headers=_auth(token),
    )
    assert patch_resp.status_code == 200
    patch_body = patch_resp.json()
    _assert_no_password_leak(patch_body)
    assert patch_body["exposed_schema"] == allow_list

    got = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert got.status_code == 200
    got_body = got.json()
    _assert_no_password_leak(got_body)
    assert got_body["exposed_schema"] == allow_list


# --------------------------------------------------------------------------- #
# Introspect — uses the stored connection
# --------------------------------------------------------------------------- #


async def test_introspect_uses_stored_connection(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    fixed_schema: list[dict[str, object]] = [
        {"table": "orders", "description": "", "columns": [{"name": "id", "description": ""}]}
    ]

    seen_kwargs: list[dict[str, object]] = []

    async def fake_introspect_schema(**kwargs: object) -> list[dict[str, object]]:
        seen_kwargs.append(kwargs)
        return fixed_schema

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)
    monkeypatch.setattr("contextvault.api.database.introspect_schema", fake_introspect_schema)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin6")
    token = await _token(client, "admin6")

    # Without a stored connection: 400, not 404.
    no_conn_resp = await client.post(
        f"/repositories/{repo.id}/database/introspect", headers=_auth(token)
    )
    assert no_conn_resp.status_code == 400

    put_resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token)
    )
    assert put_resp.status_code == 200

    resp = await client.post(f"/repositories/{repo.id}/database/introspect", headers=_auth(token))
    assert resp.status_code == 200
    resp_body = resp.json()
    _assert_no_password_leak(resp_body)
    assert resp_body == {"schema": fixed_schema}
    assert seen_kwargs[0]["password"] == "s3cret"
    assert seen_kwargs[0]["host"] == "reporting.internal"


async def test_introspect_rejects_unreachable(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    async def fake_introspect_schema(**kwargs: object) -> list[dict[str, object]]:
        raise DBConnectionError("connection refused")

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)
    monkeypatch.setattr("contextvault.api.database.introspect_schema", fake_introspect_schema)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin7")
    token = await _token(client, "admin7")

    await client.put(f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token))
    resp = await client.post(f"/repositories/{repo.id}/database/introspect", headers=_auth(token))
    assert resp.status_code == 400
    assert "connection refused" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# DELETE
# --------------------------------------------------------------------------- #


async def test_delete_removes_connection(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_test_connection(**kwargs: object) -> None:
        return None

    monkeypatch.setattr("contextvault.api.database.test_connection", fake_test_connection)

    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin8")
    token = await _token(client, "admin8")

    await client.put(f"/repositories/{repo.id}/database", json=_payload(), headers=_auth(token))

    delete_resp = await client.delete(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert get_resp.status_code == 404


async def test_delete_unknown_connection_404(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin9")
    token = await _token(client, "admin9")

    resp = await client.delete(f"/repositories/{repo.id}/database", headers=_auth(token))
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


async def test_put_rejects_invalid_port(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin10")
    token = await _token(client, "admin10")

    resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(port=0), headers=_auth(token)
    )
    assert resp.status_code == 422

    resp2 = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(port=70000), headers=_auth(token)
    )
    assert resp2.status_code == 422


async def test_put_rejects_empty_host(db_session: AsyncSession, client: AsyncClient) -> None:
    repo = await _repo(db_session)
    await _user(db_session, Role.ADMIN, "admin11")
    token = await _token(client, "admin11")

    resp = await client.put(
        f"/repositories/{repo.id}/database", json=_payload(host=""), headers=_auth(token)
    )
    assert resp.status_code == 422
