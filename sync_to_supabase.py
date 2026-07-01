"""
sync_to_supabase.py
Google Sheets → Supabase (PostgreSQL) direct sync
Run locally: python3 sync_to_supabase.py
Run via GitHub Actions: triggered by schedule
"""
import os
import json
import time
import warnings
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("SUPABASE_DB_URL")

# For local runs, fall back to secrets.toml
if not SUPABASE_URL:
    try:
        import tomllib
        with open(os.path.expanduser("~/database-chatbot/.streamlit/secrets.toml"), "rb") as f:
            secrets = tomllib.load(f)
        SUPABASE_URL = secrets["supabase"]["url"]
    except Exception:
        try:
            import toml
            secrets = toml.load(os.path.expanduser("~/database-chatbot/.streamlit/secrets.toml"))
            SUPABASE_URL = secrets["supabase"]["url"]
        except Exception as e:
            raise ValueError(f"Could not load Supabase URL: {e}")

# Google credentials — from env var (GitHub Actions) or local file
CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
CREDS_PATH = os.path.expanduser("~/database-chatbot/credentials.json")

SHEETS = {
    "Hebbagodi":   "1pbZRXWlzqZycbGYp0d_Whr03lNt85-_AlIRBLd8UZEE",
    "MRF":         "1O4r6x_Vg9bWYEQCTr9s44bDCGEMcssNQRNG_k9QTpFo",
    "Muguluru":    "1XC1Qj33C8wnQrTTNgx_Hp-on1uJpY29JL3nEkhl9cTE",
    "Jigani":      "17_KNyQUN36Q95lYskW3yY5TasfNj-vdSDn-lIwrtsd4",
    "GPR":         "1Ja2CicDFVJ0Omtz2SzMdbO8HvYpzUp6rPq8ihTmHVi4",
    "Anekal":      "1SQiSqPjQAeFnK1lR_RbWMAnqLz5gb7xFs_rPdvg7wX8",
    "Marsur":      "1VbxVVv8IGHCF1zxEtDzruSVQPDb-wRUE81Tw4AXDOZQ",
    "Mayasandra":  "13yjQc6t3Vuhlr5UqbWXks-EFnrfWW1eJ2MCe1gezrCE",
    "Bommasandra": "1LrY9ClFEdDDknc5N9DRi6NlmCWOhm_1_uuf__nVqaVg",
    "Attibele":    "1-yelqRAIhS51Etic4eLrwo4lSLSs0nqbM2SPXhCGxVQ",
}

TABLES = ["inward", "outward", "production", "expense", "revenue", "training", "training_attendees"]

# ── GOOGLE SHEETS CONNECTION ───────────────────────────────────────────────────
def connect_gsheets():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    if CREDS_JSON:
        # GitHub Actions: credentials passed as environment variable
        creds_dict = json.loads(CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # Local: use credentials.json file
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    return gspread.authorize(creds)

# ── DATA LOADING ──────────────────────────────────────────────────────────────
def load_facility(client, facility, sheet_id):
    result = {}
    try:
        wb = client.open_by_key(sheet_id)
        print(f"  Connected to {facility}")
        time.sleep(2)
        for sheet_name in ["Inward", "Production", "Outward", "Expenses", "Revenue", "Training"]:
            try:
                ws = wb.worksheet(sheet_name)
                time.sleep(1.5)
                data = ws.get_all_values()
                if len(data) < 2:
                    print(f"  ⚠️  {facility} - {sheet_name}: empty")
                    continue
                df = pd.DataFrame(data[1:], columns=data[0])
                df = df.replace("", pd.NA)
                df["facility"] = facility
                result[sheet_name] = df
                print(f"  ✅ {facility} - {sheet_name}: {len(df)} rows")
                time.sleep(1.5)
            except Exception as e:
                print(f"  ⚠️  {facility} - {sheet_name}: {e}")
                time.sleep(3)
    except Exception as e:
        print(f"  ⚠️  Could not connect to {facility}: {e}")
    return result

# ── DATA CLEANING ─────────────────────────────────────────────────────────────
def clean_cols(df):
    df.columns = [
        c.strip().lower()
         .replace(" ", "_").replace("(", "").replace(")", "")
         .replace("-", "_").replace(".", "").replace("/", "_")
        for c in df.columns
    ]
    return df

def to_num(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def process_inward(df):
    df = clean_cols(df)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = to_num(df, ["accepted_quantity", "rejected_quantity", "received_quantity",
                     "net_procurement_cost", "rate", "value_of_accepted_material",
                     "transportation_cost"])
    drop = [c for c in df.columns if any(x in c for x in
            ["photo", "gps", "invoice", "slip", "challan", "lr_copy", "e_way", "acknowledgement"])]
    return df.drop(columns=drop, errors="ignore")

def process_production(df):
    df = clean_cols(df)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = to_num(df, ["material_quantity", "no_of_staff_present",
                     "time_taken_in_hrs", "total_rejection_in_kg"])
    return df

def process_outward(df):
    df = clean_cols(df)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = to_num(df, ["accepted_quantity", "rejected_quantity", "dispatched_quantity",
                     "net_material_sales_cost", "rate", "value_of_accepted_material",
                     "total_incentive_cost", "transportation_cost"])
    drop = [c for c in df.columns if any(x in c for x in
            ["photo", "gps", "invoice", "slip", "challan", "lr_copy", "e_way"])]
    return df.drop(columns=drop, errors="ignore")

def process_expense(df):
    df = clean_cols(df)
    date_col = "record_date" if "record_date" in df.columns else "date"
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce")
        if date_col != "date":
            df["date"] = df[date_col]
    amount_cols = [c for c in df.columns if "amount" in c or "bill" in c]
    return to_num(df, amount_cols)

def process_revenue(df):
    df = clean_cols(df)
    date_col = "record_date" if "record_date" in df.columns else "date"
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce")
        if date_col != "date":
            df["date"] = df[date_col]
    amount_cols = [c for c in df.columns if "amount" in c or "bill" in c]
    return to_num(df, amount_cols)

def parse_duration_mins(d):
    try:
        parts = str(d).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0

TRAINER_MAP = {
    'Khadar': 'Khadar', 'Vishal': 'Vishal', 'Pravin': 'Praveen Vandal',
    'Praveen': 'Praveen Vandal', 'Praveen and lipak': 'Praveen Vandal & Lipak Behera',
    'Praveen Vandal and Lipak Behera': 'Praveen Vandal & Lipak Behera',
    'Ganesh Shetty': 'Ganesh Shetty', 'Jayakumar (Encore Technician)': 'Jayakumar',
    'Chandrashekar': 'Chandrashekar',
    'Jaganath Ram (from Integrated Pacline India Pvt. Ltd)': 'Jaganath Ram',
    'Siddhesh': 'Siddhesh', 'Siddhesh and Akarsh': 'Siddhesh & Akarsh',
    'Admin Infra/IIH': 'Admin Infra/IIH',
}

def process_training(df, facility):
    df = clean_cols(df)
    # Rename category column if it came in as training_category
    if 'training_category' in df.columns:
        df = df.rename(columns={'training_category': 'category'})
    df['date'] = pd.to_datetime(df['training_date'], dayfirst=True, errors='coerce')
    df['month'] = df['date'].dt.strftime('%Y-%m')
    df['duration_mins'] = df['duration'].apply(parse_duration_mins)
    df['attendee_count'] = df['attendee_names'].apply(
        lambda x: len([a.strip() for a in str(x).split(',') if a.strip() and a.strip().lower() != 'nan']) if pd.notna(x) else 0
    )
    df['trainer'] = df['conducted_by'].str.strip().map(TRAINER_MAP).fillna(df['conducted_by'].str.strip())
    df['facility'] = facility
    keep = ['training_code','date','month','topics_of_training','category','location','trainer','duration_mins','attendee_count','facility']
    keep = [c for c in keep if c in df.columns]
    result = df[keep].copy()
    result = result.rename(columns={'topics_of_training': 'topic'})
    return result

def process_training_attendees(df, facility):
    df = clean_cols(df)
    if 'training_category' in df.columns:
        df = df.rename(columns={'training_category': 'category'})
    df['date'] = pd.to_datetime(df['training_date'], dayfirst=True, errors='coerce')
    df['month'] = df['date'].dt.strftime('%Y-%m')
    df['duration_mins'] = df['duration'].apply(parse_duration_mins)
    df['trainer'] = df['conducted_by'].str.strip().map(TRAINER_MAP).fillna(df['conducted_by'].str.strip())

    rows = []
    for _, row in df.iterrows():
        names = [n.strip() for n in str(row.get('attendee_names','')).split(',') if n.strip() and n.strip().lower() != 'nan']
        roles = [r.strip() for r in str(row.get('attendee_roles','')).split(',') if r.strip() and r.strip().lower() != 'nan']
        facilities = [f.strip() for f in str(row.get('attendee_facilities','')).split(',') if f.strip() and f.strip().lower() != 'nan']
        for i, name in enumerate(names):
            role = roles[i] if i < len(roles) else 'Unknown'
            if role == 'Project Cordinator': role = 'Project Coordinator'
            att_facility = facilities[i] if i < len(facilities) else 'Unknown'
            rows.append({
                'training_code': row.get('training_code'),
                'date': row['date'],
                'month': row['month'],
                'topic': row.get('topics_of_training'),
                'category': row.get('category'),
                'trainer': row['trainer'],
                'duration_mins': row['duration_mins'],
                'attendee_name': name,
                'attendee_role': role,
                'attendee_facility': att_facility,
                'session_facility': facility,
                'facility': facility,
            })
    return pd.DataFrame(rows)

# ── MAIN SYNC ─────────────────────────────────────────────────────────────────
def sync():
    print("🌐 Connecting to Google Sheets...")
    client = connect_gsheets()

    print("🔌 Connecting to Supabase...")
    engine = create_engine(SUPABASE_URL)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("✅ Supabase connection OK!")

    all_inward, all_production, all_outward, all_expense, all_revenue = [], [], [], [], []

    facility_data = {}
    for facility, sheet_id in SHEETS.items():
        print(f"\n📊 Loading {facility}...")
        data = load_facility(client, facility, sheet_id)
        facility_data[facility] = data
        time.sleep(3)

        if "Inward"     in data: all_inward.append(process_inward(data["Inward"]))
        if "Production" in data: all_production.append(process_production(data["Production"]))
        if "Outward"    in data: all_outward.append(process_outward(data["Outward"]))
        if "Expenses"   in data: all_expense.append(process_expense(data["Expenses"]))
        if "Revenue"    in data: all_revenue.append(process_revenue(data["Revenue"]))

    print("\n💾 Writing to Supabase...")
    datasets = {
        "inward":     all_inward,
        "production": all_production,
        "outward":    all_outward,
        "expense":    all_expense,
        "revenue":    all_revenue,
    }

    for table, dfs in datasets.items():
        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df.to_sql(table, engine, if_exists="replace", index=False,
                      chunksize=500, method="multi")
            print(f"  ✅ {table}: {len(df)} rows written")
        else:
            print(f"  ⚠️  {table}: no data")

    # Training tables
    all_training, all_training_attendees = [], []
    for facility, data in facility_data.items():
        if "Training" in data:
            all_training.append(process_training(data["Training"], facility))
            all_training_attendees.append(process_training_attendees(data["Training"], facility))

    for table, dfs in [("training", all_training), ("training_attendees", all_training_attendees)]:
        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df.to_sql(table, engine, if_exists="replace", index=False,
                      chunksize=500, method="multi")
            print(f"  ✅ {table}: {len(df)} rows written")
        else:
            print(f"  ⚠️  {table}: no data")

    print("\n✅ Sync complete!")

    print("\n📊 Verifying row counts:")
    with engine.connect() as conn:
        for table in TABLES:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                print(f"  {table}: {result.fetchone()[0]} rows")
            except Exception as e:
                print(f"  {table}: {e}")

if __name__ == "__main__":
    sync()
