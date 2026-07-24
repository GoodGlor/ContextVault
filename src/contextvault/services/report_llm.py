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
        f"- The SQL must be exactly one SELECT statement in {db_type.value} syntax. "
        "Never modify data.\n"
        "- For relative date ranges use SQL date arithmetic "
        "(e.g. CURRENT_DATE - INTERVAL '30 days'),\n"
        "  never a hard-coded date, so the query stays correct when re-run later.\n"
        f"- Aggregate/group so the result is a meaningful report of at most {max_rows} rows.\n"
        '- x_column and y_column must be output column names of the SQL; use chart_type "none"\n'
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
