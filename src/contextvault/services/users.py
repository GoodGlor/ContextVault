"""User persistence: lookups and creation with password hashing."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.security import hash_password
from contextvault.models import Role, User


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(sa.select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    role: Role,
    must_change_password: bool = False,
) -> User:
    """Create and flush a user, hashing the password before it is stored."""
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.flush()
    return user


async def admin_exists(session: AsyncSession) -> bool:
    """Return True if at least one admin account exists."""
    result = await session.execute(
        sa.select(sa.func.count()).select_from(User).where(User.role == Role.ADMIN)
    )
    return (result.scalar_one() or 0) > 0
