"""GHG emissions calculations.

Currently implements: Transport emissions, using India-specific road-freight
emission intensity values (Smart Freight Centre + TCI-IIMB, "India Default
GHG Emission Values V1.0", May 2025).

Since actual vehicle size per trip isn't tracked, a distance-based tier
stands in for vehicle class — this is a documented simplification, not a
precise per-trip calculation, and is labeled as such in every result.

BIGQUERY MIGRATION NOTE: this module now takes a (client, dataset_ref) pair
instead of a Postgres connection string, and uses BigQuery's parameterized
query syntax (@name placeholders + QueryJobConfig.query_parameters) instead
of sqlalchemy's :name style. The ghg_distance_lookup table itself is created
by load_ghg_distances.py, which is a SEPARATE script that has NOT been
migrated to BigQuery yet (it still has Postgres-specific SQL -- SERIAL
PRIMARY KEY, etc.). Until that script is rewritten too, this table won't
exist in BigQuery, so load_distance_lookup() will hit its except branch and
return an empty DataFrame -- the same graceful "no distance data loaded yet"
behavior this already had for a facility with no rows, not a crash. The
Transport GHG feature will show "no distance data" for every facility until
load_ghg_distances.py is migrated as a follow-up.

Not yet implemented (waiting on data/methodology decisions):
  - Electricity (Scope 2) -- needs confirmation on whether bills state
    'units consumed' directly vs. back-calculating from bill amount / rate.
  - Machinery breakdown -- needs equipment operating-hours source confirmed
    (production.time_taken_in_hrs) once electricity totals exist to break down.
  - Material recovery avoided-emissions -- needs India-appropriate per-material
    factors, not yet sourced.
"""
import re
import pandas as pd
from google.cloud import bigquery

TRANSPORT_EMISSION_TIERS = [
    (50, 0.1400, "Medium Commercial (5-12t) — local/ward collection"),
    (150, 0.0902, "Heavy Commercial (12-20t) — regional"),
    (float("inf"), 0.0551, "Tractor-trailer (30-60t) — long-haul"),
]


def _emission_factor_for_distance(distance_km: float) -> tuple:
    for threshold, factor, label in TRANSPORT_EMISSION_TIERS:
        if distance_km < threshold:
            return factor, label
    return TRANSPORT_EMISSION_TIERS[-1][1], TRANSPORT_EMISSION_TIERS[-1][2]


def _normalize_entity(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _run_param_query(client_and_dataset, sql: str, params: dict) -> pd.DataFrame:
    """Runs a parameterized query against BigQuery. params is a plain dict
    of {name: value} -- values typed STRING here, the only type this module
    needs (facility name, date strings)."""
    client, dataset_ref = client_and_dataset
    query_params = [bigquery.ScalarQueryParameter(name, "STRING", value) for name, value in params.items()]
    job_config = bigquery.QueryJobConfig(
        default_dataset=dataset_ref,
        query_parameters=query_params,
    )
    return client.query(sql, job_config=job_config).to_dataframe()


def load_distance_lookup(client_and_dataset, facility: str) -> pd.DataFrame:
    """Reads the ghg_distance_lookup table for a facility. Returns empty
    DataFrame (not an error) if the table doesn't exist yet or has no rows --
    see module docstring for why this currently always hits the except
    branch until load_ghg_distances.py is separately migrated."""
    try:
        df = _run_param_query(
            client_and_dataset,
            "SELECT from_entity, to_entity, direction, distance_km, "
            "from_entity_normalized, to_entity_normalized "
            "FROM ghg_distance_lookup WHERE facility = @facility",
            {"facility": facility},
        )
        return df
    except Exception:
        return pd.DataFrame(columns=["from_entity", "to_entity", "direction", "distance_km",
                                      "from_entity_normalized", "to_entity_normalized"])


def calculate_transport_emissions(client_and_dataset, facility: str, date_from: str, date_to: str) -> dict:
    """Returns a dict with the total transport emissions for one facility over
    a date range, plus transparency fields and a per-route breakdown. Never
    silently guesses a distance for an unmatched trip -- those tonnes are
    reported as excluded, not folded into the total."""
    lookup = load_distance_lookup(client_and_dataset, facility)
    if lookup.empty:
        return {
            "total_kg_co2e": 0.0, "matched_tonnes": 0.0, "excluded_tonnes": 0.0,
            "excluded_trip_count": 0, "route_breakdown": pd.DataFrame(), "inconsistency_notes": [],
            "note": f"No distance data loaded yet for {facility}.",
        }

    inconsistent_notes = []
    for direction, col in [("inward", "from_entity_normalized"), ("outward", "to_entity_normalized")]:
        subset = lookup[lookup["direction"] == direction]
        grouped = subset.groupby(col)["distance_km"].nunique()
        inconsistent_keys = grouped[grouped > 1].index.tolist()
        for k in inconsistent_keys:
            variants = subset[subset[col] == k]
            inconsistent_notes.append(
                f"{direction}: '{k}' has inconsistent distances across spelling variants "
                f"({dict(zip(variants['from_entity'] if direction=='inward' else variants['to_entity'], variants['distance_km']))}) "
                f"— using the first one found."
            )

    inward_lookup = (lookup[lookup["direction"] == "inward"]
                      .drop_duplicates(subset=["from_entity_normalized"], keep="first")
                      .set_index("from_entity_normalized"))
    outward_lookup = (lookup[lookup["direction"] == "outward"]
                       .drop_duplicates(subset=["to_entity_normalized"], keep="first")
                       .set_index("to_entity_normalized"))

    # date is a native DATE column from the sync (see sync_to_bigquery.py's
    # write_table) -- no cast needed comparing it against DATE-typed params.
    inward_df = _run_param_query(
        client_and_dataset,
        "SELECT received_material_from, received_quantity FROM inward "
        "WHERE facility = @facility AND date BETWEEN @d_from AND @d_to "
        "AND received_material_from IS NOT NULL",
        {"facility": facility, "d_from": date_from, "d_to": date_to},
    )
    outward_df = _run_param_query(
        client_and_dataset,
        "SELECT customer, dispatched_quantity FROM outward "
        "WHERE facility = @facility AND date BETWEEN @d_from AND @d_to "
        "AND customer IS NOT NULL",
        {"facility": facility, "d_from": date_from, "d_to": date_to},
    )

    total_kg_co2e = 0.0
    matched_tonnes = 0.0
    excluded_tonnes = 0.0
    excluded_trip_count = 0
    route_rows = []

    for _, row in inward_df.iterrows():
        key = _normalize_entity(row["received_material_from"])
        qty_kg = row["received_quantity"] or 0
        try:
            qty_kg = float(qty_kg)
        except (TypeError, ValueError):
            qty_kg = 0.0
        tonnes = qty_kg / 1000.0
        if key in inward_lookup.index:
            distance_km = float(inward_lookup.loc[key, "distance_km"])
            factor, tier_label = _emission_factor_for_distance(distance_km)
            tonne_km = tonnes * distance_km
            kg_co2e = tonne_km * factor
            total_kg_co2e += kg_co2e
            matched_tonnes += tonnes
            route_rows.append({
                "direction": "inward", "entity": row["received_material_from"],
                "distance_km": distance_km, "tonnes": round(tonnes, 3),
                "tier": tier_label, "kg_co2e": round(kg_co2e, 2),
            })
        else:
            excluded_tonnes += tonnes
            excluded_trip_count += 1

    for _, row in outward_df.iterrows():
        key = _normalize_entity(row["customer"])
        qty_kg = row["dispatched_quantity"] or 0
        try:
            qty_kg = float(qty_kg)
        except (TypeError, ValueError):
            qty_kg = 0.0
        tonnes = qty_kg / 1000.0
        if key in outward_lookup.index:
            distance_km = float(outward_lookup.loc[key, "distance_km"])
            factor, tier_label = _emission_factor_for_distance(distance_km)
            tonne_km = tonnes * distance_km
            kg_co2e = tonne_km * factor
            total_kg_co2e += kg_co2e
            matched_tonnes += tonnes
            route_rows.append({
                "direction": "outward", "entity": row["customer"],
                "distance_km": distance_km, "tonnes": round(tonnes, 3),
                "tier": tier_label, "kg_co2e": round(kg_co2e, 2),
            })
        else:
            excluded_tonnes += tonnes
            excluded_trip_count += 1

    route_df = pd.DataFrame(route_rows)
    if not route_df.empty:
        route_df = route_df.groupby(["direction", "entity", "distance_km", "tier"], as_index=False).agg(
            tonnes=("tonnes", "sum"), kg_co2e=("kg_co2e", "sum")
        ).sort_values("kg_co2e", ascending=False)

    return {
        "total_kg_co2e": round(total_kg_co2e, 2),
        "matched_tonnes": round(matched_tonnes, 3),
        "excluded_tonnes": round(excluded_tonnes, 3),
        "excluded_trip_count": excluded_trip_count,
        "route_breakdown": route_df,
        "inconsistency_notes": inconsistent_notes,
        "note": (f"{excluded_trip_count} trip(s) totalling {round(excluded_tonnes,1)}t excluded — "
                 f"no distance on file yet for that vendor/customer." if excluded_trip_count else
                 "All trips matched to a known distance."),
    }
