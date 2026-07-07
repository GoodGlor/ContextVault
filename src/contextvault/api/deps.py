"""Shared FastAPI dependencies for authentication and authorization."""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.tokens import InvalidToken, decode_access_token
from contextvault.db.session import get_session
from contextvault.models import Role, User
from contextvault.services import users as user_service

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
