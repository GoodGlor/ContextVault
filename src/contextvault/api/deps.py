"""Shared FastAPI dependencies for authentication and authorization."""

import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.tokens import InvalidToken, decode_access_token
from contextvault.db.session import SessionLocal, get_session
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.embeddings.local import LocalEmbeddingProvider
from contextvault.models import Role, User
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
