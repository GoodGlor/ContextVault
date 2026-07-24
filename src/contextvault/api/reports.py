"""Database-backed reports API (Task 10, DB-reports spec §7): request → poll →
per-user history → PDF download.

A report is asked for in natural language against a repository's connected
reporting database (``services/database.py`` / ``DatabaseConnection``); generation
(NL→SQL→PDF, card/spec §5-§6) runs as a background task so the request returns
immediately with a ``PENDING`` row the caller polls via ``GET .../reports/{id}``.

Access follows the same shape as the query endpoint (``api/query.py``): the
repository must exist (404) and, for a non-admin, an *active* grant is required
(403). For reports and schedules, admins bypass the grant check entirely — this
is specific to this module (``query.py``/``conversations.py``/``sources.py``
still apply ``has_active_grant`` to admins too).
Report *history* is per-user by default (``GET .../reports`` returns only the
caller's own rows); ``?all=true`` is an admin-only escape hatch onto every user's
reports for this repository, and only that admin view exposes ``generated_sql`` —
the audit trail of the exact query that ran. A single report, its PDF download, and
its deletion are all owner-or-admin; a non-admin reaching for someone else's report
gets 404 (not 403), so the report's existence is never revealed to a stranger.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import get_current_user
from contextvault.db.session import get_session
from contextvault.models import (
    DatabaseConnection,
    GeneratedReport,
    ReportStatus,
    Repository,
    Role,
    User,
)
from contextvault.services import grants as grant_service
from contextvault.services.reports import run_report_generation

router = APIRouter(tags=["reports"])


class ReportRequest(BaseModel):
    """A natural-language report request against a repository's reporting database."""

    prompt: str = Field(min_length=1)


class ReportResponse(BaseModel):
    """One report's request + artifact state — never the PDF bytes themselves."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    prompt: str
    status: ReportStatus
    error: str | None
    created_at: datetime
    has_pdf: bool
    schedule_id: uuid.UUID | None


class ReportAdminResponse(ReportResponse):
    """The admin ``?all=true`` shape: adds ``generated_sql``, the audit trail."""

    generated_sql: str | None


def _base_fields(report: GeneratedReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "repository_id": report.repository_id,
        "prompt": report.prompt,
        "status": report.status,
        "error": report.error,
        "created_at": report.created_at,
        "has_pdf": report.pdf_data is not None,
        "schedule_id": report.schedule_id,
    }


def _to_response(report: GeneratedReport) -> ReportResponse:
    return ReportResponse(**_base_fields(report))


def _to_admin_response(report: GeneratedReport) -> ReportAdminResponse:
    return ReportAdminResponse(**_base_fields(report), generated_sql=report.generated_sql)


async def _get_repo_or_404(session: AsyncSession, repository_id: uuid.UUID) -> Repository:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


async def _guard_repo_access(session: AsyncSession, user: User, repository_id: uuid.UUID) -> None:
    """The query-endpoint guard: repo must exist (404); non-admins need an active
    grant (403). Admins bypass the grant check."""
    await _get_repo_or_404(session, repository_id)
    if user.role != Role.ADMIN and not await grant_service.has_active_grant(
        session, user.id, repository_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No access to this repository"
        )


async def _get_own_report_or_404(
    session: AsyncSession, user: User, repository_id: uuid.UUID, report_id: uuid.UUID
) -> GeneratedReport:
    """Load a report scoped to its repository, owner-or-admin only.

    A non-admin reaching for another user's report gets 404 — the same as an
    unknown report id — so existence is never revealed to a stranger.
    """
    await _get_repo_or_404(session, repository_id)
    report = await session.get(GeneratedReport, report_id)
    if report is None or report.repository_id != repository_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    if user.role != Role.ADMIN and report.requested_by != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return report


@router.post("/repositories/{repository_id}/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    repository_id: uuid.UUID,
    payload: ReportRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    """Request a report: creates a ``PENDING`` row and dispatches NL→SQL→PDF
    generation (``services/reports.run_report_generation``) as a background task,
    returning immediately for the caller to poll. 400 if the repository has no
    connected reporting database yet."""
    await _guard_repo_access(session, user, repository_id)
    conn = (
        await session.execute(
            select(DatabaseConnection).where(DatabaseConnection.repository_id == repository_id)
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository has no database connection configured",
        )

    report = GeneratedReport(
        repository_id=repository_id,
        connection_id=conn.id,
        requested_by=user.id,
        prompt=payload.prompt,
        status=ReportStatus.PENDING,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)

    background_tasks.add_task(run_report_generation, report.id)
    return _to_response(report)


@router.get("/repositories/{repository_id}/reports", response_model=None)
async def list_reports(
    repository_id: uuid.UUID,
    all_reports: bool = Query(default=False, alias="all"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReportResponse]:
    """The caller's own reports, newest first. ``?all=true`` is an admin-only
    escape hatch onto every user's reports for this repository, each row then
    also carrying ``generated_sql`` (the audit field)."""
    await _get_repo_or_404(session, repository_id)

    if all_reports:
        if user.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
        stmt = (
            select(GeneratedReport)
            .where(GeneratedReport.repository_id == repository_id)
            .order_by(GeneratedReport.created_at.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_admin_response(r) for r in rows]

    if user.role != Role.ADMIN and not await grant_service.has_active_grant(
        session, user.id, repository_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No access to this repository"
        )
    stmt = (
        select(GeneratedReport)
        .where(
            GeneratedReport.repository_id == repository_id,
            GeneratedReport.requested_by == user.id,
        )
        .order_by(GeneratedReport.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_response(r) for r in rows]


@router.get("/repositories/{repository_id}/reports/{report_id}")
async def get_report(
    repository_id: uuid.UUID,
    report_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    """A single report, for polling status; owner or admin only."""
    report = await _get_own_report_or_404(session, user, repository_id, report_id)
    return _to_response(report)


@router.get("/repositories/{repository_id}/reports/{report_id}/download")
async def download_report(
    repository_id: uuid.UUID,
    report_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download a report's PDF; 404 unless generation finished (``DONE`` with a
    stored artifact). Owner or admin only."""
    report = await _get_own_report_or_404(session, user, repository_id, report_id)
    if report.status != ReportStatus.DONE or report.pdf_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report PDF not available"
        )
    return Response(
        content=report.pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.pdf_filename}"'},
    )


@router.delete(
    "/repositories/{repository_id}/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_report(
    repository_id: uuid.UUID,
    report_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a report; owner or admin only."""
    report = await _get_own_report_or_404(session, user, repository_id, report_id)
    await session.delete(report)
    await session.commit()
