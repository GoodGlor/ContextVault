"""Chart PNGs and PDF assembly — including the Cyrillic-font regression guard."""

from contextvault.services.report_execution import QueryResult
from contextvault.services.report_llm import ChartSpec
from contextvault.services.report_render import build_pdf, render_chart

RESULT = QueryResult(columns=["city", "revenue"], rows=[("Київ", 120), ("Львів", 80)])


def test_bar_chart_renders_png() -> None:
    png = render_chart(
        RESULT, ChartSpec(chart_type="bar", x_column="city", y_column="revenue", title="Дохід")
    )
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_type_none_and_unknown_column_yield_no_chart() -> None:
    assert render_chart(RESULT, ChartSpec(chart_type="none", title="t")) is None
    assert (
        render_chart(
            RESULT, ChartSpec(chart_type="line", x_column="ghost", y_column="revenue", title="t")
        )
        is None
    )


def test_pdf_builds_with_cyrillic_and_chart() -> None:
    png = render_chart(
        RESULT, ChartSpec(chart_type="bar", x_column="city", y_column="revenue", title="Дохід")
    )
    pdf = build_pdf(title="Звіт по містах", prompt="звіт по Києву", result=RESULT, chart_png=png)
    assert pdf[:5] == b"%PDF-"


def test_pdf_builds_without_chart_and_with_empty_result() -> None:
    empty = QueryResult(columns=["city"], rows=[])
    assert build_pdf(title="Report", prompt="p", result=empty, chart_png=None)[:5] == b"%PDF-"
