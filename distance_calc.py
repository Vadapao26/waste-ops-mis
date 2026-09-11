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
import pandas as pd
from google.cloud import bigquery

CIRCUITY_FACTOR = 1.35
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


def distance_between(coords: dict, facility: str, entity_name: str, entity_direction: str):
    """Computes distance for one trip between `facility` (always uses its
    own precise coordinates) and `entity_name` (resolved per the business
    rules above). Returns None if either side has no usable coordinates."""
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
