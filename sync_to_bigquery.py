"""
sync_to_bigquery.py
Google Sheets → BigQuery direct sync
Run locally: python3 sync_to_bigquery.py

This replaces sync_to_supabase.py. All the Google Sheets reading and pandas
cleaning logic (load_facility, clean_cols, process_inward, etc.) is unchanged
from the Supabase version — only the write target changed. One real
improvement made while rebuilding this: several cost columns (loading_cost,
additional_cost, additional_transport_cost, total_incentive_cost) were never
actually cast to numeric in the old script — they stayed TEXT in Supabase,
which is exactly why db.py needed a whole workaround (TEXT_NUMERIC_COLS /
sanitize_sql) to cast them at query time. They're cast here now, at the
source, so BigQuery stores them properly typed and no query-time workaround
is needed for them at all.
"""
import os
import json
import time
import warnings
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from google.cloud import bigquery

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("BQ_PROJECT_ID", "waste-ops-mis-507604")
DATASET = os.environ.get("BQ_DATASET", "waste_ops")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Google credentials — from env var (GitHub Actions) or local file. Same
# service account now covers both Sheets and BigQuery access.
CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
CREDS_PATH = os.path.join(SCRIPT_DIR, "credentials.json")

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
    "Trading Data RPG/External Transfers- SCM (MRF)": "1M3hERqCou1dDkeUCEynimECXkIw2V0f7SVOeIaHN34I",
    "Interim PRF (Sarvam Jigani)": "1q9lpAv2SvGaNeHpycgwsoaFhn1byxET7CxXt1_I2yCQ",
}

TABLES = ["inward", "outward", "production", "expense", "revenue", "training", "training_attendees"]

# ── GOOGLE SHEETS CONNECTION ───────────────────────────────────────────────────
def connect_gsheets():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    if CREDS_JSON:
        creds_dict = json.loads(CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    return gspread.authorize(creds)


# ── BIGQUERY CONNECTION ────────────────────────────────────────────────────────
def connect_bigquery():
    """Separate credentials object with BigQuery's own scope — the same
    underlying key file as Sheets, but Credentials objects are scope-specific
    once created, so this is built independently rather than reusing the
    Sheets-scoped one above."""
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if CREDS_JSON:
        creds_dict = json.loads(CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


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
        raise
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
                     "transportation_cost", "loading_cost", "additional_cost"])
    drop = [c for c in df.columns if any(x in c for x in
            ["photo", "gps", "invoice", "slip", "challan", "lr_copy", "e_way", "acknowledgement"])]
    return df.drop(columns=drop, errors="ignore")

def process_production(df):
    df = clean_cols(df)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = to_num(df, ["material_quantity", "no_of_staff_present",
                     "time_taken_in_hrs", "total_rejection_in_kg"])
    # quantity_in_kg deliberately NOT cast here — same as the original script.
    # It's genuinely text-ish/inconsistent at the source (per llm.py's schema
    # notes), and queries.py already handles it explicitly with
    # NULLIF(quantity_in_kg,'')::numeric wherever it's used.
    return df

def process_outward(df):
    df = clean_cols(df)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = to_num(df, ["accepted_quantity", "rejected_quantity", "dispatched_quantity",
                     "net_material_sales_cost", "rate", "value_of_accepted_material",
                     "total_incentive_cost", "transportation_cost", "loading_cost",
                     "additional_transport_cost"])
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


# ── BIGQUERY WRITE ────────────────────────────────────────────────────────────
def write_table(bq_client, table_name, df):
    """Replaces a table's full contents — the BigQuery equivalent of the old
    df.to_sql(..., if_exists='replace'). autodetect=True infers types fresh
    from the dataframe's actual dtypes each run.

    IMPORTANT: a pandas datetime64 column can autodetect as BigQuery TIMESTAMP
    or DATETIME rather than DATE, depending on the load path — and a
    date-range filter like `date BETWEEN '2026-07-01' AND '2026-07-31'`
    behaves differently against a TIMESTAMP (midnight cutoff, could silently
    exclude same-day records with a nonzero time component) than against a
    true DATE. Rather than rely on autodetect's choice, the date column is
    explicitly converted to Python date objects here — that reliably loads
    as BigQuery DATE every time, removing the ambiguity rather than hoping
    autodetect picks correctly."""
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    table_ref = f"{PROJECT_ID}.{DATASET}.{table_name}"
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", autodetect=True)
    job = bq_client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # blocks until the load finishes or raises


# ── MAIN SYNC ─────────────────────────────────────────────────────────────────
def sync():
    print("🌐 Connecting to Google Sheets...")
    client = connect_gsheets()

    print("🔌 Connecting to BigQuery...")
    bq_client = connect_bigquery()
    bq_client.get_dataset(f"{PROJECT_ID}.{DATASET}")  # raises if dataset doesn't exist / no access
    print("✅ BigQuery connection OK!")

    all_inward, all_production, all_outward, all_expense, all_revenue = [], [], [], [], []

    facility_data = {}
    failed_facilities = []
    for facility, sheet_id in SHEETS.items():
        print(f"\n📊 Loading {facility}...")
        try:
            data = load_facility(client, facility, sheet_id)
        except Exception as e:
            print(f"  ❌ {facility} failed to load: {e}")
            failed_facilities.append(facility)
            time.sleep(3)
            continue
        facility_data[facility] = data
        time.sleep(3)

        try:
            if "Inward"     in data: all_inward.append(process_inward(data["Inward"]))
            if "Production" in data: all_production.append(process_production(data["Production"]))
            if "Outward"    in data: all_outward.append(process_outward(data["Outward"]))
            if "Expenses"   in data: all_expense.append(process_expense(data["Expenses"]))
            if "Revenue"    in data: all_revenue.append(process_revenue(data["Revenue"]))
        except Exception as e:
            print(f"  ❌ {facility} failed to process: {e}")
            failed_facilities.append(facility)

    if failed_facilities:
        print(f"\n⚠️  {len(failed_facilities)} facilit{'y' if len(failed_facilities)==1 else 'ies'} "
              f"failed to load: {', '.join(failed_facilities)}")
        print("⚠️  ABORTING WRITE — refusing to replace tables with partial data.")
        return

    print("\n🔎 Validating before write...")
    datasets = {
        "inward":     all_inward,
        "production": all_production,
        "outward":    all_outward,
        "expense":    all_expense,
        "revenue":    all_revenue,
    }

    concatenated = {}
    for table, dfs in datasets.items():
        if not dfs:
            continue
        for df in dfs:
            dup_cols = df.columns[df.columns.duplicated()].unique().tolist()
            if dup_cols:
                facility_label = df["facility"].iloc[0] if "facility" in df.columns and len(df) else "unknown facility"
                print(f"\n❌ Duplicate column(s) {dup_cols} in {table} data for '{facility_label}' after cleaning.")
                print("⚠️  ABORTING WRITE — refusing to write any table until this is fixed.")
                return
        concatenated[table] = pd.concat(dfs, ignore_index=True)

    print("\n💾 Writing to BigQuery...")
    for table, df in concatenated.items():
        write_table(bq_client, table, df)
        print(f"  ✅ {table}: {len(df)} rows written")
    for table, dfs in datasets.items():
        if not dfs:
            print(f"  ⚠️  {table}: no data")

    # Training tables
    all_training, all_training_attendees = [], []
    for facility, data in facility_data.items():
        if "Training" in data:
            all_training.append(process_training(data["Training"], facility))
            all_training_attendees.append(process_training_attendees(data["Training"], facility))

    for table, dfs in [("training", all_training), ("training_attendees", all_training_attendees)]:
        if not dfs:
            print(f"  ⚠️  {table}: no data")
            continue
        dup_found = False
        for df in dfs:
            dup_cols = df.columns[df.columns.duplicated()].unique().tolist()
            if dup_cols:
                facility_label = df["facility"].iloc[0] if "facility" in df.columns and len(df) else "unknown facility"
                print(f"\n❌ Duplicate column(s) {dup_cols} in {table} data for '{facility_label}'.")
                print(f"⚠️  Skipping {table} write — other tables already written this run are unaffected.")
                dup_found = True
                break
        if dup_found:
            continue
        df = pd.concat(dfs, ignore_index=True)
        write_table(bq_client, table, df)
        print(f"  ✅ {table}: {len(df)} rows written")

    print("\n✅ Sync complete!")

    print("\n📊 Verifying row counts:")
    for table in TABLES:
        try:
            result = bq_client.query(f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.{DATASET}.{table}`").result()
            print(f"  {table}: {list(result)[0].n} rows")
        except Exception as e:
            print(f"  {table}: {e}")

if __name__ == "__main__":
    sync()
