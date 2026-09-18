"""Excel export for the generated P&L.

Produces a workbook laid out like the hand-built monthly P&Ls -- one sheet per
month (Revenue / Direct Cost / Gross Profit / Indirect Cost / EBITDA, with the
Rs-per-kg and %-of-total columns and a quantity panel) -- plus every source row
the figures were built from.

WHY THE STATEMENT USES FORMULAS, NOT PASTED NUMBERS
---------------------------------------------------
Every P&L line is a SUMIFS over the data sheets in the same workbook. So the
reader can trace any figure to its rows, the sheet recalculates if the data is
edited, and the red FILL MANUALLY cells can simply be typed over -- the totals
update themselves.

Expense and Revenue rows carry a `pnl_line` column, assigned from
pnl_mapping.csv before writing. That is what makes the mapping auditable:
filter the Expense sheet by pnl_line to see exactly which free-text categories
landed on which line.

Column positions of the data sheets are fixed by SCHEMAS below, because the
SUMIFS formulas address them by letter.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import pnl

FONT = "Arial"
MONEY = '₹#,##0.00;(₹#,##0.00);-'
PCT = '0.0%'
QTY = '#,##0.00;(#,##0.00);-'
INK, MUTED, RED, YELLOW, BAND = "1F2937", "6B7280", "C0392B", "FFFF00", "F3F4F6"
RULE = Side(style="thin", color="D1D5DB")

# Fixed column order per data sheet — the SUMIFS formulas depend on it.
SCHEMAS = {
    "Sale": ["outward_code", "month", "date", "facility", "customer", "destination",
             "vendor_type", "material", "outward_material_category",
             "dispatched_quantity", "accepted_quantity",
             "value_of_accepted_material", "transportation_cost"],
    "Inward": ["inward_code", "month", "date", "facility", "received_material_from",
               "source_type", "material", "inward_material_category",
               "received_quantity", "accepted_quantity",
               "value_of_accepted_material", "transportation_cost", "additional_cost"],
    "Expense": ["expense_code", "month", "date", "facility", "category", "pnl_line",
                "description", "bill_amount_in_rs"],
    "Revenue": ["revenue_code", "month", "date", "facility", "category", "pnl_line",
                "description", "bill_amount_in_rs"],
}
MONEY_COLS = {"value_of_accepted_material", "transportation_cost", "additional_cost",
              "bill_amount_in_rs"}
QTY_COLS = {"dispatched_quantity", "accepted_quantity", "received_quantity"}


def _col(sheet, name):
    return get_column_letter(SCHEMAS[sheet].index(name) + 1)


def _sumifs(sheet, sum_col, criteria):
    parts = [f"'{sheet}'!${sum_col}:${sum_col}"]
    for col, val in criteria:
        parts += [f"'{sheet}'!${col}:${col}", f'"{val}"']
    return "SUMIFS(" + ",".join(parts) + ")"


def line_formula(line, month):
    """The formula for one P&L line in one month, or None if it's a manual line."""
    if line in pnl.MANUAL_LINES:
        return None
    M = lambda s: (_col(s, "month"), month)
    if line == "Sale of Recyclables":
        return "=" + _sumifs("Sale", _col("Sale", "value_of_accepted_material"), [M("Sale")])
    if line == "Material purchase cost":
        return "=" + _sumifs("Inward", _col("Inward", "value_of_accepted_material"), [M("Inward")])
    if line == "Logistics - Outward (Sale of material)":
        return "=" + _sumifs("Sale", _col("Sale", "transportation_cost"), [M("Sale")])
    if line == "Logistics - Inward (RM purchase)":
        a = _sumifs("Inward", _col("Inward", "transportation_cost"), [M("Inward")])
        b = _sumifs("Inward", _col("Inward", "additional_cost"), [M("Inward")])
        return f"={a}+{b}"
    src = "Revenue" if line in pnl.REVENUE_LINES else "Expense"
    return "=" + _sumifs(src, _col(src, "bill_amount_in_rs"),
                         [M(src), (_col(src, "pnl_line"), line)])


def _set(ws, row, col, value, size=10, bold=False, color=INK, fmt=None, fill=None):
    c = ws.cell(row, col, value)
    c.font = Font(name=FONT, size=size, bold=bold, color=color)
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    return c


def _data_sheet(wb, name, df):
    ws = wb.create_sheet(name)
    cols = SCHEMAS[name]
    df = df.reindex(columns=cols)
    for j, col in enumerate(cols, start=1):
        c = ws.cell(1, j, col)
        c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="374151")
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = max(13, min(34, len(col) + 6))
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        for j, col in enumerate(cols, start=1):
            v = row[col]
            if pd.isna(v):
                v = None
            elif hasattr(v, "strftime"):
                v = str(v)[:10]
            fmt = MONEY if col in MONEY_COLS else (QTY if col in QTY_COLS else None)
            _set(ws, i, j, v, fmt=fmt)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(2, len(df) + 1)}"
    return ws


def _month_sheet(wb, month, facility_label, generated):
    ws = wb.create_sheet(f"PnL {month}")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 44
    for c, w in (("B", 18), ("C", 18), ("D", 13), ("E", 3), ("F", 30), ("G", 16)):
        ws.column_dimensions[c].width = w

    ws.merge_cells("A1:D1")
    _set(ws, 1, 1, pnl.DISCLAIMER, size=11, bold=True, color=RED, fill=YELLOW)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    _set(ws, 2, 1, f"{facility_label} — Profit & Loss Statement | {month}", size=13, bold=True)
    _set(ws, 3, 1, f"Generated {generated} from the WiseWaste BigQuery dataset. "
                   "Yellow cells have no database source and must be entered by hand.",
         size=9, color=MUTED)

    # quantity panel first — the Rs/kg column divides by dispatched
    _set(ws, 5, 6, "Quantities (Kg)", size=11, bold=True, fill=BAND)
    _set(ws, 5, 7, "", fill=BAND)
    qmap = [("Inward received", "Inward", "received_quantity"),
            ("Inward accepted", "Inward", "accepted_quantity"),
            ("Dispatched", "Sale", "dispatched_quantity"),
            ("Dispatch accepted", "Sale", "accepted_quantity")]
    disp_row = None
    for k, (label, sheet, col) in enumerate(qmap, start=6):
        _set(ws, k, 6, label)
        _set(ws, k, 7, "=" + _sumifs(sheet, _col(sheet, col), [(_col(sheet, "month"), month)]),
             fmt=QTY)
        if label == "Dispatched":
            disp_row = k
    _set(ws, 11, 6, "Unit metrics are per kg dispatched, matching the original workbooks.",
         size=9, color=MUTED)

    r = 5
    blocks = {}

    def block(title, lines, key):
        nonlocal r
        for j, lab in enumerate(["", "Amount (Rs)", "Unit metric (Rs/Kg)", "% of total"], start=1):
            _set(ws, r, j, title if j == 1 else lab, size=11, bold=True, fill=BAND)
            ws.cell(r, j).border = Border(bottom=RULE)
        r += 1
        first = r
        for line in lines:
            manual = line in pnl.MANUAL_LINES
            _set(ws, r, 1, line, bold=manual, color=RED if manual else INK)
            if manual:
                _set(ws, r, 2, None, fmt=MONEY, fill=YELLOW)
                _set(ws, r, 5, pnl.MANUAL_PLACEHOLDER, size=9, bold=True, color=RED)
            else:
                _set(ws, r, 2, line_formula(line, month), fmt=MONEY)
            r += 1
        blocks[key] = (first, r - 1)
        return first, r - 1

    def subtotal(label, key, size=11):
        nonlocal r
        a, b = blocks[key]
        _set(ws, r, 1, label, size=size, bold=True, fill=BAND)
        _set(ws, r, 2, f"=SUM(B{a}:B{b})", size=size, bold=True, fmt=MONEY, fill=BAND)
        for j in (3, 4):
            ws.cell(r, j).fill = PatternFill("solid", fgColor=BAND)
        row = r
        r += 2
        return row

    block("Revenue", pnl.REVENUE_LINES, "rev")
    rev_total = subtotal("Total Revenue", "rev")
    block("Direct Cost (COGS)", pnl.DIRECT_LINES, "dir")
    dir_total = subtotal("Total Direct Cost", "dir")

    _set(ws, r, 1, "Gross Profit", size=11, bold=True)
    _set(ws, r, 2, f"=B{rev_total}-B{dir_total}", size=11, bold=True, fmt=MONEY)
    gp = r; r += 1
    _set(ws, r, 1, "Gross Profit Margin")
    _set(ws, r, 2, f'=IFERROR(B{gp}/B{rev_total},"")', fmt=PCT)
    r += 2

    block("Indirect Cost / Operating Expenses", pnl.INDIRECT_LINES, "ind")
    ind_total = subtotal("Total Indirect Cost", "ind")

    _set(ws, r, 1, "Total Cost", size=11, bold=True)
    _set(ws, r, 2, f"=B{dir_total}+B{ind_total}", size=11, bold=True, fmt=MONEY)
    tc = r; r += 2
    _set(ws, r, 1, "EBITDA", size=12, bold=True)
    _set(ws, r, 2, f"=B{rev_total}-B{tc}", size=12, bold=True, fmt=MONEY)
    eb = r; r += 1
    _set(ws, r, 1, "EBITDA %")
    _set(ws, r, 2, f'=IFERROR(B{eb}/B{rev_total},"")', fmt=PCT)
    r += 2
    _set(ws, r, 1, "Totals cover automated lines only — the yellow cells above are "
                   "excluded until they are filled in.", size=9, color=MUTED)

    for key, denom in (("rev", f"$B${rev_total}"), ("dir", f"$B${tc}"), ("ind", f"$B${tc}")):
        a, b = blocks[key]
        for rr in range(a, b + 1):
            _set(ws, rr, 3, f'=IFERROR(B{rr}/$G${disp_row},"")', fmt=MONEY)
            _set(ws, rr, 4, f'=IFERROR(B{rr}/{denom},"")', fmt=PCT)
    return ws


def build_workbook(months, sale, inward, expense, revenue, mapping_rows,
                   unmapped, facility_label, out_path):
    wb = Workbook()
    wb.remove(wb.active)
    generated = datetime.now().strftime("%d %b %Y %H:%M")

    for m in months:
        _month_sheet(wb, m, facility_label, generated)
    for name, df in (("Sale", sale), ("Inward", inward),
                     ("Expense", expense), ("Revenue", revenue)):
        _data_sheet(wb, name, df)

    ws = wb.create_sheet("Mapping")
    _set(ws, 1, 1, "Expense / revenue category → P&L line, as applied", size=11, bold=True)
    _set(ws, 2, 1, "Edit pnl_mapping.csv to change this. Blank pnl_line = still unmapped.",
         size=9, color=MUTED)
    md = pd.DataFrame(sorted(mapping_rows.items()), columns=["category", "pnl_line"])
    for j, col in enumerate(["category", "pnl_line"], start=1):
        _set(ws, 4, j, col, bold=True, color="FFFFFF", fill="374151")
        ws.column_dimensions[get_column_letter(j)].width = 46
    for i, (_, row) in enumerate(md.iterrows(), start=5):
        _set(ws, i, 1, row["category"]); _set(ws, i, 2, row["pnl_line"])
    ws.freeze_panes = "A5"

    ws = wb.create_sheet("Notes")
    _set(ws, 1, 1, "How this workbook was produced", size=13, bold=True)
    notes = [
        pnl.DISCLAIMER,
        "",
        f"Facilities: {facility_label}",
        f"Months: {', '.join(months)}",
        f"Generated: {generated}",
        "",
        "Every figure on a PnL sheet is a SUMIFS over the data sheets in this file —",
        "click a cell to see which rows produced it. Nothing is a pasted number.",
        "",
        "Red / yellow lines have no source in the database and must be entered by hand.",
        "They are EXCLUDED from Total Revenue, Total Cost and EBITDA until filled in,",
        "so a partial figure is never mistaken for a final one.",
        "",
        "Sourcing:",
    ]
    for line, source, note in pnl.ASSUMPTIONS:
        notes.append(f"  • {line}: {source} — {note}")
    notes += ["",
              f"Unmapped expense categories: {len(unmapped)} "
              f"(₹{unmapped['amount'].sum():,.2f})" if len(unmapped) else
              "Unmapped expense categories: none"]
    for i, t in enumerate(notes, start=3):
        _set(ws, i, 1, t, size=10, bold=t == pnl.DISCLAIMER,
             color=RED if t == pnl.DISCLAIMER else INK)
    ws.column_dimensions["A"].width = 110

    wb.save(out_path)
    return out_path


# ---- BigQuery side -------------------------------------------------------
def _rows(client, dataset_ref, table, cols, facilities, date_from, date_to, date_col="date"):
    from google.cloud import bigquery
    sel = ", ".join(cols)
    sql = (f"SELECT {sel}, FORMAT_DATE('%Y-%m', {date_col}) AS month "
           f"FROM {table} WHERE facility IN UNNEST(@facilities) "
           f"AND {date_col} BETWEEN @d_from AND @d_to ORDER BY {date_col}")
    job = bigquery.QueryJobConfig(
        default_dataset=dataset_ref,
        query_parameters=[
            bigquery.ArrayQueryParameter("facilities", "STRING", facilities),
            bigquery.ScalarQueryParameter("d_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("d_to", "DATE", date_to)])
    return client.query(sql, job_config=job).to_dataframe()


def export_workbook(client_and_dataset, facilities, date_from, date_to,
                    out_path, facility_label=None, mapping_path=None):
    """Pulls the row-level source data, tags expense/revenue rows with their
    P&L line, and writes the workbook."""
    client, dataset_ref = client_and_dataset
    mapping = pnl.load_mapping(mapping_path)

    sale = _rows(client, dataset_ref, "outward",
                 ["outward_code", "date", "facility", "customer", "destination",
                  "vendor_type", "material", "outward_material_category",
                  "dispatched_quantity", "accepted_quantity",
                  "value_of_accepted_material", "transportation_cost"],
                 facilities, date_from, date_to)
    inward = _rows(client, dataset_ref, "inward",
                   ["inward_code", "date", "facility", "received_material_from",
                    "source_type", "material", "inward_material_category",
                    "received_quantity", "accepted_quantity",
                    "value_of_accepted_material", "transportation_cost",
                    "additional_cost"],
                   facilities, date_from, date_to)
    expense = _rows(client, dataset_ref, "expense",
                    ["expense_code", "date", "facility", "category", "description",
                     "bill_amount_in_rs"],
                    facilities, date_from, date_to, pnl.PERIOD_DATE_COL)
    revenue = _rows(client, dataset_ref, "revenue",
                    ["revenue_code", "date", "facility", "category", "description",
                     "bill_amount_in_rs"],
                    facilities, date_from, date_to, pnl.PERIOD_DATE_COL)

    expense["pnl_line"] = expense["category"].map(
        lambda c: mapping.get(str(c).strip(), "Unmapped"))
    revenue["pnl_line"] = revenue["category"].map(pnl._map_revenue_category)

    unmapped = (expense[expense["pnl_line"] == "Unmapped"]
                .rename(columns={"bill_amount_in_rs": "amount"})[["category", "amount"]])
    months = sorted(set(sale["month"]) | set(inward["month"])
                    | set(expense["month"]) | set(revenue["month"]))
    months = [m for m in months if isinstance(m, str)]
    return build_workbook(months, sale, inward, expense, revenue, mapping,
                          unmapped, facility_label or ", ".join(facilities), out_path)
