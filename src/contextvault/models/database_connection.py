"""An admin-connected external SQL database a repository reports from."""

import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import DatabaseType
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DatabaseConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Read-only connection details plus the admin's exposed-schema allow-list.

    One connection per repository (slice 1). The password is Fernet ciphertext
    (``core/crypto``) and is never returned by the API. ``exposed_schema`` is the
    guardrail allow-list AND the schema the LLM is shown:
    ``[{"table": str, "description": str, "columns": [{"name": str, "description": str}]}]``.
    """

    __tablename__ = "database_connections"
    __table_args__ = (
        UniqueConstraint("repository_id", name="uq_database_connections_repository_id"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    db_type: Mapped[DatabaseType] = mapped_column(
        Enum(DatabaseType, name="database_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    exposed_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
