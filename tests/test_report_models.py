"""Persistence round-trips for the DB-reports models (design spec §4)."""

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    ReportSchedule,
    ReportStatus,
    Repository,
)


async def test_report_models_round_trip(db_session: AsyncSession) -> None:
    repo = Repository(name="Vault")
    db_session.add(repo)
    await db_session.flush()

    conn = DatabaseConnection(
        repository_id=repo.id,
        db_type=DatabaseType.POSTGRES,
        host="db.internal",
        port=5432,
        database="sales",
        username="reader",
        password_encrypted="gAAA-cipher",
        exposed_schema=[
            {"table": "orders", "description": "", "columns": [{"name": "city", "description": ""}]}
        ],
    )
    db_session.add(conn)
    await db_session.flush()

    schedule = ReportSchedule(
        repository_id=repo.id,
        connection_id=conn.id,
        owner_id=None,
        prompt="nightly sales",
        frozen_sql="SELECT 1",
        frozen_chart_spec={"chart_type": "none", "x_column": None, "y_column": None, "title": "t"},
        run_at_time=datetime.time(1, 0),
    )
    db_session.add(schedule)
    await db_session.flush()
    assert schedule.enabled is True
    assert schedule.last_run_at is None

    report = GeneratedReport(
        repository_id=repo.id,
        connection_id=conn.id,
        requested_by=None,
        prompt="report for Kyiv",
        schedule_id=schedule.id,
    )
    db_session.add(report)
    await db_session.flush()
    assert report.status is ReportStatus.PENDING
    assert report.generated_sql is None
    assert report.pdf_data is None
