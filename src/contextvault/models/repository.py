"""Repository (curated corpus) model.

Alongside identity fields, a repository picks the LLM it answers with — a
``llm_provider`` + ``llm_model`` (design spec §3). The API *key* is no longer stored
per-repository: keys live once per provider in ``ProviderSetting`` and are shared by
every repository that selects that provider. A repository can answer only once it has
picked a provider/model *and* that provider has a verified key — the second half of
that predicate lives in the service layer (it spans two tables), not here.
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

    # The LLM this repository answers with (design spec §3). Both nullable: a
    # repository starts with no model picked. The API key is not here — it lives
    # per-provider in ``ProviderSetting`` and is shared across repositories.
    llm_provider: Mapped[LLMProviderName | None] = mapped_column(
        Enum(LLMProviderName, name="llm_provider", values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @property
    def llm_selected(self) -> bool:
        """True once a provider and model are picked. Not sufficient to answer on its
        own — the chosen provider must also have a verified key (checked in the
        service layer, which can see ``ProviderSetting``)."""
        return bool(self.llm_provider and self.llm_model)
