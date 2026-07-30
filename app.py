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
    "ctx_facility": None, "ctx_timeframe_label": None,
    "ctx_date_from": None, "ctx_date_to": None, "ctx_num_months": 1,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _cap(key):
    if len(st.session_state[key]) > HISTORY_CAP:
        st.session_state[key] = st.session_state[key][-HISTORY_CAP:]


# ── CONTEXT DERIVATION (single source of truth, defined before ANY rendering) ─
selected_facility = st.session_state.ctx_facility or "All Facilities"
facility_selected = bool(st.session_state.ctx_facility) and st.session_state.ctx_locked
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
    for k in ["ctx_locked", "ctx_edit_step", "ctx_analysis_type", "ctx_facility",
              "ctx_timeframe_label", "ctx_date_from", "ctx_date_to"]:
        st.session_state[k] = defaults[k]
    st.session_state.ctx_num_months = 1
    for k in ["active_clarifications", "explorations", "_results"]:
        st.session_state[k] = None


def render_context_builder():
    edit_step = st.session_state.ctx_edit_step
    locked = st.session_state.ctx_locked
    show_type = (not locked) or edit_step == "type"
    show_facility = (not locked) or edit_step == "facility"
    show_time = (not locked) or edit_step == "time"

    if show_type:
        st.markdown("**1 · What do you want to analyse?**")
        with st.container(key="analysis_cards"):
            type_options = list(ANALYSIS_TYPES.keys())
            cols = st.columns(4)
            for i, opt in enumerate(type_options):
                with cols[i % 4]:
                    is_selected = st.session_state.ctx_analysis_type == opt
                    if st.button(opt, key=f"card_{opt}", use_container_width=True,
                                 type="primary" if is_selected else "secondary"):
                        st.session_state.ctx_analysis_type = opt
                        st.rerun()

    if show_facility:
        st.markdown("**2 · Which facility?**")
        fac_options = [user_facility] if user_role == "manager" else FACILITIES
        chosen = st.pills("Facility", fac_options, default=st.session_state.ctx_facility,
                           label_visibility="collapsed", key="pill_facility")
        if chosen is not None:
            st.session_state.ctx_facility = chosen

    if show_time:
        st.markdown("**3 · Timeframe**")
        tf_options = list(TIMEFRAME_PRESETS.keys()) + ["Custom range"]
        tf_default = st.session_state.ctx_timeframe_label if st.session_state.ctx_timeframe_label in tf_options else None
        chosen_tf = st.pills("Timeframe", tf_options, default=tf_default, label_visibility="collapsed", key="pill_time")

        if chosen_tf in TIMEFRAME_PRESETS:
            days = TIMEFRAME_PRESETS[chosen_tf]
            st.session_state.ctx_date_from = str(TODAY - timedelta(days=days))
            st.session_state.ctx_date_to = str(TODAY)
            st.session_state.ctx_timeframe_label = chosen_tf
            st.session_state.ctx_num_months = max(1, round(days / 30))
            st.caption(f"{st.session_state.ctx_date_from} to {st.session_state.ctx_date_to}")
        elif chosen_tf == "Custom range":
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("From")
                fm = st.selectbox("From Month", [MONTH_PH] + MONTHS_FULL, index=0, key="cr_fm", label_visibility="collapsed")
                fy_str = st.selectbox("From Year", [YEAR_PH] + YEARS, index=0, key="cr_fy", label_visibility="collapsed")
            with c2:
                st.markdown("To")
                tm = st.selectbox("To Month", [MONTH_PH] + MONTHS_FULL, index=0, key="cr_tm", label_visibility="collapsed")
                ty_str = st.selectbox("To Year", [YEAR_PH] + YEARS, index=0, key="cr_ty", label_visibility="collapsed")
            if fm != MONTH_PH and tm != MONTH_PH and fy_str != YEAR_PH and ty_str != YEAR_PH:
                fy_c, ty_c = int(fy_str), int(ty_str)
                fmn, tmn = int(MONTH_NUM[fm]), int(MONTH_NUM[tm])
                st.session_state.ctx_date_from = f"{fy_c}-{str(fmn).zfill(2)}-01"
                last_day = calendar.monthrange(ty_c, tmn)[1]
                st.session_state.ctx_date_to = f"{ty_c}-{str(tmn).zfill(2)}-{last_day}"
                st.session_state.ctx_num_months = max(1, (ty_c - fy_c) * 12 + (tmn - fmn) + 1)
                st.session_state.ctx_timeframe_label = f"{fm} {fy_c} – {tm} {ty_c}"
                st.caption(f"{st.session_state.ctx_date_from} to {st.session_state.ctx_date_to}")

    ready = bool(st.session_state.ctx_analysis_type and st.session_state.ctx_facility and st.session_state.ctx_date_from)
    if st.button("Update selection →" if locked else "Start analysis →", type="primary", disabled=not ready, key="lock_ctx_btn"):
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
        if st.button(f"📍  {st.session_state.ctx_facility}  ✎", key="edit_facility", use_container_width=True):
            st.session_state.ctx_edit_step = "facility"; st.rerun()
    with c3:
        if st.button(f"🗓️  {st.session_state.ctx_timeframe_label}  ✎", key="edit_time", use_container_width=True):
            st.session_state.ctx_edit_step = "time"; st.rerun()
    with c4:
        if st.button("Reset", key="reset_ctx_btn", use_container_width=True):
            reset_context(); st.rerun()
    st.markdown(
        f'<p class="ctx-caption">Every question below applies to '
        f'<b>{st.session_state.ctx_facility} · {st.session_state.ctx_timeframe_label}</b> '
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
                    "label": f"{at} Full Analysis | {st.session_state.ctx_facility} | {st.session_state.ctx_timeframe_label}"
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
        st.session_state["_results_label"] = f"{lib_key.title()} | {selected_facility} | {display_time}"


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
            with st.spinner("Compiling report..."):
                pdf_bytes = reports.generate_section_pdf(report_label, panels_for_pdf, selected_facility, display_time)
            st.download_button(
                "Save PDF", data=pdf_bytes, key="save_current_report_pdf",
                file_name=f"{report_label.replace(' ', '_').replace('|', '')[:60]}.pdf", mime="application/pdf",
            )

    elif action and action.get("type") == "clarification":
        pending = action
        with st.chat_message("user"):
            st.write(f"Show me: {pending['choice']}")
        st.session_state.messages.append({"role": "user", "content": f"Show me: {pending['choice']}"})
        with st.spinner(f"Running: {pending['choice']}..."):
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
                    st.session_state["_results_label"] = f"{pending['choice']} | {selected_facility} | {display_time}"
                    with st.spinner("Generating exploration suggestions..."):
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
        st.caption(f"Date: {d_from} to {d_to} | {selected_facility}")
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

        with st.spinner("Understanding your question..."):
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
            with st.spinner("Analysing with Groq AI..."):
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
                        st.session_state["_results_label"] = f"{question} | {selected_facility} | {display_time}"
                        st.session_state["_last_sql"] = sql
                        st.session_state["_last_question"] = question
                        with st.spinner("Generating exploration suggestions..."):
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
        f'<b>{selected_facility} · {display_time}</b>.</p>', unsafe_allow_html=True)

    st.caption("Pick only the analysis types you need — each one takes time to compile, so smaller is faster.")
    chosen_types = st.multiselect("Analysis types to include", list(ANALYSIS_TYPE_COMBINED.keys()))

    if st.button("Generate PDF report", type="primary", disabled=not chosen_types):
        with st.spinner(f"Compiling {len(chosen_types)} analysis type(s) into your report..."):
            pdf_bytes = reports.generate_selected_types_pdf(
                chosen_types, selected_facility, date_from, date_to, display_time, SUPABASE_URL)
        st.session_state["_report_pdf"] = pdf_bytes
        st.success("Report ready.")

    if st.session_state.get("_report_pdf"):
        st.download_button(
            "Download report PDF", data=st.session_state["_report_pdf"],
            file_name=f"waste_ops_report_{selected_facility.replace(' ', '_')}_{date_from}_{date_to}.pdf",
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
