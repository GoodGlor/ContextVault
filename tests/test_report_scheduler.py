"""Nightly scheduler: due-schedule selection and frozen-SQL re-execution.

DB-backed: uses the ``db_session`` fixture and skips when no migrated database
is reachable (see conftest). The ``_connected_repo`` helper mirrors the one in
``tests/test_reports_service.py`` (Task 8) — re-created locally per the task
brief rather than imported across test files.
"""

import datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.crypto import encrypt
from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    ReportSchedule,
    ReportStatus,
    Repository,
)
from contextvault.services.report_llm import ChartSpec
from contextvault.services.report_scheduler import find_due_schedules, run_due_schedules


def _fixed_factory(session: AsyncSession):  # type: ignore[no-untyped-def]
    """A session factory that always yields ``session`` without closing it."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


async def _connected_repo(db_session: AsyncSession) -> tuple[Repository, DatabaseConnection]:
    url = make_url(get_settings().database_url)
    repo = Repository(name="Vault", llm_provider="gemini", llm_model="gem-1")
    db_session.add(repo)
    await db_session.flush()
    conn = DatabaseConnection(
        repository_id=repo.id,
        db_type=DatabaseType.POSTGRES,
        host=url.host or "localhost",
        port=url.port or 5432,
        database=url.database or "",
        username=url.username or "",
        password_encrypted=encrypt(url.password or ""),
        exposed_schema=[
            {
                "table": "repositories",
                "description": "",
                "columns": [{"name": "name", "description": ""}],
            }
        ],
    )
    db_session.add(conn)
    await db_session.flush()
    return repo, conn


def _schedule(
    repo: Repository,
    conn: DatabaseConnection,
    *,
    frozen_sql: str,
    run_at_time: datetime.time,
    enabled: bool = True,
    last_run_at: datetime.datetime | None = None,
) -> ReportSchedule:
    return ReportSchedule(
        repository_id=repo.id,
        connection_id=conn.id,
        owner_id=None,
        prompt="names",
        frozen_sql=frozen_sql,
        frozen_chart_spec=ChartSpec(chart_type="none", title="t").model_dump(),
        run_at_time=run_at_time,
        enabled=enabled,
        last_run_at=last_run_at,
    )


async def test_due_logic(db_session: AsyncSession) -> None:
    repo, conn = await _connected_repo(db_session)
    now = datetime.datetime(2026, 7, 23, 1, 5, tzinfo=datetime.UTC)

    due_no_prior_run = _schedule(
        repo, conn, frozen_sql="SELECT name FROM repositories", run_at_time=datetime.time(1, 0)
    )
    due_ran_yesterday = _schedule(
        repo,
        conn,
        frozen_sql="SELECT name FROM repositories",
        run_at_time=datetime.time(1, 0),
        last_run_at=datetime.datetime(2026, 7, 22, 1, 5, tzinfo=datetime.UTC),
    )
    already_ran_today = _schedule(
        repo,
        conn,
        frozen_sql="SELECT name FROM repositories",
        run_at_time=datetime.time(1, 0),
        last_run_at=datetime.datetime(2026, 7, 23, 1, 1, tzinfo=datetime.UTC),
    )
    not_yet_time = _schedule(
        repo, conn, frozen_sql="SELECT name FROM repositories", run_at_time=datetime.time(2, 0)
    )
    disabled = _schedule(
        repo,
        conn,
        frozen_sql="SELECT name FROM repositories",
        run_at_time=datetime.time(1, 0),
        enabled=False,
    )
    for s in (due_no_prior_run, due_ran_yesterday, already_ran_today, not_yet_time, disabled):
        db_session.add(s)
    await db_session.flush()

    due = await find_due_schedules(db_session, now=now)
    due_ids = {s.id for s in due}

    assert due_no_prior_run.id in due_ids
    assert due_ran_yesterday.id in due_ids
    assert already_ran_today.id not in due_ids
    assert not_yet_time.id not in due_ids
    assert disabled.id not in due_ids


async def test_run_due_schedules_executes_frozen_and_stamps(db_session: AsyncSession) -> None:
    repo, conn = await _connected_repo(db_session)
    schedule = _schedule(
        repo, conn, frozen_sql="SELECT name FROM repositories", run_at_time=datetime.time(1, 0)
    )
    db_session.add(schedule)
    await db_session.flush()
    schedule_id = schedule.id

    now = datetime.datetime(2026, 7, 23, 1, 5, tzinfo=datetime.UTC)
    await run_due_schedules(now=now, session_factory=_fixed_factory(db_session))

    await db_session.refresh(schedule)
    assert schedule.last_run_at == now
    assert schedule.last_error is None
    assert schedule.enabled is True

    result = await db_session.execute(
        sa.select(GeneratedReport).where(GeneratedReport.schedule_id == schedule_id)
    )
    report = result.scalar_one()
    assert report.status is ReportStatus.DONE
    assert report.requested_by == schedule.owner_id


async def test_failed_run_records_last_error_but_keeps_schedule_enabled(
    db_session: AsyncSession,
) -> None:
    repo, conn = await _connected_repo(db_session)
    schedule = _schedule(
        repo, conn, frozen_sql="SELECT nope FROM missing", run_at_time=datetime.time(1, 0)
    )
    db_session.add(schedule)
    await db_session.flush()
    schedule_id = schedule.id

    now = datetime.datetime(2026, 7, 23, 1, 5, tzinfo=datetime.UTC)
    await run_due_schedules(now=now, session_factory=_fixed_factory(db_session))

    await db_session.refresh(schedule)
    assert schedule.last_run_at == now
    assert schedule.last_error is not None
    assert schedule.enabled is True

    result = await db_session.execute(
        sa.select(GeneratedReport).where(GeneratedReport.schedule_id == schedule_id)
    )
    report = result.scalar_one()
    assert report.status is ReportStatus.FAILED
