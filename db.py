"""Database layer: connection, query execution, filter injection, and a basic
safety guardrail for AI-generated SQL. BigQuery version — replaces the
Postgres/Supabase db.py.

The public interface (inject_filters, inject_vendor_filter, is_select_only)
is unchanged from the Postgres version — those are plain string operations,
dialect-agnostic. run_query()'s signature changes (see below), which means
every call site in app.py, reports.py, and ghg.py needs updating — that's
tracked separately, not done in this file.

What's different from the Postgres version, and why:
  - No TEXT_NUMERIC_COLS / sanitize_sql column-cast workaround. Supabase had
    several money/quantity columns stored as TEXT; BigQuery loads these as
    real NUMERIC/FLOAT types from the start now (see sync_to_bigquery.py),
    so there's nothing to cast around at query time.
  - No _fix_round_numeric_cast workaround either — that existed because
    Postgres's two-argument ROUND() only accepts `numeric`, never `double
    precision`. BigQuery's ROUND(FLOAT64, INT64) has no such restriction.
  - Every query is run through translate_to_bigquery() (see translate.py)
    before execution — this is what replaces sanitize_sql()'s role.
    QUERY_LIBRARY's SQL text in queries.py is UNCHANGED (still written in
    Postgres-flavored syntax); this module translates it to BigQuery's
    dialect at execution time, for both QUERY_LIBRARY's static queries and
    any AI-generated ad-hoc SQL from the chat box.
  - Bare table names in queries.py ("FROM inward") resolve automatically via
    QueryJobConfig's default_dataset, rather than needing every query
    rewritten to a fully-qualified `project.dataset.table` path.
"""
import streamlit as st
import pandas as pd
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from translate import translate_to_bigquery

FORBIDDEN_KEYWORDS = [
    "drop ", "delete ", "update ", "insert ", "alter ", "truncate ",
    "grant ", "revoke ", "create ", "attach ", "copy ", "merge ",
]

BQ_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@st.cache_resource
def get_client(project_id: str, dataset_id: str, creds_path: str = None, _creds_dict: dict = None):
    """Returns (client, dataset_ref). creds_path is a local file path (used
    for local dev); _creds_dict is the parsed credentials dict (used for
    Streamlit Cloud secrets / GitHub Actions env var) — leading underscore
    tells Streamlit not to hash it for the cache key, since a dict isn't a
    stable cache key and there's only ever one project/dataset in play."""
    if _creds_dict:
        creds = Credentials.from_service_account_info(_creds_dict, scopes=BQ_SCOPES)
    elif creds_path:
        creds = Credentials.from_service_account_file(creds_path, scopes=BQ_SCOPES)
    else:
        raise ValueError("No BigQuery credentials provided (need creds_path or _creds_dict).")
    client = bigquery.Client(project=project_id, credentials=creds)
    dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
    return client, dataset_ref


def is_select_only(sql: str) -> bool:
    """Reject anything that isn't a read-only SELECT/WITH statement.
    'merge ' added to the forbidden list vs the Postgres version — BigQuery
    supports MERGE as a DML statement, which the Postgres-only list never
    needed to guard against."""
    cleaned = sql.strip().rstrip(";").strip()
    lowered = f" {cleaned.lower()} "
    if not (cleaned.lower().startswith("select") or cleaned.lower().startswith("with")):
        return False
    return not any(kw in lowered for kw in FORBIDDEN_KEYWORDS)


@st.cache_data(ttl=60, show_spinner=False)
def _run_query_cached(_client, dataset_project: str, dataset_id: str, sql: str):
    """Cached wrapper, same 60s TTL rationale as the Postgres version.
    Keyed on the translated SQL text plus the dataset identity — leading
    underscore on _client tells Streamlit not to hash the client object."""
    job_config = bigquery.QueryJobConfig(
        default_dataset=bigquery.DatasetReference(dataset_project, dataset_id)
    )
    query_job = _client.query(sql, job_config=job_config)
    return query_job.to_dataframe()


def run_query(sql: str, client_and_dataset) -> tuple:
    """Runs a query. `client_and_dataset` is the (client, dataset_ref) tuple
    from get_client() — every call site needs to pass this instead of the
    old Postgres connection-string argument."""
    client, dataset_ref = client_and_dataset
    if not is_select_only(sql):
        return None, "Blocked: only read-only SELECT/WITH queries are allowed."
    try:
        translated = translate_to_bigquery(sql)
    except Exception as e:
        return None, f"SQL translation error: {e}"
    try:
        df = _run_query_cached(client, dataset_ref.project, dataset_ref.dataset_id, translated)
        return df, None
    except Exception as e:
        return None, str(e)


def inject_filters(sql: str, facilities, date_from: str, date_to: str) -> str:
    """Fills in the {FACILITY_FILTER} / {AND_FACILITY_FILTER} placeholders
    used throughout QUERY_LIBRARY. Unchanged from the Postgres version —
    plain text substitution, dialect-agnostic. The date::date cast here gets
    handled by translate_to_bigquery() later in the pipeline the same way as
    every other ::date cast already present in QUERY_LIBRARY's own SQL text,
    so there's no need to special-case it away here."""
    if isinstance(facilities, str):
        facilities = [facilities]
    date_clause = f"date::date BETWEEN '{date_from}' AND '{date_to}'"
    if not facilities or facilities == ["All Facilities"]:
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE {date_clause}")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND {date_clause}")
    else:
        safe = [f.replace("'", "''") for f in facilities]
        in_list = ", ".join(f"'{f}'" for f in safe)
        facility_clause = f"facility IN ({in_list})"
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE {facility_clause} AND {date_clause}")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND {facility_clause} AND {date_clause}")
    return sql


def inject_vendor_filter(sql: str, vendor: str) -> str:
    """Unchanged from the Postgres version — pure string substitution."""
    if not vendor:
        return sql.replace("{AND_VENDOR_FILTER}", "")
    safe_vendor = vendor.replace("'", "''")
    return sql.replace("{AND_VENDOR_FILTER}", f"AND received_material_from = '{safe_vendor}'")
