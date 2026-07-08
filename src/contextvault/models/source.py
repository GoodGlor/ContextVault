"""Source model — an uploaded document or an admin-authored note."""

import uuid

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import SourceKind, SourceStatus
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A unit of curated content belonging to a repository."""

    __tablename__ = "sources"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SourceKind] = mapped_column(
        Enum(SourceKind, name="source_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Set for uploaded documents; null for admin notes.
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Extracted document text or the admin note body.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ingestion pipeline state (parse→chunk→embed→store); PENDING until it runs.
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus, name="source_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SourceStatus.PENDING,
        server_default=SourceStatus.PENDING.value,
    )
    # Captured failure detail when status is FAILED; null otherwise.
    ingest_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Admin author; SET NULL on user deletion so the source survives ("by a
    # deleted user") rather than cascading away.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
