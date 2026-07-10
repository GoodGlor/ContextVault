"""User persistence: lookups and creation with password hashing."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.security import generate_temporary_password, hash_password
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


async def reset_password(session: AsyncSession, user: User) -> str:
    """Issue a random temporary password for a user (admin recovery, card #27).

    Sets a fresh temp password and forces a change on next login. Returns the
    plaintext once so the admin can hand it over — only the hash is stored; the
    admin never sees or handles the user's eventual real password (design spec §2).
    """
    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    await session.flush()
    return temporary


async def change_password(session: AsyncSession, user: User, *, new_password: str) -> None:
    """Set a user's password to one they chose and clear the forced-change flag.

    The escape hatch from the ``must_change_password`` bounce: once the user picks
    their own password, the flag clears and normal access resumes.
    """
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    await session.flush()


async def admin_exists(session: AsyncSession) -> bool:
    """Return True if at least one admin account exists."""
    result = await session.execute(
        sa.select(sa.func.count()).select_from(User).where(User.role == Role.ADMIN)
    )
    return (result.scalar_one() or 0) > 0
