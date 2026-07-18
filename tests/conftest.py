"""Shared pytest fixtures.

The ``db_session`` fixture yields an ``AsyncSession`` bound to a connection whose
outer transaction is rolled back after each test, so integration tests never
leave data behind. It skips (rather than fails) when no migrated database is
reachable, keeping the pure-unit suite green in environments without Postgres.
"""

import os

from cryptography.fernet import Fernet

# Ignore the developer's local .env during tests: settings come only from real
# environment variables + code defaults, so a local override (e.g. OPENROUTER_MODEL)
# can never bleed into the suite. Must be set before contextvault.core.config is
# imported (it resolves the env-file choice at import time). See card #76.
os.environ["CONTEXTVAULT_ENV_FILE"] = ""

# A >=32-byte secret keeps PyJWT from emitting InsecureKeyLength warnings during
# tests. Set before any settings are read. Production overrides via the env/.env.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes")

# A valid Fernet master key so the crypto module can encrypt/decrypt in tests.
# Production supplies its own via ENCRYPTION_KEY; this per-run key never persists.
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.exc import DBAPIError, OperationalError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

import contextvault.models  # noqa: E402, F401  (registers tables on Base.metadata)
from contextvault.core.config import get_settings  # noqa: E402
from contextvault.db.base import Base  # noqa: E402

_ALL_TABLES = ", ".join(t.name for t in Base.metadata.sorted_tables)


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
    # Start each test from a clean slate regardless of any committed rows; the
    # outer rollback below restores them, so the truncate is test-local only.
    await conn.execute(sa.text(f"TRUNCATE {_ALL_TABLES} RESTART IDENTITY CASCADE"))
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()
