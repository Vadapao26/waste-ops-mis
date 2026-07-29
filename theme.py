"""Design system: pastel color tokens, global CSS, and the login page.
Kept separate from business logic so the visual language can evolve without
touching a single query or LLM call."""
import streamlit as st

COLORS = {
    "bg": "#FAF8F4", "card": "#FFFFFF", "ink": "#2E2C29", "sub": "#8B8680", "line": "#E9E4DA",
    "sage_bg": "#E7F0E9", "sage_text": "#3F6B52", "sage_solid": "#7FA88F",
    "clay_bg": "#F7E7DA", "clay_text": "#A5623C", "clay_solid": "#D98B5F",
    "sky_bg": "#E7F0F7", "sky_text": "#3A6B85", "sky_solid": "#7FA8C9",
    "lavender_bg": "#EFEAF6", "lavender_text": "#6B5B95", "lavender_solid": "#A597C9",
    "rose_bg": "#F8E6E4", "rose_text": "#B0463A", "rose_solid": "#D97A7A",
}


def inject_global_css():
    st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] {{ font-family: 'Inter', sans-serif !important; }}
#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ padding: 1.5rem 2rem !important; max-width: 100% !important; }}

/* ── pastel base ─────────────────────────────────────────────────────── */
[data-theme="light"] .stApp, .light .stApp {{ background: {COLORS['bg']} !important; }}
[data-theme="light"] section[data-testid="stSidebar"] {{
  background-color: {COLORS['card']} !important; border-right: 1px solid {COLORS['line']} !important;
}}

/* ── pill / segmented control (analysis type, facility, timeframe pickers) ── */
[data-testid="stPills"] label {{
  border-radius: 10px !important; border: 1px solid {COLORS['line']} !important;
  transition: all 0.15s ease;
}}
[data-testid="stPills"] label:hover {{ border-color: {COLORS['sage_solid']} !important; }}

/* ── buttons ──────────────────────────────────────────────────────────── */
.stButton > button {{
  border-radius: 10px; border: 1px solid {COLORS['line']}; transition: all 0.15s ease;
}}
.stButton > button:hover {{ border-color: {COLORS['sage_solid']}; transform: translateY(-1px); }}
.stButton > button[kind="primary"] {{
  background: {COLORS['sage_solid']} !important; border: none !important; color: white !important;
}}
.stButton > button[kind="primary"]:hover {{ opacity: 0.92; }}

/* ── kpi cards (pastel) ───────────────────────────────────────────────── */
.kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:1.25rem; }}
.kpi-card {{ border-radius:14px; padding:1rem 1.1rem; border:1px solid {COLORS['line']}; }}
.kpi-label {{ font-size:11px; color:{COLORS['sub']}; text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px; font-weight:600; }}
.kpi-value {{ font-size:20px; font-weight:700; color:{COLORS['ink']}; }}
.kpi-sage {{ background:{COLORS['sage_bg']}; }} .kpi-sage .kpi-value {{ color:{COLORS['sage_text']}; }}
.kpi-lavender {{ background:{COLORS['lavender_bg']}; }} .kpi-lavender .kpi-value {{ color:{COLORS['lavender_text']}; }}
.kpi-peach {{ background:{COLORS['clay_bg']}; }} .kpi-peach .kpi-value {{ color:{COLORS['clay_text']}; }}
.kpi-amber {{ background:{COLORS['sky_bg']}; }} .kpi-amber .kpi-value {{ color:{COLORS['sky_text']}; }}
.kpi-rose {{ background:{COLORS['rose_bg']}; }} .kpi-rose .kpi-value {{ color:{COLORS['rose_text']}; }}

/* ── context bar chips ────────────────────────────────────────────────── */
.ctx-caption {{ font-size:12.5px; color:{COLORS['sub']}; margin: 2px 0 14px; }}
.ctx-caption b {{ color:{COLORS['ink']}; }}

/* ── fade-in on main content ─────────────────────────────────────────── */
.block-container {{ animation: fadeIn 0.35s ease; }}
@keyframes fadeIn {{ from {{ opacity:0; transform: translateY(4px); }} to {{ opacity:1; transform: translateY(0); }} }}

/* ── top-right avatar trigger ─────────────────────────────────────────── */
.st-key-avatar_trigger button {{
  border-radius: 50% !important; width: 42px !important; height: 42px !important;
  padding: 0 !important; min-width: 42px !important;
  background: linear-gradient(135deg, {COLORS['sage_solid']}, {COLORS['sky_solid']}) !important;
  color: white !important; font-weight: 700 !important; border: none !important;
  font-size: 15px !important;
}}
.st-key-avatar_trigger button:hover {{ opacity: 0.9; transform: none !important; }}
.st-key-avatar_menu button {{ border: none !important; text-align: left !important; }}

/* ── analysis type card grid ──────────────────────────────────────────── */
.st-key-analysis_cards button {{
  aspect-ratio: 1.3; height: auto !important; border-radius: 16px !important;
  font-weight: 600 !important; font-size: 14px !important;
  display: flex; align-items: center; justify-content: center; text-align: center;
  white-space: normal !important; padding: 10px !important;
}}
.st-key-analysis_cards button[kind="primary"] {{
  background: {COLORS['sage_bg']} !important; color: {COLORS['sage_text']} !important;
  border: 2px solid {COLORS['sage_solid']} !important;
}}
</style>""", unsafe_allow_html=True)


def render_topbar(user_name: str, user_role: str, on_logout) -> None:
    """Renders the app title on the left and a circular avatar on the right.
    Clicking the avatar opens a small popover with account info and Logout —
    replaces the old sidebar block that used to eat vertical space."""
    left, right = st.columns([10, 1])
    with left:
        st.markdown('<div style="font-size:21px;font-weight:700;color:#2E2C29;margin-top:6px;">Waste Operations MIS</div>', unsafe_allow_html=True)
    with right:
        initials = "".join([p[0] for p in user_name.split()][:2]).upper() or "U"
        with st.container(key="avatar_trigger"):
            with st.popover(initials, use_container_width=False):
                st.markdown(f"**{user_name}**")
                st.caption(user_role.title())
                st.divider()
                with st.container(key="avatar_menu"):
                    if st.button("Log out", use_container_width=True, key="avatar_logout_btn"):
                        on_logout()


def render_login_page(check_password_fn) -> bool:
    """Renders the redesigned login screen. Returns True once a valid login
    has been submitted (caller is expected to st.rerun() after)."""
    st.markdown(f"""<style>
.login-wrap {{
  max-width: 380px; margin: 8vh auto 0; padding: 2.5rem 2.25rem; background: {COLORS['card']};
  border-radius: 20px; border: 1px solid {COLORS['line']};
  box-shadow: 0 20px 60px rgba(46,44,41,0.08);
  animation: floatIn 0.5s ease;
}}
@keyframes floatIn {{ from {{ opacity:0; transform: translateY(10px); }} to {{ opacity:1; transform: translateY(0); }} }}
.login-mark {{
  width: 44px; height: 44px; border-radius: 12px; margin-bottom: 18px;
  background: linear-gradient(135deg, {COLORS['sage_solid']}, {COLORS['sky_solid']});
  display:flex; align-items:center; justify-content:center; color:white; font-weight:700; font-size:18px;
}}
.login-title {{ font-size: 21px; font-weight: 700; color: {COLORS['ink']}; margin: 0 0 4px; }}
.login-sub {{ font-size: 13.5px; color: {COLORS['sub']}; margin: 0 0 28px; }}
</style>
<div class="login-wrap">
  <div class="login-mark">W</div>
  <p class="login-title">Waste Ops MIS</p>
  <p class="login-sub">Sign in to access your facility analytics</p>
""", unsafe_allow_html=True)

    with st.container():
        username_input = st.text_input("Username", placeholder="your.name")
        password_input = st.text_input("Password", type="password", placeholder="••••••••")
        submitted = st.button("Sign in", type="primary", use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        if check_password_fn(username_input, password_input):
            st.session_state.authenticated = True
            st.session_state.username = username_input
            return True
        else:
            st.markdown(f"""<div style="max-width:380px;margin:10px auto 0;padding:10px 16px;
                background:{COLORS['rose_bg']};color:{COLORS['rose_text']};border-radius:10px;
                font-size:13px;text-align:center;">Incorrect username or password.</div>""",
                unsafe_allow_html=True)
    return False
