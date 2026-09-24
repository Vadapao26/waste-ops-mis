"""Phase G: the same network map as network_map.py, rendered on real Google
Maps tiles via the Maps JavaScript API instead of free OpenStreetMap tiles.
Purely visual -- reads the exact same ghg_entity_locations data, computes
nothing new, and changes no numbers anywhere in the app.

Requires a Maps JavaScript API key in st.secrets["google_maps"]["js_api_key"]
(a DIFFERENT key from the Routes API one used for road distances -- see
distance_calc.py's module docstring for why they can't be the same key).
This key's Application restriction must be "None" -- Streamlit's
st.components.v1.html() renders this page inside a sandboxed srcdoc
iframe, which has no real URL of its own, so the browser reports its
referrer as the literal string "about:srcdoc" and an HTTP-referrer
restriction can never match it. The API restriction (Maps JavaScript API
only) is the actual security boundary here, not the application
restriction -- confirmed against a live RefererNotAllowedMapError.

Callers should fall back to network_map.build_network_map() (the free OSM
Plotly version) whenever the secret isn't configured -- this module
doesn't raise on a missing/invalid key, it just produces a blank map, so
the caller decides whether to use it at all.

Click-to-filter: clicking any FACILITY marker on the map narrows the view
to just that facility's own connections (its vendors/customers and the
lines to them) -- entirely client-side, since every marker and line is
already embedded in the page; no server round-trip. Clicking the same
facility again, or the "Show all" control, clears the filter.
"""
import json
from google.cloud import bigquery

DIRECTION_COLORS = {
    "facility": "#0B57D0",
    "inward": "#146C2E",
    "outward": "#8A6218",
}
DIRECTION_LABELS = {"facility": "Facility", "inward": "Inward vendor", "outward": "Outward customer"}


def _load_points(client_and_dataset, facility_filter=None):
    """Same query and filtering logic as network_map.build_network_map --
    duplicated rather than shared, on purpose: the two renderers may want
    to diverge in what they draw as this feature matures."""
    client, dataset_ref = client_and_dataset
    job_config = bigquery.QueryJobConfig(default_dataset=dataset_ref)
    df = client.query(
        "SELECT location, direction, seen_at_facilities, "
        "COALESCE(direct_lat, pin_lat) AS latitude, COALESCE(direct_lon, pin_lon) AS longitude "
        "FROM ghg_entity_locations",
        job_config=job_config,
    ).to_dataframe()

    if facility_filter:
        facility_set = set(facility_filter)

        def _matches(row):
            if row["direction"] == "facility":
                return row["location"] in facility_set
            row_facilities = {f.strip() for f in (row["seen_at_facilities"] or "").split(",")}
            return bool(row_facilities & facility_set)

        df = df[df.apply(_matches, axis=1)]
    return df


def build_google_map_html(client_and_dataset, js_api_key: str, facility_filter: list = None,
                           height_px: int = 560) -> str:
    """Builds a full, self-contained HTML page (for st.components.v1.html)
    showing every facility/vendor/customer as a marker on real Google Maps
    tiles, with lines connecting each partner to its facility. Clicking a
    facility marker filters the view to that facility's own connections
    (client-side -- see module docstring)."""
    df = _load_points(client_and_dataset, facility_filter)
    df = df.dropna(subset=["latitude", "longitude"])

    facility_coords = {
        row.location: {"lat": row.latitude, "lng": row.longitude}
        for row in df[df["direction"] == "facility"].itertuples()
    }

    markers = []
    lines = []
    for row in df.itertuples():
        if row.direction == "facility":
            marker_facilities = [row.location]
        else:
            marker_facilities = [f.strip() for f in (row.seen_at_facilities or "").split(",") if f.strip()]

        markers.append({
            "lat": row.latitude, "lng": row.longitude,
            "label": row.location, "direction": row.direction,
            "color": DIRECTION_COLORS.get(row.direction, "#666666"),
            "facilities": marker_facilities,
        })

        if row.direction != "facility":
            for fac_name in marker_facilities:
                if fac_name in facility_coords:
                    lines.append({
                        "path": [{"lat": row.latitude, "lng": row.longitude}, facility_coords[fac_name]],
                        "facility": fac_name,
                    })

    if not df.empty:
        center = {"lat": float(df["latitude"].mean()), "lng": float(df["longitude"].mean())}
    else:
        center = {"lat": 12.9716, "lng": 77.5946}  # Bengaluru fallback if nothing to show

    markers_json = json.dumps(markers)
    lines_json = json.dumps(lines)
    center_json = json.dumps(center)
    legend_items = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin-right:14px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{DIRECTION_COLORS[d]};'
        f'display:inline-block;"></span>'
        f'<span style="font:12px Inter,Arial,sans-serif;color:#333;">{DIRECTION_LABELS[d]}</span></div>'
        for d in ["facility", "inward", "outward"]
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body, #map {{ height: {height_px}px; margin: 0; padding: 0; }}
  #wrap {{ position: relative; }}
  #legend {{ position: absolute; bottom: 12px; left: 12px; background: rgba(255,255,255,0.92);
             padding: 6px 10px; border-radius: 6px; display: flex; align-items: center; z-index: 5;
             box-shadow: 0 1px 3px rgba(0,0,0,0.2); flex-wrap: wrap; gap: 4px; }}
  #filter-status {{ position: absolute; top: 12px; left: 12px; background: rgba(11,87,208,0.95);
             color: #fff; padding: 6px 12px; border-radius: 6px; z-index: 5; display: none;
             font: 12px Inter,Arial,sans-serif; box-shadow: 0 1px 3px rgba(0,0,0,0.2);
             align-items: center; gap: 10px; }}
  #filter-status button {{ background: rgba(255,255,255,0.2); border: none; color: #fff;
             border-radius: 4px; padding: 2px 8px; cursor: pointer; font: 12px Inter,Arial,sans-serif; }}
  #filter-status button:hover {{ background: rgba(255,255,255,0.35); }}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="filter-status">
    <span id="filter-label"></span>
    <button id="clear-filter" onclick="clearFacilityFilter()">Show all</button>
  </div>
  <div id="legend">{legend_items}
    <span style="font:11px Inter,Arial,sans-serif;color:#888;margin-left:6px;">
      Click a facility to isolate its links
    </span>
  </div>
</div>
<script>
  const MARKERS = {markers_json};
  const LINES = {lines_json};
  const CENTER = {center_json};

  let map = null;
  let selectedFacility = null;
  const markerObjs = [];
  const lineObjs = [];

  function applyFacilityFilter() {{
    markerObjs.forEach(function(entry) {{
      const visible = !selectedFacility || entry.data.facilities.indexOf(selectedFacility) !== -1;
      entry.gmarker.setMap(visible ? map : null);
    }});
    lineObjs.forEach(function(entry) {{
      const visible = !selectedFacility || entry.data.facility === selectedFacility;
      entry.gline.setMap(visible ? map : null);
    }});
    const statusEl = document.getElementById("filter-status");
    const labelEl = document.getElementById("filter-label");
    if (selectedFacility) {{
      labelEl.textContent = "Showing only: " + selectedFacility;
      statusEl.style.display = "flex";
    }} else {{
      statusEl.style.display = "none";
    }}
  }}

  function clearFacilityFilter() {{
    selectedFacility = null;
    applyFacilityFilter();
  }}

  function initMap() {{
    map = new google.maps.Map(document.getElementById("map"), {{
      center: CENTER, zoom: 9.5,
    }});

    LINES.forEach(function(l) {{
      const poly = new google.maps.Polyline({{
        path: l.path, geodesic: true, strokeColor: "#999999",
        strokeOpacity: 0.45, strokeWeight: 1, map: map,
      }});
      lineObjs.push({{ data: l, gline: poly }});
    }});

    MARKERS.forEach(function(m) {{
      const marker = new google.maps.Marker({{
        position: {{ lat: m.lat, lng: m.lng }}, map: map, title: m.label,
        icon: {{
          path: google.maps.SymbolPath.CIRCLE,
          fillColor: m.color, fillOpacity: 1, strokeWeight: 0,
          scale: m.direction === "facility" ? 8 : 5,
        }},
      }});
      const infoText = m.direction === "facility"
        ? m.label + " (click to isolate its links)"
        : m.label;
      const info = new google.maps.InfoWindow({{ content: infoText }});
      marker.addListener("click", function() {{
        info.open(map, marker);
        if (m.direction === "facility") {{
          selectedFacility = (selectedFacility === m.label) ? null : m.label;
          applyFacilityFilter();
        }}
      }});
      markerObjs.push({{ data: m, gmarker: marker }});
    }});
  }}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key={js_api_key}&loading=async&callback=initMap"></script>
</body>
</html>"""
