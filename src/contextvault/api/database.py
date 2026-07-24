"""Admin database-connection API — connect, introspect, allow-list, delete.

An admin points a repository at a read-only external SQL database (DB-reports
spec). One connection per repository: ``PUT`` live-tests the connection before
storing it (card #9 goal: never store a connection that doesn't work), the
password is Fernet ciphertext (``core/crypto``) and is **never** returned by any
route, and the exposed-schema allow-list — also what the report LLM is shown —
can be edited afterward (``PATCH .../schema``) without re-testing the
connection. ``POST .../introspect`` reads live tables/columns from the stored
connection so the admin has something to annotate into that allow-list.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import require_admin
from contextvault.core.crypto import decrypt, encrypt
from contextvault.db.session import get_session
from contextvault.models import DatabaseConnection, DatabaseType, Repository, User
from contextvault.services.report_db import DBConnectionError, introspect_schema, test_connection

router = APIRouter(tags=["database"])


class DatabaseConnectionRequest(BaseModel):
    """Admin-supplied connection details.

    ``password`` is optional on update — omitted or empty keeps the currently
    stored (encrypted) password rather than wiping it, so an admin can fix a
    typo'd host without re-entering the secret. It is required the first time a
    repository's connection is created (there is nothing stored yet to keep).
    """

    db_type: DatabaseType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = ""
    exposed_schema: list[dict[str, Any]] = Field(default_factory=list)


class SchemaUpdateRequest(BaseModel):
    """The admin's edited allow-list — also what the report LLM is shown."""

    exposed_schema: list[dict[str, Any]]


class DatabaseConnectionResponse(BaseModel):
    """A stored connection as the admin manages it. The password is never
    included — only ``GET`` needs to prove that, but every route returns this
    shape."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    db_type: DatabaseType
    host: str
    port: int
    database: str
    username: str
    exposed_schema: list[dict[str, Any]]


class IntrospectResponse(BaseModel):
    """Live tables/columns read from the stored connection (descriptions empty,
    for the admin to fill in and save via ``PATCH .../schema``)."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: list[dict[str, Any]] = Field(alias="schema")


async def _get_repo(session: AsyncSession, repository_id: uuid.UUID) -> Repository:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo


async def _get_connection(
    session: AsyncSession, repository_id: uuid.UUID
) -> DatabaseConnection | None:
    result = await session.execute(
        select(DatabaseConnection).where(DatabaseConnection.repository_id == repository_id)
    )
    return result.scalar_one_or_none()


async def _get_connection_or_404(
    session: AsyncSession, repository_id: uuid.UUID
) -> DatabaseConnection:
    conn = await _get_connection(session, repository_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Database connection not found"
        )
    return conn


@router.put("/repositories/{repository_id}/database")
async def set_database_connection(
    repository_id: uuid.UUID,
    payload: DatabaseConnectionRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DatabaseConnectionResponse:
    """Live-test then store a repository's reporting-database connection
    (upsert — one row per repository, card #9). A connection that can't be
    reached is never stored: ``test_connection`` runs first and a
    :class:`DBConnectionError` becomes a 400 with its detail before anything is
    written. An omitted/empty password keeps the connection's current one."""
    await _get_repo(session, repository_id)
    existing = await _get_connection(session, repository_id)

    password = payload.password
    if not password:
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="password is required to create a new connection",
            )
        password = decrypt(existing.password_encrypted)

    try:
        await test_connection(
            db_type=payload.db_type,
            host=payload.host,
            port=payload.port,
            database=payload.database,
            username=payload.username,
            password=password,
        )
    except DBConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if existing is None:
        existing = DatabaseConnection(repository_id=repository_id, created_by=admin.id)
        session.add(existing)

    existing.db_type = payload.db_type
    existing.host = payload.host
    existing.port = payload.port
    existing.database = payload.database
    existing.username = payload.username
    existing.password_encrypted = encrypt(password)
    existing.exposed_schema = payload.exposed_schema

    await session.flush()  # populate existing.id (UUID default) on create, before use below
    await session.commit()
    await session.refresh(existing)
    return DatabaseConnectionResponse.model_validate(existing)


@router.get("/repositories/{repository_id}/database")
async def get_database_connection(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DatabaseConnectionResponse:
    """Read a repository's stored connection — never the password. 404 when
    either the repository or its connection doesn't exist."""
    await _get_repo(session, repository_id)
    conn = await _get_connection_or_404(session, repository_id)
    return DatabaseConnectionResponse.model_validate(conn)


@router.patch("/repositories/{repository_id}/database/schema")
async def update_exposed_schema(
    repository_id: uuid.UUID,
    payload: SchemaUpdateRequest,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DatabaseConnectionResponse:
    """Save the admin's edited exposed-schema allow-list without re-testing the
    connection (only ``PUT`` re-verifies reachability)."""
    await _get_repo(session, repository_id)
    conn = await _get_connection_or_404(session, repository_id)
    conn.exposed_schema = payload.exposed_schema
    await session.commit()
    await session.refresh(conn)
    return DatabaseConnectionResponse.model_validate(conn)


@router.delete("/repositories/{repository_id}/database", status_code=status.HTTP_204_NO_CONTENT)
async def delete_database_connection(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a repository's connection; its generated reports and schedules
    cascade away with it (FK ``ON DELETE CASCADE``)."""
    await _get_repo(session, repository_id)
    conn = await _get_connection_or_404(session, repository_id)
    await session.delete(conn)
    await session.commit()


@router.post("/repositories/{repository_id}/database/introspect")
async def introspect_database(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IntrospectResponse:
    """Live-read tables/columns from the stored connection, for the admin to
    annotate and save as the exposed-schema allow-list (``PATCH .../schema``).
    400 — not 404 — when no connection is stored yet, matching the "nothing to
    introspect" nature of the failure; also 400 when the stored connection is
    unreachable."""
    await _get_repo(session, repository_id)
    conn = await _get_connection(session, repository_id)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No database connection is configured for this repository",
        )
    try:
        tables = await introspect_schema(
            db_type=conn.db_type,
            host=conn.host,
            port=conn.port,
            database=conn.database,
            username=conn.username,
            password=decrypt(conn.password_encrypted),
        )
    except DBConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return IntrospectResponse(schema=tables)
