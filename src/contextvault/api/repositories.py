"""Admin per-repository LLM configuration (card #24, design spec §3).

Each repository chooses its own LLM: a provider, a model, and an API key. The key
is encrypted at rest (card #23, ``core/crypto.py``) and only ever returned masked
(``sk-…•••4f2a``) — never in full after entry. A repository has no system
default; until an admin configures it here, the query endpoint refuses to answer.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import require_admin
from contextvault.core.crypto import decrypt, encrypt, mask_key
from contextvault.db.session import get_session
from contextvault.models import LLMProviderName, Repository, User

router = APIRouter(tags=["repositories"])


class LLMConfigRequest(BaseModel):
    """Admin-supplied LLM configuration for one repository."""

    provider: LLMProviderName
    model: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class LLMConfigResponse(BaseModel):
    """A repository's LLM configuration, with the key masked (never in full)."""

    provider: LLMProviderName | None
    model: str | None
    api_key_masked: str | None
    configured: bool


def _config_response(repo: Repository) -> LLMConfigResponse:
    """Serialize a repo's config, masking the key by decrypting it in memory only
    long enough to keep its prefix/suffix — the full secret never leaves here."""
    masked = mask_key(decrypt(repo.api_key_encrypted)) if repo.api_key_encrypted else None
    return LLMConfigResponse(
        provider=repo.llm_provider,
        model=repo.llm_model,
        api_key_masked=masked,
        configured=repo.llm_configured,
    )


async def _get_repo(session: AsyncSession, repository_id: uuid.UUID) -> Repository:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


@router.put("/repositories/{repository_id}/llm-config")
async def set_llm_config(
    repository_id: uuid.UUID,
    payload: LLMConfigRequest,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LLMConfigResponse:
    """Set (or replace) a repository's LLM provider/model/key; key stored encrypted."""
    repo = await _get_repo(session, repository_id)
    repo.llm_provider = payload.provider
    repo.llm_model = payload.model
    repo.api_key_encrypted = encrypt(payload.api_key)
    await session.commit()
    await session.refresh(repo)
    return _config_response(repo)


@router.get("/repositories/{repository_id}/llm-config")
async def get_llm_config(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LLMConfigResponse:
    """Read a repository's LLM configuration (key masked; nulls if unconfigured)."""
    repo = await _get_repo(session, repository_id)
    return _config_response(repo)
