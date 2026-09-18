"""Facility P&L generation.

Replaces the hand-built monthly P&L workbooks. One fixed structure, every
month, every facility -- generated from BigQuery rather than from pivot
tables that need manual refreshing and SUMIF ranges that need re-pointing
by hand each month.

WHY A MAPPING FILE
------------------
expense.category and revenue.category are uncontrolled free text: 127
distinct spellings observed, 104 of them at a single facility, 43 used
exactly once. "Electricity" is spelled three ways; salary eighteen. No
amount of SQL fixes that, so the category -> P&L line mapping lives in
pnl_mapping.csv, editable without touching code.

Anything the mapping doesn't cover lands on an explicit "Unmapped" line
with its categories listed underneath. That is deliberate: the P&L always
reconciles to the expense table, and a new free-text category announces
itself instead of silently vanishing (the old workbooks hand-picked which
cells fell into "Other expenses" -- 8 cells in April, 14 in July, which is
why Other moved between Rs 82k and Rs 177k with no explanation).

FACILITY HANDLING
-----------------
Same contract as the rest of the app: `facilities` is a LIST. Results are
computed per facility and can be shown separately or consolidated, so
"Trading Data RPG/External Transfers- SCM (MRF)" and "Interim PRF (Sarvam
Jigani)" are ordinary selectable facilities rather than hardcoded MRF
add-ons -- they don't always belong to MRF.

DECISIONS ALREADY CONFIRMED
---------------------------
  * Revenue = value_of_accepted_material (the "AD" column in the sheets),
    NOT net_material_sales_cost -- those disagree by ~23% at MRF.
  * Cash basis. No closing-stock adjustment (tried in the April sheet,
    dropped from May onward).
  * Unit metrics are per kg DISPATCHED, matching the workbooks' $H$23/$H$24.

ASSUMPTIONS STILL UNCONFIRMED -- see the constants below. Each is isolated
as a named constant so changing it is a one-line edit, and every one is
reported by describe_assumptions() so they show on the face of the output
rather than hiding in code.
"""
import os
import csv
import pandas as pd
from google.cloud import bigquery

# ---- unconfirmed assumptions ---------------------------------------------
# Which inward cost columns make up "Logistics - Inward". The June 2026
# workbook used transportation_cost + additional_cost; April used
# transportation_cost alone.
INWARD_LOGISTICS_COLS = ["transportation_cost", "additional_cost"]

# CONFIRMED by reconciliation against the Apr and May 2026 MRF workbooks:
# outward.transportation_cost alone reproduces "Logistics - Outward" to the
# rupee (225,176.61 and 84,272.00). loading_cost and additional_transport_cost
# are NOT part of it.
OUTWARD_LOGISTICS_COLS = ["transportation_cost"]

# CONFIRMED: inward.net_procurement_cost ALREADY INCLUDES inward freight --
# workbook "Material purchase cost" + "Logistics - Inward" == net_procurement_cost
# exactly for Apr (369,754.59 + 18,050.00) and May (380,487.00 + 33,750.00).
# So "Material purchase cost" must come from value_of_accepted_material, NOT
# from net_procurement_cost; using the latter while also adding
# transportation_cost as Logistics - Inward double-counts the freight.
INWARD_PURCHASE_COL = "value_of_accepted_material"

# Denominator for the Rs/kg unit metrics.
UNIT_METRIC_QTY = "dispatched_quantity"   # vs accepted_quantity

# expense/revenue both carry `date` and `record_date`. `date` is the synced
# column the rest of the app filters on, so the P&L month follows it.
PERIOD_DATE_COL = "date"

ASSUMPTIONS = [
    ("Logistics - Inward", f"inward: {' + '.join(INWARD_LOGISTICS_COLS)}",
     "June workbook used transport + additional; April used transport only"),
    ("Logistics - Outward", f"outward: {' + '.join(OUTWARD_LOGISTICS_COLS)}",
     "CONFIRMED against the Apr and May 2026 workbooks, exact to the rupee"),
    ("Rs/kg denominator", f"outward.{UNIT_METRIC_QTY}",
     "matches the workbooks' Total Dispatched"),
    ("P&L month", f"{PERIOD_DATE_COL} column",
     "expense/revenue also have record_date"),
]

# ---- P&L structure -------------------------------------------------------
REVENUE_LINES = [
    "Sale of Recyclables",
    "Service Fee - BWG",
    "Processing Fee - Government",
    "VGF - rPG",
    "VGF - SZW",
    "Other revenue / adjustments",
]
DIRECT_LINES = [
    "Material purchase cost",
    "Salaries - Operational staff",
    "Salaries - Contract staff",
    "PPE",
    "Logistics - Inward (RM purchase)",
    "Logistics - Outward (Sale of material)",
    "Reject Disposal Cost",
    "SCF Disposal Cost",
]
INDIRECT_LINES = [
    "Facility Rent",
    "Salaries - Entrepreneur",
    "Management fee / Owner compensation",
    "Salary - Supervisor",
    "Salary - Admin",
    "Salary - Security",
    "Electricity",
    "Internet",
    "Water",
    "Repair and Maintenance",
    "Baling consumables",
    "Machine transport and hire",
    "Weigh Bridge",
    "Pest control",
    "Tea/coffee and snacks",
    "Toiletries and housekeeping",
    "Stationery",
    "Staff travel",
    "Loading and unloading",
    "Logistics (Porter/parcel)",
    "Waste collection from BWG",
    "Other expenses",
    "Unmapped",
]
SECTIONS = [
    ("Revenue", REVENUE_LINES),
    ("Direct Cost (COGS)", DIRECT_LINES),
    ("Indirect Cost / Operating Expenses", INDIRECT_LINES),
]
COST_LINES = DIRECT_LINES + INDIRECT_LINES

# ---- manual lines --------------------------------------------------------
# Lines with no BigQuery source, plus the two Siddhesh keeps by hand. These
# are NEVER auto-filled and NEVER silently counted as zero: they render in red
# as "FILL MANUALLY" and are EXCLUDED from the computed totals, so a partial
# EBITDA can't be mistaken for a final one. VGF - rPG is a hand-entered
# monthly accrual; Service Fee - BWG is typed late in the cycle. The rest
# genuinely have no source anywhere in the database.
MANUAL_LINES = frozenset({
    "Service Fee - BWG",
    "Processing Fee - Government",
    "VGF - rPG",
    "VGF - SZW",
    "Reject Disposal Cost",
    "SCF Disposal Cost",
    "Management fee / Owner compensation",
    "Salary - Supervisor",
    "Salary - Admin",
    "Salary - Security",
})

MANUAL_PLACEHOLDER = "FILL MANUALLY"

DISCLAIMER = ("This is a system generated P&L and not a final version. "
              "Kindly review it and then submit.")

SUBTOTAL_LINES = ("Total Revenue", "Total Direct Cost", "Gross Profit",
                  "Gross Profit Margin %", "Total Indirect Cost", "Total Cost",
                  "EBITDA", "EBITDA %")

# revenue.category -> P&L line. Prefix match, longest first.
REVENUE_CATEGORY_MAP = [
    ("Incentive Cost (rPG)", "VGF - rPG"),
    ("BWG_Revenue_", "Service Fee - BWG"),
    ("BWGs_", "Service Fee - BWG"),
    ("Financial Adjustment", "Other revenue / adjustments"),
    ("Trading of Material", "Other revenue / adjustments"),
]


def load_mapping(path="pnl_mapping.csv"):
    """category -> pnl_line. Rows with a blank pnl_line (the ASK rows) are
    deliberately absent, so they fall through to Unmapped."""
    mapping = {}
    if not os.path.exists(path):
        return mapping
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            line = (row.get("pnl_line") or "").strip()
            if line:
                mapping[(row["category"] or "").strip()] = line
    return mapping


def _map_revenue_category(category):
    cat = (category or "").strip()
    for prefix, line in REVENUE_CATEGORY_MAP:
        if cat.startswith(prefix):
            return line
    return "Other revenue / adjustments"


def _q(client, dataset_ref, sql, facilities, date_from, date_to):
    job_config = bigquery.QueryJobConfig(
        default_dataset=dataset_ref,
        query_parameters=[
            bigquery.ArrayQueryParameter("facilities", "STRING", facilities),
            bigquery.ScalarQueryParameter("d_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("d_to", "DATE", date_to),
        ],
    )
    return client.query(sql, job_config=job_config).to_dataframe()


def calculate_pnl(client_and_dataset, facilities, date_from, date_to,
                  mapping_path="pnl_mapping.csv"):
    """Returns a dict:

      lines     -- tidy DataFrame [facility, month, section, line, amount]
      quantities-- DataFrame [facility, month, inward_kg, accepted_kg,
                   dispatched_kg]
      unmapped  -- DataFrame [facility, month, category, amount] for
                   everything that fell through to the Unmapped line
      assumptions -- list of (line, source, note)

    Never re-aggregates across facilities: consolidation is the caller's
    choice (see pivot()), which keeps a per-facility view and a consolidated
    view derivable from the same result.
    """
    client, dataset_ref = client_and_dataset
    mapping = load_mapping(mapping_path)
    D = PERIOD_DATE_COL

    exp = _q(client, dataset_ref, f"""
        SELECT facility, FORMAT_DATE('%Y-%m', {D}) AS month, category,
               SUM(bill_amount_in_rs) AS amount
        FROM expense
        WHERE facility IN UNNEST(@facilities) AND {D} BETWEEN @d_from AND @d_to
        GROUP BY 1, 2, 3
    """, facilities, date_from, date_to)

    rev = _q(client, dataset_ref, f"""
        SELECT facility, FORMAT_DATE('%Y-%m', {D}) AS month, category,
               SUM(bill_amount_in_rs) AS amount
        FROM revenue
        WHERE facility IN UNNEST(@facilities) AND {D} BETWEEN @d_from AND @d_to
        GROUP BY 1, 2, 3
    """, facilities, date_from, date_to)

    out_cols = ", ".join(f"SUM({c}) AS {c}" for c in OUTWARD_LOGISTICS_COLS)
    outw = _q(client, dataset_ref, f"""
        SELECT facility, FORMAT_DATE('%Y-%m', date) AS month,
               SUM(value_of_accepted_material) AS sale_value,
               SUM({UNIT_METRIC_QTY}) AS dispatched_kg,
               SUM(accepted_quantity) AS accepted_out_kg,
               {out_cols}
        FROM outward
        WHERE facility IN UNNEST(@facilities) AND date BETWEEN @d_from AND @d_to
        GROUP BY 1, 2
    """, facilities, date_from, date_to)

    in_cols = ", ".join(f"SUM({c}) AS {c}" for c in INWARD_LOGISTICS_COLS)
    inw = _q(client, dataset_ref, f"""
        SELECT facility, FORMAT_DATE('%Y-%m', date) AS month,
               SUM(value_of_accepted_material) AS purchase_value,
               SUM(received_quantity) AS received_kg,
               SUM(accepted_quantity) AS accepted_in_kg,
               {in_cols}
        FROM inward
        WHERE facility IN UNNEST(@facilities) AND date BETWEEN @d_from AND @d_to
        GROUP BY 1, 2
    """, facilities, date_from, date_to)

    rows, unmapped_rows = [], []
    section_of = {ln: sec for sec, lns in SECTIONS for ln in lns}

    def add(fac, month, line, amount):
        if amount is None or pd.isna(amount):
            return
        rows.append({"facility": fac, "month": month,
                     "section": section_of.get(line, "Indirect Cost / Operating Expenses"),
                     "line": line, "amount": float(amount)})

    for _, r in exp.iterrows():
        line = mapping.get((r["category"] or "").strip())
        if line is None:
            line = "Unmapped"
            unmapped_rows.append({"facility": r["facility"], "month": r["month"],
                                  "category": r["category"], "amount": float(r["amount"] or 0)})
        add(r["facility"], r["month"], line, r["amount"])

    for _, r in rev.iterrows():
        add(r["facility"], r["month"], _map_revenue_category(r["category"]), r["amount"])

    for _, r in outw.iterrows():
        add(r["facility"], r["month"], "Sale of Recyclables", r["sale_value"])
        add(r["facility"], r["month"], "Logistics - Outward (Sale of material)",
            sum(float(r[c] or 0) for c in OUTWARD_LOGISTICS_COLS))

    for _, r in inw.iterrows():
        add(r["facility"], r["month"], "Material purchase cost", r["purchase_value"])
        add(r["facility"], r["month"], "Logistics - Inward (RM purchase)",
            sum(float(r[c] or 0) for c in INWARD_LOGISTICS_COLS))

    lines = pd.DataFrame(rows, columns=["facility", "month", "section", "line", "amount"])
    if not lines.empty:
        lines = lines.groupby(["facility", "month", "section", "line"], as_index=False)["amount"].sum()

    qty = pd.DataFrame()
    if not outw.empty or not inw.empty:
        a = outw[["facility", "month", "dispatched_kg"]] if not outw.empty else pd.DataFrame(columns=["facility", "month", "dispatched_kg"])
        b = inw[["facility", "month", "received_kg", "accepted_in_kg"]] if not inw.empty else pd.DataFrame(columns=["facility", "month", "received_kg", "accepted_in_kg"])
        qty = pd.merge(a, b, on=["facility", "month"], how="outer").fillna(0)

    return {
        "lines": lines,
        "quantities": qty,
        "unmapped": pd.DataFrame(unmapped_rows, columns=["facility", "month", "category", "amount"]),
        "assumptions": ASSUMPTIONS,
    }


def pivot(result, consolidated=True):
    """Lines down, months across, with derived subtotals.

    Manual lines (MANUAL_LINES) are returned as NaN, never 0, and are left OUT
    of every subtotal. That is deliberate: filling them with 0 would make
    EBITDA look final when e.g. VGF - rPG has not been entered, and a reader
    has no way to tell a real 0 from a not-yet-entered one. NaN + exclusion
    means the totals are honestly labelled "auto lines only" until someone
    fills the red rows in.

    consolidated=True sums the selected facilities together; False keeps them
    separate (call pivot() per facility to get per-facility subtotals).
    """
    lines = result["lines"]
    if lines.empty:
        return pd.DataFrame()
    keys = ["line"] if consolidated else ["facility", "line"]
    wide = lines.pivot_table(index=keys, columns="month", values="amount",
                             aggfunc="sum", fill_value=0.0)
    if not consolidated:
        return wide

    months = list(wide.columns)
    zero = lambda: pd.Series(0.0, index=months)
    nan = lambda: pd.Series(float("nan"), index=months)

    def row(line):
        """Manual lines are always NaN regardless of what the DB holds."""
        if line in MANUAL_LINES:
            return nan()
        return wide.loc[line] if line in wide.index else zero()

    def auto_sum(names):
        vals = [row(n) for n in names if n not in MANUAL_LINES]
        return sum(vals) if vals else zero()

    ordered = [(l, row(l)) for l in REVENUE_LINES]
    total_rev = auto_sum(REVENUE_LINES)
    ordered.append(("Total Revenue", total_rev))

    ordered += [(l, row(l)) for l in DIRECT_LINES]
    total_direct = auto_sum(DIRECT_LINES)
    ordered.append(("Total Direct Cost", total_direct))
    gross = total_rev - total_direct
    ordered.append(("Gross Profit", gross))
    ordered.append(("Gross Profit Margin %",
                    (gross / total_rev.replace(0, pd.NA) * 100).astype(float)))

    ordered += [(l, row(l)) for l in INDIRECT_LINES]
    total_indirect = auto_sum(INDIRECT_LINES)
    ordered.append(("Total Indirect Cost", total_indirect))
    total_cost = total_direct + total_indirect
    ordered.append(("Total Cost", total_cost))
    ebitda = total_rev - total_cost
    ordered.append(("EBITDA", ebitda))
    ordered.append(("EBITDA %",
                    (ebitda / total_rev.replace(0, pd.NA) * 100).astype(float)))

    return pd.DataFrame({name: s for name, s in ordered}).T[months]


def manual_line_hints(result):
    """What the database DOES hold for lines we render as manual.

    Nothing is discarded silently: if the revenue table has BWG rows, they show
    here as a suggestion next to the red row, so whoever fills it in can see
    the figure rather than having it quietly dropped."""
    lines = result["lines"]
    if lines.empty:
        return pd.DataFrame()
    hit = lines[lines["line"].isin(MANUAL_LINES) & (lines["amount"] != 0)]
    if hit.empty:
        return pd.DataFrame()
    return (hit.pivot_table(index="line", columns="month", values="amount",
                            aggfunc="sum", fill_value=0.0))


def style_pnl(pnl_wide):
    """Pandas Styler: manual rows red with FILL MANUALLY, subtotals bold.
    Streamlit renders this directly with st.dataframe / st.table."""
    def fmt(v, line):
        if line in MANUAL_LINES:
            return MANUAL_PLACEHOLDER
        if pd.isna(v):
            return "-"
        if line.endswith("%"):
            return f"{v:,.2f}%"
        return f"{v:,.2f}"

    disp = pnl_wide.copy().astype(object)
    for line in disp.index:
        for col in disp.columns:
            disp.at[line, col] = fmt(pnl_wide.at[line, col], line)

    def row_style(r):
        if r.name in MANUAL_LINES:
            return ["color:#c0392b;font-weight:600"] * len(r)
        if r.name in SUBTOTAL_LINES:
            return ["font-weight:700;background-color:rgba(0,0,0,0.04)"] * len(r)
        return [""] * len(r)

    return disp.style.apply(row_style, axis=1)


def variance(pnl_wide, month_a, month_b, top_n=8):
    """Biggest movers between two months, largest absolute change first --
    the 'why did this change' half of the ask. Ratio rows are skipped."""
    if pnl_wide.empty or month_a not in pnl_wide.columns or month_b not in pnl_wide.columns:
        return pd.DataFrame()
    # Subtotals and ratios are excluded: "Total Cost moved" is a restatement
    # of the question, not an answer to it. Only real lines explain a change.
    skip = {"Gross Profit Margin %", "EBITDA %", "Total Revenue",
            "Total Direct Cost", "Total Indirect Cost", "Total Cost",
            "Gross Profit", "EBITDA"}
    rows = []
    for line in pnl_wide.index:
        if line in skip:
            continue
        a = float(pnl_wide.at[line, month_a] or 0)
        b = float(pnl_wide.at[line, month_b] or 0)
        if a == 0 and b == 0:
            continue
        rows.append({"line": line, month_a: a, month_b: b, "change": b - a,
                     "change_%": ((b - a) / abs(a) * 100) if a else None})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.reindex(df["change"].abs().sort_values(ascending=False).index).head(top_n).reset_index(drop=True)


def describe_assumptions(result):
    return pd.DataFrame(result["assumptions"], columns=["P&L line", "Source", "Note"])
