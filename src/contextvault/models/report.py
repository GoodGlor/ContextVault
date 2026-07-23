"""Generated reports and their nightly schedules (DB-reports spec §4)."""

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, LargeBinary, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import ReportStatus
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ReportSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A frozen, validated report query re-executed nightly for its owner.

    ``frozen_sql``/``frozen_chart_spec`` are the already-guardrailed artifacts of
    the report the schedule was created from — nightly runs re-execute them
    verbatim (no LLM call). Relative windows stay rolling because generation
    expresses them as SQL date arithmetic.
    """

    __tablename__ = "report_schedules"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_sql: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_chart_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_at_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class GeneratedReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One report request and its artifact; the per-user history row.

    ``generated_sql`` is the audit trail — the exact validated SQL that ran
    (admin-visible). ``pdf_data`` holds the artifact bytes (reports are small;
    no blob storage exists in this app by design).
    """

    __tablename__ = "generated_reports"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ReportStatus.PENDING,
        server_default=ReportStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pdf_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_schedules.id", ondelete="SET NULL"), nullable=True
    )
