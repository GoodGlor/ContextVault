"""Orchestration: self-repair loop, status transitions, artifact persistence."""

from typing import Any

import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession

import contextvault.services.reports as reports_service
from contextvault.core.config import get_settings
from contextvault.core.crypto import encrypt
from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    LLMProviderName,
    ReportStatus,
    Repository,
)
from contextvault.services.report_llm import ChartSpec, ReportQuery


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


def _query(sql: str) -> ReportQuery:
    return ReportQuery(sql=sql, chart=ChartSpec(chart_type="none", title="t"))


async def test_self_repair_recovers_and_records_final_sql(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, conn = await _connected_repo(db_session)
    report = GeneratedReport(
        repository_id=repo.id, connection_id=conn.id, requested_by=None, prompt="names"
    )
    db_session.add(report)
    await db_session.flush()

    attempts: list[list[str]] = []
    replies = iter([_query("SELECT secret FROM vault_x"), _query("SELECT name FROM repositories")])

    async def fake_generate(provider: str, api_key: str, model: str, **kwargs: Any) -> ReportQuery:
        attempts.append(list(kwargs["feedback"]))
        return next(replies)

    monkeypatch.setattr(reports_service, "generate_report_query", fake_generate)
    monkeypatch.setattr(
        reports_service.provider_service,  # type: ignore[attr-defined]
        "get_provider_key",
        _fake_key,
    )

    await reports_service.generate_report(db_session, report)
    assert report.status is ReportStatus.DONE
    assert report.generated_sql is not None and "repositories" in report.generated_sql
    assert report.pdf_data is not None and report.pdf_data[:5] == b"%PDF-"
    assert len(attempts) == 2 and attempts[1]  # second attempt carried feedback


async def _fake_key(session: AsyncSession, provider: LLMProviderName) -> str:
    return "k"


async def test_three_bad_attempts_fail_the_report(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, conn = await _connected_repo(db_session)
    report = GeneratedReport(
        repository_id=repo.id, connection_id=conn.id, requested_by=None, prompt="p"
    )
    db_session.add(report)
    await db_session.flush()

    async def always_bad(provider: str, api_key: str, model: str, **kwargs: Any) -> ReportQuery:
        return _query("DROP TABLE repositories")

    monkeypatch.setattr(reports_service, "generate_report_query", always_bad)
    monkeypatch.setattr(
        reports_service.provider_service,  # type: ignore[attr-defined]
        "get_provider_key",
        _fake_key,
    )

    await reports_service.generate_report(db_session, report)
    assert report.status is ReportStatus.FAILED
    assert report.error is not None
