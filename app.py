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

BUILD_TAG = "2026-08-21-supply-chain-dashboard-ulb-bwg-fix"  # bump this string every time files are handed off

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

SUPABASE_URL = st.secrets["supabase"]["url"]
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


def _tile_grid(options, selected_check, on_click, key_prefix, n_cols=4, all_facilities_tile=None):
    """Renders a checkerboard-tinted, flex-wrap tile row: alternating indigo/
    gold, light by default, dark when selected (per option). Buttons size to
    their own text (plus padding) rather than stretching into equal-width
    columns, and wrap to the next line as needed. `selected_check(opt)`
    returns bool, `on_click(opt)` handles the click (toggle or single-select,
    caller decides). If all_facilities_tile is given, it's rendered as a
    separate tile below the row (matches the design)."""
    with st.container(key=f"tile_flexwrap_{key_prefix}"):
        for i, opt in enumerate(options):
            color = "indigo" if (i // n_cols + i % n_cols) % 2 == 0 else "gold"
            with st.container(key=f"tile_{color}_{key_prefix}_{i}"):
                is_sel = selected_check(opt)
                if st.button(opt, key=f"{key_prefix}_{opt}",
                             type="primary" if is_sel else "secondary"):
                    on_click(opt)
                    st.rerun()
    if all_facilities_tile is not None:
        with st.container(key="tile_all"):
            is_sel = selected_check(all_facilities_tile)
            if st.button(all_facilities_tile, key=f"{key_prefix}_{all_facilities_tile}", use_container_width=True,
                         type="primary" if is_sel else "secondary"):
                on_click(all_facilities_tile)
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
        _tile_grid(fac_options, _facility_selected, _facility_click, "fac", all_facilities_tile=all_tile)

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
        _tile_grid(tf_options, _tf_selected, _tf_click, "tf")

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
    "Transport Analytics": "🚚", "ULB Analytics": "🏛️", "BWG Analytics": "🏢",
    "Training Analytics": "🎓", "Environmental Impact": "🌱", "Supply Chain Analytics": "🔗",
    "Custom AI Query": "💬",
}
# Groups by real operational stage rather than one flat list: the material's
# actual path through a facility, then the vendor/citizen programs layered on
# top of it, then cross-cutting tools. Falls back gracefully — any type not
# listed here just won't be grouped (shouldn't happen, but never hides a type).
SIDEBAR_GROUP_ORDER = [
    ("Material Flow", ["Inward Analytics", "Production Analytics", "Outward Analytics", "Transport Analytics"]),
    ("Programs", ["ULB Analytics", "BWG Analytics", "Training Analytics"]),
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
    """Persistent sidebar tree for picking the analysis type — replaces the old
    step-1 tile grid. The active type expands to show its presets as nested
    sub-items (collapsible: only the active one is expanded at a time),
    matching the approved mockup's tree behavior. Facility/timeframe
    selection is unaffected by this; switching analysis type here just
    clears stale results so nothing from a previous type lingers on screen."""
    st.sidebar.markdown("### 🗑️ Wise Waste")
    st.sidebar.caption("Analytics")
    grouped_types = {t for _, types in SIDEBAR_GROUP_ORDER for t in types}
    ungrouped = [t for t in ANALYSIS_TYPES.keys() if t not in grouped_types]
    all_groups = SIDEBAR_GROUP_ORDER + ([("Other", ungrouped)] if ungrouped else [])

    for group_label, types_in_group in all_groups:
        st.sidebar.markdown(
            f'<div style="font-size:10.5px;color:{theme.PALETTE["muted"]};'
            f'text-transform:uppercase;letter-spacing:.06em;margin:12px 0 4px 4px;">{group_label}</div>',
            unsafe_allow_html=True)
        for opt in types_in_group:
            is_active = st.session_state.ctx_analysis_type == opt
            # ctx_sidebar_expanded starts as None (nothing explicitly toggled
            # yet) — in that state, the active type shows expanded by default.
            # "__collapsed__" is an explicit sentinel meaning "user collapsed
            # whatever was open," distinct from "hasn't touched it yet."
            if st.session_state.ctx_sidebar_expanded is None:
                is_expanded = is_active
            else:
                is_expanded = st.session_state.ctx_sidebar_expanded == opt
            icon = TYPE_ICONS.get(opt, "")
            has_children = bool(ANALYSIS_TYPES[opt] or ANALYSIS_TYPE_COMBINED.get(opt))
            chevron = ("▾ " if is_expanded else "▸ ") if has_children else ""
            if st.sidebar.button(f"{chevron}{icon}  {opt}".strip(), key=f"sidebar_type_{opt}", use_container_width=True,
                                  type="primary" if is_active else "secondary"):
                if is_expanded:
                    # Already expanded — collapse the tree only, leave whatever
                    # content is currently showing untouched.
                    st.session_state.ctx_sidebar_expanded = "__collapsed__"
                    st.rerun()
                else:
                    st.session_state.ctx_sidebar_expanded = opt
                    if opt != st.session_state.ctx_analysis_type:
                        st.session_state.ctx_analysis_type = opt
                        st.session_state["_ghg_result"] = None
                        st.session_state["active_clarifications"] = None
                        st.session_state["explorations"] = None
                        default_key = _default_sub_key(opt)
                        if default_key:
                            # Land on the natural default view immediately, instead
                            # of a blank panel until a sub-item is clicked too.
                            st.session_state["_action"] = {"type": "library", "key": default_key, "is_kpi": "kpi" in default_key}
                        else:
                            st.session_state["_results"] = None
                    st.rerun()
            if is_expanded and (ANALYSIS_TYPES[opt] or ANALYSIS_TYPE_COMBINED.get(opt)):
                combined_keys = ANALYSIS_TYPE_COMBINED.get(opt)
                if combined_keys:
                    with st.sidebar.container(key=f"sidebar_sub_wrap_{opt}_all"):
                        if st.button(f"All {opt.split(' ')[0]}", key=f"sidebar_sub_{opt}_all", use_container_width=True):
                            st.session_state["_action"] = {
                                "type": "combined", "keys": combined_keys,
                                "label": f"{opt} Full Analysis | {selected_facility_display} | {st.session_state.ctx_timeframe_label}"
                            }
                            st.session_state["active_clarifications"] = None
                            st.session_state["explorations"] = None
                            st.rerun()
                for key in ANALYSIS_TYPES[opt]:
                    sub_label = key.split(": ")[1].title() if ": " in key else key.title()
                    with st.sidebar.container(key=f"sidebar_sub_wrap_{opt}_{key}"):
                        if st.button(sub_label, key=f"sidebar_sub_{opt}_{key}", use_container_width=True):
                            st.session_state["_action"] = {"type": "library", "key": key, "is_kpi": "kpi" in key}
                            st.session_state["active_clarifications"] = None
                            st.session_state["explorations"] = None
                            st.rerun()

    # Visible build marker — bump BUILD_TAG whenever files are handed off, so
    # a glance at the sidebar footer proves which version is actually running
    # instead of guessing after a copy/restart.
    st.sidebar.markdown(
        f'<div style="position:fixed;bottom:10px;left:14px;font-size:10px;'
        f'color:{theme.PALETTE["muted"]};">build {BUILD_TAG}</div>', unsafe_allow_html=True)


def render_context_bar():
    c1, c2, c3, _spacer = st.columns([1.6, 1.6, 0.7, 4])
    with c1:
        if st.button(f"📍  {selected_facility_display}  ✎", key="edit_facility", use_container_width=True,
                     help="Change facility"):
            st.session_state.ctx_edit_step = "facility"; st.rerun()
    with c2:
        if st.button(f"🗓️  {st.session_state.ctx_timeframe_label}  ✎", key="edit_time", use_container_width=True,
                     help="Change timeframe"):
            st.session_state.ctx_edit_step = "time"; st.rerun()
    with c3:
        if st.button("Reset", key="reset_ctx_btn", use_container_width=True, help="Reset facility and timeframe"):
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

    if at == "Environmental Impact":
        if selected_facility == ["MRF"]:
            st.divider()
            if st.button("🚚 Transport GHG Emissions (MRF)", key="ghg_transport_mrf"):
                st.session_state["_action"] = {"type": "ghg_transport", "facility": "MRF"}
                st.session_state["active_clarifications"] = None
                st.session_state["explorations"] = None
                st.rerun()
        else:
            st.caption("🚚 Transport GHG Emissions is available for MRF only so far — "
                       "distance data for other facilities hasn't been loaded yet.")

    if at == "BWG Analytics":
        st.divider()
        st.caption("Analyze one BWG vendor at a time, rather than all of them combined.")
        vendor_list_sql = db.inject_filters(QUERY_LIBRARY["bwg: vendor list"], selected_facility, date_from, date_to)
        vendor_df, verr = db.run_query(vendor_list_sql, SUPABASE_URL)
        if verr:
            st.error(f"Couldn't load the BWG vendor list: {verr}")
        elif vendor_df is None or vendor_df.empty:
            st.info("No BWG vendors found for this facility/timeframe.")
        else:
            vendor_options = vendor_df["vendor"].dropna().unique().tolist()
            c1, c2 = st.columns([3, 1])
            with c1:
                chosen_vendor = st.selectbox("BWG vendor", vendor_options, key="bwg_vendor_picker", label_visibility="collapsed")
            with c2:
                if st.button("Analyze this vendor", key="bwg_analyze_btn", use_container_width=True):
                    st.session_state["_action"] = {"type": "bwg_drill", "vendor": chosen_vendor}
                    st.session_state["active_clarifications"] = None
                    st.session_state["explorations"] = None
                    st.rerun()

    if at == "ULB Analytics":
        st.divider()
        st.caption("Analyze one ULB vendor at a time, rather than all of them combined.")
        vendor_list_sql = db.inject_filters(QUERY_LIBRARY["ulb: vendor list"], selected_facility, date_from, date_to)
        vendor_df, verr = db.run_query(vendor_list_sql, SUPABASE_URL)
        if verr:
            st.error(f"Couldn't load the ULB vendor list: {verr}")
        elif vendor_df is None or vendor_df.empty:
            st.info("No ULB vendors found for this facility/timeframe.")
        else:
            vendor_options = vendor_df["vendor"].dropna().unique().tolist()
            c1, c2 = st.columns([3, 1])
            with c1:
                chosen_vendor = st.selectbox("ULB vendor", vendor_options, key="ulb_vendor_picker", label_visibility="collapsed")
            with c2:
                if st.button("Analyze this vendor", key="ulb_analyze_btn", use_container_width=True):
                    st.session_state["_action"] = {"type": "ulb_drill", "vendor": chosen_vendor}
                    st.session_state["active_clarifications"] = None
                    st.session_state["explorations"] = None
                    st.rerun()


# ── ACTION DISPATCHER HELPERS ─────────────────────────────────────────────────
def run_and_show_combined(keys, label):
    """Runs a set of preset queries. Only the resulting analysis panels are
    shown — no 'Loaded N analyses' bookkeeping message is added to the chat,
    since the section headings and context bar already say what's showing."""
    result_rows = []
    for key in keys:
        sql = db.inject_filters(QUERY_LIBRARY[key].strip(), selected_facility, date_from, date_to)
        df, error = db.run_query(sql, SUPABASE_URL)
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
    df, error = db.run_query(sql, SUPABASE_URL)
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
            prev_df, prev_error = db.run_query(prev_sql, SUPABASE_URL)
            if not prev_error and prev_df is not None and len(prev_df) == 1:
                st.session_state["_kpi_prev"][lib_key] = prev_df.to_dict("records")


# ── PAGE: ANALYTICS ───────────────────────────────────────────────────────────
# ── GHG TRANSPORT RESULT DISPLAY ──────────────────────────────────────────────
def render_ghg_transport_result(result: dict, display_time: str):
    st.markdown("### Transport GHG Emissions")
    st.caption(
        "Methodology: India-specific road-freight emission factors "
        "(Smart Freight Centre / TCI-IIMB, May 2025), distance-tiered as a stand-in "
        "for vehicle class since per-trip vehicle size isn't tracked. "
        "This is an internal-tracking estimate, not a certified compliance figure."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total transport emissions", f"{result['total_kg_co2e']:,.1f} kg CO2e")
    with c2:
        st.metric("Matched tonnage", f"{result['matched_tonnes']:,.2f} t")
    with c3:
        st.metric("Excluded tonnage (no distance yet)", f"{result['excluded_tonnes']:,.2f} t")

    if result["excluded_trip_count"]:
        st.warning(result["note"])
    else:
        st.success(result["note"])

    if result.get("inconsistency_notes"):
        for note in result["inconsistency_notes"]:
            st.warning(f"⚠️ Data check: {note}")

    if not result["route_breakdown"].empty:
        st.markdown("**By route**")
        st.dataframe(result["route_breakdown"], use_container_width=True, hide_index=True)


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
        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            st.session_state["_ghg_result"] = ghg.calculate_transport_emissions(
                SUPABASE_URL, action["facility"], date_from, date_to)
    elif action and action.get("type") == "bwg_drill":
        st.session_state["_ghg_result"] = None
        vendor = action["vendor"]
        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            kpi_sql = db.inject_vendor_filter(
                db.inject_filters(QUERY_LIBRARY["bwg: kpi summary"], selected_facility, date_from, date_to), vendor)
            mat_sql = db.inject_vendor_filter(
                db.inject_filters(QUERY_LIBRARY["bwg: vendor material analytics"], selected_facility, date_from, date_to), vendor)
            kpi_df, kerr = db.run_query(kpi_sql, SUPABASE_URL)
            mat_df, merr = db.run_query(mat_sql, SUPABASE_URL)
        result_rows = []
        if kerr:
            st.error(f"BWG KPI query failed: {kerr}")
        elif kpi_df is not None:
            result_rows.append(("bwg: kpi summary", kpi_sql, kpi_df.to_dict("records"), kpi_df.columns.tolist(), True))
        if merr:
            st.error(f"BWG material query failed: {merr}")
        elif mat_df is not None:
            result_rows.append(("bwg: vendor material analytics", mat_sql, mat_df.to_dict("records"), mat_df.columns.tolist(), False))
        if result_rows:
            st.session_state["_results"] = result_rows
            st.session_state["_results_label"] = f"BWG: {vendor} | {selected_facility_display} | {display_time}"
    elif action and action.get("type") == "ulb_drill":
        st.session_state["_ghg_result"] = None
        vendor = action["vendor"]
        with st.spinner(random.choice(theme.FUN_LOADING_MESSAGES)):
            kpi_sql = db.inject_vendor_filter(
                db.inject_filters(QUERY_LIBRARY["ulb: kpi summary"], selected_facility, date_from, date_to), vendor)
            mat_sql = db.inject_vendor_filter(
                db.inject_filters(QUERY_LIBRARY["ulb: vendor material analytics"], selected_facility, date_from, date_to), vendor)
            kpi_df, kerr = db.run_query(kpi_sql, SUPABASE_URL)
            mat_df, merr = db.run_query(mat_sql, SUPABASE_URL)
        result_rows = []
        if kerr:
            st.error(f"ULB KPI query failed: {kerr}")
        elif kpi_df is not None:
            result_rows.append(("ulb: kpi summary", kpi_sql, kpi_df.to_dict("records"), kpi_df.columns.tolist(), True))
        if merr:
            st.error(f"ULB material query failed: {merr}")
        elif mat_df is not None:
            result_rows.append(("ulb: vendor material analytics", mat_sql, mat_df.to_dict("records"), mat_df.columns.tolist(), False))
        if result_rows:
            st.session_state["_results"] = result_rows
            st.session_state["_results_label"] = f"ULB: {vendor} | {selected_facility_display} | {display_time}"

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
                df, error = db.run_query(sql, SUPABASE_URL)
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
                if st.button(exp["label"], key=f"explore_{i}_{date_from}", help=exp.get("description", ""), use_container_width=True):
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
                if st.button(c["label"], key=f"clarify_{i}_{abs(hash(q))%10000}", help=c.get("description", ""), use_container_width=True):
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
                    df, error = db.run_query(sql, SUPABASE_URL)
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
