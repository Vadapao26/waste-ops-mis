"""Query library and facility/preset constants — unchanged from the original app."""

FACILITIES = ["All Facilities", "Hebbagodi", "MRF", "Muguluru", "Jigani", "GPR",
              "Anekal", "Marsur", "Mayasandra", "Bommasandra", "Attibele",
              "Trading Data RPG/External Transfers- SCM (MRF)", "Interim PRF (Sarvam Jigani)"]
MONTHS_FULL = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]
MONTH_NUM = {m: str(i+1).zfill(2) for i, m in enumerate(MONTHS_FULL)}

# ── QUERY LIBRARY ─────────────────────────────────────────────────────────────
QUERY_LIBRARY = {
    "inward: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(rejected_quantity) AS total_rejected_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity),0))::numeric,2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS non_valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS total_material_value,
            ROUND((SUM(COALESCE(transportation_cost::numeric,0)))::numeric,2) AS total_transportation_cost,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS total_net_procurement_cost
        FROM inward {FACILITY_FILTER};""",

    "inward: vendor analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, received_material_from AS vendor,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity),0))::numeric,2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_value,
            ROUND((SUM(COALESCE(transportation_cost::numeric,0)))::numeric,2) AS transportation_cost,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward {FACILITY_FILTER}
        GROUP BY month,facility,vendor ORDER BY month DESC,total_received_kg DESC;""",

    "inward: vendor location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS location,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(received_quantity),0))::numeric,2) AS rejection_pct,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_value,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward {FACILITY_FILTER}
        GROUP BY month,facility,location ORDER BY month DESC,total_received_kg DESC;""",

    "inward: vendor material analytics": """
        SELECT facility, received_material_from AS vendor, vendor_location AS location, material,
            SUM(received_quantity) AS total_received_kg
        FROM inward {FACILITY_FILTER}
        GROUP BY facility, vendor, location, material ORDER BY vendor, total_received_kg DESC;""",

    "production: kpi summary": """
        WITH prod AS (
            SELECT process_equipment, production_code, date,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity END AS qty_kg
            FROM production {FACILITY_FILTER}
        )
        SELECT
            ROUND((SUM(CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN qty_kg ELSE 0 END))::numeric,2) AS total_sorted_kg,
            ROUND((SUM(CASE WHEN LOWER(process_equipment) LIKE '%bag%' THEN qty_kg ELSE 0 END))::numeric,2) AS total_bagged_kg,
            ROUND((SUM(CASE WHEN LOWER(process_equipment) LIKE '%bail%' OR LOWER(process_equipment) LIKE '%bale%' THEN qty_kg ELSE 0 END))::numeric,2) AS total_bailed_kg,
            ROUND((SUM(CASE WHEN LOWER(process_equipment) LIKE '%shred%' THEN qty_kg ELSE 0 END))::numeric,2) AS total_shredded_kg,
            ROUND((SUM(qty_kg))::numeric,2) AS total_processed_kg,
            COUNT(DISTINCT production_code) AS total_runs,
            COUNT(DISTINCT date::date) AS days_operated
        FROM prod;""",

    "production: process x equipment analysis": """
        WITH prod AS (
            SELECT date, facility, process_equipment, production_code, no_of_staff_present, time_taken_in_hrs,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity END AS qty_kg
            FROM production {FACILITY_FILTER}
        )
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility,
            CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN 'Sorting'
                 WHEN LOWER(process_equipment) LIKE '%bag%' THEN 'Bagging'
                 WHEN LOWER(process_equipment) LIKE '%bail%' OR LOWER(process_equipment) LIKE '%bale%' THEN 'Bailing'
                 WHEN LOWER(process_equipment) LIKE '%shred%' THEN 'Shredding'
                 ELSE 'Other' END AS process,
            process_equipment,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND((SUM(qty_kg))::numeric,2) AS total_qty_processed_kg,
            ROUND((AVG(no_of_staff_present))::numeric,1) AS avg_staff,
            COUNT(DISTINCT date::date) AS days_operated,
            ROUND((SUM(qty_kg)/NULLIF(COUNT(DISTINCT date::date),0))::numeric,2) AS efficiency_per_day,
            ROUND((SUM(qty_kg)/NULLIF(SUM(time_taken_in_hrs),0))::numeric,2) AS efficiency_per_hour
        FROM prod
        GROUP BY month,facility,process,process_equipment ORDER BY month DESC,process,total_qty_processed_kg DESC;""",

    "production: process material analytics": """
        SELECT facility, process_equipment, material, SUM(qty_kg) AS total_qty_kg
        FROM (
            SELECT facility, process_equipment,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN material ELSE materials END AS material,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity::numeric END AS qty_kg
            FROM production {FACILITY_FILTER}
        ) sub
        WHERE material IS NOT NULL AND material <> '' AND qty_kg IS NOT NULL
        GROUP BY facility, process_equipment, material
        ORDER BY process_equipment, total_qty_kg DESC;""",

    "production: equipment analysis": """
        WITH prod AS (
            SELECT date, facility, process_equipment, production_code, no_of_staff_present, time_taken_in_hrs,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity END AS qty_kg
            FROM production {FACILITY_FILTER}
        )
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, process_equipment,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND((SUM(qty_kg))::numeric,2) AS total_qty_processed_kg,
            ROUND((AVG(no_of_staff_present))::numeric,1) AS avg_staff,
            COUNT(DISTINCT date::date) AS days_operated,
            ROUND((SUM(qty_kg)/NULLIF(COUNT(DISTINCT date::date),0))::numeric,2) AS efficiency_per_day,
            ROUND((SUM(qty_kg)/NULLIF(SUM(time_taken_in_hrs),0))::numeric,2) AS efficiency_per_hour
        FROM prod
        GROUP BY month,facility,process_equipment ORDER BY month DESC,total_qty_processed_kg DESC;""",

    "production: shift analysis": """
        WITH prod AS (
            SELECT date, facility, shift, process_equipment, production_code, no_of_staff_present, time_taken_in_hrs,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity END AS qty_kg
            FROM production {FACILITY_FILTER}
        )
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, shift,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND((SUM(qty_kg))::numeric,2) AS total_qty_processed_kg,
            ROUND((AVG(no_of_staff_present))::numeric,1) AS avg_staff,
            ROUND((SUM(qty_kg)/NULLIF(COUNT(DISTINCT date::date),0))::numeric,2) AS efficiency_per_day,
            ROUND((SUM(qty_kg)/NULLIF(SUM(time_taken_in_hrs),0))::numeric,2) AS efficiency_per_hour
        FROM prod
        GROUP BY month,facility,shift ORDER BY month DESC,efficiency_per_day DESC;""",

    "production: equipment x shift analysis": """
        WITH prod AS (
            SELECT date, facility, process_equipment, shift, production_code, time_taken_in_hrs,
                CASE WHEN LOWER(process_equipment) LIKE '%sort%' THEN NULLIF(quantity_in_kg,'')::numeric
                     ELSE material_quantity END AS qty_kg
            FROM production {FACILITY_FILTER}
        )
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, process_equipment, shift,
            COUNT(DISTINCT production_code) AS total_runs,
            ROUND((SUM(qty_kg))::numeric,2) AS total_qty_processed_kg,
            ROUND((SUM(qty_kg)/NULLIF(COUNT(DISTINCT date::date),0))::numeric,2) AS efficiency_per_day,
            ROUND((SUM(qty_kg)/NULLIF(SUM(time_taken_in_hrs),0))::numeric,2) AS efficiency_per_hour
        FROM prod
        GROUP BY month,facility,process_equipment,shift ORDER BY month DESC,process_equipment;""",

    "transport: vendor and vehicle analysis": """
        SELECT facility, transport_vendor, vehicle_number,
            SUM(total_trips) AS total_trips, SUM(inward_trips) AS inward_trips, SUM(outward_trips) AS outward_trips,
            ROUND((SUM(total_material_kg))::numeric,2) AS total_material_kg,
            SUM(paid_trips) AS paid_trips,
            ROUND((SUM(total_transport_cost))::numeric,2) AS total_transport_cost,
            ROUND((SUM(total_transport_cost)/NULLIF(SUM(material_at_cost),0))::numeric,2) AS rate_per_kg
        FROM (
            SELECT facility, vehicle_vendor_name AS transport_vendor, vehicle_number,
                COUNT(DISTINCT inward_code) AS total_trips, COUNT(DISTINCT inward_code) AS inward_trips, 0 AS outward_trips,
                SUM(accepted_quantity) AS total_material_kg,
                COUNT(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)>0 THEN 1 END) AS paid_trips,
                SUM(COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)) AS total_transport_cost,
                SUM(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_cost,0)>0 THEN accepted_quantity ELSE 0 END) AS material_at_cost
            FROM inward WHERE vehicle_vendor_name IS NOT NULL {AND_FACILITY_FILTER}
            GROUP BY facility,transport_vendor,vehicle_number
            UNION ALL
            SELECT facility, transport_vendor, vehicle_number,
                COUNT(DISTINCT outward_code) AS total_trips, 0 AS inward_trips, COUNT(DISTINCT outward_code) AS outward_trips,
                SUM(accepted_quantity) AS total_material_kg,
                COUNT(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)>0 THEN 1 END) AS paid_trips,
                SUM(COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)) AS total_transport_cost,
                SUM(CASE WHEN COALESCE(transportation_cost,0)+COALESCE(loading_cost,0)+COALESCE(additional_transport_cost,0)>0 THEN accepted_quantity ELSE 0 END) AS material_at_cost
            FROM outward WHERE transport_vendor IS NOT NULL {AND_FACILITY_FILTER}
            GROUP BY facility,transport_vendor,vehicle_number
        ) combined
        WHERE transport_vendor IS NOT NULL AND transport_vendor!=''
        GROUP BY facility,transport_vendor,vehicle_number ORDER BY transport_vendor,total_trips DESC;""",

    "ulb: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS non_valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS total_material_value,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS total_net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER} {AND_VENDOR_FILTER};""",

    "ulb: vendor list": """
        SELECT DISTINCT received_material_from AS vendor, vendor_location AS location
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER}
        ORDER BY vendor;""",

    "ulb: vendor material analytics": """
        SELECT material, SUM(received_quantity) AS total_received_kg,
            SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER} {AND_VENDOR_FILTER}
        GROUP BY material ORDER BY total_received_kg DESC;""",


    "ulb: ward location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS ward_location,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost=0 THEN accepted_quantity ELSE 0 END) AS total_non_valuables_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_value,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER}
        GROUP BY month,facility,ward_location ORDER BY month DESC,total_received_kg DESC;""",

    "ulb: driver analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, UPPER(TRIM(driver_name)) AS driver,
            COUNT(DISTINCT inward_code) AS total_trips,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='ULB' {AND_FACILITY_FILTER}
        GROUP BY month,facility,driver ORDER BY month DESC,total_trips DESC;""",

    "bwg: kpi summary": """
        SELECT SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS total_material_value,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS total_net_procurement_cost
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER} {AND_VENDOR_FILTER};""",

    "bwg: location analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, vendor_location AS location, received_material_from AS vendor,
            SUM(received_quantity) AS total_received_kg, SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((100.0*SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END)/NULLIF(SUM(accepted_quantity),0))::numeric,2) AS valuables_pct
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER}
        GROUP BY month,facility,location,vendor ORDER BY month DESC,total_received_kg DESC;""",

    "bwg: vendor list": """
        SELECT DISTINCT received_material_from AS vendor, vendor_location AS location
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER}
        ORDER BY vendor;""",

    "bwg: vendor material analytics": """
        SELECT material, SUM(received_quantity) AS total_received_kg,
            SUM(accepted_quantity) AS total_accepted_kg,
            SUM(CASE WHEN net_procurement_cost>0 THEN accepted_quantity ELSE 0 END) AS total_valuables_kg,
            ROUND((SUM(net_procurement_cost::numeric))::numeric,2) AS net_procurement_cost
        FROM inward WHERE source='Bulk waste generator' {AND_FACILITY_FILTER} {AND_VENDOR_FILTER}
        GROUP BY material ORDER BY total_received_kg DESC;""",

    "outward: kpi summary": """
        SELECT SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(dispatched_quantity),0))::numeric,2) AS rejection_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_revenue,
            ROUND((SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS total_incentive,
            ROUND((SUM(value_of_accepted_material::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS total_revenue,
            ROUND((SUM(net_material_sales_cost::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS net_revenue
        FROM outward {FACILITY_FILTER};""",

    "outward: customer analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, customer,
            SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((100.0*SUM(rejected_quantity)/NULLIF(SUM(dispatched_quantity),0))::numeric,2) AS rejection_pct,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_revenue,
            ROUND((SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS total_incentive,
            ROUND((SUM(value_of_accepted_material::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS total_revenue,
            ROUND((SUM(COALESCE(transportation_cost::numeric,0)))::numeric,2) AS transportation_cost,
            ROUND((SUM(net_material_sales_cost::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS net_revenue
        FROM outward {FACILITY_FILTER}
        GROUP BY month,facility,customer ORDER BY month DESC,net_revenue DESC;""",

    "outward: customer destination analysis": """
        SELECT TO_CHAR(date::date,'YYYY-MM') AS month, facility, customer, destination,
            SUM(dispatched_quantity) AS total_dispatched_kg, SUM(accepted_quantity) AS total_accepted_kg,
            ROUND((SUM(value_of_accepted_material::numeric))::numeric,2) AS material_revenue,
            ROUND((SUM(value_of_accepted_material::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS total_revenue,
            ROUND((SUM(net_material_sales_cost::numeric)+SUM(COALESCE(total_incentive_cost::numeric,0)))::numeric,2) AS net_revenue
        FROM outward {FACILITY_FILTER}
        GROUP BY month,facility,customer,destination ORDER BY month DESC,customer,net_revenue DESC;""",

    "outward: customer material analytics": """
        SELECT facility, customer, destination, material,
            SUM(dispatched_quantity) AS total_dispatched_kg
        FROM outward {FACILITY_FILTER}
        GROUP BY facility, customer, destination, material ORDER BY customer, total_dispatched_kg DESC;""",

    "outward: material rate trend": """
        SELECT material, TO_CHAR(date::date,'YYYY-MM') AS month,
            ROUND(AVG(rate::numeric),2) AS avg_rate
        FROM outward {FACILITY_FILTER}
        AND rate IS NOT NULL AND rate::text <> ''
        GROUP BY material, month ORDER BY material, month DESC;""",

    # ── SUPPLY CHAIN DASHBOARD ───────────────────────────────────────────────
    # "Sheets Uploaded" metrics only, per the tracker — "Manual Input" rows
    # (vendor partners mapped, offtake agreements, debit/credit notes, etc.)
    # aren't derivable from synced data and are intentionally excluded.
    "supply chain: kpi summary": """
        WITH inward_agg AS (
            SELECT
                SUM(received_quantity::numeric) AS material_sourced_kg,
                SUM(CASE WHEN source_type = 'Aggregators' THEN received_quantity::numeric ELSE 0 END) AS aggregators_inward_kg,
                SUM(CASE WHEN source_type = 'Waste Picker' THEN received_quantity::numeric ELSE 0 END) AS wpc_kg,
                SUM(net_procurement_cost::numeric) AS cost_of_material,
                ROUND((100.0*SUM(rejected_quantity::numeric)/NULLIF(SUM(received_quantity::numeric),0))::numeric,2) AS deduction_pct_inward
            FROM inward {FACILITY_FILTER}
        ),
        outward_agg AS (
            SELECT
                SUM(dispatched_quantity::numeric) AS quantity_dispatched_kg,
                SUM(CASE WHEN COALESCE(vendor_type,'')='Recycler' THEN dispatched_quantity::numeric ELSE 0 END) AS recycling_kg,
                SUM(CASE WHEN COALESCE(vendor_type,'')='Co-Processing' THEN dispatched_quantity::numeric ELSE 0 END) AS co_processing_kg,
                SUM(CASE WHEN COALESCE(vendor_type,'')='Aggregator' THEN dispatched_quantity::numeric ELSE 0 END) AS aggregators_outward_kg,
                SUM(net_material_sales_cost::numeric) AS revenue_generated,
                SUM(COALESCE(transportation_cost::numeric,0)+COALESCE(loading_cost::numeric,0)+COALESCE(additional_transport_cost::numeric,0)) AS logistics_cost,
                ROUND((100.0*SUM(rejected_quantity::numeric)/NULLIF(SUM(dispatched_quantity::numeric),0))::numeric,2) AS deduction_pct_outward
            FROM outward {FACILITY_FILTER}
        )
        SELECT
            ROUND((material_sourced_kg/1000)::numeric,2) AS material_sourced_mt,
            ROUND((aggregators_inward_kg/1000)::numeric,2) AS aggregators_inward_mt,
            ROUND((wpc_kg/1000)::numeric,2) AS wpc_mt,
            ROUND((cost_of_material/100000)::numeric,2) AS cost_of_material_lakhs,
            deduction_pct_inward,
            ROUND((quantity_dispatched_kg/1000)::numeric,2) AS quantity_dispatched_mt,
            ROUND((recycling_kg/1000)::numeric,2) AS recycling_mt,
            ROUND((co_processing_kg/1000)::numeric,2) AS co_processing_mt,
            ROUND((aggregators_outward_kg/1000)::numeric,2) AS aggregators_outward_mt,
            ROUND((revenue_generated/100000)::numeric,2) AS revenue_generated_lakhs,
            ROUND((logistics_cost/100000)::numeric,2) AS logistics_cost_lakhs,
            deduction_pct_outward
        FROM inward_agg, outward_agg;""",

    "supply chain: inward rate rejection history": """
        SELECT material, TO_CHAR(date::date,'YYYY-MM') AS month,
            ROUND(AVG(rate::numeric),2) AS avg_rate,
            ROUND((100.0*SUM(rejected_quantity::numeric)/NULLIF(SUM(received_quantity::numeric),0))::numeric,2) AS rejection_pct,
            ROUND((SUM(received_quantity::numeric)/1000)::numeric,2) AS total_received_mt
        FROM inward {FACILITY_FILTER}
        AND rate IS NOT NULL AND rate::text <> ''
        GROUP BY material, month ORDER BY material, month DESC;""",

    "supply chain: outward rate rejection history": """
        SELECT material, TO_CHAR(date::date,'YYYY-MM') AS month,
            ROUND(AVG(rate::numeric),2) AS avg_rate,
            ROUND((100.0*SUM(rejected_quantity::numeric)/NULLIF(SUM(dispatched_quantity::numeric),0))::numeric,2) AS rejection_pct,
            ROUND((SUM(dispatched_quantity::numeric)/1000)::numeric,2) AS total_dispatched_mt
        FROM outward {FACILITY_FILTER}
        AND rate IS NOT NULL AND rate::text <> ''
        GROUP BY material, month ORDER BY material, month DESC;""",

    # ── TRAINING ──────────────────────────────────────────────────────────────
    "training: kpi summary": """
        SELECT
            COUNT(DISTINCT training_code) AS total_trainings,
            ROUND((SUM(duration_mins)::numeric / 60), 2) AS total_training_hours,
            SUM(attendee_count) AS total_people_trained,
            ROUND(AVG(duration_mins)::numeric, 0) AS avg_duration_mins
        FROM training
        {FACILITY_FILTER};""",

    "training: topic analysis": """
        SELECT
            TO_CHAR(date::date, 'YYYY-MM') AS month,
            facility,
            topic,
            category,
            COUNT(*) AS sessions,
            SUM(attendee_count) AS total_attendees,
            ROUND(AVG(duration_mins)::numeric, 0) AS avg_duration_mins,
            SUM(duration_mins) AS total_duration_mins
        FROM training
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date, 'YYYY-MM'), facility, topic, category
        ORDER BY month DESC, sessions DESC;""",

    "training: trainer analysis": """
        SELECT
            facility,
            trainer,
            COUNT(DISTINCT training_code) AS sessions_conducted,
            SUM(attendee_count) AS total_people_trained,
            ROUND((SUM(duration_mins)::numeric / 60), 2) AS total_training_hours,
            ROUND(AVG(duration_mins)::numeric, 0) AS avg_duration_mins
        FROM training
        {FACILITY_FILTER}
        GROUP BY facility, trainer
        ORDER BY sessions_conducted DESC;""",

    "training: category analysis": """
        SELECT
            TO_CHAR(date::date, 'YYYY-MM') AS month,
            facility,
            category,
            COUNT(DISTINCT training_code) AS sessions,
            SUM(attendee_count) AS total_attendees,
            ROUND((SUM(duration_mins)::numeric / 60), 2) AS total_hours
        FROM training
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date, 'YYYY-MM'), facility, category
        ORDER BY month DESC, sessions DESC;""",

    "training: role based attendance": """
        SELECT
            facility,
            attendee_role AS role,
            COUNT(DISTINCT training_code) AS sessions_attended,
            COUNT(DISTINCT attendee_name) AS unique_people,
            ROUND((SUM(duration_mins)::numeric / 60), 2) AS total_hours_received
        FROM training_attendees
        {FACILITY_FILTER}
        GROUP BY facility, attendee_role
        ORDER BY sessions_attended DESC;""",

    "training: repeat attendees": """
        SELECT
            facility,
            attendee_name AS name,
            attendee_role AS role,
            COUNT(DISTINCT training_code) AS sessions_attended,
            ROUND((SUM(duration_mins)::numeric / 60), 2) AS total_hours_received,
            STRING_AGG(DISTINCT category, ', ') AS categories_covered
        FROM training_attendees
        {FACILITY_FILTER}
        GROUP BY facility, attendee_name, attendee_role
        HAVING COUNT(DISTINCT training_code) > 1
        ORDER BY sessions_attended DESC;""",

    # ── ENVIRONMENTAL IMPACT ──────────────────────────────────────────────────
    "impact: inward kpi": """
        SELECT
            COUNT(DISTINCT received_material_from) AS total_vendors,
            ROUND((SUM(accepted_quantity::numeric)/1000)::numeric,3) AS total_inward_mt,
            ROUND((SUM(accepted_quantity::numeric)/1000/26)::numeric,3) AS avg_tpd,
            COUNT(DISTINCT source_type) AS source_types
        FROM inward
        {FACILITY_FILTER};""",

    "impact: inward by source type": """
        SELECT
            TO_CHAR(date::date,'YYYY-MM') AS month,
            facility,
            COALESCE(source_type,'Unknown') AS source_type,
            COUNT(DISTINCT received_material_from) AS unique_vendors,
            ROUND((SUM(accepted_quantity::numeric)/1000)::numeric,3) AS total_mt,
            ROUND((SUM(accepted_quantity::numeric)/1000/26)::numeric,3) AS avg_tpd
        FROM inward
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date,'YYYY-MM'),facility,COALESCE(source_type,'Unknown')
        ORDER BY month DESC,total_mt DESC;""",

    "impact: inward by material category": """
        SELECT
            TO_CHAR(date::date,'YYYY-MM') AS month,
            facility,
            COALESCE(inward_material_category,'Unknown') AS material_category,
            ROUND((SUM(accepted_quantity::numeric)/1000)::numeric,3) AS total_mt,
            ROUND((SUM(accepted_quantity::numeric)/1000*100/
                NULLIF(SUM(SUM(accepted_quantity::numeric)) OVER(PARTITION BY TO_CHAR(date::date,'YYYY-MM'),facility),0))::numeric,2) AS pct_of_total
        FROM inward
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date,'YYYY-MM'),facility,COALESCE(inward_material_category,'Unknown')
        ORDER BY month DESC,total_mt DESC;""",

    "impact: dispatch kpi": """
        SELECT
            COUNT(DISTINCT customer) AS total_customers,
            ROUND((SUM(dispatched_quantity::numeric)/1000)::numeric,3) AS total_dispatched_mt,
            ROUND((SUM(rejected_quantity::numeric)/1000)::numeric,3) AS total_rejected_mt,
            ROUND((SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END)/1000)::numeric,3) AS returned_to_source_mt,
            ROUND(((SUM(dispatched_quantity::numeric)-SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END))/1000)::numeric,3) AS net_recovered_mt,
            ROUND((SUM(CASE WHEN COALESCE(vendor_type,'')='Recycler' THEN dispatched_quantity::numeric ELSE 0 END)/1000)::numeric,3) AS recycled_mt,
            ROUND((SUM(CASE WHEN COALESCE(vendor_type,'')='Co-Processing' THEN dispatched_quantity::numeric ELSE 0 END)/1000)::numeric,3) AS co_processed_mt,
            ROUND(((SUM(dispatched_quantity::numeric)-SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END))/
                NULLIF(SUM(dispatched_quantity::numeric),0)*100)::numeric,2) AS recovery_rate_pct
        FROM outward
        {FACILITY_FILTER};""",

    "impact: dispatch by destination type": """
        SELECT
            TO_CHAR(date::date,'YYYY-MM') AS month,
            facility,
            customer,
            destination,
            COALESCE(vendor_type,'Unknown') AS destination_type,
            COALESCE(authorisation,'Unknown') AS authorization,
            ROUND((SUM(dispatched_quantity::numeric)/1000)::numeric,3) AS total_mt,
            ROUND((SUM(rejected_quantity::numeric)/1000)::numeric,3) AS rejected_mt
        FROM outward
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date,'YYYY-MM'),facility,customer,destination,COALESCE(vendor_type,'Unknown'),COALESCE(authorisation,'Unknown')
        ORDER BY month DESC,total_mt DESC;""",

    "impact: dispatch by material category": """
        SELECT
            TO_CHAR(date::date,'YYYY-MM') AS month,
            facility,
            COALESCE(outward_material_category,'Unknown') AS material_category,
            COALESCE(vendor_type,'Unknown') AS destination_type,
            ROUND((SUM(dispatched_quantity::numeric)/1000)::numeric,3) AS total_mt
        FROM outward
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date,'YYYY-MM'),facility,COALESCE(outward_material_category,'Unknown'),COALESCE(vendor_type,'Unknown')
        ORDER BY month DESC,total_mt DESC;""",

    "impact: recovery rate trend": """
        SELECT
            TO_CHAR(date::date,'YYYY-MM') AS month,
            facility,
            ROUND((SUM(dispatched_quantity::numeric)/1000)::numeric,3) AS total_dispatched_mt,
            ROUND((SUM(rejected_quantity::numeric)/1000)::numeric,3) AS total_rejected_mt,
            ROUND((SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END)/1000)::numeric,3) AS returned_to_source_mt,
            ROUND(((SUM(dispatched_quantity::numeric)-SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END))/1000)::numeric,3) AS net_recovered_mt,
            ROUND(((SUM(dispatched_quantity::numeric)-SUM(CASE WHEN COALESCE(vendor_type,'')='Returned to Source' THEN dispatched_quantity::numeric ELSE 0 END))/
                NULLIF(SUM(dispatched_quantity::numeric),0)*100)::numeric,2) AS recovery_rate_pct
        FROM outward
        {FACILITY_FILTER}
        GROUP BY TO_CHAR(date::date,'YYYY-MM'),facility
        ORDER BY month DESC;""",

    "impact: vendor coverage": """
        SELECT
            facility,
            COALESCE(source_type,'Unknown') AS source_type,
            COALESCE(authorisation,'Unknown') AS authorization,
            COUNT(DISTINCT received_material_from) AS unique_vendors,
            COUNT(DISTINCT vendor_location) AS unique_locations,
            ROUND((SUM(accepted_quantity::numeric)/1000)::numeric,3) AS total_mt
        FROM inward
        {FACILITY_FILTER}
        GROUP BY facility,COALESCE(source_type,'Unknown'),COALESCE(authorisation,'Unknown')
        ORDER BY total_mt DESC;""",
}

SIDEBAR_GROUPS = {
    "Inward Analytics": ["inward: kpi summary","inward: vendor analysis","inward: vendor location analysis","inward: vendor material analytics"],
    "Production Analytics": ["production: kpi summary","production: process x equipment analysis","production: process material analytics","production: equipment analysis","production: shift analysis","production: equipment x shift analysis"],
    "Transport Analytics": ["transport: vendor and vehicle analysis"],
    "ULB Analytics": ["ulb: ward location analysis","ulb: driver analysis"],
    "Outward Analytics": ["outward: kpi summary","outward: customer analysis","outward: customer destination analysis","outward: customer material analytics","outward: material rate trend"],
    "Training Analytics": ["training: topic analysis","training: trainer analysis","training: category analysis","training: role based attendance","training: repeat attendees"],
    "Environmental Impact": ["impact: inward kpi","impact: inward by source type","impact: inward by material category","impact: dispatch kpi","impact: dispatch by destination type","impact: dispatch by material category","impact: recovery rate trend","impact: vendor coverage","impact: transport ghg emissions"],
    "Supply Chain Analytics": ["supply chain: kpi summary","supply chain: inward rate rejection history","supply chain: outward rate rejection history"],
}

# The "run everything for this type" preset — shared between the main app's
# quick-preset buttons and the Reports PDF generator, so there's one place
# that defines what a "full analysis" for a given type actually includes.
ANALYSIS_TYPE_COMBINED = {
    "Inward Analytics": ["inward: kpi summary", "inward: vendor analysis", "inward: vendor location analysis", "inward: vendor material analytics"],
    "Outward Analytics": ["outward: kpi summary", "outward: customer analysis", "outward: customer destination analysis", "outward: customer material analytics", "outward: material rate trend"],
    "Production Analytics": ["production: kpi summary", "production: process x equipment analysis", "production: process material analytics", "production: equipment analysis", "production: shift analysis", "production: equipment x shift analysis"],
    "ULB Analytics": ["ulb: ward location analysis", "ulb: driver analysis"],
    "Training Analytics": ["training: kpi summary", "training: topic analysis", "training: trainer analysis", "training: category analysis", "training: role based attendance", "training: repeat attendees"],
    "Environmental Impact": ["impact: inward kpi", "impact: inward by source type", "impact: dispatch kpi", "impact: dispatch by destination type", "impact: recovery rate trend"],
    "Supply Chain Analytics": ["supply chain: kpi summary", "supply chain: inward rate rejection history", "supply chain: outward rate rejection history"],
}
