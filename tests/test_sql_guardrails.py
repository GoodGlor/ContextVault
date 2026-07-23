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
