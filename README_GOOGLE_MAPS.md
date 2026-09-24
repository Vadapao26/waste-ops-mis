# Google Maps Integration

Real road distances and a live-tile network map for Waste Ops MIS — built on top of the existing GHG intensity/transport-emissions layer.

## The Problem It Solves

The transport GHG emissions calculation and the Network Map view both relied on straight-line (haversine) distance estimates between facilities and vendors/customers, scaled by a fixed circuity factor to approximate real road distance. This is fast and free, but it's an approximation — actual road distance can differ meaningfully from a straight line depending on terrain, road layout, and detours.

Separately, the Network Map rendered on free OpenStreetMap tiles via Plotly, which is functional but doesn't show real road geometry, live traffic context, or familiar Google Maps imagery, and offered no way to focus on a single facility's connections when the map got crowded.

This update adds two independent, additive capabilities:

1. **Real road distances**, fetched once via the Google Routes API and cached in BigQuery, used automatically wherever the transport emissions calculation previously used the haversine estimate — with automatic fallback to the estimate for any pair not yet cached.
2. **A Google Maps–tiled version of the Network Map**, with click-to-filter on facility markers, shown automatically when a Maps API key is configured — falling back to the existing free OSM/Plotly map when it isn't.

Nothing about the existing calculation logic, existing free map, or any other part of the app was removed. Both additions are opt-in via `st.secrets["google_maps"]` and degrade gracefully to the prior behavior when that secret is absent.

## Architecture

```
                         ┌───────────────────────────────┐
                         │   Google Routes API            │
                         │   (computeRoutes, per-lookup)  │
                         └───────────────┬─────────────────┘
                                         │ one-time backfill (manual, cost-gated)
                                         ▼
                         refresh_road_distances.py
                                         │ writes
                                         ▼
                    BigQuery: ghg_distance_cache (lat/lon pairs → distance_km)
                                         │ read at query time
                                         ▼
distance_calc.py: distance_between() ──► ghg.py: calculate_transport_emissions()
        │ cache miss falls back to
        ▼
   haversine estimate × circuity factor  (unchanged, pre-existing logic)


                         ┌───────────────────────────────┐
                         │  Google Maps JavaScript API     │
                         │  (billed per map load)          │
                         └───────────────┬─────────────────┘
                                         │
                                         ▼
                    google_map.py: build_google_map_html()
                                         │ reads same data as network_map.py
                                         ▼
                    app.py: render_network_map_result()
                          │ st.secrets["google_maps"]["js_api_key"] present?
                    ┌─────┴─────┐
                   yes           no
                    │             │
                    ▼             ▼
        Google Maps view    network_map.py (free OSM/Plotly — unchanged)
        + click-to-filter
```

**Data flow — distances:**
1. `refresh_road_distances.py` is run manually, whenever the cache should be refreshed (e.g. new vendors/customers appear).
2. It finds every distinct (facility, entity, direction) pair ever seen in `inward`/`outward`, skips pairs already cached or resolving to the same point, estimates cost, and asks for confirmation before calling the Routes API.
3. Successful lookups are appended to `ghg_distance_cache` in BigQuery (never overwritten or deleted).
4. On every app load, `ghg.py` loads the full cache into memory and passes it into `distance_between()`, which checks the cache first and falls back to the haversine estimate on any miss.

**Data flow — map:**
1. `app.py` checks for `st.secrets["google_maps"]["js_api_key"]`.
2. If present, it builds a self-contained HTML page (`google_map.py`) embedding all facility/vendor/customer markers and connecting lines as JSON, and renders it via `st.components.v1.html()`.
3. If absent, it falls back to the existing `network_map.py` Plotly/OSM map — no behavior change for anyone who hasn't set up Maps.

## Languages & Libraries Added

| Layer | Technology |
|---|---|
| Real road distance | Google Routes API (`computeRoutes`) via `requests` |
| Map tiles | Google Maps JavaScript API (`google.maps.Map`, `Marker`, `Polyline`, `InfoWindow`) |
| New dependency | `requests==2.34.2` (added to `requirements.txt`) |

No new Python dependencies for the map itself — `google_map.py` generates plain HTML/JS embedded via Streamlit's existing `streamlit.components.v1.html()`.

## Features in Detail

### Real Road Distance Cache
- `ghg_distance_cache` table in BigQuery: `origin_lat`, `origin_lon`, `dest_lat`, `dest_lon`, `distance_km`, `fetched_at`.
- Coordinates are rounded to 6 decimal places (~11cm) as the cache key; lookups are direction-sensitive (A→B is not assumed equal to B→A).
- `distance_calc.py`'s `distance_between()` checks the cache first, before its existing same-point/same-PIN/haversine fallback chain — so the fallback chain itself is unchanged and still runs for any uncached pair.
- Populated only by `refresh_road_distances.py`, run manually — the live app never calls the paid Routes API itself.

### Cost-Gated Backfill Script (`refresh_road_distances.py`)
- Only fetches pairs not already cached — safe to re-run any number of times.
- Skips pairs with no usable coordinates and pairs resolving to the exact same point.
- Prints a cost estimate ($5.00 per 1000 elements, Routes API Essentials tier — verify current pricing in your own GCP billing console before relying on this figure) and requires an explicit `y` confirmation before spending anything.
- Reports which specific (facility → entity) pairs failed, rather than just a count, so failures can be investigated.
- As of the last backfill run: 216 of 216 known pairs are cached (two runs, ~$1.09 total spend).

### Google Maps Network View (`google_map.py`)
- Same underlying data as the free map (`ghg_entity_locations`) — purely a different renderer, computes nothing new.
- Facility markers (blue), inward vendor markers (green), outward customer markers (amber), connected by lines to their facility.
- **Click-to-filter:** clicking a facility marker isolates just that facility's own connections — its markers and lines are shown, everything else is hidden. Clicking the same facility again, or the "Show all" button, clears the filter. Entirely client-side (no server round-trip), since all markers/lines are already embedded in the page as JSON.
- Automatic fallback to the free OSM/Plotly map when `js_api_key` isn't configured — this module never raises on a missing/invalid key, it just wouldn't render, so `app.py` decides whether to use it.

## Setup

### Two separate API keys are required

| Key | Used for | Restriction type | Why |
|---|---|---|---|
| `routes_api_key` | Server-side Routes API calls (`refresh_road_distances.py`) | IP address restriction | Server requests don't send a `Referer` header |
| `js_api_key` | Browser-side Maps JavaScript API (`google_map.py`) | **None** (API-restricted only) | See note below |

**Important finding:** Streamlit's `st.components.v1.html()` renders content inside a sandboxed `srcdoc` iframe, which has no real URL of its own — the browser reports its referrer as the literal string `about:srcdoc`. An HTTP-referrer restriction can never match this, regardless of what URLs are added to the allow-list (localhost, production domain, etc. — this was confirmed against a live `RefererNotAllowedMapError`, not assumed). This holds whether the app is running locally or in production. As a result, `js_api_key`'s Application restriction must be set to "None," with the API restriction (Maps JavaScript API only) as the actual security boundary. Because this key is visible in page source, a GCP Billing budget alert is recommended as a safety net.

### `.streamlit/secrets.toml`

```toml
[google_maps]
routes_api_key = "..."   # IP-restricted, Routes API only
js_api_key = "..."       # unrestricted application access, API-restricted to Maps JavaScript API only
```

Both keys are optional — omitting either (or both) reverts to prior behavior (haversine distances, free OSM map) with no errors.

## Running the Backfill

```bash
python refresh_road_distances.py
```

Prints how many pairs are new vs. already cached, an estimated cost, and asks for confirmation before calling the API. Re-run periodically as new vendors/customers appear in the data.

## Testing

Two headless smoke tests were written for this work (gitignored, not part of the deployed app):

- `smoke_maps.py` — confirms `load_entity_coordinates`/`load_distance_cache` load correctly, `distance_between()` degrades identically whether the road cache is `None`, empty, or populated, cache-key rounding is consistent, and `build_google_map_html()` produces well-formed HTML with the expected markers and lines.
- `smoke_maps2.py` — confirms the click-to-filter data structures specifically: every marker carries a `facilities` list, every facility marker's own list is just itself, every line carries a `facility` field, and the `applyFacilityFilter`/`clearFacilityFilter` JS functions and click-toggle logic are present in the generated HTML.

Both are run against real BigQuery data (`python smoke_maps.py`, `python smoke_maps2.py`) since the development sandbox used to write this code has no network access to BigQuery or Google APIs.

## Project Structure (new/changed files)

```
waste-ops-mis/
├── distance_calc.py           # + Routes API fetch, cache read/write, road_cache param
├── ghg.py                     # + loads road_cache, passes it into distance_between()
├── google_map.py              # new — Google Maps–tiled network view + click-to-filter
├── network_map.py             # unchanged — kept as the free-tier fallback
├── refresh_road_distances.py  # new — manual, cost-gated cache backfill script (gitignored)
├── app.py                     # render_network_map_result() now branches on js_api_key
└── requirements.txt           # + requests==2.34.2
```

## Environment Variables / Secrets

| Secret | Where Set | Description |
|---|---|---|
| `google_maps.routes_api_key` | `.streamlit/secrets.toml` (local) / Streamlit Cloud Secrets or mounted Secret Manager secret (deployed) | IP-restricted key for server-side Routes API calls |
| `google_maps.js_api_key` | Same | API-restricted (Maps JavaScript API only), Application restriction "None" — see setup note above |

On Cloud Run, if secrets are served via a mounted Secret Manager secret rather than native Streamlit Cloud secrets, remember that a new secret version requires an explicit **Redeploy** — a running revision doesn't pick up a new version on its own.
