"""One-click PDF report generation.

Runs the same 'full analysis' preset queries used by the main app's quick
preset buttons (see queries.ANALYSIS_TYPE_COMBINED) across every analysis
type, for the currently locked facility + timeframe, and compiles everything
into a single paginated PDF: cover page, then one section per analysis type
with a KPI summary, a chart (where the data shape supports one), and the full
underlying data table — no rows dropped, columns handled via wrapped text and
auto-scaled font size rather than truncation.
"""
import io
import concurrent.futures
from datetime import datetime

import pandas as pd
import plotly.express as px
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image, KeepTogether,
)

import db
from queries import QUERY_LIBRARY, ANALYSIS_TYPE_COMBINED

# Pastel palette mirrored from theme.py, translated into reportlab colors.
INK = colors.HexColor("#2E2C29")
SUB = colors.HexColor("#8B8680")
LINE = colors.HexColor("#E9E4DA")
SAGE = colors.HexColor("#7FA88F")
SAGE_BG = colors.HexColor("#E7F0E9")
ROW_ALT = colors.HexColor("#FAF8F4")

# kaleido (chart-to-image) has a known failure mode on some Windows machines
# where its bundled Chromium subprocess hangs silently instead of raising an
# exception. A plain try/except never catches a hang. This executor gives
# chart rendering a hard wall-clock timeout — if it doesn't come back in time,
# the report proceeds without that chart instead of freezing indefinitely.
_CHART_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)
CHART_TIMEOUT_SECONDS = 12


def _render_chart_png(fig):
    return fig.to_image(format="png", scale=2)


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("ReportTitle", parent=ss["Title"], textColor=INK, fontSize=26, spaceAfter=6))
    ss.add(ParagraphStyle("ReportSub", parent=ss["Normal"], textColor=SUB, fontSize=12))
    ss.add(ParagraphStyle("SectionHeading", parent=ss["Heading1"], textColor=INK, fontSize=16, spaceBefore=18, spaceAfter=8))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=7, leading=9))
    ss.add(ParagraphStyle("CellHeader", parent=ss["Normal"], fontSize=7.5, leading=9, textColor=colors.white))
    return ss


def _fmt_value(val) -> str:
    if isinstance(val, float):
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


def _kpi_table(df: pd.DataFrame, styles) -> Table:
    row = df.iloc[0]
    data = [[Paragraph(col.replace("_", " ").title(), styles["Cell"]),
             Paragraph(_fmt_value(row[col]), styles["Cell"])] for col in df.columns[:10]]
    t = Table(data, colWidths=[6 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SAGE_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _data_table(df: pd.DataFrame, styles) -> Table:
    """Full data table — every row included (Platypus Tables paginate across
    pages automatically), column count handled by wrapping cell text in
    Paragraphs and scaling font size down as columns grow, instead of
    dropping any columns or rows."""
    n_cols = len(df.columns)
    font_size = 7 if n_cols <= 8 else (6 if n_cols <= 14 else 5)
    cell_style = ParagraphStyle("cell_dyn", parent=styles["Cell"], fontSize=font_size, leading=font_size + 2)
    header_style = ParagraphStyle("header_dyn", parent=styles["CellHeader"], fontSize=font_size + 0.5, leading=font_size + 2)

    header = [Paragraph(str(c).replace("_", " ").title(), header_style) for c in df.columns]
    body = [[Paragraph(_fmt_value(v) if not isinstance(v, str) else v, cell_style) for v in row]
            for row in df.itertuples(index=False)]
    data = [header] + body

    usable_width = landscape(A4)[0] - 2 * 2 * cm
    col_width = usable_width / max(n_cols, 1)
    t = Table(data, colWidths=[col_width] * n_cols, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def _chart_image(df: pd.DataFrame, title: str):
    """Builds a simple, presentation-ready chart for report inclusion.
    Returns None if the data shape doesn't suit a chart (e.g. a single row)."""
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()
    if len(df) < 2 or not num_cols or not cat_cols:
        return None

    x_col = "month" if "month" in cat_cols else cat_cols[0]
    y_col = num_cols[0]
    plot_df = df[~df.apply(lambda r: r.astype(str).str.upper().eq("TOTAL").any(), axis=1)].copy()
    if len(plot_df) < 2:
        return None
    plot_df = plot_df.sort_values(x_col)

    try:
        if x_col == "month":
            fig = px.line(plot_df, x=x_col, y=y_col, markers=True, title=title)
        else:
            fig = px.bar(plot_df, x=x_col, y=y_col, title=title)
        fig.update_layout(
            width=900, height=380, margin=dict(t=50, b=70, l=40, r=20),
            font=dict(size=12, color="#2E2C29"), plot_bgcolor="white", paper_bgcolor="white",
            title_font=dict(size=14),
        )
        future = _CHART_EXECUTOR.submit(_render_chart_png, fig)
        try:
            img_bytes = future.result(timeout=CHART_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            # Chart rendering hung — skip it and continue with the table.
            # The stuck worker thread is abandoned (can't be force-killed from
            # Python), but the app itself is never blocked by it again.
            return None
        return Image(io.BytesIO(img_bytes), width=17 * cm, height=17 * cm * 380 / 900)
    except Exception:
        return None  # chart rendering is a bonus, never blocks the report


def generate_section_pdf(section_title: str, panels: list, facility: str, display_time: str) -> bytes:
    """Builds a PDF from data ALREADY ON SCREEN — no database queries at all.
    `panels` is a list of (sub_title, df, is_kpi) tuples, exactly what's sitting
    in st.session_state['_results'] after any analysis has run. This is the
    'download what I'm looking at' report — instant, because nothing gets
    re-fetched. For a report spanning many analysis types instead, see
    generate_selected_types_pdf below."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    story = [
        Paragraph(section_title, styles["ReportTitle"]),
        Paragraph(f"{facility} &nbsp;·&nbsp; {display_time}", styles["ReportSub"]),
        Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", styles["ReportSub"]),
        Spacer(1, 0.6 * cm),
    ]

    for sub_title, df, is_kpi in panels:
        if df is None or df.empty:
            continue
        story.append(Paragraph(sub_title, styles["SectionHeading"]))
        if is_kpi and len(df) == 1:
            story.append(_kpi_table(df, styles))
        else:
            chart = _chart_image(df, sub_title)
            if chart:
                story.append(chart)
            story.append(Spacer(1, 0.3 * cm))
            story.append(_data_table(df, styles))
        story.append(Spacer(1, 0.5 * cm))

    doc.build(story)
    return buffer.getvalue()


def generate_selected_types_pdf(analysis_types: list, facilities, facility_display: str, date_from: str, date_to: str,
                                 display_time: str, db_url: str) -> bytes:
    """Same as generate_report_pdf but only runs the analysis types the user
    actually picked — lets a team pull just what they need instead of waiting
    for every type to query every time. `facilities` is a list (multi-select
    aware); `facility_display` is the human-readable string for the title."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    story = [
        Spacer(1, 4 * cm),
        Paragraph("Waste Operations Report", styles["ReportTitle"]),
        Paragraph(f"{facility_display} &nbsp;·&nbsp; {display_time}", styles["ReportSub"]),
        Spacer(1, 0.3 * cm),
        Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", styles["ReportSub"]),
        PageBreak(),
    ]

    any_section = False
    for analysis_type in analysis_types:
        keys = ANALYSIS_TYPE_COMBINED.get(analysis_type, [])
        section_flowables = [Paragraph(analysis_type, styles["SectionHeading"])]
        section_has_content = False

        for key in keys:
            sql = db.inject_filters(QUERY_LIBRARY[key].strip(), facilities, date_from, date_to)
            df, error = db.run_query(sql, db_url)
            sub_title = key.split(": ")[1].title() if ": " in key else key.title()

            if error:
                section_flowables.append(Paragraph(f"{sub_title}: unavailable ({error[:120]})", styles["Cell"]))
                continue
            if df is None or df.empty:
                section_flowables.append(Paragraph(f"{sub_title}: no data for this period.", styles["Cell"]))
                continue

            section_has_content = True
            section_flowables.append(Paragraph(sub_title, styles["Heading2"]))
            if "kpi" in key and len(df) == 1:
                section_flowables.append(_kpi_table(df, styles))
            else:
                chart = _chart_image(df, sub_title)
                if chart:
                    section_flowables.append(chart)
                section_flowables.append(Spacer(1, 0.3 * cm))
                section_flowables.append(_data_table(df, styles))
            section_flowables.append(Spacer(1, 0.5 * cm))

        if section_has_content:
            any_section = True
            story.extend(section_flowables)
            story.append(PageBreak())

    if not any_section:
        story.append(Paragraph("No data was available for the selected analysis types.", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()


def generate_report_pdf(facility: str, date_from: str, date_to: str, display_time: str, db_url: str) -> bytes:
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    story = []

    # ── cover ──
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("Waste Operations Report", styles["ReportTitle"]))
    story.append(Paragraph(f"{facility} &nbsp;·&nbsp; {display_time}", styles["ReportSub"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", styles["ReportSub"]))
    story.append(PageBreak())

    any_section = False
    for analysis_type, keys in ANALYSIS_TYPE_COMBINED.items():
        section_flowables = [Paragraph(analysis_type, styles["SectionHeading"])]
        section_has_content = False

        for key in keys:
            sql = db.inject_filters(QUERY_LIBRARY[key].strip(), facility, date_from, date_to)
            df, error = db.run_query(sql, db_url)
            sub_title = key.split(": ")[1].title() if ": " in key else key.title()

            if error:
                section_flowables.append(Paragraph(f"{sub_title}: unavailable ({error[:120]})", styles["Cell"]))
                continue
            if df is None or df.empty:
                section_flowables.append(Paragraph(f"{sub_title}: no data for this period.", styles["Cell"]))
                continue

            section_has_content = True
            section_flowables.append(Paragraph(sub_title, styles["Heading2"]))

            if "kpi" in key and len(df) == 1:
                section_flowables.append(_kpi_table(df, styles))
            else:
                chart = _chart_image(df, sub_title)
                if chart:
                    section_flowables.append(chart)
                section_flowables.append(Spacer(1, 0.3 * cm))
                section_flowables.append(_data_table(df, styles))
            section_flowables.append(Spacer(1, 0.5 * cm))

        if section_has_content:
            any_section = True
            story.extend(section_flowables)
            story.append(PageBreak())

    if not any_section:
        story.append(Paragraph("No data was available for any analysis type in this facility/timeframe.", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()
