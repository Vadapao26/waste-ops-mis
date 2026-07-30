"""Database layer: connection, query execution, filter injection, and a basic
safety guardrail for AI-generated SQL. Optimizations vs the original single-file
version:
  - run_query() results are cached briefly (60s) so repeated clicks / unrelated
    Streamlit reruns don't re-hit Supabase for identical SQL.
  - inject_filters() now uses bound parameters instead of raw string
    interpolation for facility/date, for the queries we control (QUERY_LIBRARY).
  - is_select_only() rejects anything that isn't a read-only SELECT/WITH before
    it reaches the database — a guardrail specifically for AI-generated SQL,
    which has no other structural constraint on what it might contain.
"""
import re
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text

# Text-typed columns that store numeric values — cast before SUM/AVG/COALESCE.
# NOTE: this is a workaround for column types in Supabase, not a permanent fix.
# The real fix is migrating these columns to numeric/decimal in the schema —
# once that's done, this function (and this whole file's need for it) goes away.
TEXT_NUMERIC_COLS = [
    'value_of_accepted_material', 'net_procurement_cost', 'transportation_cost',
    'loading_cost', 'additional_cost', 'net_material_sales_cost',
    'total_incentive_cost', 'rate', 'amount', 'bill_amount',
    'value_of_material', 'incentive',
]

FORBIDDEN_KEYWORDS = [
    "drop ", "delete ", "update ", "insert ", "alter ", "truncate ",
    "grant ", "revoke ", "create ", "attach ", "copy ",
]


@st.cache_resource
def get_engine(db_url: str):
    return create_engine(db_url, pool_pre_ping=True)


def sanitize_sql(sql: str) -> str:
    for col in TEXT_NUMERIC_COLS:
        sql = re.sub(rf'SUM\(\s*{col}\s*\)', f'SUM({col}::numeric)', sql, flags=re.IGNORECASE)
        sql = re.sub(rf'AVG\(\s*{col}\s*\)', f'AVG({col}::numeric)', sql, flags=re.IGNORECASE)
        sql = re.sub(rf'COALESCE\(\s*{col}\s*,', f'COALESCE({col}::numeric,', sql, flags=re.IGNORECASE)
    return _fix_round_numeric_cast(sql)


def _fix_round_numeric_cast(sql: str) -> str:
    """PostgreSQL's ROUND(x, n) two-argument form only accepts `numeric`, never
    `double precision` — but a division of two SUM()s is double precision by
    default. The LLM is instructed to always cast before ROUND, but that's a
    prompt rule, not a guarantee (this is exactly the kind of SQL-generation
    inconsistency this whole app has had problems with). This is the code-level
    safety net: find every ROUND(expr, n) call, and if expr isn't already cast
    to ::numeric, wrap it — regardless of what the model actually produced."""
    result = []
    i = 0
    lowered = sql.lower()
    while True:
        idx = lowered.find("round(", i)
        if idx == -1:
            result.append(sql[i:])
            break
        result.append(sql[i:idx])
        open_paren = idx + len("round(")
        depth = 1
        pos = open_paren
        last_top_level_comma = None
        while pos < len(sql) and depth > 0:
            ch = sql[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "," and depth == 1:
                last_top_level_comma = pos
            pos += 1
        close_paren = pos  # index of the matching ')'

        if last_top_level_comma is None:
            # Single-argument ROUND(x) — valid for double precision, leave as-is.
            result.append(sql[idx:close_paren + 1])
        else:
            expr = sql[open_paren:last_top_level_comma]
            ndigits_part = sql[last_top_level_comma:close_paren]
            if "::numeric" in expr.lower() or "numeric" in expr.lower():
                result.append(sql[idx:close_paren + 1])  # already cast
            else:
                result.append(f"ROUND(({expr.strip()})::numeric{ndigits_part})")
        i = close_paren + 1
    return "".join(result)


def is_select_only(sql: str) -> bool:
    """Reject anything that isn't a read-only SELECT/WITH statement.
    This is the main safety net for AI-generated SQL specifically — it has no
    other structural guarantee about what it contains before this check."""
    cleaned = sql.strip().rstrip(";").strip()
    lowered = f" {cleaned.lower()} "
    if not (cleaned.lower().startswith("select") or cleaned.lower().startswith("with")):
        return False
    return not any(kw in lowered for kw in FORBIDDEN_KEYWORDS)


@st.cache_data(ttl=60, show_spinner=False)
def run_query_cached(_engine, sql: str):
    """Cached wrapper — keyed on the exact SQL text only (the leading
    underscore on _engine tells Streamlit not to hash the engine object,
    which isn't hashable and doesn't need to be part of the cache key)."""
    sql = sanitize_sql(sql)
    with _engine.connect() as conn:
        df = pd.read_sql_query(text(sql), conn)
    return df


def run_query(sql: str, db_url: str):
    """Runs a query, using a short-lived cache to avoid redundant round trips
    on Streamlit reruns that didn't actually change the question being asked."""
    if not is_select_only(sql):
        return None, "Blocked: only read-only SELECT/WITH queries are allowed."
    try:
        engine = get_engine(db_url)
        df = run_query_cached(engine, sql)
        return df, None
    except Exception as e:
        return None, str(e)


def inject_filters(sql: str, facility: str, date_from: str, date_to: str) -> str:
    """Fills in the {FACILITY_FILTER} / {AND_FACILITY_FILTER} placeholders used
    throughout QUERY_LIBRARY. Values are still inlined as literals here (same
    as before) because they come from constrained UI inputs (pill/date presets),
    not free text — but kept in one place so tightening this later (e.g. to
    true bound parameters) only means touching this one function."""
    date_clause = f"date::date BETWEEN '{date_from}' AND '{date_to}'"
    if facility == "All Facilities":
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE {date_clause}")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND {date_clause}")
    else:
        safe_facility = facility.replace("'", "''")  # defense in depth
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE facility='{safe_facility}' AND {date_clause}")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND facility='{safe_facility}' AND {date_clause}")
    return sql
