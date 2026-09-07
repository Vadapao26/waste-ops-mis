"""Postgres -> BigQuery Standard SQL translation layer.

Design rationale: rather than hand-editing all 40 queries in QUERY_LIBRARY
(high risk — 40 chances to introduce a bug into already-tested SQL), this
translates the handful of Postgres-specific syntax PATTERNS these queries
actually use, at query-execution time. queries.py stays byte-for-byte
unchanged. This also means AI-generated ad-hoc SQL (still written to target
Postgres per llm.py's existing prompts) gets translated the same way,
without needing to rewrite the LLM prompts either.

Patterns handled, in order:
  1. TO_CHAR(expr, 'YYYY-MM')        -> FORMAT_DATE('%Y-%m', expr)
  2. expr::numeric                    -> SAFE_CAST(expr AS FLOAT64)
  3. expr::date                       -> SAFE_CAST(expr AS DATE)
  4. expr::text                       -> CAST(expr AS STRING)
  5. bare `::` cast catch-all         -> (none expected after 2-4; asserted)

Note on FLOAT64 vs BigQuery's NUMERIC type: FLOAT64 is used deliberately —
it's the closer semantic match to Postgres's untyped numeric division
results, and BigQuery's ROUND(FLOAT64, INT64) has no equivalent restriction
to Postgres's numeric-only ROUND, so none of db.py's _fix_round_numeric_cast
workaround is needed at all in BigQuery.
"""
import re


def _translate_one_cast(sql: str, pg_type: str, bq_expr_fn) -> str | None:
    """Finds and replaces the LEFTMOST `<expr>::pg_type` occurrence only,
    returning the updated string, or None if no occurrence exists. For
    same-type nested casts (e.g. ROUND((SUM(x::numeric))::numeric,2)), the
    leftmost occurrence of a given marker is always the innermost one — the
    inner cast's marker necessarily appears before the outer cast's marker
    in left-to-right text order, since the outer marker only appears after
    everything inside it, including the inner marker. Processing exactly one
    occurrence per call and restarting the scan on the fully-updated string
    each time (see translate_to_bigquery's loop) is what makes nesting work
    correctly — a single pass over multiple occurrences was tried first and
    produces genuinely malformed output when one occurrence's expression
    boundary extends backward past an already-processed position."""
    marker = f"::{pg_type}"
    idx = sql.find(marker)
    if idx == -1:
        return None
    j = idx
    if j > 0 and sql[j - 1] == ")":
        depth = 1
        k = j - 2
        while k >= 0 and depth > 0:
            if sql[k] == ")":
                depth += 1
            elif sql[k] == "(":
                depth -= 1
            k -= 1
        expr_start = k + 1
        # The paren group alone isn't the whole expression when it's a
        # function call — NULLIF(x,'')::numeric, SUM(x)::numeric, etc.
        # Extend further back through any identifier immediately preceding
        # the opening paren, so the function name is included too.
        m = expr_start - 1
        while m >= 0 and (sql[m].isalnum() or sql[m] == "_"):
            m -= 1
        expr_start = m + 1
    else:
        k = j - 1
        while k >= 0 and (sql[k].isalnum() or sql[k] in "_."):
            k -= 1
        expr_start = k + 1
    expr = sql[expr_start:idx]
    return sql[:expr_start] + bq_expr_fn(expr) + sql[idx + len(marker):]


def translate_to_bigquery(sql: str) -> str:
    # 1. TO_CHAR(expr, 'YYYY-MM') -> FORMAT_DATE('%Y-%m', expr)
    #    (only the YYYY-MM pattern is actually used anywhere in QUERY_LIBRARY —
    #    verified by grep before writing this — so no need for a generic
    #    Postgres-format-string translator.)
    def to_char_repl(m):
        expr = m.group(1).strip()
        return f"FORMAT_DATE('%Y-%m', {expr})"
    sql = re.sub(r"TO_CHAR\(\s*(.*?)\s*,\s*'YYYY-MM'\s*\)", to_char_repl, sql, flags=re.IGNORECASE | re.DOTALL)

    # 2-4. Type casts — one occurrence at a time, restarting on the updated
    # string each call (see _translate_one_cast's docstring for why this is
    # what makes nested same-type casts resolve correctly).
    for pg_type, bq_expr_fn in [
        ("numeric", lambda e: f"SAFE_CAST({e} AS FLOAT64)"),
        ("date", lambda e: f"SAFE_CAST({e} AS DATE)"),
        ("text", lambda e: f"CAST({e} AS STRING)"),
    ]:
        while True:
            updated = _translate_one_cast(sql, pg_type, bq_expr_fn)
            if updated is None:
                break
            sql = updated

    # Safety net: any remaining `::` means a cast pattern wasn't handled above —
    # fail loudly rather than send invalid syntax to BigQuery silently.
    if "::" in sql:
        remaining = re.findall(r".{0,20}::\w+", sql)
        raise ValueError(f"Untranslated Postgres cast(s) found: {remaining}")

    return sql
