"""Tests for the provider-settings service: custom (OpenAI-compatible) support.

Covers the data-layer additions (a nullable base_url and a now-optional key) plus
the service seams that resolve a base URL and call credentials, including the
keyless-local-server path. DB-backed tests use the rolled-back ``db_session``.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import LLMProviderName, ProviderSetting


def test_custom_is_a_provider_value() -> None:
    assert LLMProviderName.CUSTOM.value == "custom"


async def test_custom_row_persists_with_base_url_and_no_key(db_session: AsyncSession) -> None:
    row = ProviderSetting(
        provider=LLMProviderName.CUSTOM,
        api_key_encrypted=None,
        base_url="http://localhost:11434/v1",
        verified_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.api_key_encrypted is None
    assert row.base_url == "http://localhost:11434/v1"
