"""Shared FastAPI dependencies for authentication and authorization."""

import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.crypto import decrypt
from contextvault.core.tokens import InvalidToken, decode_access_token
from contextvault.db.session import SessionLocal, get_session
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.embeddings.local import LocalEmbeddingProvider
from contextvault.llm import LLMProvider, get_llm_provider
from contextvault.models import Repository, Role, User
from contextvault.services import users as user_service
from contextvault.services.ingestion import SessionFactory

_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the bearer token to the current user, or raise 401."""
    if credentials is None:
        raise _UNAUTHENTICATED
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(claims.subject)
    except (InvalidToken, ValueError) as exc:
        raise _UNAUTHENTICATED from exc

    user = await user_service.get_user_by_id(session, user_id)
    if user is None:
        raise _UNAUTHENTICATED
    return user


def require_role(*allowed: Role) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that passes only users whose role is in ``allowed``."""

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges",
            )
        return user

    return dependency


require_admin = require_role(Role.ADMIN)


@lru_cache
def _default_embedder() -> LocalEmbeddingProvider:
    """The process-wide local embedder; the heavy model loads lazily on first use."""
    settings = get_settings()
    return LocalEmbeddingProvider(
        model_name=settings.embedding_model, dimension=settings.embedding_dim
    )


def get_embedder() -> EmbeddingProvider:
    """Dependency yielding the active ``EmbeddingProvider`` (overridable in tests)."""
    return _default_embedder()


def get_ingestion_session_factory() -> SessionFactory:
    """Dependency yielding the session factory background ingestion opens.

    Defaults to ``SessionLocal`` so the background task gets its own session; tests
    override it to run ingestion inside their transaction.
    """
    return SessionLocal


RepoLLMBuilder = Callable[[Repository], LLMProvider]


def build_repo_llm(repo: Repository) -> LLMProvider:
    """Build the LLM provider a repository is configured to generate with (card #25).

    Each repository carries its own provider, model, and encrypted key (card #24);
    routing decrypts that key in memory and constructs the matching provider so the
    query loop generates through the repo's *own* LLM, never a process-wide default
    (design spec §3/§4). The query endpoint refuses unconfigured repositories (409)
    before reaching here, so all three fields are present.
    """
    assert repo.llm_provider is not None and repo.api_key_encrypted is not None
    return get_llm_provider(
        repo.llm_provider.value,
        api_key=decrypt(repo.api_key_encrypted),
        model=repo.llm_model,
    )


def get_llm_builder() -> RepoLLMBuilder:
    """Dependency yielding the per-repo provider builder (overridable in tests).

    The query endpoint (card #19/#25) resolves each request's provider from the
    target repository's stored configuration through this seam; tests override it
    to route to a recording fake instead of constructing a real vendor client.
    """
    return build_repo_llm
