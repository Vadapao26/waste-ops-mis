"""LLM layer: Groq client, schema/domain context, and prompt construction.

Optimizations vs the original version:
  - Groq client is cached (@st.cache_resource) instead of rebuilt on every
    Streamlit rerun.
  - db_context.json is parsed once and cached (@st.cache_data) instead of
    re-read from disk on every single LLM call.
  - get_clarifications/get_explorations were ~90% duplicate code (same prompt
    scaffolding, same Groq call, same JSON parsing) — merged into one
    get_suggestions() with a `mode` flag.
  - ask_groq_sql/ask_llm were the same duplication — merged into one
    generate_sql().
"""
import os
import json
import streamlit as st
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"

SCHEMA_CONTEXT = """
TABLE: inward — waste received at facility
COLUMNS: inward_code, date, facility, received_material_from (vendor), vendor_location (ward),
source (ULB/Bulk waste generator/Aggregator/DWCC/SHGc/Waste Picker), source_type, authorisation,
driver_name, vehicle_vendor_name, vehicle_number, material, inward_material_category,
received_quantity, accepted_quantity, rejected_quantity, value_of_accepted_material,
net_procurement_cost, transportation_cost, loading_cost, additional_cost
NOTE: NO destination column. Use facility for facility name.

TABLE: production — processing runs
COLUMNS: production_code, date, facility, shift (Day/Night/General),
process_equipment, material_quantity (output kg), no_of_staff_present, time_taken_in_hrs

TABLE: outward — material dispatched to customers
COLUMNS: outward_code, date, facility, customer, destination, vendor_type, authorisation,
material, outward_material_category, dispatched_quantity, accepted_quantity, rejected_quantity,
value_of_accepted_material, net_material_sales_cost, total_incentive_cost, transportation_cost,
loading_cost, additional_transport_cost

TABLE: expense — date, facility, category, bill_amount_in_rs
TABLE: revenue — date, facility, category, bill_amount_in_rs
NOTE: Use PostgreSQL syntax. Use TO_CHAR(date::date,'YYYY-MM') for monthly grouping instead of strftime.
"""


@st.cache_resource
def get_groq_client(api_key: str) -> Groq:
    return Groq(api_key=api_key)


@st.cache_data
def _load_db_context_raw() -> dict:
    ctx_path = os.path.join(os.path.dirname(__file__), "db_context.json")
    try:
        return json.load(open(ctx_path))
    except Exception:
        return {}


def get_db_context(facility: str = "All Facilities") -> str:
    """Builds the facility-specific domain context block. The underlying JSON
    is cached (see above); only this cheap string formatting runs per-call."""
    ctx = _load_db_context_raw()
    if not ctx:
        return ""
    lines = []

    g = ctx.get("_global", {})
    if g:
        lines.append("=== TERMINOLOGY ALIASES (treat these as the same thing) ===")
        for col, aliases in g.get("terminology_aliases", {}).items():
            lines.append(f"  '{col}' = also called: {', '.join(aliases)}")
        lines.append("\n=== MATERIAL NAME ALIASES ===")
        for mat, aliases in g.get("material_aliases", {}).items():
            lines.append(f"  '{mat}' = also called: {', '.join(aliases)}")
        lines.append("\n=== SQL RULES (always follow these) ===")
        for rule in g.get("sql_rules", []):
            lines.append(f"  - {rule}")

    facilities = [k for k in ctx.keys() if not k.startswith("_")] if facility == "All Facilities" else [facility]
    for f in facilities:
        if f not in ctx:
            continue
        d = ctx[f]
        lines.append(f"\n=== {f.upper()} FACILITY ===")
        if d.get("description"):
            lines.append(f"  About: {d['description']}")
        if d.get("key_vendors"):
            lines.append(f"  Key vendors [column: received_material_from]: {', '.join(d['key_vendors'])}")
        if d.get("key_ward_locations"):
            lines.append(f"  Ward locations [column: vendor_location]: {', '.join(d['key_ward_locations'][:10])}")
        if d.get("key_materials"):
            lines.append(f"  Key materials [column: material]: {', '.join(d['key_materials'])}")
        if d.get("notes"):
            lines.append(f"  Notes: {d['notes']}")

    return "\n".join(lines)


def get_conversation_context() -> str:
    hist = st.session_state.get("conversation_history", [])
    if not hist:
        return ""
    lines = ["PREVIOUS QUESTIONS IN THIS SESSION:"]
    for i, h in enumerate(hist[-5:]):
        lines.append(f"  Q{i+1}: {h['question']}")
        if h.get("clarification"):
            lines.append(f"  → Chose: {h['clarification']}")
        if h.get("sql"):
            lines.append(f"  → SQL: {h['sql'][:120]}...")
    return "\n".join(lines)


def _call_groq(client: Groq, prompt: str, system: str = None, temperature: float = 0.2) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    response = client.chat.completions.create(model=GROQ_MODEL, messages=messages, temperature=temperature)
    return response.choices[0].message.content


def get_suggestions(client: Groq, mode: str, question: str, facility: str, date_from: str, date_to: str,
                     df_columns=None) -> list:
    """Replaces the old get_clarifications/get_explorations pair.
    mode: 'clarify' (before running a query) or 'explore' (after, based on results)."""
    db_context = get_db_context(facility)
    conv_context = get_conversation_context()

    if mode == "clarify":
        prompt = f"""You are a waste operations data analyst assistant.
{conv_context}
User question: "{question}"
Facility: {facility} | Date range: {date_from} to {date_to}
{db_context}

Generate 4-5 SPECIFIC clarification options that directly answer what the user asked.
Rules:
- Each option must be a DIFFERENT angle on the SAME question (not generic options)
- Use actual column names, metric names, or entity names from the data context
- Options should be actionable SQL queries (e.g. "By vendor", "Monthly trend", "Top 10 by weight")
- If question mentions a specific topic (training, inward, outward), ALL options must relate to that topic
- Do NOT suggest unrelated topics
Return ONLY a JSON array: [{{"label": "short label", "description": "what this shows"}}]
No other text, no markdown, no explanation."""
    else:
        cols = ", ".join(list(df_columns)[:8]) if df_columns is not None else ""
        prompt = f"""Waste management analyst.
{conv_context}
User asked: "{question}", result columns: {cols}
Facility: {facility}
{db_context}

Generate 4-5 specific follow-up exploration options based on this result.
Return ONLY a JSON array with "label" and "description" keys. No other text."""

    try:
        text = _call_groq(client, prompt, temperature=0.2 if mode == "clarify" else 0.3).strip()
        start, end = text.find("["), text.rfind("]") + 1
        return json.loads(text[start:end]) if start != -1 and end > start else []
    except Exception as e:
        st.error(f"⚠️ Groq AI is unavailable: {e}. Please check your API key or try again shortly.")
        return []


def generate_sql(client: Groq, question: str, clarification: str, facility: str, date_from: str, date_to: str,
                  original_sql: str = None) -> str:
    """Replaces the old ask_groq_sql/ask_llm pair — same prompt shape either way,
    clarification is optional (empty string for a direct free-text question)."""
    if facility != "All Facilities":
        fclause = f"WHERE facility='{facility}' AND date>='{date_from}' AND date<='{date_to}'"
        fnote = f"Filter by facility='{facility}'"
    else:
        fclause = f"WHERE date>='{date_from}' AND date<='{date_to}'"
        fnote = "No facility filter. Include facility in SELECT."

    db_context = get_db_context(facility)
    conv_context = get_conversation_context()
    prior_sql_note = f"\nPrevious SQL: {original_sql[:200]}\n" if original_sql else ""
    want = clarification or question

    prompt = f"""PostgreSQL SQL expert for waste management database.
{conv_context}
{prior_sql_note}
Question: {question}
User wants: {want}
Filter: {fclause}
{fnote}

RULES:
1. Return ONLY SQL in ```sql ``` blocks
2. PostgreSQL syntax. Semicolon. NULLIF. LIMIT 500.
3. Use TO_CHAR(date::date,'YYYY-MM') AS month for monthly grouping
4. inward vendor=received_material_from (LIKE '%name%'). NO destination in inward.
5. production output=material_quantity. outward customer=customer column.
6. Use LIKE '%name%' for partial name matching
7. Read-only: SELECT / WITH statements only. Never DROP, DELETE, UPDATE, INSERT, ALTER.

{db_context}
{SCHEMA_CONTEXT}

Write SQL for: {question} — showing {want}"""

    try:
        return _call_groq(client, prompt, system="Return ONLY SQL in ```sql ``` blocks.", temperature=0.1)
    except Exception as e:
        return f"Error: {e}"
