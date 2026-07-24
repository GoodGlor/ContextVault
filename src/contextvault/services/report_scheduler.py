"""Nightly report scheduler (design spec — Database-Backed Reports).

``find_due_schedules`` selects enabled schedules whose ``run_at_time`` has
passed and that have not already run today. ``run_due_schedules`` is the
awaitable core: it re-executes each due schedule's frozen SQL via
``execute_frozen`` (no LLM call — see ``services/reports.py``) and stamps the
schedule with when it ran and whether it failed. A failed run is recorded but
never disables the schedule — it simply tries again the following day.

``scheduler_loop`` is the process-lifetime seam: a simple poll loop, wired to
the app via ``main.py``'s lifespan. Any per-tick failure is logged and
swallowed so one bad tick can never kill the loop.
"""

import asyncio
import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.db.session import SessionLocal
from contextvault.models import ReportSchedule
from contextvault.services.ingestion import SessionFactory
from contextvault.services.reports import execute_frozen

logger = logging.getLogger(__name__)


async def find_due_schedules(session: AsyncSession, *, now: datetime) -> list[ReportSchedule]:
    """Enabled schedules whose run time has passed and haven't run today."""
    result = await session.execute(
        sa.select(ReportSchedule).where(
            ReportSchedule.enabled.is_(True),
            ReportSchedule.run_at_time <= now.time(),
            sa.or_(
                ReportSchedule.last_run_at.is_(None),
                sa.func.date(ReportSchedule.last_run_at) < now.date(),
            ),
        )
    )
    return list(result.scalars().all())


async def run_due_schedules(
    *, now: datetime, session_factory: SessionFactory = SessionLocal
) -> None:
    """Re-run every due schedule's frozen query, stamping the outcome."""
    async with session_factory() as session:
        due = await find_due_schedules(session, now=now)
        for schedule in due:
            report = await execute_frozen(session, schedule=schedule)
            schedule.last_run_at = now
            schedule.last_error = report.error
            await session.commit()


async def scheduler_loop(
    *, interval_seconds: int = 60, session_factory: SessionFactory = SessionLocal
) -> None:
    """Poll forever, running due schedules once per tick. Never dies on error."""
    while True:
        try:
            await run_due_schedules(now=datetime.now(UTC), session_factory=session_factory)
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(interval_seconds)
