"""GHG emissions calculations.

Transport emissions: combines three independently-built and verified
pieces --

  Phase B: geocoded facility/vendor/customer locations (ghg_entity_locations)
  Phase C: auto-calculated distance (distance_calc.py)
  Phase D: inferred vehicle size from material + loading type + quantity
           (vehicle_size.py), mapped to the official SFC/TCI-IIMB
           emission-factor class

Loading type: inward is always loose; outward is always bagged (not yet
tracked per-shipment -- documented placeholder, confirmed).

Vehicle size is inferred once per DELIVERY (grouped by inward_code /
outward_code -- one code is one real truck trip, even when it spans
multiple rows for different materials collected on that trip), not once
per material row -- a single 1000kg mixed-material trip is one shipment,
not several small ones.

Output structure (redesigned after a real reporting bug: the original
version aggregated every delivery across the whole date range into one
row per vehicle class, which made ~50 separate ~1t deliveries over 3
months look like a single 54t truck):

  daily_breakdown  -- ONE ROW PER DELIVERY PER MATERIAL. Never aggregated
                       across dates. This is the ground truth -- every
                       number here traces to one real, dated shipment.
  summary_by_partner -- daily_breakdown grouped by (direction, entity),
                       for a "who contributes most emissions" rollup.
                       Aggregation is fine here since it's explicitly a
                       summary, not presented as a single shipment.
  totals           -- dict with the overall figures (total/inward/outward
                       kg CO2e, matched/excluded tonnes, note).

Final formula: emissions (kg CO2e) = tonnes x distance_km x WTW emission
factor (kg CO2e/tonne-km) for the inferred vehicle class -- standard
tonne-km methodology.
"""
import re
import pandas as pd
from google.cloud import bigquery

from distance_calc import load_entity_coordinates, distance_between
from vehicle_size import infer_vehicle_class

INWARD_LOADING_TYPE = "loose"    # confirmed: inward material is always loose
OUTWARD_LOADING_TYPE = "bagged"  # confirmed: not yet tracked per-shipment; documented placeholder


def _normalize_entity(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def calculate_transport_emissions(client_and_dataset, facility: str, date_from: str, date_to: str) -> dict:
    """Returns transport emissions for one facility over a date range, as
    a per-delivery daily breakdown, a partner-level summary, and overall
    totals. See module docstring for why these are separate."""
    client, dataset_ref = client_and_dataset
    coords = load_entity_coordinates(client_and_dataset)
    empty_totals = {
        "total_kg_co2e": 0.0, "inward_kg_co2e": 0.0, "outward_kg_co2e": 0.0,
        "matched_tonnes": 0.0, "excluded_tonnes": 0.0, "excluded_trip_count": 0,
        "note": "",
    }
    if not coords:
        empty_totals["note"] = "No location data available yet -- run the Phase A/B location + geocoding scripts first."
        return {"daily_breakdown": pd.DataFrame(), "summary_by_partner": pd.DataFrame(), "totals": empty_totals}

    job_config_base = bigquery.QueryJobConfig(default_dataset=dataset_ref)
    inward_df = client.query(
        "SELECT inward_code, date, vendor_location, material, inward_material_category, received_quantity FROM inward "
        "WHERE facility = @facility AND date BETWEEN @d_from AND @d_to AND vendor_location IS NOT NULL",
        job_config=bigquery.QueryJobConfig(
            default_dataset=dataset_ref,
            query_parameters=[
                bigquery.ScalarQueryParameter("facility", "STRING", facility),
                bigquery.ScalarQueryParameter("d_from", "STRING", date_from),
                bigquery.ScalarQueryParameter("d_to", "STRING", date_to),
            ],
        ),
    ).to_dataframe()
    outward_df = client.query(
        "SELECT outward_code, date, destination, material, outward_material_category, dispatched_quantity FROM outward "
        "WHERE facility = @facility AND date BETWEEN @d_from AND @d_to AND destination IS NOT NULL",
        job_config=bigquery.QueryJobConfig(
            default_dataset=dataset_ref,
            query_parameters=[
                bigquery.ScalarQueryParameter("facility", "STRING", facility),
                bigquery.ScalarQueryParameter("d_from", "STRING", date_from),
                bigquery.ScalarQueryParameter("d_to", "STRING", date_to),
            ],
        ),
    ).to_dataframe()

    total_kg_co2e = 0.0
    matched_tonnes = 0.0
    excluded_tonnes = 0.0
    excluded_trip_count = 0
    inward_kg_co2e = 0.0
    outward_kg_co2e = 0.0
    daily_rows = []  # one row per delivery per material -- never aggregated across dates

    def _process(df, code_col, location_col, direction, quantity_col, category_col, loading_type):
        nonlocal total_kg_co2e, matched_tonnes, excluded_tonnes, excluded_trip_count
        nonlocal inward_kg_co2e, outward_kg_co2e
        if df.empty:
            return
        df = df.copy()
        df[quantity_col] = pd.to_numeric(df[quantity_col], errors="coerce").fillna(0)

        for code, delivery in df.groupby(code_col):
            other_location = delivery[location_col].iloc[0]
            delivery_date = delivery["date"].iloc[0]
            total_qty_kg = delivery[quantity_col].sum()
            if total_qty_kg <= 0:
                continue
            total_tonnes = total_qty_kg / 1000.0

            dist_km = distance_between(coords, facility, other_location, direction)
            if dist_km is None:
                excluded_tonnes += total_tonnes
                excluded_trip_count += 1
                continue

            # Vehicle size is inferred from the CATEGORY column (Paper/
            # Flexible Plastics/Rigid Plastics/Glass/Others/Unsorted
            # Drywaste), not the granular material name -- the material
            # name (e.g. "Mixed Rigid Plastics", "Plastics_PETE Mixed")
            # won't match vehicle_size.py's category lookup table, and was
            # silently defaulting everything to "Others" before this fix.
            dominant_idx = delivery[quantity_col].idxmax()
            dominant_category = delivery.loc[dominant_idx, category_col] or "Others"
            dominant_material_name = delivery.loc[dominant_idx, "material"] or "Others"
            vehicle = infer_vehicle_class(dominant_category, loading_type, total_qty_kg)
            delivery_kg_co2e = total_tonnes * dist_km * vehicle["wtw_kg_co2e_per_tkm"]

            total_kg_co2e += delivery_kg_co2e
            matched_tonnes += total_tonnes
            if direction == "inward":
                inward_kg_co2e += delivery_kg_co2e
            else:
                outward_kg_co2e += delivery_kg_co2e

            # One row per material within this ONE delivery -- this delivery's
            # date and code stay attached, so nothing here can later be
            # mistaken for a different, bigger shipment.
            for material, material_qty in delivery.groupby("material")[quantity_col].sum().items():
                material = material or "Others"
                share = material_qty / total_qty_kg
                daily_rows.append({
                    "date": delivery_date, "delivery_code": code,
                    "direction": direction, "entity": other_location, "material": material,
                    "category_used_for_vehicle": dominant_category,
                    "distance_km": dist_km, "tonnes": round(material_qty / 1000.0, 3),
                    "vehicle_class": vehicle["official_class"],
                    "kg_co2e": round(delivery_kg_co2e * share, 2),
                })

    _process(inward_df, "inward_code", "vendor_location", "inward", "received_quantity", "inward_material_category", INWARD_LOADING_TYPE)
    _process(outward_df, "outward_code", "destination", "outward", "dispatched_quantity", "outward_material_category", OUTWARD_LOADING_TYPE)

    daily_breakdown = pd.DataFrame(daily_rows)
    if not daily_breakdown.empty:
        daily_breakdown = daily_breakdown.sort_values(["direction", "date"]).reset_index(drop=True)
        summary_by_partner = daily_breakdown.groupby(["direction", "entity"], as_index=False).agg(
            total_tonnes=("tonnes", "sum"),
            total_kg_co2e=("kg_co2e", "sum"),
            n_deliveries=("delivery_code", "nunique"),
        ).sort_values("total_kg_co2e", ascending=False).reset_index(drop=True)
    else:
        summary_by_partner = pd.DataFrame()

    totals = {
        "total_kg_co2e": round(total_kg_co2e, 2),
        "inward_kg_co2e": round(inward_kg_co2e, 2),
        "outward_kg_co2e": round(outward_kg_co2e, 2),
        "matched_tonnes": round(matched_tonnes, 3),
        "excluded_tonnes": round(excluded_tonnes, 3),
        "excluded_trip_count": excluded_trip_count,
        "note": (f"{excluded_trip_count} trip(s) totalling {round(excluded_tonnes,1)}t excluded -- "
                 f"no coordinates on file yet for that vendor/customer location." if excluded_trip_count else
                 "All trips matched to a geocoded location."),
    }
    return {"daily_breakdown": daily_breakdown, "summary_by_partner": summary_by_partner, "totals": totals}
