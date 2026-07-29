"""Result rendering: table formatting, KPI cards, and auto-charting."""
import io
import pandas as pd
import streamlit as st
import plotly.express as px

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

def render_kpi_cards(df):
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
        cards_html += f'<div class="kpi-card {color}"><div class="kpi-label">{label}</div><div class="kpi-value">{display}</div></div>'
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

    # Chart controls
    with st.expander("📊 Chart Options", expanded=True):
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

    try:
        if chart_type == "Bar":
            fig = px.bar(plot_df, x=x_axis, y=y_axis, color=color_col,
                        title=title, color_continuous_scale="Greens" if not color_col else None)
            fig.update_layout(xaxis_tickangle=-30)
        elif chart_type == "Line":
            fig = px.line(plot_df, x=x_axis, y=y_axis, color=color_col,
                         markers=True, title=title)
        elif chart_type == "Area":
            fig = px.area(plot_df, x=x_axis, y=y_axis, color=color_col, title=title)
        elif chart_type == "Scatter":
            y2_opts = [c for c in num_cols if c != y_axis]
            size_col = y2_opts[0] if y2_opts else None
            fig = px.scatter(plot_df, x=x_axis, y=y_axis, color=color_col,
                           size=size_col, title=title, hover_data=all_cols[:5])
        elif chart_type == "Pie":
            fig = px.pie(plot_df, names=x_axis, values=y_axis, title=title)

        fig.update_layout(
            height=380,
            margin=dict(t=40, b=60, l=20, r=20),
            showlegend=True if color_col else False,
            xaxis=dict(
                tickangle=-30,
                tickfont=dict(size=11),
                # Format YYYY-MM as "Jan 2026" for readability
                tickformat="%b %Y" if x_axis == "month" else None,
            ),
            yaxis=dict(tickfont=dict(size=11)),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{uid}")
    except Exception as e:
        st.caption(f"Chart error: {e}")

def show_result_panel(df, sql, label, num_months, is_kpi=False, panel_id=None):
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
        render_kpi_cards(df.copy())
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
