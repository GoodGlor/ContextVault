"""Report generation orchestration (DB-reports spec §6) + the frozen-SQL path.

Mirrors ``services/ingestion.py``: an awaitable core (``generate_report``) that
drives PROCESSING→DONE|FAILED on the row, and a BackgroundTasks seam
(``run_report_generation``) that opens its own session. The self-repair loop
gives the LLM at most 3 attempts, feeding every rejection back as feedback;
each attempt passes the FULL guardrail stack.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.crypto import decrypt
from contextvault.db.session import SessionLocal
from contextvault.models import (
    DatabaseConnection,
    GeneratedReport,
    ReportSchedule,
    ReportStatus,
    Repository,
)
from contextvault.services import providers as provider_service
from contextvault.services.ingestion import SessionFactory
from contextvault.services.report_execution import QueryExecutionError, run_validated_query
from contextvault.services.report_llm import ChartSpec, ReportQueryParseError, generate_report_query
from contextvault.services.report_render import build_pdf, render_chart
from contextvault.services.sql_guardrails import SQLValidationError, validate_sql

_MAX_ATTEMPTS = 3  # 1 initial + 2 self-repairs (spec §5)


class ReportGenerationError(Exception):
    """No valid query could be produced within the attempt budget."""


def _conn_params(connection: DatabaseConnection) -> dict[str, Any]:
    return {
        "db_type": connection.db_type,
        "host": connection.host,
        "port": connection.port,
        "database": connection.database,
        "username": connection.username,
        "password": decrypt(connection.password_encrypted),
    }


async def _run_and_render(
    report: GeneratedReport, connection: DatabaseConnection, sql: str, chart: ChartSpec
) -> None:
    """Execute validated SQL and attach the PDF artifact to the report row."""
    result = await run_validated_query(**_conn_params(connection), sql=sql)
    chart_png = render_chart(result, chart)
    title = chart.title or report.prompt
    pdf = build_pdf(title=title, prompt=report.prompt, result=result, chart_png=chart_png)
    report.generated_sql = sql
    report.chart_spec = chart.model_dump()
    report.pdf_data = pdf
    report.pdf_filename = f"report-{report.id}.pdf"
    report.status = ReportStatus.DONE
    report.error = None


async def generate_report(session: AsyncSession, report: GeneratedReport) -> None:
    """The full NL→SQL→PDF pipeline for one report row; never raises."""
    report.status = ReportStatus.PROCESSING
    await session.commit()
    try:
        connection = await session.get(DatabaseConnection, report.connection_id)
        repo = await session.get(Repository, report.repository_id)
        if connection is None or repo is None or repo.llm_provider is None:
            raise ReportGenerationError(
                "Repository is missing its database connection or LLM config."
            )
        if not await provider_service.repo_is_answerable(session, repo):
            raise ReportGenerationError("The repository's LLM provider has no verified key.")
        api_key, base_url = await provider_service.get_call_credentials(session, repo.llm_provider)
        # Release the pooled app-DB connection before slow LLM/reporting-DB work
        # (same discipline as ingestion's OCR path).
        await session.commit()

        feedback: list[str] = []
        for _ in range(_MAX_ATTEMPTS):
            try:
                query = await generate_report_query(
                    repo.llm_provider,
                    api_key,
                    repo.llm_model or "",
                    base_url=base_url,
                    user_prompt=report.prompt,
                    exposed_schema=connection.exposed_schema,
                    db_type=connection.db_type,
                    feedback=feedback,
                )
            except ReportQueryParseError as exc:
                feedback.append(str(exc))
                continue
            try:
                safe_sql = validate_sql(
                    query.sql, db_type=connection.db_type, exposed_schema=connection.exposed_schema
                )
                await _run_and_render(report, connection, safe_sql, query.chart)
                await session.commit()
                return
            except (SQLValidationError, QueryExecutionError) as exc:
                feedback.append(f"Query `{query.sql}` was rejected: {exc}")
        raise ReportGenerationError(
            "Could not produce a valid query for this request. Last problem: "
            + (feedback[-1] if feedback else "no attempts succeeded")
        )
    except Exception as exc:  # noqa: BLE001 — any failure lands on the row, never silent
        await session.rollback()
        report.status = ReportStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
        await session.commit()


async def run_report_generation(
    report_id: uuid.UUID, *, session_factory: SessionFactory = SessionLocal
) -> None:
    """BackgroundTasks entrypoint: fresh session, load, delegate. No-op if deleted."""
    async with session_factory() as session:
        report = await session.get(GeneratedReport, report_id)
        if report is None:
            return
        await generate_report(session, report)


async def execute_frozen(session: AsyncSession, *, schedule: ReportSchedule) -> GeneratedReport:
    """Run a schedule's frozen SQL as a new report for its owner (no LLM call)."""
    report = GeneratedReport(
        repository_id=schedule.repository_id,
        connection_id=schedule.connection_id,
        requested_by=schedule.owner_id,
        prompt=schedule.prompt,
        schedule_id=schedule.id,
        status=ReportStatus.PROCESSING,
    )
    session.add(report)
    await session.flush()
    try:
        connection = await session.get(DatabaseConnection, schedule.connection_id)
        if connection is None:
            raise ReportGenerationError("The schedule's database connection is gone.")
        chart = ChartSpec.model_validate(schedule.frozen_chart_spec)
        await _run_and_render(report, connection, schedule.frozen_sql, chart)
    except Exception as exc:  # noqa: BLE001
        report.status = ReportStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
    await session.commit()
    return report
