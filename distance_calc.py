"""Phase C: auto-calculated distance between any two named locations, using
the coordinates synced into ghg_entity_locations (Phase B) instead of a
manually-typed-in lookup table.

Uses the Haversine formula (straight-line "great circle" distance) times a
circuity factor -- a standard adjustment used in transportation research to
approximate real road distance from straight-line distance, since trucks
don't drive in straight lines. 1.35x is a commonly-cited average for mixed
urban/highway routes in India; this is an approximation for aggregate
emissions estimation, not routing-grade precision.

Two refinements over a naive version:
  1. Locations are keyed by (name, direction), not name alone -- a name
     like "Madani Traders" can legitimately refer to two different
     branches (one seen inward, one outward), each with its own PIN. A
     name-only key would silently discard one of them.
  2. Same-PIN pairs (e.g. a facility and a vendor sharing a PIN code) don't
     just resolve to a flat 0km -- pin_area_radii.csv gives each PIN a
     real, data-driven "radius" (computed from the actual spread of post
     offices within that PIN, capped at 30km to drop a handful of
     erroneous source coordinates), used as a realistic minimum distance
     floor rather than an arbitrary flat number. PIN areas vary in size,
     so this floor does too.
"""
import os
import math
import pandas as pd
from google.cloud import bigquery

CIRCUITY_FACTOR = 1.35
FALLBACK_RADIUS_KM = 7.55  # median across all multi-office PINs -- used only
                            # for a PIN with just one office, where no
                            # real spread can be computed at all

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_radii_df = pd.read_csv(os.path.join(SCRIPT_DIR, "pin_area_radii.csv"))
PIN_RADIUS = dict(zip(_radii_df["pincode"], _radii_df["radius_km"]))


def load_entity_coordinates(client_and_dataset) -> dict:
    """Loads every geocoded location from ghg_entity_locations into a
    {(location_name, direction): (latitude, longitude, pincode)} lookup.
    Returns an empty dict (not an error) if the table doesn't exist yet or
    has no rows -- callers should treat that as 'no location data
    available yet', matching the same graceful-degradation pattern as
    load_distance_lookup()."""
    client, dataset_ref = client_and_dataset
    try:
        job_config = bigquery.QueryJobConfig(default_dataset=dataset_ref)
        df = client.query(
            "SELECT location, direction, latitude, longitude, pincode FROM ghg_entity_locations",
            job_config=job_config,
        ).to_dataframe()
        return {(row.location, row.direction): (row.latitude, row.longitude, row.pincode)
                for row in df.itertuples()}
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


def distance_between(coords: dict, location_a: str, direction_a: str, location_b: str, direction_b: str):
    """Looks up two (name, direction) pairs and returns the estimated road
    distance in km, or None if either location isn't geocoded yet (caller
    should treat that as 'no distance available', not an error).

    If both locations resolve to the same PIN code, the raw Haversine
    result would be 0 -- instead this returns that PIN's real, data-driven
    area radius as a realistic minimum, since same-PIN doesn't mean
    same-point."""
    a = coords.get((location_a, direction_a))
    b = coords.get((location_b, direction_b))
    if a is None or b is None:
        return None
    lat1, lng1, pin1 = a
    lat2, lng2, pin2 = b
    if pin1 == pin2:
        return PIN_RADIUS.get(pin1, FALLBACK_RADIUS_KM)
    return estimate_road_distance_km(lat1, lng1, lat2, lng2)
