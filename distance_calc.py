"""Phase C: auto-calculated distance between any two named locations,
using the coordinates synced into ghg_entity_locations (Phase B).

Every location stores BOTH a PIN-code centroid AND (where available) a
direct coordinate, because which one to actually use for a given trip
depends on that specific trip's context, per your confirmed business
rules:

  1. Route-type entities (name ends in CMC/ULB/TMC/GP/SS) -- these
     represent a collection ROUTE covering an area, not a single point,
     so always use the PIN-code centroid regardless of anything else.
  2. Name-matching entities (name ends in CC/DWCC) -- strip the suffix
     and compare to the current facility's name:
       a. Matches the CURRENT facility -> forward processing (material
          sent elsewhere from this facility) -> PIN-code centroid.
       b. Matches a DIFFERENT known facility -> rejects-return trip ->
          use that OTHER facility's own precise coordinates.
       c. Matches no known facility at all -> fall through to case 3.
  3. Everything else (no recognized suffix, or a CC/DWCC suffix that
     doesn't match any facility) -- use the entity's own direct
     coordinate if it has one, otherwise its PIN centroid.

Once both ends of a trip have resolved coordinates, one more refinement
applies before computing distance:
  - If the two resolved points are exactly the same coordinate -> use
    that PIN's real, data-driven area radius as a realistic minimum
    (same-point doesn't mean 0km apart in reality).
  - If they share the same PIN code but have genuinely different
    coordinates (e.g. two distinct facilities that happen to share a
    rural PIN) -> trust the coordinates, compute real distance between
    them rather than falling back to the coarse same-PIN floor.
  - Otherwise -> standard Haversine x circuity factor.
"""
import os
import math
from datetime import datetime, timezone
import pandas as pd
import requests
from google.cloud import bigquery

CIRCUITY_FACTOR = 1.35

# -- Real road distance via Google Routes API (optional -- see below) --
ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GEO_ROUND = 6  # ~11cm precision when rounding a coordinate for cache lookups
FALLBACK_RADIUS_KM = 7.55  # median across all multi-office PINs -- used only
                            # for a PIN with just one office, where no
                            # real spread can be computed at all

ROUTE_SUFFIXES = {"CMC", "ULB", "TMC", "GP", "SS"}       # always PIN-code, confirmed
NAME_MATCH_SUFFIXES = {"CC", "DWCC"}                      # strip and compare to facility names, confirmed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_radii_df = pd.read_csv(os.path.join(SCRIPT_DIR, "pin_area_radii.csv"))
PIN_RADIUS = dict(zip(_radii_df["pincode"], _radii_df["radius_km"]))


def load_entity_coordinates(client_and_dataset) -> dict:
    """Loads every geocoded location from ghg_entity_locations into a
    {(location_name, direction): {"pin": (lat,lon,pincode) or None,
    "direct": (lat,lon,pincode) or None}} lookup. Returns an empty dict
    (not an error) if the table doesn't exist yet or has no rows."""
    client, dataset_ref = client_and_dataset
    try:
        job_config = bigquery.QueryJobConfig(default_dataset=dataset_ref)
        df = client.query(
            "SELECT location, direction, pincode, pin_lat, pin_lon, direct_lat, direct_lon "
            "FROM ghg_entity_locations",
            job_config=job_config,
        ).to_dataframe()
        result = {}
        for row in df.itertuples():
            pin_coord = (row.pin_lat, row.pin_lon, row.pincode) if pd.notna(row.pin_lat) else None
            direct_coord = (row.direct_lat, row.direct_lon, row.pincode) if pd.notna(row.direct_lat) else None
            result[(row.location, row.direction)] = {"pin": pin_coord, "direct": direct_coord}
        return result
    except Exception:
        return {}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in km."""
    R = 6371.0  # Earth's radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_road_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance x circuity factor -- see module docstring."""
    return round(haversine_km(lat1, lng1, lat2, lng2) * CIRCUITY_FACTOR, 2)


def _strip_suffix(name: str, suffixes: set) -> str | None:
    """If the last word of name is one of suffixes, returns the name with
    that word removed (stripped). Otherwise returns None. Word-based, not
    raw string suffix matching, so "DWCC" and "CC" don't get confused
    with each other (a naive .endswith("CC") would wrongly match DWCC)."""
    words = name.split()
    if words and words[-1].upper() in suffixes:
        return " ".join(words[:-1]).strip()
    return None


def resolve_entity_coords(coords: dict, entity_name: str, entity_direction: str, current_facility: str):
    """Implements the 4-case business rule to decide which coordinate to
    use for the non-facility side of a trip. Returns (lat, lon, pincode)
    or None if nothing usable is available at all."""
    entry = coords.get((entity_name, entity_direction))

    # Case 1: route-type suffix -> always PIN centroid, no exceptions.
    route_base = _strip_suffix(entity_name, ROUTE_SUFFIXES)
    if route_base is not None:
        return entry["pin"] if entry else None

    # Case 2: name-matching suffix -> strip and compare to facility names.
    name_match_base = _strip_suffix(entity_name, NAME_MATCH_SUFFIXES)
    if name_match_base is not None:
        if name_match_base == current_facility:
            # 2a: forward processing -- this facility sending material
            # into its own named area for further handling.
            return entry["pin"] if entry else None
        other_facility_entry = coords.get((name_match_base, "facility"))
        if other_facility_entry is not None:
            # 2b: rejects-return -- use the OTHER (named) facility's own
            # precise coordinates, not whatever's attached to this row.
            facility_coord = other_facility_entry["direct"] or other_facility_entry["pin"]
            if facility_coord is not None:
                return facility_coord
        # 2c: suffix stripped but no matching facility found -- fall
        # through to case 3 below, same as an unrecognized suffix.

    # Case 3: no recognized suffix (or a CC/DWCC suffix matching nothing)
    # -- prefer this entity's own direct coordinate, PIN centroid otherwise.
    if entry is None:
        return None
    return entry["direct"] or entry["pin"]


def distance_between(coords: dict, facility: str, entity_name: str, entity_direction: str, road_cache: dict = None):
    """Computes distance for one trip between `facility` (always uses its
    own precise coordinates) and `entity_name` (resolved per the business
    rules above). Returns None if either side has no usable coordinates.

    road_cache: optional {(o_lat,o_lon,d_lat,d_lon): distance_km} from
    load_distance_cache(). When a REAL road distance has already been
    fetched for this exact pair (via refresh_road_distances.py), it is
    used instead of the haversine x circuity estimate below. A pair the
    cache has not reached yet -- because the backfill script has not run
    for it, or no Google Maps API key is configured at all -- silently
    falls through to the same estimate this function has always used.
    Nothing breaks and nothing is blocked on the cache being complete."""
    facility_entry = coords.get((facility, "facility"))
    if facility_entry is None:
        return None
    facility_coord = facility_entry["direct"] or facility_entry["pin"]
    if facility_coord is None:
        return None

    other_coord = resolve_entity_coords(coords, entity_name, entity_direction, facility)
    if other_coord is None:
        return None

    lat1, lng1, pin1 = facility_coord
    lat2, lng2, pin2 = other_coord

    if road_cache:
        cached = road_cache.get(_cache_key(lat1, lng1, lat2, lng2))
        if cached is not None:
            return cached

    if lat1 == lat2 and lng1 == lng2:
        # Exactly the same point -- use that PIN's real area radius as a
        # realistic minimum rather than a literal, meaningless 0km.
        return PIN_RADIUS.get(pin1, FALLBACK_RADIUS_KM)
    if pin1 == pin2:
        # Same PIN, but genuinely different coordinates (e.g. two
        # distinct facilities sharing a rural PIN) -- trust the more
        # precise coordinates instead of the coarse same-PIN floor.
        return estimate_road_distance_km(lat1, lng1, lat2, lng2)
    return estimate_road_distance_km(lat1, lng1, lat2, lng2)


# ── Real road distance: Google Routes API + a BigQuery-backed cache ────────
# Real road distance is billed per lookup, so it is only ever fetched by
# refresh_road_distances.py (a standalone, manually-run backfill script) --
# never on a live page load. Distances only depend on (origin, destination)
# coordinate pairs, which are static, so each unique pair is looked up ONCE
# and reused forever after via this cache. distance_between() above reads
# the cache; it never calls the API itself.

def _cache_key(lat1, lng1, lat2, lng2):
    """Rounds a coordinate pair for cache lookups. Direction matters (A->B
    is not assumed equal to B->A) -- one-way streets and routing
    asymmetries make that a real difference, not just noise."""
    return (round(lat1, GEO_ROUND), round(lng1, GEO_ROUND),
            round(lat2, GEO_ROUND), round(lng2, GEO_ROUND))


def load_distance_cache(client_and_dataset) -> dict:
    """Loads every previously-fetched road distance from ghg_distance_cache
    into a {(o_lat,o_lon,d_lat,d_lon): distance_km} lookup. Returns an
    empty dict (not an error) if the table doesn't exist yet -- e.g. before
    refresh_road_distances.py has ever been run, or if no Maps API key has
    ever been configured. Callers should treat an empty cache exactly like
    "no real road distances available yet", not like a failure."""
    client, dataset_ref = client_and_dataset
    try:
        job_config = bigquery.QueryJobConfig(default_dataset=dataset_ref)
        df = client.query(
            "SELECT origin_lat, origin_lon, dest_lat, dest_lon, distance_km "
            "FROM ghg_distance_cache",
            job_config=job_config,
        ).to_dataframe()
        return {
            _cache_key(r.origin_lat, r.origin_lon, r.dest_lat, r.dest_lon): r.distance_km
            for r in df.itertuples()
        }
    except Exception:
        return {}


def fetch_road_distance_km(api_key: str, lat1: float, lng1: float, lat2: float, lng2: float):
    """Calls the Google Routes API for ONE origin-destination pair and
    returns real road distance in km, or None on any failure (bad key,
    rate limit, network error, no route found) -- this never raises, so
    one bad lookup in a backfill run of hundreds can't take the rest down."""
    try:
        resp = requests.post(
            ROUTES_API_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "routes.distanceMeters",
            },
            json={
                "origin": {"location": {"latLng": {"latitude": lat1, "longitude": lng1}}},
                "destination": {"location": {"latLng": {"latitude": lat2, "longitude": lng2}}},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
            },
            timeout=10,
        )
        resp.raise_for_status()
        routes = resp.json().get("routes", [])
        if not routes or "distanceMeters" not in routes[0]:
            return None
        return round(routes[0]["distanceMeters"] / 1000.0, 2)
    except Exception:
        return None


def write_distance_cache_rows(client_and_dataset, rows: list):
    """Appends newly-fetched (origin_lat, origin_lon, dest_lat, dest_lon,
    distance_km) tuples to ghg_distance_cache, creating the table on first
    use. Only ever appends: refresh_road_distances.py is responsible for
    skipping pairs load_distance_cache() already returned, so this never
    needs to upsert or de-duplicate anything itself."""
    if not rows:
        return
    client, dataset_ref = client_and_dataset
    table_ref = f"{client.project}.{dataset_ref.dataset_id}.ghg_distance_cache"
    schema = [
        bigquery.SchemaField("origin_lat", "FLOAT64"),
        bigquery.SchemaField("origin_lon", "FLOAT64"),
        bigquery.SchemaField("dest_lat", "FLOAT64"),
        bigquery.SchemaField("dest_lon", "FLOAT64"),
        bigquery.SchemaField("distance_km", "FLOAT64"),
        bigquery.SchemaField("fetched_at", "TIMESTAMP"),
    ]
    try:
        client.get_table(table_ref)
    except Exception:
        client.create_table(bigquery.Table(table_ref, schema=schema))

    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {"origin_lat": r[0], "origin_lon": r[1], "dest_lat": r[2], "dest_lon": r[3],
         "distance_km": r[4], "fetched_at": now}
        for r in rows
    ]
    errors = client.insert_rows_json(table_ref, payload)
    if errors:
        raise RuntimeError(f"Failed to write distance cache rows: {errors}")
