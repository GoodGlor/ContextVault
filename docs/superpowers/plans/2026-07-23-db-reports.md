# Database-Backed Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect a Postgres/MySQL database to a repository; users request reports in natural language; the LLM generates guardrailed SQL; results render as a PDF (chart + stats) with per-user history and nightly schedules.

**Architecture:** New `database_connections` / `generated_reports` / `report_schedules` tables. Report generation mirrors source ingestion (background task, PENDING→PROCESSING→DONE|FAILED, frontend polls). LLM SQL generation goes through a new `llm/textgen.py` (mirrors `llm/ocr.py`); every generated query passes a sqlglot AST guardrail + allow-list + LIMIT/timeout before executing on a per-call async engine in a read-only transaction. Nightly schedules re-execute frozen SQL via an in-process asyncio loop in the FastAPI lifespan.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, sqlglot, aiomysql, matplotlib (Agg), fpdf2, React + react-i18next.

**Spec:** `docs/superpowers/specs/2026-07-23-db-reports-design.md` (approved).

## Global Constraints

- Every backend check must pass: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, `uv run pytest`. Frontend: `npm run lint`, `npm run format:check`, `npm run typecheck`, `npm test -- --run`, `npm run build` (run from `frontend/`).
- `ruff check` passing does NOT imply `ruff format --check` passes — run both.
- If mypy reports `attr-defined` errors on `contextvault.services` submodules that look wrong, run `rm -rf .mypy_cache` and retry before debugging.
- UUID PKs are populated **at flush**, not construction — `await session.flush()` before using `obj.id` as an FK value.
- Migrations live outside ruff/mypy scope (`src tests`); follow the existing migration file style. New migration's `down_revision = "f333a95e2154"`.
- New enum types in Postgres: `database_type` (`postgres|mysql`), `report_status` (`pending|processing|done|failed`). Model enums use the existing idiom: `Enum(PyEnum, name="...", values_callable=lambda e: [m.value for m in e])`.
- Guardrails are non-negotiable: exactly one SELECT, sqlglot-AST-validated, allow-list enforced, LIMIT ≤ 10 000 injected, statement timeout 15 s, READ ONLY transaction always rolled back. No regex-only validation.
- The public-host SSRF guard from `services/web_source.py` must NOT be applied to DB connections (internal hosts are the point). Only admins create connections.
- All new UI strings go into BOTH `frontend/src/i18n/locales/en.json` and `uk.json`.
- PDF/chart text must render Cyrillic — register matplotlib's bundled DejaVu Sans in fpdf2 (`font_manager.findfont("DejaVu Sans")`); never use fpdf2's core fonts for content.
- In tests, never run raw `db_session.execute` immediately after an API call that spawned background work — verify via API responses.
- Access guard for user-facing report routes mirrors `api/query.py:157-163`: repo `session.get` → 404; `grant_service.has_active_grant(session, user.id, repository_id)` → 403.

---

### Task 1: Enums, models, migration

**Files:**
- Modify: `src/contextvault/models/enums.py`
- Create: `src/contextvault/models/database_connection.py`
- Create: `src/contextvault/models/report.py`
- Modify: `src/contextvault/models/__init__.py`
- Create: `migrations/versions/b8c2d5e7f901_db_reports_tables.py`
- Test: `tests/test_report_models.py`

**Interfaces:**
- Produces: `DatabaseType` (`POSTGRES|MYSQL`), `ReportStatus` (`PENDING|PROCESSING|DONE|FAILED`), models `DatabaseConnection`, `GeneratedReport`, `ReportSchedule` — all importable from `contextvault.models`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_models.py
"""Persistence round-trips for the DB-reports models (design spec §4)."""

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    Repository,
    ReportSchedule,
    ReportStatus,
)


async def test_report_models_round_trip(db_session: AsyncSession) -> None:
    repo = Repository(name="Vault")
    db_session.add(repo)
    await db_session.flush()

    conn = DatabaseConnection(
        repository_id=repo.id,
        db_type=DatabaseType.POSTGRES,
        host="db.internal",
        port=5432,
        database="sales",
        username="reader",
        password_encrypted="gAAA-cipher",
        exposed_schema=[{"table": "orders", "description": "", "columns": [{"name": "city", "description": ""}]}],
    )
    db_session.add(conn)
    await db_session.flush()

    schedule = ReportSchedule(
        repository_id=repo.id,
        connection_id=conn.id,
        owner_id=None,
        prompt="nightly sales",
        frozen_sql="SELECT 1",
        frozen_chart_spec={"chart_type": "none", "x_column": None, "y_column": None, "title": "t"},
        run_at_time=datetime.time(1, 0),
    )
    db_session.add(schedule)
    await db_session.flush()
    assert schedule.enabled is True
    assert schedule.last_run_at is None

    report = GeneratedReport(
        repository_id=repo.id,
        connection_id=conn.id,
        requested_by=None,
        prompt="report for Kyiv",
        schedule_id=schedule.id,
    )
    db_session.add(report)
    await db_session.flush()
    assert report.status is ReportStatus.PENDING
    assert report.generated_sql is None
    assert report.pdf_data is None
```

Note: `owner_id`/`requested_by` are nullable FKs in the model (SET NULL / CASCADE respectively per spec §4 — `requested_by` uses CASCADE so per-user history dies with the user; make the column nullable anyway so the test can pass `None`. Actually per spec: `requested_by` FK CASCADE **non-nullable is wrong** here — keep it nullable with CASCADE so scheduled system rows and this test are valid).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_models.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'DatabaseConnection'`.

- [ ] **Step 3: Add enums**

Append to `src/contextvault/models/enums.py`:

```python
class DatabaseType(enum.StrEnum):
    """SQL engine of an admin-connected reporting database (DB-reports spec §2)."""

    POSTGRES = "postgres"
    MYSQL = "mysql"


class ReportStatus(enum.StrEnum):
    """Generation state of a report; mirrors SourceStatus's lifecycle."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
```

- [ ] **Step 4: Add the models**

```python
# src/contextvault/models/database_connection.py
"""An admin-connected external SQL database a repository reports from."""

import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import DatabaseType
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DatabaseConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Read-only connection details plus the admin's exposed-schema allow-list.

    One connection per repository (slice 1). The password is Fernet ciphertext
    (``core/crypto``) and is never returned by the API. ``exposed_schema`` is the
    guardrail allow-list AND the schema the LLM is shown:
    ``[{"table": str, "description": str, "columns": [{"name": str, "description": str}]}]``.
    """

    __tablename__ = "database_connections"
    __table_args__ = (UniqueConstraint("repository_id", name="uq_database_connections_repository_id"),)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    db_type: Mapped[DatabaseType] = mapped_column(
        Enum(DatabaseType, name="database_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    exposed_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
```

```python
# src/contextvault/models/report.py
"""Generated reports and their nightly schedules (DB-reports spec §4)."""

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, LargeBinary, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.enums import ReportStatus
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ReportSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A frozen, validated report query re-executed nightly for its owner.

    ``frozen_sql``/``frozen_chart_spec`` are the already-guardrailed artifacts of
    the report the schedule was created from — nightly runs re-execute them
    verbatim (no LLM call). Relative windows stay rolling because generation
    expresses them as SQL date arithmetic.
    """

    __tablename__ = "report_schedules"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_sql: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_chart_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_at_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class GeneratedReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One report request and its artifact; the per-user history row.

    ``generated_sql`` is the audit trail — the exact validated SQL that ran
    (admin-visible). ``pdf_data`` holds the artifact bytes (reports are small;
    no blob storage exists in this app by design).
    """

    __tablename__ = "generated_reports"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ReportStatus.PENDING,
        server_default=ReportStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pdf_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_schedules.id", ondelete="SET NULL"), nullable=True
    )
```

Register in `src/contextvault/models/__init__.py`: import `DatabaseConnection`, `DatabaseType`, `GeneratedReport`, `ReportSchedule`, `ReportStatus` and add them to `__all__` (keep both alphabetized).

- [ ] **Step 5: Write the migration**

`migrations/versions/b8c2d5e7f901_db_reports_tables.py`, matching the house style (see `f333a95e2154_gap_rejections.py`): `revision = "b8c2d5e7f901"`, `down_revision = "f333a95e2154"`. Create **`database_connections`** first, then **`report_schedules`**, then **`generated_reports`** (it FKs the other two). Columns exactly as the models above; enums as `sa.Enum("postgres", "mysql", name="database_type")` and `sa.Enum("pending", "processing", "done", "failed", name="report_status")`; JSONB via `postgresql.JSONB` import; `server_default=sa.text("true")` for `enabled`, `server_default="pending"` for status; the unique constraint and the three indexes (`repository_id` on schedules and reports, `requested_by` on reports). Downgrade drops the three tables in reverse order, then `op.execute("DROP TYPE report_status")` and `op.execute("DROP TYPE database_type")`.

- [ ] **Step 6: Apply migration and run test**

Run: `uv run alembic upgrade head && uv run pytest tests/test_report_models.py -x -q`
Expected: PASS.

- [ ] **Step 7: Full checks and commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -q`
Expected: all clean.

```bash
git add src/contextvault/models migrations/versions/b8c2d5e7f901_db_reports_tables.py tests/test_report_models.py
git commit -m "feat: models + migration for database connections, reports, schedules"
```

---

### Task 2: Dependencies + DB connector (test connection, introspection)

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/contextvault/services/report_db.py`
- Test: `tests/test_report_db.py`

**Interfaces:**
- Produces: `build_url(conn_params) -> str`; `async test_connection(...) -> None` (raises `DBConnectionError`); `async introspect_schema(...) -> list[dict]` in exposed_schema shape (empty descriptions); exception `DBConnectionError`.
- All functions take explicit params (`db_type: DatabaseType, host, port, database, username, password`) — NOT a model instance — so the API layer can test credentials before persisting.

- [ ] **Step 1: Add dependencies**

Run: `uv add aiomysql sqlglot matplotlib fpdf2`
Expected: resolves; `uv.lock` updated. (sqlglot/matplotlib/fpdf2 are used by later tasks; adding once here keeps one lock change.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_report_db.py
"""Connector round-trips against the test Postgres itself as the 'external' DB."""

import pytest
from sqlalchemy.engine.url import make_url

from contextvault.core.config import get_settings
from contextvault.models import DatabaseType
from contextvault.services.report_db import DBConnectionError, introspect_schema, test_connection


def _own_db_params() -> dict:
    url = make_url(get_settings().database_url)
    return {
        "db_type": DatabaseType.POSTGRES,
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "database": url.database or "",
        "username": url.username or "",
        "password": url.password or "",
    }


async def test_connection_succeeds_against_real_db() -> None:
    await test_connection(**_own_db_params())  # must not raise


async def test_connection_bad_password_raises() -> None:
    params = {**_own_db_params(), "password": "definitely-wrong", "username": "nobody"}
    with pytest.raises(DBConnectionError):
        await test_connection(**params)


async def test_introspect_lists_own_tables() -> None:
    schema = await introspect_schema(**_own_db_params())
    tables = {t["table"] for t in schema}
    assert "repositories" in tables  # our own schema is visible
    repo_table = next(t for t in schema if t["table"] == "repositories")
    assert {"name": "name", "description": ""} in repo_table["columns"]
    assert all(t["description"] == "" for t in schema)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_report_db.py -x -q`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement**

```python
# src/contextvault/services/report_db.py
"""Connect/introspect admin-supplied reporting databases (DB-reports spec §3/§8).

Engines are created per call and disposed — connections are rare, admin-driven
events, not hot-path traffic. NOTE: unlike web sources there is deliberately no
public-host (SSRF) guard here — internal DB hosts are the feature; the boundary
is that only admins may create connections (spec §11).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine.url import URL

from contextvault.models import DatabaseType

_DRIVERS = {DatabaseType.POSTGRES: "postgresql+asyncpg", DatabaseType.MYSQL: "mysql+aiomysql"}

# information_schema is shared SQL vocabulary: this one query introspects both engines.
_INTROSPECT_SQL = text(
    """
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_schema = :schema
    ORDER BY table_name, ordinal_position
    """
)


class DBConnectionError(Exception):
    """The reporting database could not be reached/queried with these details."""


def build_url(
    *, db_type: DatabaseType, host: str, port: int, database: str, username: str, password: str
) -> URL:
    """Assemble the SQLAlchemy URL for a reporting connection (never log it)."""
    return URL.create(
        _DRIVERS[db_type], username=username, password=password, host=host, port=port, database=database
    )


def _default_schema(db_type: DatabaseType, database: str) -> str:
    # Postgres user tables live in ``public``; MySQL's "schema" IS the database.
    return "public" if db_type is DatabaseType.POSTGRES else database


async def test_connection(
    *, db_type: DatabaseType, host: str, port: int, database: str, username: str, password: str
) -> None:
    """SELECT 1 against the target; raise :class:`DBConnectionError` on any failure."""
    engine = create_async_engine(
        build_url(db_type=db_type, host=host, port=port, database=database, username=username, password=password)
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — driver errors vary; one clean failure type
        raise DBConnectionError(f"Could not connect: {exc}") from exc
    finally:
        await engine.dispose()


async def introspect_schema(
    *, db_type: DatabaseType, host: str, port: int, database: str, username: str, password: str
) -> list[dict[str, Any]]:
    """Live tables/columns in exposed-schema shape (descriptions empty, for the admin to fill)."""
    engine = create_async_engine(
        build_url(db_type=db_type, host=host, port=port, database=database, username=username, password=password)
    )
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(_INTROSPECT_SQL, {"schema": _default_schema(db_type, database)})
            ).all()
    except Exception as exc:  # noqa: BLE001
        raise DBConnectionError(f"Could not introspect: {exc}") from exc
    finally:
        await engine.dispose()
    tables: dict[str, list[dict[str, str]]] = {}
    for table_name, column_name in rows:
        tables.setdefault(table_name, []).append({"name": column_name, "description": ""})
    return [{"table": name, "description": "", "columns": cols} for name, cols in tables.items()]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_report_db.py -x -q`
Expected: PASS.

- [ ] **Step 6: Full checks and commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -q`

```bash
git add pyproject.toml uv.lock src/contextvault/services/report_db.py tests/test_report_db.py
git commit -m "feat: reporting-DB connector — deps, connection test, schema introspection"
```

---

### Task 3: SQL guardrails

**Files:**
- Create: `src/contextvault/services/sql_guardrails.py`
- Test: `tests/test_sql_guardrails.py`

**Interfaces:**
- Produces: `validate_sql(sql: str, *, db_type: DatabaseType, exposed_schema: list[dict], max_rows: int = 10_000) -> str` — returns the normalized SQL with LIMIT enforced, or raises `SQLValidationError(reason)`. Pure function, no I/O.

- [ ] **Step 1: Write the failing test (accept/reject matrix)**

```python
# tests/test_sql_guardrails.py
"""The guardrail accept/reject matrix (DB-reports spec §5, layers 2–4)."""

import pytest

from contextvault.models import DatabaseType
from contextvault.services.sql_guardrails import SQLValidationError, validate_sql

SCHEMA = [
    {
        "table": "orders",
        "description": "sales orders",
        "columns": [
            {"name": "id", "description": ""},
            {"name": "city", "description": ""},
            {"name": "total", "description": ""},
            {"name": "created_at", "description": ""},
        ],
    }
]


def _ok(sql: str) -> str:
    return validate_sql(sql, db_type=DatabaseType.POSTGRES, exposed_schema=SCHEMA)


def _bad(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        _ok(sql)


def test_plain_select_passes_and_gets_a_limit() -> None:
    out = _ok("SELECT city, SUM(total) AS revenue FROM orders GROUP BY city")
    assert "LIMIT 10000" in out


def test_existing_small_limit_is_kept() -> None:
    assert "LIMIT 50" in _ok("SELECT city FROM orders LIMIT 50")


def test_oversized_limit_is_clamped() -> None:
    assert "LIMIT 10000" in _ok("SELECT city FROM orders LIMIT 999999")


def test_order_by_select_alias_is_allowed() -> None:
    _ok("SELECT city, COUNT(*) AS cnt FROM orders GROUP BY city ORDER BY cnt DESC")


def test_relative_date_expressions_pass() -> None:
    _ok("SELECT city FROM orders WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'")


def test_rejects_ddl_dml_and_multi_statement() -> None:
    _bad("DELETE FROM orders")
    _bad("UPDATE orders SET total = 0")
    _bad("DROP TABLE orders")
    _bad("INSERT INTO orders (id) VALUES (1)")
    _bad("SELECT 1; SELECT 2")
    _bad("SELECT city FROM orders; DROP TABLE orders")


def test_rejects_unlisted_table_and_column() -> None:
    _bad("SELECT * FROM users")
    _bad("SELECT secret FROM orders")


def test_rejects_dangerous_functions() -> None:
    _bad("SELECT pg_sleep(10)")
    _bad("SELECT pg_read_file('/etc/passwd')")


def test_rejects_unparseable_and_empty() -> None:
    _bad("SELEKT wat")
    _bad("")


def test_mysql_dialect_parses() -> None:
    out = validate_sql(
        "SELECT city FROM orders WHERE created_at >= CURDATE() - INTERVAL 30 DAY",
        db_type=DatabaseType.MYSQL,
        exposed_schema=SCHEMA,
    )
    assert "LIMIT 10000" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sql_guardrails.py -x -q` — FAIL, module not found.

- [ ] **Step 3: Implement**

```python
# src/contextvault/services/sql_guardrails.py
"""AST-level validation of LLM-generated report SQL (DB-reports spec §5).

Layer 2–4 of the guardrail stack: parse (never regex), require exactly one
SELECT, forbid write/DDL nodes and dangerous functions, enforce the admin's
table/column allow-list, and clamp/inject a row LIMIT. DB-level read-only
roles (layer 1) and the audit trail (layer 5) live elsewhere.
"""

from typing import Any

import sqlglot
from sqlglot import exp

from contextvault.models import DatabaseType

_DIALECTS = {DatabaseType.POSTGRES: "postgres", DatabaseType.MYSQL: "mysql"}

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.TruncateTable,
    exp.Command,
    exp.Grant,
)

_FORBIDDEN_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_ls_dir",
    "pg_terminate_backend",
    "dblink",
    "copy",
    "lo_import",
    "lo_export",
    "sleep",
    "benchmark",
    "load_file",
}


class SQLValidationError(Exception):
    """The generated SQL violated the guardrails; the message is LLM-repair feedback."""


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate_sql(
    sql: str,
    *,
    db_type: DatabaseType,
    exposed_schema: list[dict[str, Any]],
    max_rows: int = 10_000,
) -> str:
    """Validate ``sql`` against the allow-list; return it normalized with a LIMIT.

    Raises :class:`SQLValidationError` with a reason usable as self-repair
    feedback for the LLM. CTE names count as allowed tables (their inner selects
    are themselves validated); SELECT-list aliases count as allowed columns so
    ``ORDER BY alias`` passes.
    """
    dialect = _DIALECTS[db_type]
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"SQL does not parse: {exc}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is required.")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SQLValidationError("Only a single SELECT statement is allowed.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden operation: {node.key.upper()}.")
        if isinstance(node, exp.Func) and _function_name(node) in _FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(f"Forbidden function: {_function_name(node)}.")

    allowed_tables = {t["table"].lower() for t in exposed_schema}
    allowed_columns = {
        c["name"].lower() for t in exposed_schema for c in t["columns"]
    }
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}

    for table in tree.find_all(exp.Table):
        if table.name.lower() not in allowed_tables | cte_names:
            raise SQLValidationError(f"Table not allowed: {table.name}.")
    for column in tree.find_all(exp.Column):
        if column.name.lower() not in allowed_columns | aliases:
            raise SQLValidationError(f"Column not allowed: {column.name}.")

    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal):
        try:
            current = int(limit.expression.this)
        except ValueError:
            current = None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows)
    return tree.sql(dialect=dialect)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sql_guardrails.py -x -q` — PASS. If a matrix case fails on a sqlglot version quirk (e.g. node class name), fix the implementation, not the test's intent.

- [ ] **Step 5: Full checks and commit**

```bash
git add src/contextvault/services/sql_guardrails.py tests/test_sql_guardrails.py
git commit -m "feat: sqlglot guardrails — single-SELECT AST validation, allow-list, LIMIT clamp"
```

---

### Task 4: LLM plain-text generation helper

**Files:**
- Create: `src/contextvault/llm/textgen.py`
- Test: `tests/test_llm_textgen.py`

**Interfaces:**
- Produces: `async generate_text(provider: str, api_key: str, model: str, *, prompt: str, base_url: str | None = None) -> str`; exception `TextGenError`. Mirrors `llm/ocr.py` exactly: standalone per-provider async functions, name dispatch, all failures wrapped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_textgen.py
"""Dispatch + error-wrapping contract of the plain-text generation helper."""

import pytest

import contextvault.llm.textgen as textgen
from contextvault.llm.textgen import TextGenError, generate_text


async def test_unknown_provider_raises() -> None:
    with pytest.raises(TextGenError, match="Unsupported provider"):
        await generate_text("mystery", "k", "m", prompt="hi")


async def test_dispatches_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(api_key: str, model: str, prompt: str) -> str:
        assert (api_key, model, prompt) == ("k", "m", "hi")
        return "generated"

    monkeypatch.setattr(textgen, "_generate_gemini", fake)
    assert await generate_text("gemini", "k", "m", prompt="hi") == "generated"


async def test_provider_failure_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(api_key: str, model: str, prompt: str) -> str:
        raise RuntimeError("quota")

    monkeypatch.setattr(textgen, "_generate_gemini", boom)
    with pytest.raises(TextGenError, match="quota"):
        await generate_text("gemini", "k", "m", prompt="hi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_textgen.py -x -q` — FAIL, module not found.

- [ ] **Step 3: Implement**

Copy `llm/ocr.py`'s structure minus imaging. Providers and SDK calls are identical to ocr.py's, but the user message is just `prompt` text:

```python
# src/contextvault/llm/textgen.py
"""Plain-text generation via a provider's global key — non-RAG LLM calls.

The RAG loop speaks through ``LLMProvider.answer`` (grounded, cited); some
features — report SQL generation — need a raw completion instead. This module
mirrors :mod:`contextvault.llm.ocr`: standalone per-provider async functions
dispatched by name, every failure wrapped in :class:`TextGenError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock
from openai import AsyncOpenAI

from contextvault.core.config import get_settings

if TYPE_CHECKING:
    from google import genai

__all__ = ["TextGenError", "generate_text"]

_MAX_TOKENS = 2048


class TextGenError(Exception):
    """The provider could not generate text (bad key, network, quota, …)."""


def _genai_client(api_key: str) -> genai.Client:
    from google import genai

    return genai.Client(api_key=api_key)


async def _generate_gemini(api_key: str, model: str, prompt: str) -> str:
    from google.genai import types

    client = _genai_client(api_key)
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=_MAX_TOKENS),
    )
    return (response.text or "").strip()


async def _generate_openai_compatible(
    api_key: str, model: str, prompt: str, base_url: str | None
) -> str:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    completion = await client.chat.completions.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return (completion.choices[0].message.content or "").strip()


async def _generate_anthropic(api_key: str, model: str, prompt: str) -> str:
    client = AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in message.content if isinstance(b, TextBlock)).strip()


async def generate_text(
    provider: str, api_key: str, model: str, *, prompt: str, base_url: str | None = None
) -> str:
    """Generate a completion for ``prompt`` with ``provider``'s model ``model``."""
    name = provider.lower()
    try:
        if name == "gemini":
            return await _generate_gemini(api_key, model, prompt)
        if name == "openai":
            return await _generate_openai_compatible(api_key, model, prompt, None)
        if name == "openrouter":
            base = base_url or get_settings().openrouter_base_url
            return await _generate_openai_compatible(api_key, model, prompt, base)
        if name == "anthropic":
            return await _generate_anthropic(api_key, model, prompt)
    except TextGenError:
        raise
    except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes a clean error
        raise TextGenError(f"Could not generate text: {exc}") from exc
    raise TextGenError(f"Unsupported provider: {provider!r}")
```

- [ ] **Step 4: Run test, full checks, commit**

Run: `uv run pytest tests/test_llm_textgen.py -x -q`, then the full check set.

```bash
git add src/contextvault/llm/textgen.py tests/test_llm_textgen.py
git commit -m "feat: llm/textgen — provider-dispatched plain-text generation"
```

---

### Task 5: Report query generation (prompt + JSON parsing)

**Files:**
- Create: `src/contextvault/services/report_llm.py`
- Test: `tests/test_report_llm.py`

**Interfaces:**
- Produces: pydantic models `ChartSpec` (`chart_type: Literal["bar","line","pie","none"]`, `x_column: str | None`, `y_column: str | None`, `title: str`) and `ReportQuery` (`sql: str`, `chart: ChartSpec`); `build_prompt(user_prompt, *, exposed_schema, db_type, today: datetime.date, max_rows: int, feedback: list[str]) -> str`; `parse_report_query(text: str) -> ReportQuery` (raises `ReportQueryParseError`); `async generate_report_query(provider, api_key, model, *, base_url, user_prompt, exposed_schema, db_type, feedback) -> ReportQuery`.
- Consumes: `generate_text` from Task 4.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_llm.py
"""Prompt construction and strict JSON parsing for report-query generation."""

import datetime
import json

import pytest

import contextvault.services.report_llm as report_llm
from contextvault.models import DatabaseType
from contextvault.services.report_llm import (
    ReportQueryParseError,
    build_prompt,
    generate_report_query,
    parse_report_query,
)

SCHEMA = [
    {
        "table": "orders",
        "description": "sales orders",
        "columns": [{"name": "city", "description": "customer city"}],
    }
]


def test_prompt_contains_schema_descriptions_dialect_and_feedback() -> None:
    prompt = build_prompt(
        "report for Kyiv",
        exposed_schema=SCHEMA,
        db_type=DatabaseType.MYSQL,
        today=datetime.date(2026, 7, 23),
        max_rows=10_000,
        feedback=["Column not allowed: secret."],
    )
    assert "orders" in prompt and "customer city" in prompt
    assert "mysql" in prompt.lower()
    assert "2026-07-23" in prompt
    assert "Column not allowed: secret." in prompt
    assert "report for Kyiv" in prompt


def test_parse_accepts_plain_and_fenced_json() -> None:
    payload = {
        "sql": "SELECT city FROM orders",
        "chart": {"chart_type": "bar", "x_column": "city", "y_column": None, "title": "Кількість"},
    }
    for text in (json.dumps(payload), f"```json\n{json.dumps(payload)}\n```"):
        query = parse_report_query(text)
        assert query.sql == "SELECT city FROM orders"
        assert query.chart.chart_type == "bar"
        assert query.chart.title == "Кількість"


def test_parse_rejects_prose_bad_chart_type_and_missing_sql() -> None:
    for bad in ("no json here", '{"chart": {"chart_type": "bar", "title": "t"}}',
                '{"sql": "SELECT 1", "chart": {"chart_type": "hologram", "title": "t"}}'):
        with pytest.raises(ReportQueryParseError):
            parse_report_query(bad)


async def test_generate_calls_textgen_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate_text(provider, api_key, model, *, prompt, base_url=None):
        assert provider == "gemini" and "orders" in prompt
        return '{"sql": "SELECT city FROM orders", "chart": {"chart_type": "none", "x_column": null, "y_column": null, "title": "t"}}'

    monkeypatch.setattr(report_llm, "generate_text", fake_generate_text)
    query = await generate_report_query(
        "gemini", "k", "m",
        base_url=None, user_prompt="p", exposed_schema=SCHEMA,
        db_type=DatabaseType.POSTGRES, feedback=[],
    )
    assert query.sql == "SELECT city FROM orders"
```

- [ ] **Step 2: Run to verify FAIL, then implement**

```python
# src/contextvault/services/report_llm.py
"""Turn a natural-language report request into {sql, chart} via the repo's LLM.

The LLM's only contract is a strict JSON object; parsing failures raise
:class:`ReportQueryParseError` whose message doubles as self-repair feedback.
Validation of the SQL itself is the guardrails' job (``sql_guardrails``), not
this module's.
"""

import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from contextvault.llm.textgen import generate_text
from contextvault.models import DatabaseType


class ReportQueryParseError(Exception):
    """The LLM's output was not the required JSON contract."""


class ChartSpec(BaseModel):
    chart_type: Literal["bar", "line", "pie", "none"]
    x_column: str | None = None
    y_column: str | None = None
    title: str = ""


class ReportQuery(BaseModel):
    sql: str = Field(min_length=1)
    chart: ChartSpec


def _schema_block(exposed_schema: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for table in exposed_schema:
        desc = f" -- {table['description']}" if table.get("description") else ""
        lines.append(f"TABLE {table['table']}{desc}")
        for col in table["columns"]:
            col_desc = f" -- {col['description']}" if col.get("description") else ""
            lines.append(f"  {col['name']}{col_desc}")
    return "\n".join(lines)


def build_prompt(
    user_prompt: str,
    *,
    exposed_schema: list[dict[str, Any]],
    db_type: DatabaseType,
    today: datetime.date,
    max_rows: int,
    feedback: list[str],
) -> str:
    """The full instruction the LLM sees; only the exposed schema is revealed."""
    feedback_block = ""
    if feedback:
        joined = "\n".join(f"- {f}" for f in feedback)
        feedback_block = (
            f"\nYour previous attempt(s) were rejected. Fix these problems:\n{joined}\n"
        )
    return (
        f"You write one read-only SQL query for a {db_type.value} database, plus a chart "
        "specification, answering a user's report request.\n\n"
        "You may ONLY use these tables and columns (anything else is forbidden):\n"
        f"{_schema_block(exposed_schema)}\n\n"
        "Rules:\n"
        "- Output ONLY a JSON object, no prose, no code fences:\n"
        '  {"sql": "...", "chart": {"chart_type": "bar|line|pie|none", '
        '"x_column": "...", "y_column": "...", "title": "..."}}\n'
        f"- The SQL must be exactly one SELECT statement in {db_type.value} syntax. Never modify data.\n"
        "- For relative date ranges use SQL date arithmetic (e.g. CURRENT_DATE - INTERVAL '30 days'),\n"
        "  never a hard-coded date, so the query stays correct when re-run later.\n"
        f"- Aggregate/group so the result is a meaningful report of at most {max_rows} rows.\n"
        "- x_column and y_column must be output column names of the SQL; use chart_type \"none\"\n"
        "  when no chart makes sense.\n"
        "- Write the chart title in the language of the user's request.\n"
        f"{feedback_block}\n"
        f"Today's date: {today.isoformat()}\n"
        f"User request: {user_prompt}\n"
    )


def parse_report_query(text: str) -> ReportQuery:
    """Parse the LLM's reply into a :class:`ReportQuery`; tolerate code fences only."""
    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ReportQueryParseError("Reply contained no JSON object.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ReportQueryParseError(f"Reply was not valid JSON: {exc}") from exc
    try:
        return ReportQuery.model_validate(payload)
    except ValidationError as exc:
        raise ReportQueryParseError(f"JSON did not match the contract: {exc}") from exc


async def generate_report_query(
    provider: str,
    api_key: str,
    model: str,
    *,
    base_url: str | None,
    user_prompt: str,
    exposed_schema: list[dict[str, Any]],
    db_type: DatabaseType,
    feedback: list[str],
    max_rows: int = 10_000,
) -> ReportQuery:
    """One generation attempt: prompt → provider → parsed contract."""
    prompt = build_prompt(
        user_prompt,
        exposed_schema=exposed_schema,
        db_type=db_type,
        today=datetime.date.today(),
        max_rows=max_rows,
        feedback=feedback,
    )
    reply = await generate_text(provider, api_key, model, prompt=prompt, base_url=base_url)
    return parse_report_query(reply)
```

- [ ] **Step 3: Run test to verify PASS, full checks, commit**

```bash
git add src/contextvault/services/report_llm.py tests/test_report_llm.py
git commit -m "feat: report query generation — schema-scoped prompt + strict JSON contract"
```

---

### Task 6: Guardrailed query execution

**Files:**
- Create: `src/contextvault/services/report_execution.py`
- Test: `tests/test_report_execution.py`

**Interfaces:**
- Produces: `@dataclass QueryResult(columns: list[str], rows: list[tuple])`; `async run_validated_query(*, db_type, host, port, database, username, password, sql: str, timeout_ms: int = 15_000) -> QueryResult` raising `QueryExecutionError`.
- The caller passes only guardrail-validated SQL; this layer still runs read-only + timed out as defense in depth.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_execution.py
"""Execution layer: results come back; writes and slow queries die (spec §5 layer 4)."""

import pytest
from sqlalchemy.engine.url import make_url

from contextvault.core.config import get_settings
from contextvault.models import DatabaseType
from contextvault.services.report_execution import QueryExecutionError, run_validated_query


def _params() -> dict:
    url = make_url(get_settings().database_url)
    return {
        "db_type": DatabaseType.POSTGRES,
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "database": url.database or "",
        "username": url.username or "",
        "password": url.password or "",
    }


async def test_select_returns_columns_and_rows() -> None:
    result = await run_validated_query(**_params(), sql="SELECT 1 AS one, 'kyiv' AS city")
    assert result.columns == ["one", "city"]
    assert result.rows == [(1, "kyiv")]


async def test_write_is_refused_by_read_only_transaction() -> None:
    # Even if a write slipped every guardrail, the READ ONLY transaction kills it.
    with pytest.raises(QueryExecutionError):
        await run_validated_query(**_params(), sql="CREATE TABLE smuggled (id int)")


async def test_statement_timeout_kills_slow_queries() -> None:
    with pytest.raises(QueryExecutionError):
        await run_validated_query(**_params(), sql="SELECT pg_sleep(2)", timeout_ms=200)
```

- [ ] **Step 2: Run to verify FAIL, then implement**

```python
# src/contextvault/services/report_execution.py
"""Run validated report SQL with belt-and-braces safety (spec §5, layer 4).

The guardrails already vetted the SQL; this layer independently enforces a
READ ONLY transaction (always rolled back), a statement timeout, and per-call
engines so a reporting DB can never hold app resources.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from contextvault.models import DatabaseType
from contextvault.services.report_db import build_url


class QueryExecutionError(Exception):
    """The validated query failed at the database (timeout, refused, bad SQL for engine)."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]


async def run_validated_query(
    *,
    db_type: DatabaseType,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    sql: str,
    timeout_ms: int = 15_000,
) -> QueryResult:
    engine = create_async_engine(
        build_url(db_type=db_type, host=host, port=port, database=database, username=username, password=password)
    )
    try:
        async with engine.connect() as conn:
            if db_type is DatabaseType.POSTGRES:
                # First statement of the autobegun transaction — READ ONLY applies to it.
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            else:
                await conn.execute(text("SET SESSION transaction_read_only = 1"))
                await conn.execute(text(f"SET SESSION max_execution_time = {int(timeout_ms)}"))
            result = await conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(row) for row in result.all()]
            await conn.rollback()
            return QueryResult(columns=columns, rows=rows)
    except QueryExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001 — driver errors vary; one clean failure type
        raise QueryExecutionError(f"Query failed: {exc}") from exc
    finally:
        await engine.dispose()
```

- [ ] **Step 3: Run test to verify PASS, full checks, commit**

```bash
git add src/contextvault/services/report_execution.py tests/test_report_execution.py
git commit -m "feat: report execution — read-only rolled-back txn, statement timeout"
```

---

### Task 7: Chart + PDF rendering

**Files:**
- Create: `src/contextvault/services/report_render.py`
- Test: `tests/test_report_render.py`

**Interfaces:**
- Produces: `render_chart(result: QueryResult, chart: ChartSpec) -> bytes | None` (PNG, `None` for `chart_type="none"` or missing columns); `build_pdf(*, title: str, prompt: str, result: QueryResult, chart_png: bytes | None) -> bytes`.
- Consumes: `QueryResult` (Task 6), `ChartSpec` (Task 5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_render.py
"""Chart PNGs and PDF assembly — including the Cyrillic-font regression guard."""

from contextvault.services.report_execution import QueryResult
from contextvault.services.report_llm import ChartSpec
from contextvault.services.report_render import build_pdf, render_chart

RESULT = QueryResult(columns=["city", "revenue"], rows=[("Київ", 120), ("Львів", 80)])


def test_bar_chart_renders_png() -> None:
    png = render_chart(RESULT, ChartSpec(chart_type="bar", x_column="city", y_column="revenue", title="Дохід"))
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_type_none_and_unknown_column_yield_no_chart() -> None:
    assert render_chart(RESULT, ChartSpec(chart_type="none", title="t")) is None
    assert render_chart(RESULT, ChartSpec(chart_type="line", x_column="ghost", y_column="revenue", title="t")) is None


def test_pdf_builds_with_cyrillic_and_chart() -> None:
    png = render_chart(RESULT, ChartSpec(chart_type="bar", x_column="city", y_column="revenue", title="Дохід"))
    pdf = build_pdf(title="Звіт по містах", prompt="звіт по Києву", result=RESULT, chart_png=png)
    assert pdf[:5] == b"%PDF-"


def test_pdf_builds_without_chart_and_with_empty_result() -> None:
    empty = QueryResult(columns=["city"], rows=[])
    assert build_pdf(title="Report", prompt="p", result=empty, chart_png=None)[:5] == b"%PDF-"
```

- [ ] **Step 2: Run to verify FAIL, then implement**

```python
# src/contextvault/services/report_render.py
"""Render a report's chart (matplotlib/Agg) and assemble the PDF (fpdf2).

Cyrillic is a hard requirement: fpdf2's core fonts render it as garbage, so we
register matplotlib's bundled DejaVu Sans for the PDF too (no font file ships in
this repo). Charts render off-screen (Agg) to in-memory PNG.
"""

from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402 — backend must be set before pyplot
from fpdf import FPDF  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from contextvault.services.report_execution import QueryResult  # noqa: E402
from contextvault.services.report_llm import ChartSpec  # noqa: E402

_MAX_TABLE_ROWS = 50  # keep the printed table readable; the chart carries the shape


def _dejavu_path() -> str:
    return font_manager.findfont("DejaVu Sans")


def render_chart(result: QueryResult, chart: ChartSpec) -> bytes | None:
    """PNG bytes for the requested chart, or None when no chart applies."""
    if chart.chart_type == "none":
        return None
    if chart.x_column not in result.columns or chart.y_column not in result.columns:
        return None
    if not result.rows:
        return None
    xi, yi = result.columns.index(chart.x_column), result.columns.index(chart.y_column)
    xs = [str(row[xi]) for row in result.rows]
    ys = [float(row[yi]) if row[yi] is not None else 0.0 for row in result.rows]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    try:
        if chart.chart_type == "bar":
            ax.bar(xs, ys)
        elif chart.chart_type == "line":
            ax.plot(xs, ys, marker="o")
        elif chart.chart_type == "pie":
            ax.pie(ys, labels=xs, autopct="%1.1f%%")
        if chart.chart_type != "pie":
            ax.set_xlabel(chart.x_column)
            ax.set_ylabel(chart.y_column)
            fig.autofmt_xdate(rotation=45)
        ax.set_title(chart.title)
        buffer = BytesIO()
        fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
        return buffer.getvalue()
    finally:
        plt.close(fig)


def _numeric_stats(result: QueryResult) -> list[tuple[str, str]]:
    """(label, value) summary lines for each numeric column."""
    stats: list[tuple[str, str]] = [("Rows", str(len(result.rows)))]
    for index, name in enumerate(result.columns):
        values = [row[index] for row in result.rows if isinstance(row[index], (int, float))]
        if values and len(values) == len(result.rows):
            stats.append((f"Σ {name}", f"{sum(values):,.2f}"))
            stats.append((f"x̄ {name}", f"{sum(values) / len(values):,.2f}"))
    return stats


def build_pdf(
    *, title: str, prompt: str, result: QueryResult, chart_png: bytes | None
) -> bytes:
    """One-page-or-more PDF: title, request, chart, stats, capped result table."""
    pdf = FPDF()
    pdf.add_font("DejaVu", "", _dejavu_path())
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("DejaVu", size=18)
    pdf.multi_cell(0, 10, title)
    pdf.set_font("DejaVu", size=10)
    pdf.multi_cell(0, 6, prompt)
    pdf.ln(4)
    if chart_png is not None:
        pdf.image(BytesIO(chart_png), w=pdf.epw)
        pdf.ln(4)
    pdf.set_font("DejaVu", size=11)
    for label, value in _numeric_stats(result):
        pdf.cell(0, 7, f"{label}: {value}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("DejaVu", size=9)
    col_width = pdf.epw / max(len(result.columns), 1)
    for name in result.columns:
        pdf.cell(col_width, 7, str(name)[:40], border=1)
    pdf.ln()
    for row in result.rows[:_MAX_TABLE_ROWS]:
        for value in row:
            pdf.cell(col_width, 7, str(value)[:40], border=1)
        pdf.ln()
    if len(result.rows) > _MAX_TABLE_ROWS:
        pdf.cell(0, 7, f"… {len(result.rows) - _MAX_TABLE_ROWS} more rows omitted")
    return bytes(pdf.output())
```

- [ ] **Step 3: Run test to verify PASS, full checks, commit**

```bash
git add src/contextvault/services/report_render.py tests/test_report_render.py
git commit -m "feat: report rendering — matplotlib charts + Unicode-safe fpdf2 PDF"
```

---

### Task 8: Report generation orchestrator (self-repair loop + background seam)

**Files:**
- Create: `src/contextvault/services/reports.py`
- Test: `tests/test_reports_service.py`

**Interfaces:**
- Produces: `async generate_report(session, report: GeneratedReport, *, embed_attempts: int = 3) -> None` (drives PROCESSING→DONE|FAILED in place); `async run_report_generation(report_id: uuid.UUID, *, session_factory=SessionLocal) -> None` (the BackgroundTasks seam, mirrors `run_ingestion`); `async execute_frozen(session, *, schedule: ReportSchedule) -> GeneratedReport` (runs frozen SQL, creates the report row — used by the scheduler in Task 11).
- Consumes: Tasks 3–7 (`validate_sql`, `generate_report_query`, `run_validated_query`, `render_chart`, `build_pdf`), `provider_service.get_provider_key`, `core.crypto.decrypt`.

- [ ] **Step 1: Write the failing test**

Test strategy: monkeypatch `report_llm.generate_report_query`-level behavior via `reports.generate_report_query` to return fixed `ReportQuery` objects; point the connection at the test Postgres itself (as in Task 6); assert status transitions, the self-repair loop (first attempt invalid → second attempt valid → DONE with 2 attempts consumed), and failure after 3 bad attempts.

```python
# tests/test_reports_service.py
"""Orchestration: self-repair loop, status transitions, artifact persistence."""

import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession

import contextvault.services.reports as reports_service
from contextvault.core.config import get_settings
from contextvault.core.crypto import encrypt
from contextvault.models import (
    DatabaseConnection,
    DatabaseType,
    GeneratedReport,
    Repository,
    ReportStatus,
)
from contextvault.services.report_llm import ChartSpec, ReportQuery


async def _connected_repo(db_session: AsyncSession) -> tuple[Repository, DatabaseConnection]:
    url = make_url(get_settings().database_url)
    repo = Repository(name="Vault", llm_provider="gemini", llm_model="gem-1")
    db_session.add(repo)
    await db_session.flush()
    conn = DatabaseConnection(
        repository_id=repo.id,
        db_type=DatabaseType.POSTGRES,
        host=url.host or "localhost",
        port=url.port or 5432,
        database=url.database or "",
        username=url.username or "",
        password_encrypted=encrypt(url.password or ""),
        exposed_schema=[
            {"table": "repositories", "description": "", "columns": [{"name": "name", "description": ""}]}
        ],
    )
    db_session.add(conn)
    await db_session.flush()
    return repo, conn


def _query(sql: str) -> ReportQuery:
    return ReportQuery(sql=sql, chart=ChartSpec(chart_type="none", title="t"))


async def test_self_repair_recovers_and_records_final_sql(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, conn = await _connected_repo(db_session)
    report = GeneratedReport(
        repository_id=repo.id, connection_id=conn.id, requested_by=None, prompt="names"
    )
    db_session.add(report)
    await db_session.flush()

    attempts: list[list[str]] = []
    replies = iter([_query("SELECT secret FROM vault_x"), _query("SELECT name FROM repositories")])

    async def fake_generate(provider, api_key, model, **kwargs):
        attempts.append(list(kwargs["feedback"]))
        return next(replies)

    monkeypatch.setattr(reports_service, "generate_report_query", fake_generate)
    monkeypatch.setattr(reports_service.provider_service, "get_provider_key", _fake_key)

    await reports_service.generate_report(db_session, report)
    assert report.status is ReportStatus.DONE
    assert report.generated_sql is not None and "repositories" in report.generated_sql
    assert report.pdf_data is not None and report.pdf_data[:5] == b"%PDF-"
    assert len(attempts) == 2 and attempts[1]  # second attempt carried feedback


async def _fake_key(session, provider):
    return "k"


async def test_three_bad_attempts_fail_the_report(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, conn = await _connected_repo(db_session)
    report = GeneratedReport(
        repository_id=repo.id, connection_id=conn.id, requested_by=None, prompt="p"
    )
    db_session.add(report)
    await db_session.flush()

    async def always_bad(provider, api_key, model, **kwargs):
        return _query("DROP TABLE repositories")

    monkeypatch.setattr(reports_service, "generate_report_query", always_bad)
    monkeypatch.setattr(reports_service.provider_service, "get_provider_key", _fake_key)

    await reports_service.generate_report(db_session, report)
    assert report.status is ReportStatus.FAILED
    assert report.error is not None
```

- [ ] **Step 2: Run to verify FAIL, then implement**

```python
# src/contextvault/services/reports.py
"""Report generation orchestration (DB-reports spec §6) + the frozen-SQL path.

Mirrors ``services/ingestion.py``: an awaitable core (``generate_report``) that
drives PROCESSING→DONE|FAILED on the row, and a BackgroundTasks seam
(``run_report_generation``) that opens its own session. The self-repair loop
gives the LLM at most 3 attempts, feeding every rejection back as feedback;
each attempt passes the FULL guardrail stack.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.config import get_settings
from contextvault.core.crypto import decrypt
from contextvault.db.session import SessionLocal
from contextvault.models import (
    DatabaseConnection,
    GeneratedReport,
    Repository,
    ReportSchedule,
    ReportStatus,
)
from contextvault.services import providers as provider_service
from contextvault.services.report_execution import QueryExecutionError, run_validated_query
from contextvault.services.report_llm import ChartSpec, ReportQueryParseError, generate_report_query
from contextvault.services.report_render import build_pdf, render_chart
from contextvault.services.sql_guardrails import SQLValidationError, validate_sql
from contextvault.services.ingestion import SessionFactory

_MAX_ATTEMPTS = 3  # 1 initial + 2 self-repairs (spec §5)


class ReportGenerationError(Exception):
    """No valid query could be produced within the attempt budget."""


def _conn_params(connection: DatabaseConnection) -> dict[str, Any]:
    return {
        "db_type": connection.db_type,
        "host": connection.host,
        "port": connection.port,
        "database": connection.database,
        "username": connection.username,
        "password": decrypt(connection.password_encrypted),
    }


async def _run_and_render(
    report: GeneratedReport, connection: DatabaseConnection, sql: str, chart: ChartSpec
) -> None:
    """Execute validated SQL and attach the PDF artifact to the report row."""
    result = await run_validated_query(**_conn_params(connection), sql=sql)
    chart_png = render_chart(result, chart)
    title = chart.title or report.prompt
    pdf = build_pdf(title=title, prompt=report.prompt, result=result, chart_png=chart_png)
    report.generated_sql = sql
    report.chart_spec = chart.model_dump()
    report.pdf_data = pdf
    report.pdf_filename = f"report-{report.id}.pdf"
    report.status = ReportStatus.DONE
    report.error = None


async def generate_report(session: AsyncSession, report: GeneratedReport) -> None:
    """The full NL→SQL→PDF pipeline for one report row; never raises."""
    report.status = ReportStatus.PROCESSING
    await session.commit()
    try:
        connection = await session.get(DatabaseConnection, report.connection_id)
        repo = await session.get(Repository, report.repository_id)
        if connection is None or repo is None or repo.llm_provider is None:
            raise ReportGenerationError("Repository is missing its database connection or LLM config.")
        api_key = await provider_service.get_provider_key(session, repo.llm_provider)
        if not api_key:
            raise ReportGenerationError("The repository's LLM provider has no verified key.")
        base_url = (
            get_settings().openrouter_base_url if repo.llm_provider == "openrouter" else None
        )
        # Release the pooled app-DB connection before slow LLM/reporting-DB work
        # (same discipline as ingestion's OCR path).
        await session.commit()

        feedback: list[str] = []
        for _ in range(_MAX_ATTEMPTS):
            try:
                query = await generate_report_query(
                    repo.llm_provider,
                    api_key,
                    repo.llm_model or "",
                    base_url=base_url,
                    user_prompt=report.prompt,
                    exposed_schema=connection.exposed_schema,
                    db_type=connection.db_type,
                    feedback=feedback,
                )
            except ReportQueryParseError as exc:
                feedback.append(str(exc))
                continue
            try:
                safe_sql = validate_sql(
                    query.sql, db_type=connection.db_type, exposed_schema=connection.exposed_schema
                )
                await _run_and_render(report, connection, safe_sql, query.chart)
                await session.commit()
                return
            except (SQLValidationError, QueryExecutionError) as exc:
                feedback.append(f"Query `{query.sql}` was rejected: {exc}")
        raise ReportGenerationError(
            "Could not produce a valid query for this request. Last problem: "
            + (feedback[-1] if feedback else "no attempts succeeded")
        )
    except Exception as exc:  # noqa: BLE001 — any failure lands on the row, never silent
        await session.rollback()
        report.status = ReportStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
        await session.commit()


async def run_report_generation(
    report_id: uuid.UUID, *, session_factory: SessionFactory = SessionLocal
) -> None:
    """BackgroundTasks entrypoint: fresh session, load, delegate. No-op if deleted."""
    async with session_factory() as session:
        report = await session.get(GeneratedReport, report_id)
        if report is None:
            return
        await generate_report(session, report)


async def execute_frozen(session: AsyncSession, *, schedule: ReportSchedule) -> GeneratedReport:
    """Run a schedule's frozen SQL as a new report for its owner (no LLM call)."""
    report = GeneratedReport(
        repository_id=schedule.repository_id,
        connection_id=schedule.connection_id,
        requested_by=schedule.owner_id,
        prompt=schedule.prompt,
        schedule_id=schedule.id,
        status=ReportStatus.PROCESSING,
    )
    session.add(report)
    await session.flush()
    try:
        connection = await session.get(DatabaseConnection, schedule.connection_id)
        if connection is None:
            raise ReportGenerationError("The schedule's database connection is gone.")
        chart = ChartSpec.model_validate(schedule.frozen_chart_spec)
        await _run_and_render(report, connection, schedule.frozen_sql, chart)
    except Exception as exc:  # noqa: BLE001
        report.status = ReportStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
    await session.commit()
    return report
```

Note for the implementer: `SessionFactory` import from `services/ingestion.py` reuses the existing alias; if mypy complains about the `session_factory=SessionLocal` default (it did not for `run_ingestion` — follow that file's exact pattern).

- [ ] **Step 3: Run test to verify PASS, full checks, commit**

```bash
git add src/contextvault/services/reports.py tests/test_reports_service.py
git commit -m "feat: report orchestrator — self-repair loop, background seam, frozen path"
```

---

### Task 9: Database-connection API

**Files:**
- Create: `src/contextvault/api/database.py`
- Modify: `src/contextvault/main.py` (import + `app.include_router(database_router)`)
- Test: `tests/test_database_api.py`

**Interfaces:**
- Produces routes (all admin-only via `require_admin`, repo 404 via `session.get`):
  - `PUT /repositories/{repository_id}/database` — body `{db_type, host, port, database, username, password, exposed_schema?}`; live-tests the connection first (400 `detail` from `DBConnectionError` on failure); encrypts password; upserts the single row per repo. Password optional on update (keep stored one when omitted/empty).
  - `GET /repositories/{repository_id}/database` — 404 when repo or connection missing; returns `{id, db_type, host, port, database, username, exposed_schema}` — **never the password**.
  - `PATCH /repositories/{repository_id}/database/schema` — body `{exposed_schema: [...]}` — save the edited allow-list without re-testing the connection.
  - `DELETE /repositories/{repository_id}/database` — 204; reports/schedules cascade.
  - `POST /repositories/{repository_id}/database/introspect` — returns `{schema: [...]}` from `introspect_schema` using the STORED connection (400 when none / unreachable).
- Pydantic request models validate `port` 1–65535 and non-empty strings.

- [ ] **Step 1: Write the failing tests**

`tests/test_database_api.py`, using the same app/client fixture pattern as `tests/test_admin_notes_api.py` (create_app + dependency_overrides for `get_session`; users via `user_service.create_user`; `_auth` helper). Monkeypatch `contextvault.api.database.test_connection` and `...introspect_schema` where liveness is needed. Cover:

```python
async def test_put_tests_connection_encrypts_and_masks(...):
    # monkeypatch test_connection to succeed; PUT; expect 200, no "password" key in body;
    # GET returns username but no password; a second PUT with new host upserts (same id or replaced row — one row per repo).

async def test_put_rejects_unreachable_database(...):
    # monkeypatch test_connection to raise DBConnectionError("no route"); PUT → 400 with detail containing "no route".

async def test_requires_admin(...):
    # regular user: PUT/GET/DELETE/introspect → 403.

async def test_unknown_repo_404(...)

async def test_patch_schema_saves_allow_list(...):
    # PUT (mocked ok) then PATCH schema with descriptions → GET shows the edited exposed_schema.

async def test_introspect_uses_stored_connection(...):
    # monkeypatch introspect_schema → fixed schema; POST introspect → 200 {"schema": [...]}; without a stored connection → 400.

async def test_delete_removes_connection(...):
    # DELETE → 204; GET → 404.
```

Write these as full tests following the house helpers; assert exact status codes and JSON bodies.

- [ ] **Step 2: Run to verify FAIL, implement the router**

Follow `api/repositories.py` idioms (`require_admin`, `_get_repo`-style helper, encrypt via `core.crypto.encrypt`). Upsert: `select(DatabaseConnection).where(repository_id=...)` → update fields or create; `await session.flush()` before using `.id`. Register the router in `main.py` after `sources_router`.

- [ ] **Step 3: Run test to verify PASS, full checks, commit**

```bash
git add src/contextvault/api/database.py src/contextvault/main.py tests/test_database_api.py
git commit -m "feat: admin database-connection API — connect, introspect, allow-list, delete"
```

---

### Task 10: Reports API

**Files:**
- Create: `src/contextvault/api/reports.py`
- Modify: `src/contextvault/main.py`
- Test: `tests/test_reports_api.py`

**Interfaces:**
- Produces (user routes use the query-endpoint guard: repo 404 → `has_active_grant` 403; admins bypass the grant check):
  - `POST /repositories/{repository_id}/reports` `{prompt: str (min_length=1)}` → 201 `ReportResponse`; 400 when the repo has no database connection; creates PENDING row, `background_tasks.add_task(run_report_generation, report.id)`.
  - `GET /repositories/{repository_id}/reports` → own reports, newest first. `?all=true` → every user's reports, **admin-only** (403 otherwise), each row then includes `generated_sql`.
  - `GET /repositories/{repository_id}/reports/{report_id}` → single row (owner or admin; 404 cross-user for non-admins).
  - `GET /repositories/{repository_id}/reports/{report_id}/download` → `Response(content=pdf_data, media_type="application/pdf")` with `Content-Disposition: attachment; filename="..."`; 404 unless DONE with a PDF; owner or admin only.
  - `DELETE /repositories/{repository_id}/reports/{report_id}` → 204, owner or admin.
- `ReportResponse`: `{id, repository_id, prompt, status, error, created_at, has_pdf: bool, schedule_id}` (+ `generated_sql` only in the admin `?all=true` listing). Never includes `pdf_data` inline.

- [ ] **Step 1: Write the failing tests**

`tests/test_reports_api.py`, same fixture pattern. Monkeypatch `contextvault.api.reports.run_report_generation` with a fake that marks the report DONE and writes `pdf_data=b"%PDF-fake"` through its own session — or simpler, a no-op fake plus direct status assertions on the 201 body (PENDING). Cover: 201 + background dispatch (fake called with the report id); 400 without a connection; 404 unknown repo; 403 ungranted user; own-only listing (user A cannot see user B's report; B's `GET {id}` as A → 404); `?all=true` admin-only + includes `generated_sql`; download 404 while pending, 200 + `application/pdf` + attachment header once the row has `pdf_data` (set it via the fake generation function's session); delete own → 204.

- [ ] **Step 2: Implement, verify PASS, full checks, commit**

```bash
git add src/contextvault/api/reports.py src/contextvault/main.py tests/test_reports_api.py
git commit -m "feat: reports API — request, poll, per-user history, PDF download"
```

---

### Task 11: Scheduler service + lifespan wiring

**Files:**
- Create: `src/contextvault/services/report_scheduler.py`
- Modify: `src/contextvault/main.py` (lifespan)
- Test: `tests/test_report_scheduler.py`

**Interfaces:**
- Produces: `async find_due_schedules(session, *, now: datetime) -> list[ReportSchedule]` — enabled AND `run_at_time <= now.time()` AND (`last_run_at IS NULL` OR `last_run_at` date < `now` date); `async run_due_schedules(*, now, session_factory=SessionLocal) -> None` — for each due schedule call `execute_frozen`, set `last_run_at=now`, `last_error` from the produced report's error (or None); `async scheduler_loop(*, interval_seconds: int = 60, session_factory=SessionLocal) -> None` — `while True: run_due_schedules(now=datetime.now(UTC)); asyncio.sleep(interval)` with a broad try/except that logs and keeps looping.
- `main.py`: add an `asynccontextmanager` lifespan that `asyncio.create_task(scheduler_loop())` on startup and cancels it on shutdown; `FastAPI(title=..., lifespan=lifespan)`. Tests are unaffected (httpx `ASGITransport` does not run lifespan).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report_scheduler.py — key cases
async def test_due_logic(db_session):
    # now = 2026-07-23 01:05 UTC.
    # due: enabled, run_at 01:00, last_run_at None            -> included
    # due: enabled, run_at 01:00, last_run_at yesterday 01:05 -> included
    # not: enabled, run_at 01:00, last_run_at today 01:01     -> excluded (already ran today)
    # not: enabled, run_at 02:00                              -> excluded (not yet time)
    # not: disabled, run_at 01:00                             -> excluded

async def test_run_due_schedules_executes_frozen_and_stamps(db_session, monkeypatch):
    # Schedule with frozen_sql "SELECT name FROM repositories" against the test DB
    # (connection as in tests/test_reports_service.py). run_due_schedules(now=...)
    # -> a DONE GeneratedReport exists for the owner with schedule_id set,
    #    schedule.last_run_at == now, last_error is None.

async def test_failed_run_records_last_error_but_keeps_schedule_enabled(db_session):
    # frozen_sql "SELECT nope FROM missing" -> report FAILED, schedule.last_error set, enabled still True.
```

Write them fully with the `_connected_repo` helper pattern from Task 8's test (extract shared helpers into the test file; do not import across test files).

- [ ] **Step 2: Implement, verify PASS**

`find_due_schedules` compares with SQL: `ReportSchedule.enabled.is_(True)`, `ReportSchedule.run_at_time <= now.time()`, `sa.or_(ReportSchedule.last_run_at.is_(None), sa.func.date(ReportSchedule.last_run_at) < now.date())`.

- [ ] **Step 3: Full checks and commit**

```bash
git add src/contextvault/services/report_scheduler.py src/contextvault/main.py tests/test_report_scheduler.py
git commit -m "feat: nightly report scheduler — due logic, frozen re-runs, lifespan task"
```

---

### Task 12: Schedules API

**Files:**
- Create: `src/contextvault/api/report_schedules.py`
- Modify: `src/contextvault/main.py`
- Test: `tests/test_report_schedules_api.py`

**Interfaces:**
- Produces:
  - `POST /repositories/{repository_id}/report-schedules` `{report_id: uuid, run_at_time: "01:00"}` → 201. Source report must be DONE, belong to the caller (admins may schedule any), and have `generated_sql`+`chart_spec`; those freeze into the schedule (`owner_id` = caller). 400 otherwise.
  - `GET /repositories/{repository_id}/report-schedules` → own schedules; `?all=true` admin-only.
  - `PATCH /report-schedules/{schedule_id}` `{enabled?, run_at_time?}` → owner or admin; 404 cross-user for non-admins.
  - `DELETE /report-schedules/{schedule_id}` → 204, owner or admin.
- `ScheduleResponse`: `{id, repository_id, prompt, run_at_time, enabled, last_run_at, last_error, created_at}`.

- [ ] **Step 1: Failing tests** — same fixture pattern: freeze from a DONE report (created directly in the DB with `generated_sql`/`chart_spec` set); 400 freezing a PENDING/FAILED report; own-only listing + admin `?all=true`; PATCH toggle + time change; cross-user PATCH → 404; DELETE → 204; grant guard on the repo-scoped routes (403 without grant).

- [ ] **Step 2: Implement, verify PASS, full checks, commit**

```bash
git add src/contextvault/api/report_schedules.py src/contextvault/main.py tests/test_report_schedules_api.py
git commit -m "feat: report-schedules API — freeze, list, toggle, delete"
```

---

### Task 13: Frontend — admin Database tab

**Files:**
- Create: `frontend/src/api/database.ts`
- Create: `frontend/src/pages/AdminDatabasePage.tsx`
- Test: `frontend/src/pages/AdminDatabasePage.test.tsx`
- Modify: `frontend/src/App.tsx` (route `/admin/database`, admin-guarded like `/admin/sources`)
- Modify: `frontend/src/components/Layout.tsx` (NavLink after Sources: `<NavLink to="/admin/database">{t("nav.database")}</NavLink>`)
- Modify: `frontend/src/i18n/locales/en.json`, `frontend/src/i18n/locales/uk.json`

**Interfaces:**
- `database.ts` mirrors `api/database.py`: types `DatabaseType = "postgres" | "mysql"`, `ExposedColumn {name, description}`, `ExposedTable {table, description, columns}`, `DatabaseConnection {id, db_type, host, port, database, username, exposed_schema}`; functions `getDatabase(repositoryId)`, `putDatabase(repositoryId, payload)`, `patchSchema(repositoryId, exposed_schema)`, `deleteDatabase(repositoryId)`, `introspect(repositoryId)` — all via the `api` helper from `./client`.

- [ ] **Step 1: Failing component test** — vitest + testing-library, mirroring `AdminSourcesPage.test.tsx`'s setup (mock the api module). Cases: renders connection form when no connection (`getDatabase` rejects with 404 ApiError); submits the form → `putDatabase` called with typed values; on connected state shows masked "connected" summary + "Introspect" button; after introspect, tables render with per-table/column description inputs and checkboxes; "Save allow-list" calls `patchSchema` with only checked tables/columns; delete button calls `deleteDatabase` after `window.confirm`.

- [ ] **Step 2: Implement page** — repo selector identical to `AdminSourcesPage.tsx` (copy its selector pattern); two visual states (setup form / connected view with allow-list editor). Keep the allow-list editor simple: checkbox per table, checkbox per column, one text input per description. All strings via `t("adminDatabase.…")`; add every key to `en.json` AND `uk.json` (Ukrainian translations, not transliterations).

- [ ] **Step 3: Verify, full frontend checks, commit**

Run: `cd frontend && npm test -- --run && npm run lint && npm run format:check && npm run typecheck && npm run build`

```bash
git add frontend/src/api/database.ts frontend/src/pages/AdminDatabasePage.tsx frontend/src/pages/AdminDatabasePage.test.tsx frontend/src/App.tsx frontend/src/components/Layout.tsx frontend/src/i18n/locales
git commit -m "feat: admin Database tab — connect, introspect, allow-list editor"
```

---

### Task 14: Frontend — Reports page + schedules + docs

**Files:**
- Create: `frontend/src/api/reports.ts`
- Create: `frontend/src/pages/ReportsPage.tsx`
- Test: `frontend/src/pages/ReportsPage.test.tsx`
- Modify: `frontend/src/api/client.ts` (add `api.getBlob(path): Promise<Blob>` following the existing helper idiom — same auth/headers, returns `response.blob()`)
- Modify: `frontend/src/App.tsx` (route `/reports`, any authenticated user), `frontend/src/components/Layout.tsx` (NavLink visible to all users, next to the query link)
- Modify: `frontend/src/i18n/locales/en.json`, `uk.json`
- Modify: `docs/HANDOFF.md` (Done recently + Next up), `README.md` (feature list — reports)

**Interfaces:**
- `reports.ts`: `ReportStatus`, `Report {id, repository_id, prompt, status, error, created_at, has_pdf, schedule_id, generated_sql?}`, `isGenerating(status)` (pending|processing), `createReport(repositoryId, prompt)`, `listReports(repositoryId, all?: boolean)`, `downloadReport(repositoryId, reportId): Promise<Blob>`, `deleteReport`, plus schedules: `Schedule {…}`, `createSchedule(repositoryId, reportId, runAtTime)`, `listSchedules(repositoryId, all?)`, `patchSchedule(scheduleId, body)`, `deleteSchedule(scheduleId)`.

- [ ] **Step 1: Failing component test** — cases: repo picker from `listRepositories`; submitting a prompt calls `createReport` and the new row shows a generating state; polling (`vi.useFakeTimers`, 2000 ms like `SOURCE_POLL_MS`) refreshes until DONE; DONE row shows Download button → `downloadReport` called (mock `URL.createObjectURL`); FAILED row shows the error text; "Repeat nightly" prompts for a time and calls `createSchedule`; schedules section lists schedules with an enable/disable toggle calling `patchSchedule`.

- [ ] **Step 2: Implement page** — model the polling effect on `AdminSourcesPage.tsx:78-89` (2 s while any report `isGenerating`). Download: `const blob = await downloadReport(...); const url = URL.createObjectURL(blob); <a download>` click; revoke after. All strings in both locales (`reports.…` keys).

- [ ] **Step 3: Docs** — update `README.md` feature list and `docs/HANDOFF.md` (this feature under Done recently; follow-ups — DOCX/PPTX, MySQL CI service, retention — under Next up). Keep HANDOFF edits honest: note MySQL is unit-tested via dialect abstraction only (no MySQL in CI).

- [ ] **Step 4: Verify everything, commit**

Run: full frontend checks AND full backend checks (`uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -q`).

```bash
git add frontend/src docs/HANDOFF.md README.md
git commit -m "feat: Reports page — request, poll, download PDF, nightly schedules; docs"
```

---

## Self-Review Notes (already applied)

- Spec §4 said `requested_by` non-null CASCADE; Task 1 makes it nullable (schedule-produced and user-deleted rows) — deliberate deviation, documented in the model docstring.
- Task 8's orchestrator releases the app-DB session before LLM + reporting-DB work (ingestion's `_ocr_image` discipline).
- `ChartSpec` lives in `report_llm.py` and is imported by `report_render.py`/`reports.py` — one definition, no drift.
- The scheduler is lifespan-only; tests never start it (ASGITransport runs no lifespan).
- Type consistency verified: `QueryResult` (Task 6) is consumed by Tasks 7–8 by that exact name; `validate_sql` returns `str`; `generate_report_query` keyword set matches Task 8's call site.
