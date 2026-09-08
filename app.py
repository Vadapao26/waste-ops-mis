"""Waste Ops MIS — main app.
Architecture: this file only orchestrates. Business logic lives in:
  db.py        — Supabase connection, query execution, SQL safety guardrail
  llm.py       — Groq client, prompt construction, schema/domain context
  queries.py   — the preset query library and facility/analysis constants
  results.py   — table formatting, KPI cards, auto-charting
  nlp_dates.py — relative date phrase parsing for free-text questions
  reports.py   — one-click PDF report generation
  theme.py     — design tokens, global CSS, login page, top-right avatar
"""
import calendar
import random
from datetime import date, timedelta

import bcrypt
import pandas as pd
import streamlit as st

import theme
import db
import llm
import results
import nlp_dates
import reports
import ghg
from queries import FACILITIES, MONTHS_FULL, MONTH_NUM, QUERY_LIBRARY, SIDEBAR_GROUPS, ANALYSIS_TYPE_COMBINED

st.set_page_config(page_title="Waste Ops MIS", layout="wide", initial_sidebar_state="expanded")
theme.inject_global_css()

BUILD_TAG = "2026-09-08-fix-use-container-width-deprecation"  # bump this string every time files are handed off

# ── AUTH ────────────────────────────────────────────────────────────────────
def check_password(username, password):
    users = st.secrets.get("credentials", {}).get("usernames", {})
    if username not in users:
        return False
    stored_hash = users[username].get("password", "")
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


def get_user_info(username):
    return dict(st.secrets["credentials"]["usernames"][username])


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.username = None

if not st.session_state.authenticated:
    if theme.render_login_page(check_password):
        st.rerun()
    st.stop()

username = st.session_state.username
user_info = get_user_info(username)
user_role = user_info["role"]
user_facility = user_info["facility"]
user_name = user_info["name"]

# BigQuery project/dataset + credentials, all from st.secrets — same
# consistent pattern already used for the Groq API key below. app.py
# always runs via Streamlit, so st.secrets is always populated —
# locally from .streamlit/secrets.toml, or on Streamlit Cloud/Cloud Run
# from wherever secrets are provisioned there.
BQ_PROJECT_ID = st.secrets["bigquery"]["project_id"]
BQ_DATASET = st.secrets["bigquery"]["dataset"]
BQ_CLIENT_AND_DATASET = db.get_client(BQ_PROJECT_ID, BQ_DATASET, _creds_dict=dict(st.secrets["bigquery"]["credentials"]))
GROQ_API_KEY = st.secrets["groq"]["api_key"]
groq_client = llm.get_groq_client(GROQ_API_KEY)


def _logout():
    st.session_state.authenticated = False
    st.session_state.username = None
    st.rerun()


# ── SESSION STATE ───────────────────────────────────────────────────────────
HISTORY_CAP = 50
defaults = {
    "messages": [], "conversation_history": [], "result_history": [],
    "active_clarifications": None, "clarification_question": None,
    "clarification_date_from": None, "clarification_date_to": None,
    "explorations": None, "_results": None, "_results_label": None, "_ghg_result": None, "_chat_open": False,
    "_kpi_prev": {},
    "ctx_sidebar_expanded": None,
    "ctx_locked": False, "ctx_edit_step": None, "ctx_analysis_type": None,
    "ctx_facilities": [], "ctx_timeframe_label": None,
    "ctx_date_from": None, "ctx_date_to": None, "ctx_num_months": 1,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _cap(key):
    if len(st.session_state[key]) > HISTORY_CAP:
        st.session_state[key] = st.session_state[key][-HISTORY_CAP:]


# ── ANALYSIS TYPE MAP ─────────────────────────────────────────────────────────
ANALYSIS_TYPES = {name: keys for name, keys in SIDEBAR_GROUPS.items()}
ANALYSIS_TYPES["Custom AI Query"] = None
if not st.session_state.ctx_analysis_type:
    st.session_state.ctx_analysis_type = list(ANALYSIS_TYPES.keys())[0]
TIMEFRAME_PRESETS = {"Last 7 days": 7, "Last 30 days": 30, "Last 3 months": 90, "Last 6 months": 180, "Last 12 months": 365}
MONTH_PH, YEAR_PH = "— Select —", "— Year —"
TODAY = date.today()
YEARS = list(range(2023, TODAY.year + 2))

# Default straight into an analytics view on first-ever load, rather than
# forcing facility/timeframe selection before showing anything — insight-first,
# not configuration-first. A user who's never touched the context (ctx_date_from
# still empty) gets sensible defaults; anything they've actually picked is left alone.
if not st.session_state.ctx_date_from:
    st.session_state.ctx_facilities = [user_facility] if user_role == "manager" else ["All Facilities"]
    default_days = TIMEFRAME_PRESETS["Last 30 days"]
    st.session_state.ctx_date_from = str(TODAY - timedelta(days=default_days))
    st.session_state.ctx_date_to = str(TODAY)
    st.session_state.ctx_timeframe_label = "Last 30 days"
    st.session_state.ctx_num_months = 1
    st.session_state.ctx_locked = True

# ── CONTEXT DERIVATION (single source of truth, defined before ANY rendering) ─
# selected_facility is now a LIST (multi-select) — db.inject_filters/reports
# functions take lists directly. selected_facility_display is the human-
# readable string used everywhere else (labels, filenames, chat text).
# IMPORTANT: this must run AFTER the auto-default block above, not before —
# on a first-ever session, ctx_date_from/ctx_facilities/etc. get set to real
# values inside that block during THIS SAME render. Reading them into plain
# variables earlier than that captured stale empty strings on that first
# render specifically, which was invisible everywhere else in the app (every
# other analysis view only runs after an explicit button click, which forces
# a fresh rerun where session state is already correct) but broke the
# facility-comparison view outright, since that one queries automatically
# without waiting for a click.
selected_facility = st.session_state.ctx_facilities or ["All Facilities"]
facility_selected = bool(st.session_state.ctx_facilities) and st.session_state.ctx_locked
if selected_facility == ["All Facilities"]:
    selected_facility_display = "All Facilities"
elif len(selected_facility) <= 2:
    selected_facility_display = ", ".join(selected_facility)
else:
    selected_facility_display = f"{len(selected_facility)} facilities"
date_from = st.session_state.ctx_date_from or ""
date_to = st.session_state.ctx_date_to or ""
display_time = st.session_state.ctx_timeframe_label or ""
time_selected = bool(date_from and date_to) and st.session_state.ctx_locked
num_months = st.session_state.ctx_num_months


def reset_context():
    for k in ["ctx_locked", "ctx_edit_step", "ctx_facilities",
              "ctx_timeframe_label", "ctx_date_from", "ctx_date_to"]:
        st.session_state[k] = defaults[k]
    st.session_state.ctx_num_months = 1
    for k in ["active_clarifications", "explorations", "_results", "_ghg_result"]:
        st.session_state[k] = None


# ── Facility icon mapping — standard processing facilities get a building
# icon; the two trading/transfer entries (not physical processing sites)
# get a distinct icon so their different nature is visible at a glance. ────
_FACILITY_ICONS = {
    "All Facilities": "🏭",
    "Trading Data RPG/External Transfers- SCM (MRF)": "🔄",
    "Interim PRF (Sarvam Jigani)": "🔄",
}


def _facility_card_grid(options, selected_check, on_click, all_facilities_tile=None, n_cols=4):
    """M3 selectable-card grid for facility picking. True st.columns grid
    (uniform-width cards) rather than flex-wrap chips. Selected cards get
    the M3 primary fill plus a leading checkmark; unselected cards stay on
    surface-container-low. Icon is a plain-text emoji prefix in the button
    label, since Streamlit buttons don't render arbitrary HTML."""
    all_options = list(options)
    if all_facilities_tile is not None:
        all_options = [all_facilities_tile] + all_options
    with st.container(key="facility_card_grid"):
        rows = [all_options[i:i + n_cols] for i in range(0, len(all_options), n_cols)]
        for row in rows:
            cols = st.columns(n_cols)
            for col, opt in zip(cols, row):
                with col:
                    icon = _FACILITY_ICONS.get(opt, "🏢")
                    is_sel = selected_check(opt)
                    label = f"✓ {icon}  {opt}" if is_sel else f"{icon}  {opt}"
                    if st.button(label, key=f"fac_card_{opt}", width='stretch',
                                 type="primary" if is_sel else "secondary"):
                        on_click(opt)
                        st.rerun()


def _timeframe_segmented_bar(options, selected_check, on_click):
    """M3 segmented-button bar for timeframe picking — a single connected
    row (shared border, only end buttons rounded) rather than separate
    pill chips. Visual connection is done in CSS (see .st-key-timeframe_
    segmented_bar in theme.py); this just renders plain buttons in a row
    with the right container key for that CSS to target."""
    with st.container(key="timeframe_segmented_bar"):
        cols = st.columns(len(options))
        for col, opt in zip(cols, options):
            with col:
                is_sel = selected_check(opt)
                if st.button(opt, key=f"tf_seg_{opt}", width='stretch',
                             type="primary" if is_sel else "secondary"):
                    on_click(opt)
                    st.rerun()


def render_context_builder():
    edit_step = st.session_state.ctx_edit_step
    locked = st.session_state.ctx_locked
    show_facility = (not locked) or edit_step == "facility"
    show_time = (not locked) or edit_step == "time"

    if show_facility:
        st.markdown("**1 · Which facility?** *(pick one or more)*")
        if user_role == "manager":
            fac_options = [user_facility]
            all_tile = None
        else:
            fac_options = [f for f in FACILITIES if f != "All Facilities"]
            all_tile = "All Facilities"

        def _facility_click(opt):
            current = list(st.session_state.ctx_facilities)
            if opt == "All Facilities":
                st.session_state.ctx_facilities = ["All Facilities"] if current != ["All Facilities"] else []
            else:
                if "All Facilities" in current:
                    current = []
                if opt in current:
                    current.remove(opt)
                else:
                    current.append(opt)
                st.session_state.ctx_facilities = current

        def _facility_selected(opt):
            return opt in st.session_state.ctx_facilities
        _facility_card_grid(fac_options, _facility_selected, _facility_click, all_facilities_tile=all_tile)

    if show_time:
        st.markdown("**2 · Timeframe**")
        tf_options = list(TIMEFRAME_PRESETS.keys())

        def _tf_click(opt):
            days = TIMEFRAME_PRESETS[opt]
            st.session_state.ctx_date_from = str(TODAY - timedelta(days=days))
            st.session_state.ctx_date_to = str(TODAY)
            st.session_state.ctx_timeframe_label = opt
            st.session_state.ctx_num_months = max(1, round(days / 30))
            st.session_state["_show_custom_range"] = False

        def _tf_selected(opt):
            return st.session_state.ctx_timeframe_label == opt and not st.session_state.get("_show_custom_range")
        _timeframe_segmented_bar(tf_options, _tf_selected, _tf_click)

        custom_active = st.session_state.get("_show_custom_range", False)
        with st.container(key="tile_custom_range"):
            if st.button("Custom range" + (" ✓" if custom_active else ""), key="custom_range_toggle",
                         type="primary" if custom_active else "secondary"):
                st.session_state["_show_custom_range"] = not custom_active
                st.rerun()

        if st.session_state.get("_show_custom_range"):
            c1, c2 = st.columns(2)
            with c1:
                from_date = st.date_input("From", key="cr_from_date", value=None,
                                           min_value=date(2023, 1, 1), max_value=TODAY)
            with c2:
                to_date = st.date_input("To", key="cr_to_date", value=None,
                                         min_value=date(2023, 1, 1), max_value=TODAY)
            if from_date and to_date and from_date <= to_date:
                st.session_state.ctx_date_from = str(from_date)
                st.session_state.ctx_date_to = str(to_date)
                st.session_state.ctx_num_months = max(1, (to_date.year - from_date.year) * 12 + (to_date.month - from_date.month) + 1)
                st.session_state.ctx_timeframe_label = f"{from_date.strftime('%d %b %Y')} – {to_date.strftime('%d %b %Y')}"
                st.caption(f"{st.session_state.ctx_date_from} to {st.session_state.ctx_date_to}")
            elif from_date and to_date and from_date > to_date:
                st.caption("⚠️ 'From' date must be before 'To' date.")

    ready = bool(st.session_state.ctx_analysis_type and st.session_state.ctx_facilities and st.session_state.ctx_date_from)
    with st.container(key="run_analysis_btn"):
        if st.button("Update selection →" if locked else "Start cooking stats →", disabled=not ready, key="lock_ctx_btn"):
            st.session_state.ctx_locked = True
            st.session_state.ctx_edit_step = None
            st.rerun()
    if locked and edit_step and st.button("Cancel", key="cancel_edit_ctx"):
        st.session_state.ctx_edit_step = None
        st.rerun()


TYPE_ICONS = {
    "Inward Analytics": "📥", "Production Analytics": "⚙️", "Outward Analytics": "📤",
    "Transport Analytics": "🚚", "ULB Analytics": "🏛️",
    "Training Analytics": "🎓", "Environmental Impact": "🌱", "Supply Chain Analytics": "🔗",
    "Custom AI Query": "💬",
}
# Groups by real operational stage rather than one flat list: the material's
# actual path through a facility, then the vendor/citizen programs layered on
# top of it, then cross-cutting tools. Falls back gracefully — any type not
# listed here just won't be grouped (shouldn't happen, but never hides a type).
SIDEBAR_GROUP_ORDER = [
    ("Material Flow", ["Inward Analytics", "Production Analytics", "Outward Analytics", "Transport Analytics"]),
    ("Programs", ["ULB Analytics", "Training Analytics"]),
    ("Impact & Tools", ["Environmental Impact", "Supply Chain Analytics", "Custom AI Query"]),
]


def _default_sub_key(opt):
    """The natural landing view for a type when it's first selected — prefer
    a 'kpi summary' entry if one exists, otherwise the first preset in the
    list. Returns None for types with no presets (e.g. Custom AI Query)."""
    keys = ANALYSIS_TYPES.get(opt) or []
    if not keys:
        return None
    return next((k for k in keys if "kpi summary" in k), keys[0])


def render_analysis_sidebar():
    """App-drawer tile grid — replaces the icon-rail + panel layout from the
    previous pass, which read as confusing in practice. Top-level categories
    render as a 3-column grid of icon tiles; the active category's
    sub-items list directly below the grid (not in a separate side panel).
    Same click/state logic as before — clicking a tile switches the active
    analysis type and lands on its default sub-view, or shows the full
    sub-item list below if it's already active."""
    st.sidebar.markdown("### 🗑️ Wise Waste")
    st.sidebar.caption("Analytics")
    grouped_types = {t for _, types in SIDEBAR_GROUP_ORDER for t in types}
    ungrouped = [t for t in ANALYSIS_TYPES.keys() if t not in grouped_types]
    all_groups = SIDEBAR_GROUP_ORDER + ([("Other", ungrouped)] if ungrouped else [])
    all_types_in_order = [t for _, types in all_groups for t in types]

    with st.sidebar.container(key="nav_tile_grid"):
        n_cols = 3
        rows = [all_types_in_order[i:i + n_cols] for i in range(0, len(all_types_in_order), n_cols)]
        for row in rows:
            cols = st.columns(n_cols)
            for col, opt in zip(cols, row):
                with col:
                    is_active = st.session_state.ctx_analysis_type == opt
                    icon = TYPE_ICONS.get(opt, "•")
                    short_label = opt.replace(" Analytics", "").replace(" Impact", "")
                    label = f"{icon}\n{short_label}"
                    if st.button(label, key=f"tile_type_{opt}", width='stretch',
                                 type="primary" if is_active else "secondary"):
                        if opt != st.session_state.ctx_analysis_type:
                            st.session_state.ctx_analysis_type = opt
                            st.session_state["_ghg_result"] = None
                            st.session_state["active_clarifications"] = None
                            st.session_state["explorations"] = None
                            default_key = _default_sub_key(opt)
                            if default_key:
                                st.session_state["_action"] = {"type": "library", "key": default_key, "is_kpi": "kpi" in default_key}
                            else:
                                st.session_state["_results"] = None
                        st.rerun()

    with st.sidebar.container(key="nav_sub_panel"):
        opt = st.session_state.ctx_analysis_type
        if opt and (ANALYSIS_TYPES.get(opt) or ANALYSIS_TYPE_COMBINED.get(opt)):
            icon = TYPE_ICONS.get(opt, "")
            st.markdown(f'<div class="nav-panel-title">{icon}&nbsp;&nbsp;{opt}</div>', unsafe_allow_html=True)
            combined_keys = ANALYSIS_TYPE_COMBINED.get(opt)
            if combined_keys:
                with st.container(key=f"sidebar_sub_wrap_{opt}_all"):
                    if st.button(f"All {opt.split(' ')[0]}", key=f"sidebar_sub_{opt}_all", width='stretch'):
                        st.session_state["_action"] = {
                            "type": "combined", "keys": combined_keys,
                            "label": f"{opt} Full Analysis | {selected_facility_display} | {st.session_state.ctx_timeframe_label}"
                        }
                        st.session_state["active_clarifications"] = None
                        st.session_state["explorations"] = None
                        st.rerun()
            for key in ANALYSIS_TYPES.get(opt) or []:
                is_ghg_transport = key == "impact: transport ghg emissions"
                sub_label = "🚚 Transport GHG Emissions" if is_ghg_transport else (
                    key.split(": ")[1].title() if ": " in key else key.title())
                with st.container(key=f"sidebar_sub_wrap_{opt}_{key}"):
                    if st.button(sub_label, key=f"sidebar_sub_{opt}_{key}", width='stretch'):
                        if is_ghg_transport:
                            st.session_state["_action"] = {"type": "ghg_transport"}
                        else:
                            st.session_state["_action"] = {"type": "library", "key": key, "is_kpi": "kpi" in key}
                        st.session_state["active_clarifications"] = None
                        st.session_state["explorations"] = None
                        st.rerun()
        else:
            icon = TYPE_ICONS.get(opt, "💬")
            st.markdown(f'<div class="nav-panel-title">{icon}&nbsp;&nbsp;{opt}</div>', unsafe_allow_html=True)
            st.caption("Ask your question in the chat box below.")

    # Visible build marker — bump BUILD_TAG whenever files are handed off, so
    # a glance at the sidebar footer proves which version is actually running
    # instead of guessing after a copy/restart.
    st.sidebar.markdown(
        f'<div style="position:fixed;bottom:10px;left:14px;font-size:10px;'
        f'color:{theme.PALETTE["muted"]};">build {BUILD_TAG}</div>', unsafe_allow_html=True)

def render_context_bar():
    c1, c2, c3, _spacer = st.columns([1.6, 1.6, 0.7, 4])
    with c1:
        if st.button(f"📍  {selected_facility_display}  ✎", key="edit_facility", width='stretch',
                     help="Change facility"):
            st.session_state.ctx_edit_step = "facility"; st.rerun()
    with c2:
        if st.button(f"🗓️  {st.session_state.ctx_timeframe_label}  ✎", key="edit_time", width='stretch',
                     help="Change timeframe"):
            st.session_state.ctx_edit_step = "time"; st.rerun()
    with c3:
        if st.button("Reset", key="reset_ctx_btn", width='stretch', help="Reset facility and timeframe"):
            reset_context(); st.rerun()
    st.markdown(
        f'<p class="ctx-caption">Every question below applies to '
        f'<b>{selected_facility_display} · {st.session_state.ctx_timeframe_label}</b> '
        f'until you edit a selection above.</p>', unsafe_allow_html=True)


def render_type_presets():
    at = st.session_state.ctx_analysis_type
    if not at or at == "Custom AI Query":
        st.caption("Ask your question about this facility and timeframe in the chat box below.")
        return
    keys = ANALYSIS_TYPES.get(at) or []
    if not keys:
        return


# ── ACTION DISPATCHER HELPERS ─────────────────────────────────────────────────
def run_and_show_combined(keys, label):
    """Runs a set of preset queries. Only the resulting analysis panels are
    shown — no 'Loaded N analyses' bookkeeping message is added to the chat,
    since the section headings and context bar already say what's showing."""
    result_rows = []
    for key in keys:
        sql = db.inject_filters(QUERY_LIBRARY[key].strip(), selected_facility, date_from, date_to)
        df, error = db.run_query(sql, BQ_CLIENT_AND_DATASET)
        if error:
            st.error(f"Query failed for {key}: {error}")
        elif df is not None:
            if key in PIVOT_MATERIAL_PRESETS:
                df = results.pivot_material_breakdown(df, id_cols=PIVOT_MATERIAL_PRESETS[key])
            result_rows.append((key, sql, df.to_dict("records"), df.columns.tolist(), "kpi" in key))
    if result_rows:
        st.session_state["_results"] = result_rows
        st.session_state["_results_label"] = label


PIVOT_MATERIAL_PRESETS = {
    "inward: vendor material analytics": ["facility", "vendor", "location"],
    "outward: customer material analytics": ["facility", "customer", "destination"],
    "production: process material analytics": ["facility", "process_equipment"],
}


def _prior_period(d_from: str, d_to: str):
    """Same-length window immediately preceding [d_from, d_to] — used for the
    KPI trend arrows ('vs prior period'). Pure date math, no query involved."""
    start = date.fromisoformat(d_from)
    end = date.fromisoformat(d_to)
    span = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=span - 1)
    return str(prior_start), str(prior_end)


def run_and_show_single(lib_key, is_kpi):
    sql = db.inject_filters(QUERY_LIBRARY[lib_key].strip(), selected_facility, date_from, date_to)
    df, error = db.run_query(sql, BQ_CLIENT_AND_DATASET)
    if error:
        st.error(f"Error running {lib_key}: {error}")
        st.session_state["_results"] = None
    else:
        if lib_key in PIVOT_MATERIAL_PRESETS:
            df = results.pivot_material_breakdown(df, id_cols=PIVOT_MATERIAL_PRESETS[lib_key])
        st.session_state["_results"] = [(lib_key, sql, df.to_dict("records"), df.columns.tolist(), is_kpi)]
        st.session_state["_results_label"] = f"{lib_key.title()} | {selected_facility_display} | {display_time}"

        # KPI trend comparison — only for single-row KPI results, since that's
        # the only shape render_kpi_cards can meaningfully diff against.
        st.session_state["_kpi_prev"] = {}
        if is_kpi and len(df) == 1:
            prior_from, prior_to = _prior_period(date_from, date_to)
            prev_sql = db.inject_filters(QUERY_LIBRARY[lib_key].strip(), selected_facility, prior_from, prior_to)
            prev_df, prev_error = db.run_query(prev_sql, BQ_CLIENT_AND_DATASET)
            if not prev_error and prev_df is not None and len(prev_df) == 1:
                st.session_state["_kpi_prev"][lib_key] = prev_df.to_dict("records")


# ── PAGE: ANALYTICS ───────────────────────────────────────────────────────────
# ── GHG TRANSPORT RESULT DISPLAY ──────────────────────────────────────────────
def _combine_ghg_results(per_facility_results: list) -> dict:
    """Combines calculate_transport_emissions() results from multiple
    facilities into one result, for when the context is "All Facilities"
    or a multi-facility selection. Each facility's daily_breakdown and
    summary_by_partner get a facility column and are concatenated as-is
    (each is already correctly aggregated within its own facility, so no
    re-aggregation across facilities -- a vendor serving two facilities
    shows as two distinct summary rows, which is more accurate than
    merging them). Totals are summed; notes are combined."""
    daily_frames, summary_frames = [], []
    total_kg_co2e = inward_kg_co2e = outward_kg_co2e = 0.0
    matched_tonnes = excluded_tonnes = 0.0
    excluded_trip_count = 0
    facility_notes = []

    for facility_name, result in per_facility_results:
        totals = result["totals"]
        total_kg_co2e += totals["total_kg_co2e"]
        inward_kg_co2e += totals["inward_kg_co2e"]
        outward_kg_co2e += totals["outward_kg_co2e"]
        matched_tonnes += totals["matched_tonnes"]
        excluded_tonnes += totals["excluded_tonnes"]
        excluded_trip_count += totals["excluded_trip_count"]
        if totals["excluded_trip_count"]:
            facility_notes.append(f"{facility_name}: {totals['note']}")

        if not result["daily_breakdown"].empty:
            df = result["daily_breakdown"].copy()
            df.insert(0, "facility", facility_name)
            daily_frames.append(df)
        if not result["summary_by_partner"].empty:
            sf = result["summary_by_partner"].copy()
            sf.insert(0, "facility", facility_name)
            summary_frames.append(sf)

    daily_breakdown = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    summary_by_partner = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    if not summary_by_partner.empty:
        summary_by_partner = summary_by_partner.sort_values("total_kg_co2e", ascending=False).reset_index(drop=True)

    note = "; ".join(facility_notes) if facility_notes else "All trips matched to a geocoded location."
    return {
        "daily_breakdown": daily_breakdown,
        "summary_by_partner": summary_by_partner,
        "totals": {
            "total_kg_co2e": round(total_kg_co2e, 2),
            "inward_kg_co2e": round(inward_kg_co2e, 2),
            "outward_kg_co2e": round(outward_kg_co2e, 2),
            "matched_tonnes": round(matched_tonnes, 3),
            "excluded_tonnes": round(excluded_tonnes, 3),
            "excluded_trip_count": excluded_trip_count,
            "note": note,
        },
    }


def render_ghg_transport_result(result: dict, display_time: str):
    st.markdown("### Transport GHG Emissions")
    st.caption(
        "Methodology: India-specific road-freight emission factors "
        "(Smart Freight Centre / TCI-IIMB, May 2025). Vehicle size is inferred "
        "per delivery from material category + quantity, using thresholds "
        "confirmed against your own operational data. "
        "This is an internal-tracking estimate, not a certified compliance figure."
    )

    totals = result["totals"]
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total transport emissions", f"{totals['total_kg_co2e']:,.1f} kg CO2e")
    with c2:
        st.metric("Inward", f"{totals['inward_kg_co2e']:,.1f} kg CO2e")
    with c3:
        st.metric("Outward", f"{totals['outward_kg_co2e']:,.1f} kg CO2e")

    if totals["excluded_trip_count"]:
        st.warning(totals["note"])
    else:
        st.success(totals["note"])

    daily = result["daily_breakdown"]
    summary = result["summary_by_partner"]

    st.markdown("---")
    st.markdown("#### 1 · Daily breakdown")
    st.caption("Every real delivery, on its own date — nothing here is summed across days.")
    if daily.empty:
        st.info("No deliveries found for this facility/timeframe.")
    else:
        tab_in, tab_out = st.tabs(["Inward", "Outward"])
        with tab_in:
            inward_daily = daily[daily["direction"] == "inward"].drop(columns=["direction"])
            if inward_daily.empty:
                st.caption("No inward deliveries in this window.")
            else:
                st.dataframe(inward_daily, width='stretch', hide_index=True)
        with tab_out:
            outward_daily = daily[daily["direction"] == "outward"].drop(columns=["direction"])
            if outward_daily.empty:
                st.caption("No outward deliveries in this window.")
            else:
                st.dataframe(outward_daily, width='stretch', hide_index=True)

    st.markdown("---")
    st.markdown("#### 2 · Overall summary, by vendor/customer")
    st.caption("Aggregated on purpose — delivery count shown so it's clear each row can represent multiple trips.")
    if summary.empty:
        st.info("No deliveries found for this facility/timeframe.")
    else:
        tab_in2, tab_out2 = st.tabs(["Inward (by vendor)", "Outward (by customer)"])
        with tab_in2:
            inward_summary = summary[summary["direction"] == "inward"].drop(columns=["direction"])
            if inward_summary.empty:
                st.caption("No inward deliveries in this window.")
            else:
                st.dataframe(inward_summary, width='stretch', hide_index=True)
        with tab_out2:
            outward_summary = summary[summary["direction"] == "outward"].drop(columns=["direction"])
            if outward_summary.empty:
                st.caption("No outward deliveries in this window.")
            else:
                st.dataframe(outward_summary, width='stretch', hide_index=True)


def analytics_page():
    render_analysis_sidebar()
    theme.render_topbar(user_name, user_role, on_logout=_logout,
                         breadcrumb=f"Wise Waste / Analytics / {st.session_state.ctx_analysis_type}")

    if not st.session_state.ctx_locked or st.session_state.ctx_edit_step:
        render_context_builder()
    else:
        render_context_bar()
        render_type_presets()

    chat_open = st.session_state["_chat_open"]
    launcher_key = "chat_launcher_wrap_open" if chat_open else "chat_launcher_wrap_closed"
    with st.container(key=launcher_key):
        launcher_label = "✕  Close" if chat_open else "Ask a question"
        launcher_help = "Close chat" if chat_open else "Ask a question about your waste operations data"
        if st.button(launcher_label, key="chat_launcher_btn", help=launcher_help):
            st.session_state["_chat_open"] = not st.session_state["_chat_open"]
            st.rerun()

    question = None
    if st.session_state["_chat_open"]:
        with st.container(key="chat_panel"):
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])
            question = st.chat_input("Ask anything about your waste operations data...")

    if not facility_selected or not time_selected:
        st.info("👆 Finish building your analysis above (analysis type, facility, timeframe) to begin.")
        return

    action = st.session_state.pop("_action", None)

    if action and action.get("type") == "combined":
        st.session_state["_ghg_result"] = None
        run_and_show_combined(action["keys"], action["label"])
    elif action and action.get("type") == "library":
        st.session_state["_ghg_result"] = None
        run_and_show_single(action["key"], action["is_kpi"])
    elif action and action.get("type") == "ghg_transport":
        st.session_state["_results"] = None
        facilities_to_run = ([f for f in FACILITIES if f != "All Facilities"]
                              if selected_facility == ["All Facilities"] else selected_facility)
        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            per_facility_results = [
                (fac, ghg.calculate_transport_emissions(BQ_CLIENT_AND_DATASET, fac, date_from, date_to))
                for fac in facilities_to_run
            ]
        st.session_state["_ghg_result"] = _combine_ghg_results(per_facility_results)

    if st.session_state.get("_ghg_result"):
        render_ghg_transport_result(st.session_state["_ghg_result"], display_time)

    if st.session_state.get("_results"):
        panels_for_pdf = []
        kpi_prev = st.session_state.get("_kpi_prev") or {}
        for idx, (key, sql, records, columns, is_kpi) in enumerate(st.session_state["_results"]):
            df = pd.DataFrame(records, columns=columns)
            sub_title = key.split(': ')[1].title() if ': ' in key else key.title()
            st.markdown(f"### {sub_title}")
            panel_label = key.replace(" ", "_").replace(":", "")
            prev_records = kpi_prev.get(key)
            prev_df = pd.DataFrame(prev_records) if prev_records else None
            results.show_result_panel(df, sql, panel_label, num_months, is_kpi,
                                       panel_id=f"result_{idx}_{panel_label}_{date_from}", prev_df=prev_df)
            panels_for_pdf.append((sub_title, df, is_kpi))
            st.divider()

        # Instant download — built from what's already on screen, no re-querying.
        report_label = st.session_state.get("_results_label") or st.session_state.ctx_analysis_type or "Analysis"
        if st.button("📄 Download this as a report (PDF)", key="download_current_report"):
            loader = st.empty()
            theme.render_fun_loader(loader, theme.FUN_PDF_MESSAGES)
            pdf_bytes = reports.generate_section_pdf(report_label, panels_for_pdf, selected_facility_display, display_time)
            loader.empty()
            st.download_button(
                "Save PDF", data=pdf_bytes, key="save_current_report_pdf",
                file_name=f"{report_label.replace(' ', '_').replace('|', '')[:60]}.pdf", mime="application/pdf",
            )

    elif action and action.get("type") == "clarification":
        pending = action
        with st.chat_message("user"):
            st.write(f"Show me: {pending['choice']}")
        st.session_state.messages.append({"role": "user", "content": f"Show me: {pending['choice']}"})
        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            llm_response = llm.generate_sql(
                groq_client, pending["question"], pending["choice"], selected_facility,
                pending["date_from"], pending["date_to"], original_sql=pending.get("original_sql"))
            sql = results.extract_sql(llm_response)
            if sql:
                df, error = db.run_query(sql, BQ_CLIENT_AND_DATASET)
                if error:
                    with st.chat_message("assistant"):
                        st.error(f"Query error: {error}")
                else:
                    st.session_state.conversation_history.append({
                        "question": pending["question"], "clarification": pending["choice"],
                        "sql": sql, "result_summary": f"{len(df)} rows"})
                    _cap("conversation_history")
                    msg = f"Found {len(df)} results for: {pending['choice']}"
                    st.session_state.messages.append({"role": "assistant", "content": msg})
                    _cap("messages")
                    # Persist into _results (same store preset buttons use) instead of
                    # rendering once inline — this is what lets the panel survive a
                    # later rerun, e.g. changing the chart type dropdown.
                    st.session_state["_results"] = [("clarification_result", sql, df.to_dict("records"), df.columns.tolist(), False)]
                    st.session_state["_results_label"] = f"{pending['choice']} | {selected_facility_display} | {display_time}"
                    with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
                        st.session_state.explorations = llm.get_suggestions(
                            groq_client, "explore", pending["question"], selected_facility,
                            pending["date_from"], pending["date_to"], df_columns=df.columns)
                    st.session_state["_last_sql"] = sql
                    st.session_state["_last_question"] = pending["question"]
                    st.rerun()

    if st.session_state.get("explorations"):
        st.divider()
        st.markdown("**Want to explore further?**")
        exp_cols = st.columns(min(len(st.session_state.explorations), 5))
        for i, exp in enumerate(st.session_state.explorations[:5]):
            with exp_cols[i]:
                if st.button(exp["label"], key=f"explore_{i}_{date_from}", help=exp.get("description", ""), width='stretch'):
                    st.session_state["_action"] = {
                        "type": "clarification", "question": exp["label"], "choice": exp["label"],
                        "date_from": date_from, "date_to": date_to,
                        "original_sql": st.session_state.get("_last_sql"),
                    }
                    st.session_state.explorations = None
                    st.rerun()

    if st.session_state.get("active_clarifications") and not st.session_state.get("_action"):
        clarifications = st.session_state["active_clarifications"]
        q = st.session_state.get("clarification_question", "")
        d_from = st.session_state.get("clarification_date_from", date_from)
        d_to = st.session_state.get("clarification_date_to", date_to)
        st.markdown("---")
        st.markdown("**How would you like to see this data?**")
        st.caption(f"Date: {d_from} to {d_to} | {selected_facility_display}")
        cols = st.columns(min(len(clarifications), 4))
        for i, c in enumerate(clarifications[:4]):
            with cols[i]:
                if st.button(c["label"], key=f"clarify_{i}_{abs(hash(q))%10000}", help=c.get("description", ""), width='stretch'):
                    st.session_state["_action"] = {"type": "clarification", "question": q, "choice": c["label"],
                                                    "date_from": d_from, "date_to": d_to}
                    st.session_state["active_clarifications"] = None
                    st.rerun()

    elif question:
        with st.chat_message("user"):
            st.write(question)
        st.session_state.messages.append({"role": "user", "content": question})
        _cap("messages")

        date_override_from, date_override_to = nlp_dates.parse_relative_dates(question, date_from, date_to)

        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            clarifications = llm.get_suggestions(groq_client, "clarify", question, selected_facility,
                                                  date_override_from, date_override_to)

        if clarifications:
            st.session_state.messages.append({"role": "assistant", "content": f"How would you like to see this? ({len(clarifications)} options shown)"})
            _cap("messages")
            st.session_state["active_clarifications"] = clarifications
            st.session_state["clarification_question"] = question
            st.session_state["clarification_date_from"] = date_override_from
            st.session_state["clarification_date_to"] = date_override_to
            st.rerun()
        else:
            with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
                llm_response = llm.generate_sql(groq_client, question, "", selected_facility, date_override_from, date_override_to)
                sql = results.extract_sql(llm_response)
                if sql:
                    df, error = db.run_query(sql, BQ_CLIENT_AND_DATASET)
                    if error:
                        with st.chat_message("assistant"):
                            st.error(f"Query error: {error}")
                    else:
                        st.session_state.conversation_history.append({
                            "question": question, "clarification": None, "sql": sql, "result_summary": f"{len(df)} rows"})
                        _cap("conversation_history")
                        msg = f"Found {len(df)} results."
                        st.session_state.messages.append({"role": "assistant", "content": msg})
                        _cap("messages")
                        st.session_state["_results"] = [("custom_query", sql, df.to_dict("records"), df.columns.tolist(), False)]
                        st.session_state["_results_label"] = f"{question} | {selected_facility_display} | {display_time}"
                        st.session_state["_last_sql"] = sql
                        st.session_state["_last_question"] = question
                        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
                            st.session_state.explorations = llm.get_suggestions(
                                groq_client, "explore", question, selected_facility,
                                date_override_from, date_override_to, df_columns=df.columns)
                        st.rerun()
                else:
                    with st.chat_message("assistant"):
                        st.write(llm_response)
                    st.session_state.messages.append({"role": "assistant", "content": llm_response})
                    _cap("messages")


# ── SINGLE-PAGE APP ────────────────────────────────────────────────────────
# No multipage nav — reports download inline after each analysis instead of
# needing a separate page, so there's nothing left for a second page to do.
analytics_page()
