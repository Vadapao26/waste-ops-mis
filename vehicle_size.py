"""Phase D: infers which truck size class was most likely used for a given
shipment, from material category, loading type, and quantity.

Rebuilt on a simpler, more robust methodology than the original version:
weight-primary classification, using your original packing table's
practical weight thresholds directly (these already reflect real-world
material bulkiness -- no need to separately re-derive cargo volume
underneath them, which was the earlier version's mistake and produced bad
results on real data, e.g. Unsorted Dry Waste).

Truck weight ratings (2-4MT / 4-15MT / 15+MT) are taken directly from your
original table, not derived. Your confirmed universal loading-type rule
(bagged = 60% of bailed's threshold, loose = 40%) is applied directly to
these weight thresholds.
"""

# Practical max payload (kg) per material category, loading type, and
# truck size -- from your original packing table's "Bailed" row (the
# anchor), scaled by your confirmed universal rule for bagged/loose.
# Values are the upper bound of each size class (e.g. "3-6" -> 6000).
BAILED_THRESHOLDS_KG = {
    "Paper":              {"small": 2000, "medium": 6000, "large": None},
    "Flexible Plastics":  {"small": 2000, "medium": 6000, "large": None},
    "Rigid Plastics":     {"small": 2000, "medium": 7000, "large": None},
    "Others":             {"small": 2000, "medium": 6000, "large": None},
}
# Glass has no baling distinction -- uses its own measured thresholds
# directly (from the original table's "Unprocessed/Bags" row, its only row).
GLASS_THRESHOLDS_KG = {"small": 2000, "medium": 6000, "large": None}

# Materials confirmed to use the FULL truck weight rating directly, with
# no bulkiness-based reduction for any loading type -- unlike the 5 broad
# categories, these don't "cube out" before hitting the truck's weight
# capacity (Unsorted Dry Waste is typically already bagged even when
# categorized as loose by the inward-default rule, so it doesn't get
# penalized the way genuinely loose bulky material does).
FULL_RATING_THRESHOLDS_KG = {
    "Unsorted Drywaste": {"small": 4000, "medium": 15000, "large": None},
}

LOADING_SCALE = {"bailed": 1.0, "bagged": 0.6, "loose": 0.4}

# Official SFC/TCI-IIMB emission-factor classes (kg CO2e/tonne-km, WTW,
# diesel) -- see India Default GHG Emission Values V1.0, May 2025.
OFFICIAL_CLASSES = {
    "small_commercial":    {"payload_range_mt": (0.5, 2),  "wtw": 0.3795},
    "medium_commercial_1": {"payload_range_mt": (2, 3.5),  "wtw": 0.2222},
    "medium_commercial_2": {"payload_range_mt": (3.5, 8),  "wtw": 0.1400},
    "heavy_commercial_1":  {"payload_range_mt": (8, 12),   "wtw": 0.0902},
    "heavy_commercial_2":  {"payload_range_mt": (12, 20),  "wtw": 0.0823},
    "heavy_commercial_3":  {"payload_range_mt": (20, 40),  "wtw": 0.0663},
    "tractor_trailer":     {"payload_range_mt": (20, 50),  "wtw": 0.0551},
}
# Confirmed Small/Medium/Large -> official class mapping (by capacity
# midpoint overlap with the official payload ranges).
SIZE_TO_OFFICIAL_CLASS = {
    "small": "medium_commercial_1",
    "medium": "heavy_commercial_1",
    "large": "heavy_commercial_3",
}


def _thresholds_kg(material_category: str, loading_type: str) -> dict:
    """Returns {"small": kg, "medium": kg, "large": None} for a material
    category/loading combo. Checks materials confirmed to use the full
    truck weight rating first (no loading-type reduction), then falls
    back to 'Others' for anything not explicitly covered (confirmed
    catch-all, e.g. Tetra Pak)."""
    if material_category == "Glass":
        return GLASS_THRESHOLDS_KG
    if material_category in FULL_RATING_THRESHOLDS_KG:
        return FULL_RATING_THRESHOLDS_KG[material_category]
    base = BAILED_THRESHOLDS_KG.get(material_category, BAILED_THRESHOLDS_KG["Others"])
    scale = LOADING_SCALE.get(loading_type.lower(), LOADING_SCALE["bagged"])
    return {"small": round(base["small"] * scale), "medium": round(base["medium"] * scale), "large": None}


def capacity_kg(material_category: str, loading_type: str, truck_size: str) -> float:
    """Max practical payload (kg) for this material/loading combo in a
    truck of the given size class. Large has no explicit upper bound in
    the original table -- returns None, meaning 'no ceiling'."""
    return _thresholds_kg(material_category, loading_type)[truck_size]


def infer_vehicle_class(material_category: str, loading_type: str, quantity_kg: float) -> dict:
    """Given a shipment's material, loading type, and quantity, infers the
    smallest truck size whose practical weight threshold covers it, and
    returns the matching official emission-factor class + WTW value."""
    thresholds = _thresholds_kg(material_category, loading_type)
    if quantity_kg <= thresholds["small"]:
        size = "small"
    elif quantity_kg <= thresholds["medium"]:
        size = "medium"
    else:
        size = "large"
    official = SIZE_TO_OFFICIAL_CLASS[size]
    return {
        "truck_size": size,
        "official_class": official,
        "wtw_kg_co2e_per_tkm": OFFICIAL_CLASSES[official]["wtw"],
        "threshold_kg": thresholds[size],
        "note": f"Quantity fits within a {size} vehicle's practical threshold for this material/loading combo.",
    }
