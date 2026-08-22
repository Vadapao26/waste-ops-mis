"""Result rendering: table formatting, KPI cards, and auto-charting."""
import io
import re
import pandas as pd
import streamlit as st
import plotly.express as px
import theme

# Chart palette pulled from theme.PALETTE["chart"] — same tokens the KPI
# card top-accents use, so a bar chart's colors don't clash with the rest
# of the (Linear/Vercel-inspired) dashboard UI.
CHART_COLORS = theme.PALETTE["chart"]

def extract_sql(text):
    match = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL)
    if match: return match.group(1).strip()
    match2 = re.search(r'SELECT.*?;', text, re.DOTALL | re.IGNORECASE)
    if match2: return match2.group(0).strip()
    return None

def add_summary_row(df):
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols: return df
    sum_row = {col: df[col].sum() if col in numeric_cols else ("TOTAL" if i==0 else "") for i,col in enumerate(df.columns)}
    avg_row = {col: round(df[col].mean(),2) if col in numeric_cols else ("AVG" if i==0 else "") for i,col in enumerate(df.columns)}
    return pd.concat([df, pd.DataFrame([sum_row, avg_row])], ignore_index=True)

def df_to_csv_bytes(df): return df.to_csv(index=False).encode("utf-8")

def df_to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        add_summary_row(df).to_excel(writer, sheet_name="Data", index=False)
    return output.getvalue()

def format_dataframe(df):
    MONEY_SUFFIXES = ['_cost','_revenue','_paid','_amount','_value','_incentive','_procurement']
    PCT_SUFFIXES = ['_pct','_percent']
    COST_COLS = ['net_procurement_cost','net_cost_per_kg','net_material_sales_cost','net_revenue','net_revenue_per_kg']
    for col in df.columns:
        col_lower = col.lower()
        if any(col_lower.endswith(s) for s in PCT_SUFFIXES):
            df[col] = df[col].apply(lambda x: f'{x:.2f}%' if isinstance(x,(int,float)) and str(x) not in ['TOTAL','AVG',''] else x)
        elif col_lower in COST_COLS:
            def fmt_cost(x):
                if not isinstance(x,(int,float)) or str(x) in ['TOTAL','AVG','']: return x
                if x < 0: return f'₹{abs(x):,.2f} (cost)'
                return f'₹{x:,.2f}'
            df[col] = df[col].apply(fmt_cost)
        elif any(col_lower.endswith(s) for s in MONEY_SUFFIXES):
            df[col] = df[col].apply(lambda x: f'₹{x:,.2f}' if isinstance(x,(int,float)) and str(x) not in ['TOTAL','AVG',''] else x)
    return df

# Metric-name patterns → good/warn/bad thresholds, so a KPI card's color means
# something (this metric is fine / worth a look / needs attention) instead of
# just cycling through a fixed palette. Matched by substring on the lowercased
# column name; first match wins. `higher_is_worse=True` means climbing toward
# `warn`/`bad` is the concerning direction (e.g. rejection); False means the
# opposite (e.g. recovery — falling is concerning).
KPI_THRESHOLDS = [
    ("rejection", {"warn": 5, "bad": 10, "higher_is_worse": True}),
    ("recovery",  {"warn": 70, "bad": 50, "higher_is_worse": False}),
    ("valuables_pct", {"warn": 30, "bad": 15, "higher_is_worse": False}),
]

def _kpi_status(col_lower, val):
    """Returns 'good' / 'warn' / 'bad' / None (no rule matched — use the
    default neutral cycling color instead)."""
    if not isinstance(val, (int, float)):
        return None
    for pattern, rule in KPI_THRESHOLDS:
        if pattern in col_lower:
            worse_dir = rule["higher_is_worse"]
            if worse_dir:
                if val >= rule["bad"]: return "bad"
                if val >= rule["warn"]: return "warn"
                return "good"
            else:
                if val <= rule["bad"]: return "bad"
                if val <= rule["warn"]: return "warn"
                return "good"
    return None

def _truncate_label(val, max_len=18):
    """Shortens a long entity name for chart axes/legends (e.g. full vendor
    addresses) while the untruncated value stays available for hover text."""
    s = str(val)
    return s if len(s) <= max_len else s[:max_len - 1].rstrip() + "…"

def render_kpi_cards(df, prev_df=None):
    if df is None or len(df)==0: return
    colors = ["kpi-sage","kpi-lavender","kpi-peach","kpi-amber","kpi-rose"]
    MONEY_SUFFIXES = ['_cost','_revenue','_paid','_amount','_value','_incentive','_procurement']
    PCT_SUFFIXES = ['_pct','_percent']
    KG_SUFFIXES = ['_kg','_quantity','_runs','_days','_trips']
    cards_html = '<div class="kpi-grid">'
    for i, col in enumerate(df.columns[:10]):
        val = df[col].iloc[0]
        color = colors[i % len(colors)]
        label = col.replace("_"," ").title()
        col_lower = col.lower()
        if isinstance(val,(int,float)) and str(val) not in ['nan']:
            if any(col_lower.endswith(s) for s in PCT_SUFFIXES):
                display = f"{val:,.2f}%"
            elif any(col_lower.endswith(s) for s in MONEY_SUFFIXES) and not any(col_lower.endswith(s) for s in KG_SUFFIXES):
                display = f"₹{val:,.2f}"
            elif isinstance(val,float):
                display = f"{val:,.2f}"
            else:
                display = f"{int(val):,}"
        else:
            display = str(val)

        status = _kpi_status(col_lower, val)
        status_class = f" kpi-status-{status}" if status else ""

        trend_html = ""
        if prev_df is not None and col in prev_df.columns and len(prev_df) == 1:
            prev_val = prev_df[col].iloc[0]
            if isinstance(val, (int, float)) and isinstance(prev_val, (int, float)) and prev_val != 0:
                delta_pct = (val - prev_val) / abs(prev_val) * 100
                if abs(delta_pct) >= 0.5:  # ignore noise-level changes
                    higher_is_worse = next((r["higher_is_worse"] for p, r in KPI_THRESHOLDS if p in col_lower), None)
                    arrow = "▲" if delta_pct > 0 else "▼"
                    if higher_is_worse is None:
                        trend_class = "kpi-trend-neutral"
                    else:
                        rose = (delta_pct > 0) == higher_is_worse  # did it move the "bad" way?
                        trend_class = "kpi-trend-down" if rose else "kpi-trend-up"
                    trend_html = f'<div class="kpi-trend {trend_class}">{arrow} {abs(delta_pct):.1f}% vs prior period</div>'

        cards_html += (f'<div class="kpi-card {color}{status_class}">'
                        f'<div class="kpi-label">{label}</div>'
                        f'<div class="kpi-value">{display}</div>{trend_html}</div>')
    cards_html += '</div>'
    st.markdown(cards_html, unsafe_allow_html=True)


def auto_chart(df, uid):
    """Interactive chart with X/Y axis and chart type selectors."""
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()
    all_cols = df.columns.tolist()

    if len(df) < 2 or not num_cols:
        return

    # Remove total/summary rows for charting
    chart_df = df[~df.apply(lambda r: r.astype(str).str.upper().eq("TOTAL").any(), axis=1)].copy()
    if len(chart_df) < 2:
        return

    # Guess smart defaults
    default_x = "month" if "month" in cat_cols else (cat_cols[0] if cat_cols else all_cols[0])
    default_y = num_cols[0] if num_cols else all_cols[-1]
    default_color = next((c for c in cat_cols if c not in [default_x, "facility", "date"]), None)

    # Chart controls — collapsed by default; the chart itself should be
    # readable at a glance without forcing everyone through 4 dropdowns first.
    with st.expander("📊 Chart Options", expanded=False):
        ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
        with ctrl1:
            chart_type = st.selectbox("Chart Type",
                ["Bar", "Line", "Area", "Scatter", "Pie"],
                key=f"ctype_{uid}")
        with ctrl2:
            x_axis = st.selectbox("X Axis", all_cols,
                index=all_cols.index(default_x) if default_x in all_cols else 0,
                key=f"xaxis_{uid}")
        with ctrl3:
            y_axis = st.selectbox("Y Axis", num_cols,
                index=num_cols.index(default_y) if default_y in num_cols else 0,
                key=f"yaxis_{uid}")
        with ctrl4:
            color_opts = ["None"] + [c for c in cat_cols if c != x_axis]
            color_col = st.selectbox("Color By", color_opts,
                index=color_opts.index(default_color) if default_color in color_opts else 0,
                key=f"color_{uid}")
            color_col = None if color_col == "None" else color_col

    # Build chart
    title = f"{y_axis.replace('_',' ').title()} by {x_axis.replace('_',' ').title()}"
    plot_df = chart_df.sort_values(x_axis)

    # Long entity names (full vendor names, addresses) crowd axis labels and
    # legends — truncate for display only, full name still shows on hover via
    # the original column, which is passed through in hover_data.
    truncate_cols = [c for c in (x_axis, color_col) if c and plot_df[c].dtype == object]
    for c in truncate_cols:
        display_col = f"__{c}_short"
        plot_df[display_col] = plot_df[c].apply(_truncate_label)
    plot_x = f"__{x_axis}_short" if x_axis in truncate_cols else x_axis
    plot_color = f"__{color_col}_short" if color_col in truncate_cols else color_col

    try:
        if chart_type == "Bar":
            fig = px.bar(plot_df, x=plot_x, y=y_axis, color=plot_color, title=title,
                        color_discrete_sequence=CHART_COLORS, hover_name=x_axis if plot_x != x_axis else None)
            fig.update_layout(xaxis_tickangle=-30)
        elif chart_type == "Line":
            fig = px.line(plot_df, x=plot_x, y=y_axis, color=plot_color,
                         markers=True, title=title, color_discrete_sequence=CHART_COLORS,
                         hover_name=x_axis if plot_x != x_axis else None)
        elif chart_type == "Area":
            fig = px.area(plot_df, x=plot_x, y=y_axis, color=plot_color, title=title,
                         color_discrete_sequence=CHART_COLORS, hover_name=x_axis if plot_x != x_axis else None)
        elif chart_type == "Scatter":
            y2_opts = [c for c in num_cols if c != y_axis]
            size_col = y2_opts[0] if y2_opts else None
            fig = px.scatter(plot_df, x=plot_x, y=y_axis, color=plot_color,
                           size=size_col, title=title, hover_data=all_cols[:5],
                           color_discrete_sequence=CHART_COLORS)
        elif chart_type == "Pie":
            fig = px.pie(plot_df, names=plot_x, values=y_axis, title=title,
                        color_discrete_sequence=CHART_COLORS, hover_name=x_axis if plot_x != x_axis else None)

        fig.update_layout(
            height=380,
            margin=dict(t=40, b=60, l=20, r=20),
            showlegend=True if color_col else False,
            font=dict(family="Inter, sans-serif", color=theme.PALETTE["ink"], size=12),
            title_font=dict(size=13, color=theme.PALETTE["sub"]),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                title=x_axis.replace("_", " ").title(),
                tickangle=-30,
                tickfont=dict(size=11),
                gridcolor=theme.PALETTE["border"],
                # Format YYYY-MM as "Jan 2026" for readability
                tickformat="%b %Y" if x_axis == "month" else None,
            ),
            yaxis=dict(tickfont=dict(size=11), gridcolor=theme.PALETTE["border"]),
            legend_title_text=color_col.replace("_", " ").title() if color_col else None,
        )
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{uid}")
    except Exception as e:
        st.caption(f"Chart error: {e}")

def pivot_material_breakdown(df, id_cols, material_col="material", value_col=None):
    """Transforms a long-format (entity identifiers, material, quantity) table
    into wide format — one column per material, plus a Total column — matching
    how the material-crosstab analytics are meant to read (one row per
    vendor/customer, materials across the top). If df is empty or the value
    column is missing, returns df unchanged rather than erroring."""
    if df is None or df.empty:
        return df
    if value_col is None:
        numeric_cols = [c for c in df.columns if c not in id_cols + [material_col]]
        if not numeric_cols:
            return df
        value_col = numeric_cols[0]
    try:
        pivoted = df.pivot_table(index=id_cols, columns=material_col, values=value_col,
                                  aggfunc="sum", fill_value=0).reset_index()
        material_cols = [c for c in pivoted.columns if c not in id_cols]
        pivoted["Total"] = pivoted[material_cols].sum(axis=1)
        return pivoted
    except Exception:
        return df  # if pivoting fails for any reason, show the long-format table rather than crash


def show_result_panel(df, sql, label, num_months, is_kpi=False, panel_id=None, prev_df=None):
    uid = panel_id or label
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download CSV", data=df_to_csv_bytes(df),
            file_name=f"{label}.csv", mime="text/csv", key=f"csv_{uid}")
    with c2:
        st.download_button("Download Excel", data=df_to_excel_bytes(df),
            file_name=f"{label}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{uid}")
    st.caption(f"{len(df)} results")

    if is_kpi and len(df)==1:
        render_kpi_cards(df.copy(), prev_df=prev_df)
    elif "month" in df.columns and df["month"].nunique()>1:
        # Chart first, then tabbed data
        auto_chart(df, uid)
        months = sorted(df["month"].unique(), reverse=True)
        tabs = st.tabs([str(m) for m in months]+["All Data"])
        for i, month in enumerate(months):
            with tabs[i]:
                month_df = df[df["month"]==month].reset_index(drop=True)
                st.dataframe(format_dataframe(add_summary_row(month_df.copy())), use_container_width=True, height=280)
                st.download_button(f"Download {month}", data=df_to_csv_bytes(month_df),
                    file_name=f"{label}_{month}.csv", mime="text/csv", key=f"csv_{uid}_{month}_{i}")
        with tabs[-1]:
            st.dataframe(format_dataframe(add_summary_row(df.copy())), use_container_width=True, height=280)
    else:
        # Chart + table side by side for single-month data with enough rows
        if len(df) >= 3:
            auto_chart(df, uid)
        st.dataframe(format_dataframe(add_summary_row(df.copy())), use_container_width=True, height=320)
