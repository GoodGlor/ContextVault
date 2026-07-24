"""Reports API (Task 10, DB-reports spec §7): request, poll, per-user history, PDF.

Mirrors ``test_admin_notes_api``'s fixture pattern. ``run_report_generation`` — the
BackgroundTasks entrypoint imported into ``contextvault.api.reports`` — is
monkeypatched per test: a fake that opens the shared ``db_session`` and marks the
report DONE with PDF bytes when a test needs a completed artifact, or a no-op when a
test wants to observe the PENDING state (e.g. the download-while-pending 404).
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import contextvault.api.reports as reports_api
from contextvault.core.crypto import encrypt
from contextvault.db.session import get_session
from contextvault.main import create_app
from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    ReportStatus,
    Repository,
    Role,
    User,
)
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


async def _user(db_session: AsyncSession, role: Role, username: str) -> User:
    return await user_service.create_user(db_session, username=username, password="pw", role=role)


async def _token(client: AsyncClient, username: str) -> str:
    resp = await client.post("/auth/login", json={"username": username, "password": "pw"})
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _repo(db_session: AsyncSession) -> Repository:
    repo = Repository(name="Vault", llm_provider="gemini", llm_model="gem-1")
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _connection(db_session: AsyncSession, repository_id: uuid.UUID) -> DatabaseConnection:
    conn = DatabaseConnection(
        repository_id=repository_id,
        db_type=DatabaseType.POSTGRES,
        host="localhost",
        port=5432,
        database="reporting",
        username="ro_user",
        password_encrypted=encrypt("secret"),
        exposed_schema=[],
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _grant(db_session: AsyncSession, user_id: uuid.UUID, repo_id: uuid.UUID) -> None:
    from contextvault.models import Grant

    db_session.add(Grant(user_id=user_id, repository_id=repo_id))
    await db_session.flush()


def _make_completing_fake(db_session: AsyncSession, calls: list[uuid.UUID]):  # type: ignore[no-untyped-def]
    """A fake ``run_report_generation`` that marks the report DONE with PDF bytes."""

    async def fake(report_id: uuid.UUID) -> None:
        calls.append(report_id)
        report = await db_session.get(GeneratedReport, report_id)
        assert report is not None
        report.status = ReportStatus.DONE
        report.generated_sql = "SELECT count(*) FROM users"
        report.pdf_data = b"%PDF-fake"
        report.pdf_filename = f"report-{report_id}.pdf"
        await db_session.commit()

    return fake


def _make_noop_fake(calls: list[uuid.UUID]):  # type: ignore[no-untyped-def]
    """A fake ``run_report_generation`` that records the call but leaves PENDING."""

    async def fake(report_id: uuid.UUID) -> None:
        calls.append(report_id)

    return fake


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #


async def test_create_report_201_dispatches_background(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)

    calls: list[uuid.UUID] = []
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake(calls))

    resp = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "How many users signed up last week?"},
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["repository_id"] == str(repo.id)
    assert body["prompt"] == "How many users signed up last week?"
    assert body["status"] == "pending"
    assert body["has_pdf"] is False
    assert body["schedule_id"] is None
    assert "generated_sql" not in body
    assert calls == [uuid.UUID(body["id"])]


async def test_create_report_400_without_connection(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)  # no DatabaseConnection
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    resp = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "anything"},
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 400


async def test_create_report_404_unknown_repo(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _user(db_session, Role.USER, "alice")
    resp = await client.post(
        f"/repositories/{uuid.uuid4()}/reports",
        json={"prompt": "anything"},
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 404


async def test_create_report_403_ungranted_user(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    await _user(db_session, Role.USER, "alice")  # no grant
    resp = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "anything"},
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 403


async def test_create_report_rejects_empty_prompt(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)
    resp = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": ""},
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Listing: own-only vs admin ?all=true
# --------------------------------------------------------------------------- #


async def test_list_reports_is_own_only_newest_first(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    alice = await _user(db_session, Role.USER, "alice")
    bob = await _user(db_session, Role.USER, "bob")
    await _grant(db_session, alice.id, repo.id)
    await _grant(db_session, bob.id, repo.id)
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    alice_token = await _token(client, "alice")
    bob_token = await _token(client, "bob")

    first = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "alice report one"},
        headers=_auth(alice_token),
    )
    second = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "alice report two"},
        headers=_auth(alice_token),
    )
    await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "bob report"},
        headers=_auth(bob_token),
    )
    assert first.status_code == second.status_code == 201

    # Postgres's `now()` resolves to the transaction's start time, and this whole
    # test runs inside the single shared ``db_session`` transaction — so every row
    # created here gets an identical ``created_at`` from the server default. Force
    # distinct timestamps directly so "newest first" is observable within one test
    # transaction; in production, separate requests run in separate transactions
    # and naturally get distinct real-clock values.
    base = datetime.now(UTC)
    await db_session.execute(
        sa.update(GeneratedReport)
        .where(GeneratedReport.id == uuid.UUID(first.json()["id"]))
        .values(created_at=base)
    )
    await db_session.execute(
        sa.update(GeneratedReport)
        .where(GeneratedReport.id == uuid.UUID(second.json()["id"]))
        .values(created_at=base + timedelta(seconds=1))
    )
    await db_session.commit()

    listing = await client.get(f"/repositories/{repo.id}/reports", headers=_auth(alice_token))
    assert listing.status_code == 200
    body = listing.json()
    assert [r["prompt"] for r in body] == ["alice report two", "alice report one"]
    assert all("generated_sql" not in r for r in body)


async def test_get_other_users_report_is_404_not_403(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    alice = await _user(db_session, Role.USER, "alice")
    bob = await _user(db_session, Role.USER, "bob")
    await _grant(db_session, alice.id, repo.id)
    await _grant(db_session, bob.id, repo.id)
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    bob_token = await _token(client, "bob")
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "bob's private report"},
        headers=_auth(bob_token),
    )
    report_id = created.json()["id"]

    resp = await client.get(
        f"/repositories/{repo.id}/reports/{report_id}",
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 404


async def test_list_all_true_is_admin_only_and_includes_generated_sql(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    alice = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, alice.id, repo.id)
    await _user(db_session, Role.ADMIN, "root")

    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        reports_api, "run_report_generation", _make_completing_fake(db_session, calls)
    )

    alice_token = await _token(client, "alice")
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "alice's report"},
        headers=_auth(alice_token),
    )
    assert created.status_code == 201

    # A non-admin passing ?all=true is forbidden, even though it is their own repo access.
    forbidden = await client.get(
        f"/repositories/{repo.id}/reports?all=true", headers=_auth(alice_token)
    )
    assert forbidden.status_code == 403

    admin_token = await _token(client, "root")
    listing = await client.get(
        f"/repositories/{repo.id}/reports?all=true", headers=_auth(admin_token)
    )
    assert listing.status_code == 200
    body = listing.json()
    assert len(body) == 1
    assert body[0]["prompt"] == "alice's report"
    assert body[0]["generated_sql"] == "SELECT count(*) FROM users"
    assert body[0]["status"] == "done"
    assert body[0]["has_pdf"] is True


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #


async def test_download_404_while_pending_then_200_once_done(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)
    token = await _token(client, "alice")

    calls: list[uuid.UUID] = []
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake(calls))
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "pending report"},
        headers=_auth(token),
    )
    report_id = created.json()["id"]

    pending_download = await client.get(
        f"/repositories/{repo.id}/reports/{report_id}/download", headers=_auth(token)
    )
    assert pending_download.status_code == 404

    # Now mark it DONE with PDF bytes directly (simulating generation finishing).
    report = await db_session.get(GeneratedReport, uuid.UUID(report_id))
    assert report is not None
    report.status = ReportStatus.DONE
    report.pdf_data = b"%PDF-fake-bytes"
    report.pdf_filename = "report-x.pdf"
    await db_session.commit()

    done_download = await client.get(
        f"/repositories/{repo.id}/reports/{report_id}/download", headers=_auth(token)
    )
    assert done_download.status_code == 200
    assert done_download.headers["content-type"] == "application/pdf"
    assert done_download.headers["content-disposition"] == 'attachment; filename="report-x.pdf"'
    assert done_download.content == b"%PDF-fake-bytes"


async def test_download_pdf_bytes_never_appear_in_json_listing(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)
    token = await _token(client, "alice")

    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        reports_api, "run_report_generation", _make_completing_fake(db_session, calls)
    )
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "report with pdf"},
        headers=_auth(token),
    )
    assert created.status_code == 201

    listing = await client.get(f"/repositories/{repo.id}/reports", headers=_auth(token))
    assert listing.status_code == 200
    raw_text = listing.text
    assert "%PDF" not in raw_text
    body = listing.json()
    assert "pdf_data" not in body[0]
    assert body[0]["has_pdf"] is True


async def test_download_403_for_ungranted_stranger_repo_visible_only_via_grant(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Sanity: repository existence still gates as 404 for an unknown repo id on
    the download route (routed through the same owner/admin lookup)."""
    await _user(db_session, Role.USER, "alice")
    resp = await client.get(
        f"/repositories/{uuid.uuid4()}/reports/{uuid.uuid4()}/download",
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


async def test_delete_own_report_204(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    user = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, user.id, repo.id)
    token = await _token(client, "alice")
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "to delete"},
        headers=_auth(token),
    )
    report_id = created.json()["id"]

    resp = await client.delete(f"/repositories/{repo.id}/reports/{report_id}", headers=_auth(token))
    assert resp.status_code == 204

    follow_up = await client.get(
        f"/repositories/{repo.id}/reports/{report_id}", headers=_auth(token)
    )
    assert follow_up.status_code == 404


async def test_delete_other_users_report_404(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    alice = await _user(db_session, Role.USER, "alice")
    bob = await _user(db_session, Role.USER, "bob")
    await _grant(db_session, alice.id, repo.id)
    await _grant(db_session, bob.id, repo.id)
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    bob_token = await _token(client, "bob")
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "bob's report"},
        headers=_auth(bob_token),
    )
    report_id = created.json()["id"]

    resp = await client.delete(
        f"/repositories/{repo.id}/reports/{report_id}",
        headers=_auth(await _token(client, "alice")),
    )
    assert resp.status_code == 404


async def test_admin_can_delete_any_users_report(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _repo(db_session)
    await _connection(db_session, repo.id)
    alice = await _user(db_session, Role.USER, "alice")
    await _grant(db_session, alice.id, repo.id)
    await _user(db_session, Role.ADMIN, "root")
    monkeypatch.setattr(reports_api, "run_report_generation", _make_noop_fake([]))

    alice_token = await _token(client, "alice")
    created = await client.post(
        f"/repositories/{repo.id}/reports",
        json={"prompt": "alice's report"},
        headers=_auth(alice_token),
    )
    report_id = created.json()["id"]

    resp = await client.delete(
        f"/repositories/{repo.id}/reports/{report_id}",
        headers=_auth(await _token(client, "root")),
    )
    assert resp.status_code == 204
