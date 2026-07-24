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
    for bad in (
        "no json here",
        '{"chart": {"chart_type": "bar", "title": "t"}}',
        '{"sql": "SELECT 1", "chart": {"chart_type": "hologram", "title": "t"}}',
    ):
        with pytest.raises(ReportQueryParseError):
            parse_report_query(bad)


async def test_generate_calls_textgen_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate_text(
        provider: str, api_key: str, model: str, *, prompt: str, base_url: str | None = None
    ) -> str:
        assert provider == "gemini" and "orders" in prompt
        return (
            '{"sql": "SELECT city FROM orders", "chart": {"chart_type": "none", '
            '"x_column": null, "y_column": null, "title": "t"}}'
        )

    monkeypatch.setattr(report_llm, "generate_text", fake_generate_text)
    query = await generate_report_query(
        "gemini",
        "k",
        "m",
        base_url=None,
        user_prompt="p",
        exposed_schema=SCHEMA,
        db_type=DatabaseType.POSTGRES,
        feedback=[],
    )
    assert query.sql == "SELECT city FROM orders"
