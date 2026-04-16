"""Output Parquet schemas for foreqcast.

Three output files:
  - forecast.parquet: per-product demand forecast with regression stats
  - replenishment_rules.parquet: computed min/max for Odoo orderpoints
  - inventory_analysis.parquet: inventory position and health classification (when inventory_mode != 'ignore')
"""

import pyarrow as pa

FORECAST_SCHEMA = pa.schema(
    [
        ("_odoo_product_id", pa.int64()),
        ("product_name", pa.string()),
        ("_odoo_warehouse_id", pa.int64()),
        ("warehouse_code", pa.string()),
        ("bucket_type", pa.string()),  # 'daily' or 'weekly'
        ("data_points", pa.int64()),
        ("first_observation", pa.timestamp("us", tz="UTC")),
        ("last_observation", pa.timestamp("us", tz="UTC")),
        ("avg_daily_demand", pa.float64()),
        ("trend_slope", pa.float64()),  # units per day
        ("intercept", pa.float64()),
        ("r_squared", pa.float64()),
        ("forecasted_daily_demand", pa.float64()),  # projected at horizon midpoint
        ("forecast_horizon_days", pa.int64()),
        ("confidence", pa.string()),  # 'high', 'medium', 'low', 'insufficient_data'
    ]
)

REPLENISHMENT_RULES_SCHEMA = pa.schema(
    [
        ("_odoo_product_id", pa.int64()),
        ("product_name", pa.string()),
        ("_odoo_warehouse_id", pa.int64()),
        ("warehouse_code", pa.string()),
        ("_odoo_location_id", pa.int64()),  # warehouse lot_stock_id
        ("product_min_qty", pa.float64()),
        ("product_max_qty", pa.float64()),
        ("lead_time_days", pa.int64()),
        ("service_level", pa.float64()),
        ("review_period_days", pa.int64()),
        ("forecasted_daily_demand", pa.float64()),
        ("trigger", pa.string()),  # 'auto'
        ("action", pa.string()),  # 'create', 'update', 'skip'
        ("skip_reason", pa.string()),  # null or reason for skipping
        # Inventory analysis columns (populated when inventory_mode != 'ignore')
        ("on_hand_qty", pa.float64()),
        ("reserved_qty", pa.float64()),
        ("incoming_supply_qty", pa.float64()),
        ("outgoing_demand_qty", pa.float64()),
        ("net_available_qty", pa.float64()),
        ("coverage_days", pa.float64()),  # null if no demand
        ("inventory_flag", pa.string()),  # 'ok','overstock','understock','at_risk','no_stock','no_demand','no_data'
    ]
)

INVENTORY_ANALYSIS_SCHEMA = pa.schema(
    [
        ("_odoo_product_id", pa.int64()),
        ("product_name", pa.string()),
        ("_odoo_warehouse_id", pa.int64()),
        ("warehouse_code", pa.string()),
        # On-hand breakdown
        ("on_hand_qty", pa.float64()),
        ("reserved_qty", pa.float64()),
        ("available_qty", pa.float64()),  # on_hand - reserved (when respect_reservations=true)
        # Supply pipeline
        ("incoming_po_qty", pa.float64()),
        ("incoming_move_qty", pa.float64()),
        ("incoming_mo_qty", pa.float64()),
        ("total_incoming", pa.float64()),
        # Demand pipeline
        ("outgoing_so_qty", pa.float64()),
        ("outgoing_move_qty", pa.float64()),
        ("outgoing_mo_consumption", pa.float64()),
        ("total_outgoing", pa.float64()),
        # Net position
        ("net_position", pa.float64()),
        ("forecasted_daily_demand", pa.float64()),
        ("coverage_days", pa.float64()),  # null if no demand
        # Classification
        ("inventory_flag", pa.string()),  # ok, overstock, understock, at_risk, no_stock, no_demand, no_data
        ("computed_min_qty", pa.float64()),
        ("computed_max_qty", pa.float64()),
    ]
)
