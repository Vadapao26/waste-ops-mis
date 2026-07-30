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
from queries import FACILITIES, MONTHS_FULL, MONTH_NUM, QUERY_LIBRARY, SIDEBAR_GROUPS, ANALYSIS_TYPE_COMBINED

st.set_page_config(page_title="Waste Ops MIS", layout="wide", initial_sidebar_state="expanded")
theme.inject_global_css()

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
    "explorations": None, "_results": None, "_results_label": None,
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


# ── CONTEXT DERIVATION (single source of truth, defined before ANY rendering) ─
# selected_facility is now a LIST (multi-select) — db.inject_filters/reports
# functions take lists directly. selected_facility_display is the human-
# readable string used everywhere else (labels, filenames, chat text).
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

# ── ANALYSIS TYPE MAP ─────────────────────────────────────────────────────────
ANALYSIS_TYPES = {name: keys for name, keys in SIDEBAR_GROUPS.items()}
ANALYSIS_TYPES["Custom AI Query"] = None
TIMEFRAME_PRESETS = {"Last 7 days": 7, "Last 30 days": 30, "Last 3 months": 90, "Last 6 months": 180, "Last 12 months": 365}
MONTH_PH, YEAR_PH = "— Select —", "— Year —"
TODAY = date.today()
YEARS = list(range(2023, TODAY.year + 2))


def reset_context():
    for k in ["ctx_locked", "ctx_edit_step", "ctx_analysis_type", "ctx_facilities",
              "ctx_timeframe_label", "ctx_date_from", "ctx_date_to"]:
        st.session_state[k] = defaults[k]
    st.session_state.ctx_num_months = 1
    for k in ["active_clarifications", "explorations", "_results"]:
        st.session_state[k] = None


def _tile_grid(options, selected_check, on_click, key_prefix, n_cols=4, all_facilities_tile=None):
    """Renders a checkerboard-tinted tile grid: alternating indigo/gold,
    light by default, dark when selected (per option). `selected_check(opt)`
    returns bool, `on_click(opt)` handles the click (toggle or single-select,
    caller decides). If all_facilities_tile is given, it's rendered as a
    separate full-width tile below the grid (matches the design)."""
    cols = st.columns(n_cols)
    for i, opt in enumerate(options):
        color = "indigo" if (i // n_cols + i % n_cols) % 2 == 0 else "gold"
        with cols[i % n_cols]:
            with st.container(key=f"tile_{color}_{key_prefix}_{i}"):
                is_sel = selected_check(opt)
                if st.button(opt, key=f"{key_prefix}_{opt}", use_container_width=True,
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
    show_type = (not locked) or edit_step == "type"
    show_facility = (not locked) or edit_step == "facility"
    show_time = (not locked) or edit_step == "time"

    if show_type:
        st.markdown("**1 · What do you want to analyse?**")
        type_options = list(ANALYSIS_TYPES.keys())

        def _type_click(opt):
            st.session_state.ctx_analysis_type = opt
        _tile_grid(type_options, lambda o: st.session_state.ctx_analysis_type == o, _type_click, "type")

    if show_facility:
        st.markdown("**2 · Which facility?** *(pick one or more)*")
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
        st.markdown("**3 · Timeframe**")
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
        if st.button("Custom range" + (" ✓" if custom_active else ""), key="custom_range_toggle",
                     type="primary" if custom_active else "secondary", use_container_width=True):
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


def render_context_bar():
    c1, c2, c3, c4 = st.columns([2.6, 2.2, 2.6, 1])
    with c1:
        if st.button(f"🔎  {st.session_state.ctx_analysis_type}  ✎", key="edit_type", use_container_width=True):
            st.session_state.ctx_edit_step = "type"; st.rerun()
    with c2:
        if st.button(f"📍  {selected_facility_display}  ✎", key="edit_facility", use_container_width=True):
            st.session_state.ctx_edit_step = "facility"; st.rerun()
    with c3:
        if st.button(f"🗓️  {st.session_state.ctx_timeframe_label}  ✎", key="edit_time", use_container_width=True):
            st.session_state.ctx_edit_step = "time"; st.rerun()
    with c4:
        if st.button("Reset", key="reset_ctx_btn", use_container_width=True):
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
    st.caption(f"Quick presets — {at}")
    combined_keys = ANALYSIS_TYPE_COMBINED.get(at)
    n_cols = min(4, len(keys) + (1 if combined_keys else 0))
    cols = st.columns(n_cols)
    col_i = 0
    if combined_keys:
        with cols[col_i % n_cols]:
            if st.button(f"All {at.split(' ')[0]}", key=f"combined_{at}", use_container_width=True):
                st.session_state["_action"] = {
                    "type": "combined", "keys": combined_keys,
                    "label": f"{at} Full Analysis | {selected_facility_display} | {st.session_state.ctx_timeframe_label}"
                }
                st.session_state["active_clarifications"] = None
                st.session_state["explorations"] = None
                st.rerun()
        col_i += 1
    for key in keys:
        label = key.split(": ")[1].title()
        with cols[col_i % n_cols]:
            if st.button(label, key=f"preset_{key}", use_container_width=True):
                st.session_state["_action"] = {"type": "library", "key": key, "is_kpi": "kpi" in key}
                st.session_state["active_clarifications"] = None
                st.session_state["explorations"] = None
                st.rerun()
        col_i += 1


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
            result_rows.append((key, sql, df.to_dict("records"), df.columns.tolist(), "kpi" in key))
    if result_rows:
        st.session_state["_results"] = result_rows
        st.session_state["_results_label"] = label


def run_and_show_single(lib_key, is_kpi):
    sql = db.inject_filters(QUERY_LIBRARY[lib_key].strip(), selected_facility, date_from, date_to)
    df, error = db.run_query(sql, SUPABASE_URL)
    if error:
        st.error(f"Error running {lib_key}: {error}")
        st.session_state["_results"] = None
    else:
        st.session_state["_results"] = [(lib_key, sql, df.to_dict("records"), df.columns.tolist(), is_kpi)]
        st.session_state["_results_label"] = f"{lib_key.title()} | {selected_facility_display} | {display_time}"


# ── PAGE: ANALYTICS ───────────────────────────────────────────────────────────
def analytics_page():
    theme.render_topbar(user_name, user_role, on_logout=_logout)

    if not st.session_state.ctx_locked or st.session_state.ctx_edit_step:
        render_context_builder()
    else:
        render_context_bar()
        render_type_presets()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    question = st.chat_input("Ask anything about your waste operations data...")

    if not facility_selected or not time_selected:
        st.info("👆 Finish building your analysis above (analysis type, facility, timeframe) to begin.")
        return

    action = st.session_state.pop("_action", None)

    if action and action.get("type") == "combined":
        run_and_show_combined(action["keys"], action["label"])
    elif action and action.get("type") == "library":
        run_and_show_single(action["key"], action["is_kpi"])

    if st.session_state.get("_results"):
        panels_for_pdf = []
        for idx, (key, sql, records, columns, is_kpi) in enumerate(st.session_state["_results"]):
            df = pd.DataFrame(records, columns=columns)
            sub_title = key.split(': ')[1].title() if ': ' in key else key.title()
            st.markdown(f"### {sub_title}")
            panel_label = key.replace(" ", "_").replace(":", "")
            results.show_result_panel(df, sql, panel_label, num_months, is_kpi,
                                       panel_id=f"result_{idx}_{panel_label}_{date_from}")
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


# ── PAGE: REPORTS ─────────────────────────────────────────────────────────────
def reports_page():
    theme.render_topbar(user_name, user_role, on_logout=_logout)
    st.markdown("### Generate a report")

    if not facility_selected or not time_selected:
        st.info("Set your facility and timeframe on the Analytics page first — Reports uses that same context.")
        return

    st.markdown(
        f'<p class="ctx-caption">Reports use the context set on the Analytics page: '
        f'<b>{selected_facility_display} · {display_time}</b>.</p>', unsafe_allow_html=True)

    st.caption("Pick only the analysis types you need — each one takes time to compile, so smaller is faster.")
    chosen_types = st.multiselect("Analysis types to include", list(ANALYSIS_TYPE_COMBINED.keys()))

    if st.button("Generate PDF report", type="primary", disabled=not chosen_types):
        loader = st.empty()
        theme.render_fun_loader(loader, theme.FUN_PDF_MESSAGES)
        pdf_bytes = reports.generate_selected_types_pdf(
            chosen_types, selected_facility, selected_facility_display, date_from, date_to, display_time, SUPABASE_URL)
        loader.empty()
        st.session_state["_report_pdf"] = pdf_bytes
        st.success("Report ready.")

    if st.session_state.get("_report_pdf"):
        st.download_button(
            "Download report PDF", data=st.session_state["_report_pdf"],
            file_name=f"waste_ops_report_{selected_facility_display.replace(' ', '_')}_{date_from}_{date_to}.pdf",
            mime="application/pdf", type="primary",
        )

    st.divider()
    st.caption("Tip: after running any analysis on the Analytics page, you can also download just "
               "that result as a report instantly — no need to come here for a single analysis type.")


# ── NAVIGATION ─────────────────────────────────────────────────────────────────
pages = [
    st.Page(analytics_page, title="Analytics", icon="💬", default=True),
    st.Page(reports_page, title="Reports", icon="📄"),
]
st.navigation(pages).run()
