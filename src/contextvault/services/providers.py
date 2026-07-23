"""Global provider-key settings — store, verify, and hand out one key per vendor.

The single place that touches ``ProviderSetting``. Keys are entered once per provider
(design: global provider settings), verified against the live provider before being
stored, kept as Fernet ciphertext, and decrypted only here at call time. The query
loop and image OCR both resolve a repository's key through :func:`get_provider_key`,
so neither depends on where the key is stored.

Saving a key is verify-then-store: :func:`set_provider_key` asks the provider for its
model list with the entered key (reusing :func:`contextvault.llm.models.list_models`)
and refuses to store a key that does not work, raising :class:`ProviderKeyInvalid`.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.crypto import decrypt, encrypt
from contextvault.llm.models import ModelListError, list_models
from contextvault.models import LLMProviderName, ProviderSetting, Repository


class ProviderKeyInvalid(Exception):
    """The supplied API key failed its live check against the provider."""


def _base_url_for(provider: LLMProviderName) -> str | None:
    """The OpenAI-compatible base URL a provider needs (only OpenRouter)."""
    return get_settings().openrouter_base_url if provider == LLMProviderName.OPENROUTER else None


async def get_setting(session: AsyncSession, provider: LLMProviderName) -> ProviderSetting | None:
    """The stored setting row for ``provider``, or ``None`` if no key is set."""
    result = await session.execute(
        sa.select(ProviderSetting).where(ProviderSetting.provider == provider)
    )
    return result.scalar_one_or_none()


async def list_settings(session: AsyncSession) -> list[ProviderSetting]:
    """Every stored provider setting, ordered by provider name."""
    result = await session.execute(sa.select(ProviderSetting).order_by(ProviderSetting.provider))
    return list(result.scalars().all())


async def verified_provider_names(session: AsyncSession) -> set[LLMProviderName]:
    """The providers that currently have a stored, verified key.

    A repository may only pick a model from one of these — its key is what makes
    the repository answerable."""
    result = await session.execute(
        sa.select(ProviderSetting.provider).where(ProviderSetting.verified_at.is_not(None))
    )
    return set(result.scalars().all())


async def repo_is_answerable(session: AsyncSession, repo: Repository) -> bool:
    """True when ``repo`` can actually answer: a model is picked *and* its provider
    has a verified key. This is the predicate the query and image-upload paths gate
    on (the model choice alone is not enough — the shared key must exist)."""
    if repo.llm_provider is None or not repo.llm_selected:
        return False
    return repo.llm_provider in await verified_provider_names(session)


async def get_provider_key(session: AsyncSession, provider: LLMProviderName) -> str | None:
    """The decrypted key for ``provider``, or ``None`` when none is stored.

    Decryption happens only here; the plaintext key never leaves the caller's frame."""
    setting = await get_setting(session, provider)
    return decrypt(setting.api_key_encrypted) if setting else None


async def verify_key(provider: LLMProviderName, api_key: str) -> None:
    """Check ``api_key`` works for ``provider``, raising :class:`ProviderKeyInvalid` if not.

    A successful provider ``list_models`` call is the liveness check — it exercises
    both the key and network path without generating anything."""
    try:
        await list_models(provider.value, api_key, base_url=_base_url_for(provider))
    except ModelListError as exc:
        raise ProviderKeyInvalid(str(exc)) from exc


async def set_provider_key(
    session: AsyncSession,
    provider: LLMProviderName,
    api_key: str,
    *,
    now: datetime,
) -> ProviderSetting:
    """Verify ``api_key`` and store it for ``provider`` (upsert), stamping ``verified_at``.

    Raises :class:`ProviderKeyInvalid` — and stores nothing — when the key does not
    work, so a saved key is always a working one. ``now`` is injected so the stamp is
    testable."""
    await verify_key(provider, api_key)

    setting = await get_setting(session, provider)
    if setting is None:
        setting = ProviderSetting(provider=provider)
        session.add(setting)
    setting.api_key_encrypted = encrypt(api_key)
    setting.verified_at = now
    await session.commit()
    await session.refresh(setting)
    return setting


async def delete_provider_key(session: AsyncSession, provider: LLMProviderName) -> bool:
    """Remove ``provider``'s stored key. Returns ``False`` if there was none."""
    setting = await get_setting(session, provider)
    if setting is None:
        return False
    await session.delete(setting)
    await session.commit()
    return True
