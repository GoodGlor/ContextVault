"""Shared pytest fixtures.

The ``db_session`` fixture yields an ``AsyncSession`` bound to a connection whose
outer transaction is rolled back after each test, so integration tests never
leave data behind. It skips (rather than fails) when no migrated database is
reachable, keeping the pure-unit suite green in environments without Postgres.
"""

from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from contextvault.core.config import get_settings


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as check:
            await check.execute(sa.text("SELECT 1 FROM users LIMIT 0"))
    except (OperationalError, DBAPIError) as exc:  # unreachable or unmigrated
        await engine.dispose()
        pytest.skip(f"no migrated database available: {exc.__class__.__name__}")

    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()
