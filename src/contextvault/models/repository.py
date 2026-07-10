"""Repository (curated corpus) model.

Alongside identity fields, a repository carries its own LLM configuration —
``llm_provider`` / ``llm_model`` / the encrypted ``api_key`` (design spec §3).
There is no system default: a repository must be fully configured before it can
answer (enforced at query time), and the API key is stored only as ciphertext
(card #23) and never returned in full.
"""

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import LLMProviderName
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A curated knowledge corpus that users query one at a time."""

    __tablename__ = "repositories"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-repository LLM configuration (design spec §3). All nullable: a
    # repository starts unconfigured and cannot answer until an admin sets a
    # provider, model, and API key. The key is Fernet ciphertext at rest
    # (core/crypto.py), decrypted only in memory at call time.
    llm_provider: Mapped[LLMProviderName | None] = mapped_column(
        Enum(LLMProviderName, name="llm_provider", values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def llm_configured(self) -> bool:
        """True once provider, model, and key are all set — the predicate the
        query endpoint gates on so an unconfigured repo never reaches generation."""
        return bool(self.llm_provider and self.llm_model and self.api_key_encrypted)
