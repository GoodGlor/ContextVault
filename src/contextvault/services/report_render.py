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


def build_pdf(*, title: str, prompt: str, result: QueryResult, chart_png: bytes | None) -> bytes:
    """One-page-or-more PDF: title, request, chart, stats, capped result table."""
    pdf = FPDF()
    pdf.add_font("DejaVu", "", _dejavu_path())
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("DejaVu", size=18)
    pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    pdf.multi_cell(0, 6, prompt, new_x="LMARGIN", new_y="NEXT")
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
