# Waste Ops MIS

**An AI-powered Management Information System for waste processing facilities — built for WiseWaste.**

---

## The Problem It Solves

WiseWaste operates 10 waste processing facilities across Bangalore. Each facility records daily operations — inward procurement, outward sales, production, expenses, and revenue — across separate Google Sheets. Before this system:

- Managers had to manually compile data from multiple sheets to answer basic questions like "What was our rejection rate at Hebbagodi last month?"
- Cross-facility comparisons required hours of spreadsheet work
- There was no single source of truth for operations data
- AI-assisted querying didn't exist — every insight required manual analysis

**Waste Ops MIS solves this by centralising all facility data into a single cloud database and wrapping it with a natural language AI interface.** Any manager can now ask questions in plain English and get instant answers with charts and tables — no SQL knowledge required.

---

## Architecture

```
Google Sheets (10 Facilities)
        │
        ▼
GitHub Actions (Weekly Sync — Every Monday 6 AM IST)
        │
        ▼
sync_to_supabase.py
        │
        ▼
Supabase PostgreSQL (Cloud Database)
        │
        ▼
Streamlit App (app.py) ◄──── Groq AI (llama-3.3-70b-versatile)
        │
        ▼
Browser (waste-ops-mis.streamlit.app)
```

**Data Flow:**
1. Facility teams update Google Sheets daily as usual
2. Every Monday, GitHub Actions triggers `sync_to_supabase.py`
3. Script pulls all 10 facility sheets, cleans and standardises the data, and writes to Supabase PostgreSQL
4. Streamlit app reads from Supabase on every query
5. Groq AI (llama-3.3-70b-versatile) translates natural language questions into SQL and returns results

---

## Languages & Libraries

| Layer | Technology |
|---|---|
| Frontend | Streamlit 1.50 |
| AI / LLM | Groq API — llama-3.3-70b-versatile |
| Database | Supabase PostgreSQL (cloud) |
| ORM | SQLAlchemy 2.0 + psycopg2 |
| Data Processing | Pandas, NumPy |
| Charts | Plotly Express & Graph Objects |
| Authentication | Custom bcrypt login (no third-party auth library) |
| Google Sheets Sync | gspread, google-auth |
| Automation | GitHub Actions (cron schedule) |
| Language | Python 3.9+ |
| Hosting | Streamlit Cloud |
| Version Control | GitHub (private repo: Vadapao26/waste-ops-mis) |

---

## Features in Detail

### AI Natural Language Querying
Ask anything about your operations data in plain English. The system uses a two-step Groq AI flow:
1. **Clarification** — AI generates 3-4 focused query options from your question
2. **SQL Generation** — AI converts your selected option into a PostgreSQL query
3. **Result rendering** — Data returned as formatted table with download options

Conversation history is maintained within a session so follow-up questions have context.

### Preset Analytics Buttons
One-click full analysis for each operational area:
- **All Inward** — KPI summary, vendor analysis, vendor location analysis
- **All Outward** — KPI summary, customer analysis, customer destination analysis
- **All Production** — KPI summary, equipment analysis, shift analysis, equipment × shift cross-analysis
- **All ULB** — KPI summary, ward location analysis, driver analysis

### Sidebar Drill-Down
Expandable analytics sections for granular access:
- Inward Analytics, Production Analytics, Transport Analytics, ULB Analytics, BWG Analytics, Outward Analytics
- Each section contains 3-6 specific preset queries

### KPI Cards
Single-row results are automatically rendered as metric cards instead of tables — showing totals, percentages, and cost figures at a glance.

### Multi-Month Views
When data spans multiple months, results are automatically split into tabs — one tab per month plus an "All Data" tab.

### Role-Based Access Control
- **Admin** — sees all facilities, can switch between any facility in the sidebar
- **Manager** — locked to their assigned facility only

### Data Export
Every result panel includes Download CSV and Download Excel buttons.

### Weekly Automated Sync
GitHub Actions workflow pulls fresh data from all 10 Google Sheets every Monday at 6 AM IST and writes to Supabase. Can also be triggered manually from GitHub Actions UI at any time.

---

## Supported Datasets

| Table | Description | Key Columns |
|---|---|---|
| `inward` | Inward procurement records | date, facility, material, vendor, received_qty, accepted_qty, rejected_qty, net_procurement_cost |
| `outward` | Outward sales and dispatch | date, facility, material, customer, destination, dispatched_qty, net_material_sales_cost |
| `production` | Processing and production logs | date, facility, material, equipment, shift, material_quantity, rejection_kg |
| `expense` | Facility operating expenses | date, facility, category, amount |
| `revenue` | Facility revenue records | date, facility, source, amount |

**Facilities covered:** Hebbagodi, MRF, Muguluru, Jigani, GPR, Anekal, Marsur, Mayasandra, Bommasandra, Attibele

---

## Use Cases

**Operations Manager — Daily Use**
- "What was the rejection rate at Hebbagodi in June 2026?"
- "Show me vendor-wise procurement cost for MRF from January to March"
- "Which shift had the highest production output last month?"

**Senior Management — Weekly Review**
- Click All Inward → see KPI summary across all facilities for the month
- Compare facility performance side by side
- Download Excel reports for board presentations

**Finance Team — Monthly Reconciliation**
- Query net procurement cost vs material sales cost
- Cross-check expense records against revenue
- Export tables directly to Excel for accounting

**Operations Analyst — Ad Hoc Analysis**
- Ask multi-month trend questions in natural language
- Get exploration suggestions after each query for deeper dives
- Chain follow-up questions in the same session

---

## Time Saved

| Task | Before | After |
|---|---|---|
| Monthly KPI report across all facilities | 4-6 hours manual compilation | 30 seconds |
| Vendor rejection rate analysis | 45 minutes | 10 seconds |
| Cross-facility comparison | 2-3 hours | 1 click |
| Ad hoc data question | Email ops team, wait 24 hours | Instant |
| Weekly data consolidation | 1-2 hours manual copy-paste | Fully automated |

**Estimated time saved per week: 8-12 hours across the team.**

---

## Input / Output

**Input:**
- Natural language questions typed into the chat bar
- One-click preset analytics buttons (All Inward, All Outward, All Production, All ULB)
- Sidebar drill-down buttons for specific metrics
- Facility selector and date range (From month/year → To month/year)

**Output:**
- Interactive data tables with auto-formatting (currency columns, totals row)
- KPI metric cards for summary data
- Multi-month tabbed views for trend data
- Plotly charts where relevant
- Download buttons for CSV and Excel export
- SQL query viewer (expandable) for transparency
- AI-generated exploration suggestions for follow-up queries

---

## How to Use

### Web App (Streamlit Cloud)
1. Go to [waste-ops-mis-2pm5wqqb2mp4yrgc6q7v4y.streamlit.app](https://waste-ops-mis-2pm5wqqb2mp4yrgc6q7v4y.streamlit.app)
2. Login with your username and password
3. Select a **Facility** from the sidebar dropdown
4. Select a **Time Period** (From month/year → To month/year)
5. Click any analytics button or type a question in the chat bar

### Local Development
```bash
# Clone the repo
git clone https://github.com/Vadapao26/waste-ops-mis.git
cd waste-ops-mis

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Add secrets
mkdir -p .streamlit
# Create .streamlit/secrets.toml with Supabase URL, Groq key, and credentials

# Run
streamlit run app.py
```

### Adding a New User
1. Generate a bcrypt password hash:
```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
```
2. Go to Streamlit Cloud → your app → Settings → Secrets
3. Add a new block:
```toml
[credentials.usernames.new_username]
name = 'Full Name'
email = 'email@example.com'
password = '$2b$12$...'  # paste hash here
role = 'admin'           # or 'operator'
facility = 'All Facilities'  # or specific facility name
```

### Triggering a Manual Data Sync
1. Go to [github.com/Vadapao26/waste-ops-mis/actions](https://github.com/Vadapao26/waste-ops-mis/actions)
2. Click **Weekly Sheets → Supabase Sync**
3. Click **Run workflow → Run workflow**
4. Wait ~7 minutes for all 10 facilities to sync

### Pushing Code Changes
```bash
cd waste-ops-mis
# make your changes to app.py
git add app.py
git commit -m "describe your change"
git push
# Streamlit Cloud auto-redeploys within 60 seconds
```

---

## Project Structure

```
waste-ops-mis/
├── app.py                    # Main Streamlit application
├── sync_to_supabase.py       # Google Sheets → Supabase sync script
├── requirements.txt          # Python dependencies
├── db_context.json           # Facility-specific entity context for AI
├── .github/
│   └── workflows/
│       └── sync.yml          # GitHub Actions weekly sync schedule
├── .gitignore                # Excludes secrets.toml and credentials.json
└── README.md                 # This file
```

---

## Environment Variables / Secrets

| Secret | Where Set | Description |
|---|---|---|
| `supabase.url` | Streamlit Cloud Secrets | PostgreSQL connection string |
| `groq.api_key` | Streamlit Cloud Secrets | Groq API key for LLM queries |
| `credentials.usernames.*` | Streamlit Cloud Secrets | User login credentials |
| `SUPABASE_URL` | GitHub Actions Secrets | Same connection string for sync |
| `GOOGLE_CREDENTIALS_JSON` | GitHub Actions Secrets | Google Service Account JSON key |

---

*Built for WiseWaste — Bangalore's circular economy waste processing network.*
