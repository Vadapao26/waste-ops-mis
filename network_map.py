"""Phase F: network map showing every facility, vendor, and customer as a
point, with lines connecting each partner to whichever facility/facilities
it's associated with -- the "spider web" view of the whole logistics
network, built on top of the same ghg_entity_locations table Phases B-E
already use (no new data needed).
"""
import plotly.graph_objects as go
from google.cloud import bigquery

DIRECTION_COLORS = {
    "facility": "#0B57D0",   # primary blue, matches the app's M3 palette
    "inward": "#146C2E",     # green -- material coming in
    "outward": "#8A6218",    # amber -- material going out
}
DIRECTION_LABELS = {"facility": "Facility", "inward": "Inward vendor", "outward": "Outward customer"}


def build_network_map(client_and_dataset, facility_filter: list = None) -> go.Figure:
    """Builds the network map. If facility_filter is given (a list of
    facility names), only shows partners connected to at least one of
    those facilities, plus the facilities themselves -- keeps the map
    readable when looking at one facility instead of all 12 at once."""
    client, dataset_ref = client_and_dataset
    job_config = bigquery.QueryJobConfig(default_dataset=dataset_ref)
    df = client.query(
        "SELECT location, direction, seen_at_facilities, latitude, longitude FROM ghg_entity_locations",
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

    facility_coords = {
        row.location: (row.latitude, row.longitude)
        for row in df[df["direction"] == "facility"].itertuples()
    }

    fig = go.Figure()

    # Edges first (drawn as one trace with None-separated segments, so the
    # whole network's worth of lines is a single efficient trace rather
    # than one trace per connection -- matters once this scales to
    # hundreds of partner locations).
    edge_lats, edge_lons = [], []
    for row in df[df["direction"] != "facility"].itertuples():
        for fac_name in (row.seen_at_facilities or "").split(","):
            fac_name = fac_name.strip()
            if fac_name in facility_coords:
                fac_lat, fac_lon = facility_coords[fac_name]
                edge_lats += [row.latitude, fac_lat, None]
                edge_lons += [row.longitude, fac_lon, None]
    fig.add_trace(go.Scattermap(
        lat=edge_lats, lon=edge_lons, mode="lines",
        line=dict(width=1, color="rgba(100,100,100,0.35)"),
        hoverinfo="skip", showlegend=False,
    ))

    # Points, one trace per direction so the legend groups them sensibly
    # and each gets its own color.
    for direction in ["inward", "outward", "facility"]:
        sub = df[df["direction"] == direction]
        if sub.empty:
            continue
        fig.add_trace(go.Scattermap(
            lat=sub["latitude"], lon=sub["longitude"], mode="markers",
            marker=dict(size=16 if direction == "facility" else 9, color=DIRECTION_COLORS[direction]),
            text=sub["location"], hoverinfo="text",
            name=DIRECTION_LABELS[direction],
        ))

    if not df.empty:
        center_lat, center_lon = df["latitude"].mean(), df["longitude"].mean()
    else:
        center_lat, center_lon = 12.9716, 77.5946  # Bengaluru fallback if nothing to show

    fig.update_layout(
        map=dict(style="open-street-map", center=dict(lat=center_lat, lon=center_lon), zoom=8.5),
        margin=dict(l=0, r=0, t=0, b=0), height=560,
        legend=dict(orientation="h", yanchor="bottom", y=0.01, xanchor="left", x=0.01,
                    bgcolor="rgba(255,255,255,0.85)"),
    )
    return fig
