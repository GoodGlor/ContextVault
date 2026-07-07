"""Repository (curated corpus) model.

Per-repository LLM configuration (provider / model / encrypted key) is added in
the multi-provider phase; the foundation schema carries only identity fields.
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A curated knowledge corpus that users query one at a time."""

    __tablename__ = "repositories"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
