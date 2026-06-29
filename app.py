import streamlit as st
import pandas as pd
import re
import os
import io
import calendar
import json
from datetime import date
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
from groq import Groq
import bcrypt

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Waste Ops MIS", layout="wide", initial_sidebar_state="expanded")

# ── AUTH ──────────────────────────────────────────────────────────────────────
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
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## Waste Ops MIS")
        st.markdown("---")
        username_input = st.text_input("Username")
        password_input = st.text_input("Password", type="password")
        if st.button("Login", use_container_width=True):
            if check_password(username_input, password_input):
                st.session_state.authenticated = True
                st.session_state.username = username_input
                st.rerun()
            else:
                st.error("Incorrect username or password")
    st.stop()

# Get user info
username = st.session_state.username
user_info = get_user_info(username)
user_role = user_info["role"]
user_facility = user_info["facility"]
user_name = user_info["name"]

# ── DB CONNECTION ─────────────────────────────────────────────────────────────
SUPABASE_URL = st.secrets["supabase"]["url"]
GROQ_API_KEY = st.secrets["groq"]["api_key"]
groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "llama-3.3-70b-versatile"

@st.cache_resource
def get_engine():
    return create_engine(SUPABASE_URL, pool_pre_ping=True)

def run_query(sql):
    try:
        engine = get_engine()
        with engine.connect() as conn:
            df = pd.read_sql_query(text(sql), conn)
        return df, None
    except Exception as e:
        return None, str(e)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
FACILITIES = ["All Facilities", "Hebbagodi", "MRF", "Muguluru", "Jigani", "GPR",
              "Anekal", "Marsur", "Mayasandra", "Bommasandra", "Attibele"]
MONTHS_FULL = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]
MONTH_NUM = {m: str(i+1).zfill(2) for i, m in enumerate(MONTHS_FULL)}

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif!important;}
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding:1rem 1.5rem!important;max-width:100%!important;}
section[data-testid="stSidebar"]{background-color:#F7F3EE!important;border-right:1px solid #E8E0D5!important;min-width:320px!important;max-width:320px!important;}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:1.25rem;}
.kpi-card{border-radius:10px;padding:0.875rem 1rem;}
.kpi-sage{background:#E8F0E8;}.kpi-lavender{background:#F0EDF8;}
.kpi-peach{background:#FDF0E8;}.kpi-amber{background:#FDF6E8;}.kpi-rose{background:#FDF0F2;}
.kpi-label{font-size:10px;color:#9B9490;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}
.kpi-value{font-size:18px;font-weight:600;color:#2C2A28;}
</style>
""", unsafe_allow_html=True)

# ── QUERY LIBRARY ─────────────────────────────────────────────────────────────
QUERY_LIBRARY = {
    "inward: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(rejected_quantity) AS total_rejected_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity))::numeric,0),2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS non_valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS total_material_value,
            ROUND(SUM(COALESCE(transportation_cost,0)),2) AS total_transportation_cost,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS total_net_procurement_cost
        FROM inward {FACILITY_FILTER};""",

    "inward: vendor analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, received_material_from AS vendor,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity))::numeric,0),2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_value,
            ROUND(SUM(COALESCE(transportation_cost,0)),2) AS transportation_cost,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS net_procurement_cost
        FROM inward {FACILITY_FILTER}
        GROUP BY month,facility,vendor ORDER BY month DESC,total_received_kg DESC;""",

    "inward: vendor location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS location,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity))::numeric,0),2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_value,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS net_procurement_cost
        FROM inward {FACILITY_FILTER}
        GROUP BY month,facility,location ORDER BY month DESC,total_received_kg DESC;""",

    "production: kpi summary": """
        SELECT
            ROUND(SUM(CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN material_quantity ELSE 0 END),2) AS total_sorted_kg,
            ROUND(SUM(CASE WHEN LOWER(process_equipment) LIKE '%bag%' THEN material_quantity ELSE 0 END),2) AS total_bagged_kg,
            ROUND(SUM(CASE WHEN LOWER(process_equipment) LIKE '%bail%' OR LOWER(process_equipment) LIKE '%bale%' THEN material_quantity ELSE 0 END),2) AS total_bailed_kg,
            ROUND(SUM(material_quantity)::numeric,2) AS total_processed_kg,
            COUNT(DISTINCT production_code) AS total_runs,
            COUNT(DISTINCT date::date) AS days_operated
        FROM production {FACILITY_FILTER};""",

    "production: equipment analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, process_equipment,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND(SUM(material_quantity)::numeric,2) AS total_qty_processed_kg,
            ROUND(AVG(no_of_staff_present)::numeric,1) AS avg_staff,
            COUNT(DISTINCT date::date) AS days_operated,
            ROUND(SUM(material_quantity)/NULLIF(COUNT(DISTINCT date::date),0),2) AS efficiency_per_day,
            ROUND(SUM(material_quantity)/NULLIF(SUM(time_taken_in_hrs),0),2) AS efficiency_per_hour
        FROM production {FACILITY_FILTER}
        GROUP BY month,facility,process_equipment ORDER BY month DESC,total_qty_processed_kg DESC;""",

    "production: shift analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, shift,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND(SUM(material_quantity)::numeric,2) AS total_qty_processed_kg,
            ROUND(AVG(no_of_staff_present)::numeric,1) AS avg_staff,
            ROUND(SUM(material_quantity)/NULLIF(COUNT(DISTINCT date::date),0),2) AS efficiency_per_day,
            ROUND(SUM(material_quantity)/NULLIF(SUM(time_taken_in_hrs),0),2) AS efficiency_per_hour
        FROM production {FACILITY_FILTER}
        GROUP BY month,facility,shift ORDER BY month DESC,efficiency_per_day DESC;""",

    "production: equipment x shift analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, process_equipment, shift,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND(SUM(material_quantity)::numeric,2) AS total_qty_processed_kg,
            ROUND(SUM(material_quantity)/NULLIF(COUNT(DISTINCT date::date),0),2) AS efficiency_per_day,
            ROUND(SUM(material_quantity)/NULLIF(SUM(time_taken_in_hrs),0),2) AS efficiency_per_hour
        FROM production {FACILITY_FILTER}
        GROUP BY month,facility,process_equipment,shift ORDER BY month DESC,process_equipment;""",

    "transport: vendor and vehicle analysis": """
        SELECT facility, transport_vendor, vehicle_number,
            SUM(total_trips) AS total_trips, SUM(inward_trips) AS inward_trips, SUM(outward_trips) AS outward_trips,
            ROUND(SUM(total_material_kg)::numeric,2) AS total_material_kg,
            SUM(paid_trips) AS paid_trips,
            ROUND(SUM(total_transport_cost)::numeric,2) AS total_transport_cost,
            ROUND(SUM(total_transport_cost)/NULLIF(SUM(material_at_cost),0),2) AS rate_per_kg
        FROM (
            SELECT facility, vehicle_vendor_name AS transport_vendor, vehicle_number,
                COUNT(DISTINCT inward_code) AS total_trips, COUNT(DISTINCT inward_code) AS inward_trips, 0 AS outward_trips,
                SUM(accepted_quantity) AS total_material_kg,
                COUNT(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)>0 THEN 1 END) AS paid_trips,
                SUM(COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)) AS total_transport_cost,
                SUM(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)>0 THEN accepted_quantity ELSE 0 END) AS material_at_cost
            FROM inward WHERE vehicle_vendor_name IS NOT NULL {AND_FACILITY_FILTER}
            GROUP BY facility,transport_vendor,vehicle_number
            UNION ALL
            SELECT facility, transport_vendor, vehicle_number,
                COUNT(DISTINCT outward_code) AS total_trips, 0 AS inward_trips, COUNT(DISTINCT outward_code) AS outward_trips,
                SUM(accepted_quantity) AS total_material_kg,
                COUNT(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)>0 THEN 1 END) AS paid_trips,
                SUM(COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)) AS total_transport_cost,
                SUM(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)>0 THEN accepted_quantity ELSE 0 END) AS material_at_cost
            FROM outward WHERE transport_vendor IS NOT NULL {AND_FACILITY_FILTER}
            GROUP BY facility,transport_vendor,vehicle_number
        ) combined
        WHERE transport_vendor IS NOT NULL AND transport_vendor!=''
        GROUP BY facility,transport_vendor,vehicle_number ORDER BY transport_vendor,total_trips DESC;""",

    "ulb: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS non_valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS total_material_value,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS total_net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER};""",

    "ulb: ward location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS ward_location,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_value,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER}
        GROUP BY month,facility,ward_location ORDER BY month DESC,total_received_kg DESC;""",

    "ulb: driver analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, UPPER(TRIM(driver_name)) AS driver,
            COUNT(DISTINCT inward_code) AS total_trips,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER}
        GROUP BY month,facility,driver ORDER BY month DESC,total_trips DESC;""",

    "bwg: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS total_material_value,
            ROUND(SUM(net_procurement_cost)::numeric,2) AS total_net_procurement_cost
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER};""",

    "bwg: location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS location, received_material_from AS vendor,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity))::numeric,0),2) AS valuables_pct
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER}
        GROUP BY month,facility,location,vendor ORDER BY month DESC,total_received_kg DESC;""",

    "outward: kpi summary": """
        SELECT SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(dispatched_quantity))::numeric,0),2) AS rejection_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_revenue,
            ROUND(SUM(COALESCE(total_incentive_cost,0)),2) AS total_incentive,
            ROUND(SUM(value_of_accepted_material)+SUM(COALESCE(total_incentive_cost,0)),2) AS total_revenue,
            ROUND(SUM(net_material_sales_cost)+SUM(COALESCE(total_incentive_cost,0)),2) AS net_revenue
        FROM outward {FACILITY_FILTER};""",

    "outward: customer analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, customer,
            SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(dispatched_quantity))::numeric,0),2) AS rejection_pct,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_revenue,
            ROUND(SUM(COALESCE(total_incentive_cost,0)),2) AS total_incentive,
            ROUND(SUM(value_of_accepted_material)+SUM(COALESCE(total_incentive_cost,0)),2) AS total_revenue,
            ROUND(SUM(COALESCE(transportation_cost,0)),2) AS transportation_cost,
            ROUND(SUM(net_material_sales_cost)+SUM(COALESCE(total_incentive_cost,0)),2) AS net_revenue
        FROM outward {FACILITY_FILTER}
        GROUP BY month,facility,customer ORDER BY month DESC,net_revenue DESC;""",

    "outward: customer destination analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, customer, destination,
            SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND(SUM(value_of_accepted_material)::numeric,2) AS material_revenue,
            ROUND(SUM(value_of_accepted_material)+SUM(COALESCE(total_incentive_cost,0)),2) AS total_revenue,
            ROUND(SUM(net_material_sales_cost)+SUM(COALESCE(total_incentive_cost,0)),2) AS net_revenue
        FROM outward {FACILITY_FILTER}
        GROUP BY month,facility,customer,destination ORDER BY month DESC,customer,net_revenue DESC;""",
}

SIDEBAR_GROUPS = {
    "Inward Analytics": ["inward: kpi summary","inward: vendor analysis","inward: vendor location analysis"],
    "Production Analytics": ["production: kpi summary","production: equipment analysis","production: shift analysis","production: equipment x shift analysis"],
    "Transport Analytics": ["transport: vendor and vehicle analysis"],
    "ULB Analytics": ["ulb: kpi summary","ulb: ward location analysis","ulb: driver analysis"],
    "BWG Analytics": ["bwg: kpi summary","bwg: location analysis"],
    "Outward Analytics": ["outward: kpi summary","outward: customer analysis","outward: customer destination analysis"],
}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def inject_filters(sql, facility, date_from, date_to):
    if facility == "All Facilities":
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE date>='{date_from}' AND date<='{date_to}'")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND date>='{date_from}' AND date<='{date_to}'")
    else:
        sql = sql.replace("{FACILITY_FILTER}", f"WHERE facility='{facility}' AND date>='{date_from}' AND date<='{date_to}'")
        sql = sql.replace("{AND_FACILITY_FILTER}", f"AND facility='{facility}' AND date>='{date_from}' AND date<='{date_to}'")
    return sql

def extract_sql(text):
    match = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL)
    if match: return match.group(1).strip()
    match2 = re.search(r'SELECT.*?;', text, re.DOTALL | re.IGNORECASE)
    if match2: return match2.group(0).strip()
    return None

def add_summary_row(df):
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols: return df
    sum_row = {col: df[col].sum() if col in numeric_cols else ("TOTAL" if i==0 else "") for i,col in enumerate(df.columns)}
    avg_row = {col: round(df[col].mean(),2) if col in numeric_cols else ("AVG" if i==0 else "") for i,col in enumerate(df.columns)}
    return pd.concat([df, pd.DataFrame([sum_row, avg_row])], ignore_index=True)

def df_to_csv_bytes(df): return df.to_csv(index=False).encode("utf-8")

def df_to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        add_summary_row(df).to_excel(writer, sheet_name="Data", index=False)
    return output.getvalue()

def format_dataframe(df):
    MONEY_SUFFIXES = ['_cost','_revenue','_paid','_amount','_value','_incentive','_procurement']
    PCT_SUFFIXES = ['_pct','_percent']
    COST_COLS = ['net_procurement_cost','net_cost_per_kg','net_material_sales_cost','net_revenue','net_revenue_per_kg']
    for col in df.columns:
        col_lower = col.lower()
        if any(col_lower.endswith(s) for s in PCT_SUFFIXES):
            df[col] = df[col].apply(lambda x: f'{x:.2f}%' if isinstance(x,(int,float)) and str(x) not in ['TOTAL','AVG',''] else x)
        elif col_lower in COST_COLS:
            def fmt_cost(x):
                if not isinstance(x,(int,float)) or str(x) in ['TOTAL','AVG','']: return x
                if x < 0: return f'₹{abs(x):,.2f} (cost)'
                return f'₹{x:,.2f}'
            df[col] = df[col].apply(fmt_cost)
        elif any(col_lower.endswith(s) for s in MONEY_SUFFIXES):
            df[col] = df[col].apply(lambda x: f'₹{x:,.2f}' if isinstance(x,(int,float)) and str(x) not in ['TOTAL','AVG',''] else x)
    return df

def render_kpi_cards(df):
    if df is None or len(df)==0: return
    colors = ["kpi-sage","kpi-lavender","kpi-peach","kpi-amber","kpi-rose"]
    MONEY_SUFFIXES = ['_cost','_revenue','_paid','_amount','_value','_incentive','_procurement']
    PCT_SUFFIXES = ['_pct','_percent']
    KG_SUFFIXES = ['_kg','_quantity','_runs','_days','_trips']
    cards_html = '<div class="kpi-grid">'
    for i, col in enumerate(df.columns[:10]):
        val = df[col].iloc[0]
        color = colors[i % len(colors)]
        label = col.replace("_"," ").title()
        col_lower = col.lower()
        if isinstance(val,(int,float)) and str(val) not in ['nan']:
            if any(col_lower.endswith(s) for s in PCT_SUFFIXES):
                display = f"{val:,.2f}%"
            elif any(col_lower.endswith(s) for s in MONEY_SUFFIXES) and not any(col_lower.endswith(s) for s in KG_SUFFIXES):
                display = f"₹{val:,.2f}"
            elif isinstance(val,float):
                display = f"{val:,.2f}"
            else:
                display = f"{int(val):,}"
        else:
            display = str(val)
        cards_html += f'<div class="kpi-card {color}"><div class="kpi-label">{label}</div><div class="kpi-value">{display}</div></div>'
    cards_html += '</div>'
    st.markdown(cards_html, unsafe_allow_html=True)

def get_db_context(facility="All Facilities"):
    ctx_path = os.path.join(os.path.dirname(__file__), "db_context.json")
    try:
        ctx = json.load(open(ctx_path))
    except Exception:
        return ""  # db_context.json missing or unreadable; AI will operate without facility context
    facilities = list(ctx.keys()) if facility == "All Facilities" else [facility]
    lines = []
    for f in facilities:
        if f not in ctx: continue
        d = ctx[f]
        vendor_list = [v[0] + f" (source={v[1]})" if isinstance(v,list) else str(v) for v in d.get("vendors",[])]
        if vendor_list:
            lines.append(f"INWARD VENDORS for {f} [column: received_material_from]: " + ", ".join(vendor_list))
        customers = d.get("customers",[])
        if customers:
            lines.append(f"OUTWARD CUSTOMERS for {f} [column: customer]: " + ", ".join(customers))
        wards = d.get("wards",[])
        if wards:
            lines.append(f"WARDS for {f} [column: vendor_location]: " + ", ".join(wards[:20]))
        in_mats = d.get("inward_materials",[])
        if in_mats:
            lines.append(f"INWARD MATERIALS for {f}: " + ", ".join(in_mats))
        out_mats = d.get("outward_materials",[])
        if out_mats:
            lines.append(f"OUTWARD MATERIALS for {f}: " + ", ".join(out_mats))
    return chr(10).join(lines)

def get_conversation_context():
    hist = st.session_state.get("conversation_history",[])
    if not hist: return ""
    lines = ["PREVIOUS QUESTIONS IN THIS SESSION:"]
    for i, h in enumerate(hist[-5:]):
        lines.append(f"  Q{i+1}: {h['question']}")
        if h.get("clarification"): lines.append(f"  → Chose: {h['clarification']}")
        if h.get("sql"): lines.append(f"  → SQL: {h['sql'][:120]}...")
    return chr(10).join(lines)

SCHEMA_CONTEXT = """
TABLE: inward — waste received at facility
COLUMNS: inward_code, date, facility, received_material_from (vendor), vendor_location (ward),
source (ULB/Bulk waste generator/Aggregator/DWCC/SHGc/Waste Picker), driver_name,
vehicle_vendor_name, vehicle_number, material, received_quantity, accepted_quantity,
rejected_quantity, value_of_accepted_material, net_procurement_cost,
transportation_cost, loading_cost, additional_cost
NOTE: NO destination column. Use facility for facility name.

TABLE: production — processing runs
COLUMNS: production_code, date, facility, shift (Day/Night/General),
process_equipment, material_quantity (output kg), no_of_staff_present, time_taken_in_hrs

TABLE: outward — material dispatched to customers
COLUMNS: outward_code, date, facility, customer, destination, material,
dispatched_quantity, accepted_quantity, rejected_quantity, value_of_accepted_material,
net_material_sales_cost, total_incentive_cost, transportation_cost, loading_cost, additional_transport_cost

TABLE: expense — date, facility, category, bill_amount_in_rs
TABLE: revenue — date, facility, category, bill_amount_in_rs
NOTE: Use PostgreSQL syntax. Use TO_CHAR(date::date,'YYYY-MM') for monthly grouping instead of strftime.
"""

def get_clarifications(question, facility, date_from, date_to):
    db_context = get_db_context(facility)
    conv_context = get_conversation_context()
    prompt = f"""Waste management analyst.
{conv_context}
User asked: "{question}"
Facility: {facility}, dates {date_from} to {date_to}
{db_context}

Generate 4-5 clarification options showing HOW to view this data.
Options must be specific using actual names from the data above.
Return ONLY a JSON array with "label" and "description" keys. No other text."""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"user","content":prompt}],
            temperature=0.2
        )
        import json as json_lib
        text = response.choices[0].message.content.strip()
        start = text.find('['); end = text.rfind(']')+1
        if start!=-1 and end>start:
            return json_lib.loads(text[start:end])
        return []
    except Exception as e:
        st.error(f"⚠️ Groq AI is unavailable: {e}. Please check your API key or try again shortly.")
        return []

def get_explorations(question, df_columns, facility, date_from, date_to):
    db_context = get_db_context(facility)
    conv_context = get_conversation_context()
    cols = ", ".join(list(df_columns)[:8])
    prompt = f"""Waste management analyst.
{conv_context}
User asked: "{question}", result columns: {cols}
Facility: {facility}
{db_context}

Generate 4-5 specific follow-up exploration options based on this result.
Return ONLY a JSON array with "label" and "description" keys. No other text."""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"user","content":prompt}],
            temperature=0.3
        )
        import json as json_lib
        text = response.choices[0].message.content.strip()
        start = text.find('['); end = text.rfind(']')+1
        if start!=-1 and end>start:
            return json_lib.loads(text[start:end])
        return []
    except Exception as e:
        st.error(f"⚠️ Groq AI is unavailable: {e}. Exploration suggestions could not be generated.")
        return []

def ask_groq_sql(question, clarification, facility, date_from, date_to, original_sql=None, original_question=None):
    if facility!="All Facilities":
        fclause = f"WHERE facility='{facility}' AND date>='{date_from}' AND date<='{date_to}'"
        fnote = f"Filter by facility='{facility}'"
    else:
        fclause = f"WHERE date>='{date_from}' AND date<='{date_to}'"
        fnote = "No facility filter. Include facility in SELECT."
    db_context = get_db_context(facility)
    conv_context = get_conversation_context()
    context = f"\nPrevious SQL: {original_sql[:200]}\n" if original_sql else ""
    prompt = f"""PostgreSQL SQL expert for waste management database.
{conv_context}
{context}
Question: {question}
User wants: {clarification}
Filter: {fclause}
{fnote}

RULES:
1. Return ONLY SQL in ```sql ``` blocks
2. PostgreSQL syntax. Semicolon. NULLIF. LIMIT 500.
3. Use TO_CHAR(date::date,'YYYY-MM') AS month for monthly grouping
4. inward vendor=received_material_from (LIKE '%name%'). NO destination in inward.
5. production output=material_quantity. outward customer=customer column.
6. Use LIKE '%name%' for partial name matching

{db_context}
{SCHEMA_CONTEXT}

Write SQL for: {question} — showing {clarification}"""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"system","content":"Return ONLY SQL in ```sql ``` blocks."},
                      {"role":"user","content":prompt}],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {e}"

def ask_llm(question, facility, date_from, date_to):
    if facility!="All Facilities":
        fclause = f"WHERE facility='{facility}' AND date>='{date_from}' AND date<='{date_to}'"
        fnote = f"Filter by facility='{facility}'"
    else:
        fclause = f"WHERE date>='{date_from}' AND date<='{date_to}'"
        fnote = "No facility filter."
    db_context = get_db_context(facility)
    conv_context = get_conversation_context()
    prompt = f"""PostgreSQL SQL expert. Return ONLY SQL in ```sql ``` blocks.
{conv_context}
Filter: {fclause}
{fnote}
Rules: PostgreSQL. NULLIF. LIMIT 500. TO_CHAR(date::date,'YYYY-MM') for months.
inward vendor=received_material_from (LIKE). production=material_quantity. inward NO destination.
{db_context}
{SCHEMA_CONTEXT}
Question: {question}"""
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role":"system","content":"Return ONLY SQL in ```sql ``` blocks."},
                      {"role":"user","content":prompt}],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {e}"

def show_result_panel(df, sql, label, num_months, is_kpi=False, panel_id=None):
    uid = panel_id or label
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download CSV", data=df_to_csv_bytes(df),
            file_name=f"{label}.csv", mime="text/csv", key=f"csv_{uid}")
    with c2:
        st.download_button("Download Excel", data=df_to_excel_bytes(df),
            file_name=f"{label}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{uid}")
    st.caption(f"{len(df)} results")
    if is_kpi and len(df)==1:
        render_kpi_cards(df.copy())
    elif "month" in df.columns and df["month"].nunique()>1:
        months = sorted(df["month"].unique(), reverse=True)
        tabs = st.tabs([str(m) for m in months]+["All Data"])
        for i, month in enumerate(months):
            with tabs[i]:
                month_df = df[df["month"]==month].reset_index(drop=True)
                st.dataframe(format_dataframe(add_summary_row(month_df.copy())), use_container_width=True, height=280)
                st.download_button(f"Download {month}", data=df_to_csv_bytes(month_df),
                    file_name=f"{label}_{month}.csv", mime="text/csv", key=f"csv_{uid}_{month}_{i}")
        with tabs[-1]:
            st.dataframe(format_dataframe(add_summary_row(df.copy())), use_container_width=True, height=280)
    else:
        st.dataframe(format_dataframe(add_summary_row(df.copy())), use_container_width=True, height=320)
    with st.expander("View SQL", key=f"sql_{uid}"):
        st.code(sql, language='sql')

# ── SESSION STATE ─────────────────────────────────────────────────────────────
defaults = {
    'messages': [], 'current_df': None, 'current_sql': None,
    'current_label': None, 'num_months': 1, 'is_kpi': False,
    'pending_clarification': None, 'pending_question': None,
    'display_time': '', 'explorations': None,
    'result_history': [], 'conversation_history': [],
    'active_clarifications': None, 'clarification_question': None,
    'clarification_date_from': None, 'clarification_date_to': None,
    'combined_results': None
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## Waste Ops MIS")
    st.caption(f"👤 {user_name} ({user_role})")
    if st.button("Logout"): st.session_state.authenticated = False; st.session_state.username = None; st.rerun()
    st.divider()

    # Facility selector — restrict managers to their facility
    st.markdown("**Facility**")
    if user_role == "manager":
        selected_facility = st.selectbox("facility", [user_facility], label_visibility="collapsed")
        facility_selected = True
    else:
        fac_opts = ["— Select Facility —"] + FACILITIES[1:]
        selected_facility = st.selectbox("facility", fac_opts, index=0, label_visibility="collapsed")
        facility_selected = selected_facility != "— Select Facility —"

    st.divider()
    st.markdown("**Time period**")
    today = date.today()
    years = list(range(2023, today.year+2))
    MONTH_PH = "— Select —"
    YEAR_PH = "— Year —"
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("From")
        fm = st.selectbox("From Month", [MONTH_PH]+MONTHS_FULL, index=0, key="cr_fm", label_visibility="collapsed")
        fy_str = st.selectbox("From Year", [YEAR_PH]+years, index=0, key="cr_fy", label_visibility="collapsed")
    with c2:
        st.markdown("To")
        tm = st.selectbox("To Month", [MONTH_PH]+MONTHS_FULL, index=0, key="cr_tm", label_visibility="collapsed")
        ty_str = st.selectbox("To Year", [YEAR_PH]+years, index=0, key="cr_ty", label_visibility="collapsed")

    time_selected = fm!=MONTH_PH and tm!=MONTH_PH and fy_str!=YEAR_PH and ty_str!=YEAR_PH
    if time_selected:
        fy_c = int(fy_str); ty_c = int(ty_str)
        fmn = int(MONTH_NUM[fm]); tmn = int(MONTH_NUM[tm])
        date_from = f"{fy_c}-{str(fmn).zfill(2)}-01"
        last_day = calendar.monthrange(ty_c, tmn)[1]
        date_to = f"{ty_c}-{str(tmn).zfill(2)}-{last_day}"
        num_months = max(1,(ty_c-fy_c)*12+(tmn-fmn)+1)
        display_time = f"{fm} {fy_c} to {tm} {ty_c}"
        st.caption(f"{date_from} to {date_to}")
    else:
        date_from = date_to = display_time = ""
        num_months = 1
        st.caption("Select From and To period")

    st.divider()
    st.markdown("**Analytics**")
    st.caption("Run full group at once")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("All Inward", use_container_width=True, key="all_inward"):
            if facility_selected and time_selected:
                st.session_state["_action"] = {"type":"combined","keys":["inward: kpi summary","inward: vendor analysis","inward: vendor location analysis"],"label":f"Inward Full Analysis | {selected_facility} | {display_time}"}
                st.session_state.pop("active_clarifications", None); st.session_state.pop("explorations", None)
                st.rerun()
    with col2:
        if st.button("All Outward", use_container_width=True, key="all_outward"):
            if facility_selected and time_selected:
                st.session_state["_action"] = {"type":"combined","keys":["outward: kpi summary","outward: customer analysis","outward: customer destination analysis"],"label":f"Outward Full Analysis | {selected_facility} | {display_time}"}
                st.session_state.pop("active_clarifications", None); st.session_state.pop("explorations", None)
                st.rerun()
    col3, col4 = st.columns(2)
    with col3:
        if st.button("All Production", use_container_width=True, key="all_prod"):
            if facility_selected and time_selected:
                st.session_state["_action"] = {"type":"combined","keys":["production: kpi summary","production: equipment analysis","production: shift analysis","production: equipment x shift analysis"],"label":f"Production Full Analysis | {selected_facility} | {display_time}"}
                st.session_state.pop("active_clarifications", None); st.session_state.pop("explorations", None)
                st.rerun()
    with col4:
        if st.button("All ULB", use_container_width=True, key="all_ulb"):
            if facility_selected and time_selected:
                st.session_state["_action"] = {"type":"combined","keys":["ulb: kpi summary","ulb: ward location analysis","ulb: driver analysis"],"label":f"ULB Full Analysis | {selected_facility} | {display_time}"}
                st.session_state.pop("active_clarifications", None); st.session_state.pop("explorations", None)
                st.rerun()
    st.divider()
    for group_name, preset_keys in SIDEBAR_GROUPS.items():
        with st.expander(group_name, expanded=False):
            for key in preset_keys:
                label = key.split(": ")[1].title()
                if st.button(label, key=f"btn_{key}", use_container_width=True):
                    st.session_state["_action"] = {"type":"library","key":key,"is_kpi":"kpi" in key}
                    st.session_state.pop("active_clarifications", None); st.session_state.pop("explorations", None)
                    st.rerun()
    st.divider()
    st.caption("Or type a question below")

# ── MAIN ──────────────────────────────────────────────────────────────────────
st.markdown('<div style="font-size:20px;font-weight:600;color:#2C2A28;margin-bottom:6px;">Waste Operations MIS</div>', unsafe_allow_html=True)

if not facility_selected or not time_selected:
    missing = []
    if not facility_selected: missing.append("Facility")
    if not time_selected: missing.append("Time Period")
    st.warning(f"⚠️ Please select {' and '.join(missing)} to begin")
else:
    st.markdown(f'<div style="background:#F0F8F0;border:1px solid #B8D4B8;border-radius:8px;padding:8px 14px;display:flex;align-items:center;gap:16px;margin-bottom:1rem;font-size:12px;"><span style="color:#4A7A4A;">✓</span><span style="color:#9B9490;">Facility</span><span style="color:#2C2A28;font-weight:500;">{selected_facility}</span><span style="color:#E8E0D5;">|</span><span style="color:#9B9490;">Period</span><span style="color:#2C2A28;font-weight:500;">{display_time}</span><span style="color:#E8E0D5;">|</span><span style="color:#9B9490;">{date_from} to {date_to}</span></div>', unsafe_allow_html=True)

# ── CHAT HISTORY ──────────────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg['role']):
        st.write(msg['content'])

# ── CHAT INPUT ────────────────────────────────────────────────────────────────
question = st.chat_input("Ask anything about your waste operations data...")

if not facility_selected or not time_selected:
    st.info("👈 Please select a Facility and Time Period in the sidebar to begin analysis.")
    st.stop()

# ── ACTION DISPATCHER ─────────────────────────────────────────────────────────
# Determine what action to run this render cycle
action = st.session_state.pop("_action", None)

def run_and_show_combined(keys, label):
    """Run multiple preset queries and render all results inline — no rerun needed."""
    st.session_state.messages.append({"role": "user", "content": label})
    with st.chat_message("user"):
        st.write(label)
    results = []
    for key in keys:
        sql = inject_filters(QUERY_LIBRARY[key].strip(), selected_facility, date_from, date_to)
        df, error = run_query(sql)
        if error:
            st.error(f"Query failed for {key}: {error}")
        elif df is not None:
            results.append((key, sql, df, "kpi" in key))
    if results:
        msg = f"Loaded {len(results)} analyses."
        st.session_state.messages.append({"role": "assistant", "content": msg})
        with st.chat_message("assistant"):
            st.write(msg)
        for idx, (key, sql, df, is_kpi) in enumerate(results):
            st.markdown(f"### {key.split(': ')[1].title()}")
            panel_label = key.replace(" ", "_").replace(":", "")
            show_result_panel(df, sql, panel_label, num_months, is_kpi,
                              panel_id=f"combined_{idx}_{panel_label}_{date_from}")
            st.divider()

def run_and_show_single(lib_key, is_kpi):
    """Run a single preset query and render inline."""
    label = f"{lib_key.title()} | {selected_facility} | {display_time}"
    st.session_state.messages.append({"role": "user", "content": label})
    with st.chat_message("user"):
        st.write(label)
    sql = inject_filters(QUERY_LIBRARY[lib_key].strip(), selected_facility, date_from, date_to)
    df, error = run_query(sql)
    if error:
        with st.chat_message("assistant"):
            st.error(f"Query error: {error}")
    else:
        msg = f"Found {len(df)} results."
        st.session_state.messages.append({"role": "assistant", "content": msg})
        with st.chat_message("assistant"):
            st.write(msg)
        panel_label = lib_key.replace(" ", "_").replace(":", "")
        show_result_panel(df, sql, panel_label, num_months, is_kpi,
                          panel_id=f"single_{panel_label}_{date_from}")

# ── HANDLE BUTTON ACTIONS ─────────────────────────────────────────────────────
if action and action.get("type") == "combined":
    run_and_show_combined(action["keys"], action["label"])

elif action and action.get("type") == "library":
    run_and_show_single(action["key"], action["is_kpi"])

# ── HANDLE CLARIFICATION CHOICE ───────────────────────────────────────────────
elif action and action.get("type") == "clarification":
    pending = action
    with st.chat_message("user"):
        st.write(f"Show me: {pending['choice']}")
    st.session_state.messages.append({"role": "user", "content": f"Show me: {pending['choice']}"})
    with st.spinner(f"Running: {pending['choice']}..."):
        llm_response = ask_groq_sql(
            pending["question"], pending["choice"], selected_facility,
            pending["date_from"], pending["date_to"],
            original_sql=pending.get("original_sql"),
            original_question=pending.get("original_question")
        )
        sql = extract_sql(llm_response)
        if sql:
            df, error = run_query(sql)
            if error:
                with st.chat_message("assistant"):
                    st.error(f"Query error: {error}")
                    st.code(sql, language="sql")
            else:
                if not st.session_state.conversation_history:
                    st.session_state.conversation_history = []
                st.session_state.conversation_history.append({
                    "question": pending["question"], "clarification": pending["choice"],
                    "sql": sql, "result_summary": f"{len(df)} rows"
                })
                msg = f"Found {len(df)} results for: {pending['choice']}"
                st.session_state.messages.append({"role": "assistant", "content": msg})
                with st.chat_message("assistant"):
                    st.write(msg)
                panel_id = f"clarify_{pending['choice'][:20].replace(' ','_')}_{date_from}"
                show_result_panel(df, sql, "clarification_result", num_months, False, panel_id=panel_id)
                with st.spinner("Generating exploration suggestions..."):
                    st.session_state.explorations = get_explorations(
                        pending["question"], df.columns, selected_facility,
                        pending["date_from"], pending["date_to"]
                    )
                st.session_state._last_sql = sql
                st.session_state._last_question = pending["question"]

# ── SHOW EXPLORATION SUGGESTIONS ──────────────────────────────────────────────
if st.session_state.get("explorations"):
    st.divider()
    st.markdown("**Want to explore further?**")
    exp_cols = st.columns(min(len(st.session_state.explorations), 5))
    for i, exp in enumerate(st.session_state.explorations[:5]):
        with exp_cols[i]:
            if st.button(exp["label"], key=f"explore_{i}_{date_from}", help=exp.get("description", ""), use_container_width=True):
                st.session_state["_action"] = {
                    "type": "clarification",
                    "question": exp["label"], "choice": exp["label"],
                    "date_from": date_from, "date_to": date_to,
                    "original_sql": st.session_state.get("_last_sql"),
                    "original_question": st.session_state.get("_last_question")
                }
                st.session_state.explorations = None
                st.rerun()

# ── SHOW CLARIFICATION OPTIONS ────────────────────────────────────────────────
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
                st.session_state["_action"] = {
                    "type": "clarification",
                    "question": q, "choice": c["label"],
                    "date_from": d_from, "date_to": d_to
                }
                st.session_state["active_clarifications"] = None
                st.rerun()

# ── HANDLE FREE-TEXT QUESTION ─────────────────────────────────────────────────
elif question:
    with st.chat_message("user"):
        st.write(question)
    st.session_state.messages.append({"role": "user", "content": question})

    import re as re2, calendar as cal2
    date_override_from = date_from
    date_override_to = date_to
    year_months = re2.findall(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})", question, re2.IGNORECASE)
    month_map2 = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
                  "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
    if len(year_months) >= 2:
        m1, y1 = year_months[0]; m2, y2 = year_months[-1]
        date_override_from = f"{y1}-{month_map2[m1.lower()[:3]]}-01"
        last = cal2.monthrange(int(y2), int(month_map2[m2.lower()[:3]]))[1]
        date_override_to = f"{y2}-{month_map2[m2.lower()[:3]]}-{last}"
    elif len(year_months) == 1:
        m1, y1 = year_months[0]
        date_override_from = f"{y1}-{month_map2[m1.lower()[:3]]}-01"
        last = cal2.monthrange(int(y1), int(month_map2[m1.lower()[:3]]))[1]
        date_override_to = f"{y1}-{month_map2[m1.lower()[:3]]}-{last}"

    with st.spinner("Understanding your question..."):
        clarifications = get_clarifications(question, selected_facility, date_override_from, date_override_to)

    if clarifications:
        st.session_state.messages.append({"role": "assistant", "content": f"How would you like to see this? ({len(clarifications)} options shown)"})
        st.session_state["active_clarifications"] = clarifications
        st.session_state["clarification_question"] = question
        st.session_state["clarification_date_from"] = date_override_from
        st.session_state["clarification_date_to"] = date_override_to
        st.rerun()
    else:
        with st.spinner("Analysing with Groq AI..."):
            llm_response = ask_llm(question, selected_facility, date_override_from, date_override_to)
            sql = extract_sql(llm_response)
            if sql:
                df, error = run_query(sql)
                if error:
                    with st.chat_message("assistant"):
                        st.error(f"Query error: {error}")
                        st.code(sql, language="sql")
                else:
                    if not st.session_state.conversation_history:
                        st.session_state.conversation_history = []
                    st.session_state.conversation_history.append({
                        "question": question, "clarification": None,
                        "sql": sql, "result_summary": f"{len(df)} rows"
                    })
                    msg = f"Found {len(df)} results."
                    st.session_state.messages.append({"role": "assistant", "content": msg})
                    with st.chat_message("assistant"):
                        st.write(msg)
                    panel_id = f"freetext_{date_from}_{abs(hash(question))%10000}"
                    show_result_panel(df, sql, "custom_query", num_months, False, panel_id=panel_id)
                    st.session_state._last_sql = sql
                    st.session_state._last_question = question
                    with st.spinner("Generating exploration suggestions..."):
                        st.session_state.explorations = get_explorations(
                            question, df.columns, selected_facility, date_override_from, date_override_to
                        )
                    st.rerun()
            else:
                with st.chat_message("assistant"):
                    st.write(llm_response)
                st.session_state.messages.append({"role": "assistant", "content": llm_response})
