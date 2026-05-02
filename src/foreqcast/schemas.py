"""Output Parquet schemas for foreqcast.

Three output files:
  - forecast.parquet: per-product demand forecast with regression stats
  - replenishment_rules.parquet: computed min/max for Odoo orderpoints
  - inventory_analysis.parquet: inventory position and health classification (when inventory_mode != 'ignore')

Each pa.schema below has a matching TypedDict for the Python row shape
produced by pa.Table.to_pylist() / to_pydict(). Keep them in lockstep
when adding/removing columns — the schema test in tests/ enforces this.
"""

from datetime import datetime
from typing import Literal, TypedDict

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
        ("quantile_min_qty", pa.float64()),
        ("quantile_max_qty", pa.float64()),
    ]
)

EXTERNAL_FORECAST_SCHEMA = pa.schema(
    [
        ("_odoo_product_id", pa.int64()),
        ("_odoo_warehouse_id", pa.int64()),
        ("forecasted_daily_demand", pa.float64()),
        ("confidence", pa.string()),  # optional, default 'external'
        ("quantile_min_qty", pa.float64()),
        ("quantile_max_qty", pa.float64()),
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
        ("planned_lead_time_days", pa.int64()),
        ("empirical_lead_time_days", pa.int64()),
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


# ---- Python row types matching each pa.schema above ----

BucketType = Literal["daily", "weekly"]
Confidence = Literal["high", "medium", "low", "insufficient_data"]
Action = Literal["create", "update", "skip"]
InventoryFlag = Literal[
    "ok", "overstock", "understock", "at_risk", "no_stock", "no_demand", "no_data"
]


class ForecastRow(TypedDict):
    _odoo_product_id: int
    product_name: str
    _odoo_warehouse_id: int
    warehouse_code: str
    bucket_type: BucketType
    data_points: int
    first_observation: datetime
    last_observation: datetime
    avg_daily_demand: float
    trend_slope: float
    intercept: float
    r_squared: float
    forecasted_daily_demand: float
    forecast_horizon_days: int
    confidence: Confidence
    quantile_min_qty: float | None
    quantile_max_qty: float | None


class RuleRow(TypedDict):
    _odoo_product_id: int
    product_name: str
    _odoo_warehouse_id: int
    warehouse_code: str
    _odoo_location_id: int
    product_min_qty: float
    product_max_qty: float
    planned_lead_time_days: int
    empirical_lead_time_days: int
    service_level: float
    review_period_days: int
    forecasted_daily_demand: float
    trigger: str
    action: Action
    skip_reason: str | None
    on_hand_qty: float | None
    reserved_qty: float | None
    incoming_supply_qty: float | None
    outgoing_demand_qty: float | None
    net_available_qty: float | None
    coverage_days: float | None
    inventory_flag: InventoryFlag | None


class InventoryAnalysisRow(TypedDict):
    _odoo_product_id: int
    product_name: str
    _odoo_warehouse_id: int
    warehouse_code: str
    on_hand_qty: float
    reserved_qty: float
    available_qty: float
    incoming_po_qty: float
    incoming_move_qty: float
    incoming_mo_qty: float
    total_incoming: float
    outgoing_so_qty: float
    outgoing_move_qty: float
    outgoing_mo_consumption: float
    total_outgoing: float
    net_position: float
    forecasted_daily_demand: float
    coverage_days: float | None
    inventory_flag: InventoryFlag
    computed_min_qty: float
    computed_max_qty: float


# ---- Decisions schema (mirrors parqcast-core inbound.py) ----
# This schema is the protocol contract between foreqcast and
# parqcast-ingesters.  Keep it in lockstep with:
#   parqcast/packages/parqcast-core/src/parqcast/schemas/inbound.py

DecisionType = Literal[
    "ORDERPOINT", "LEAD_TIME", "PO", "MO", "RESCHEDULE", "DISTRIBUTION"
]

DECISIONS_SCHEMA = pa.schema(
    [
        ("decision_id", pa.string()),
        ("run_uuid", pa.string()),
        ("decision_timestamp", pa.timestamp("us", tz="UTC")),
        ("decision_type", pa.string()),
        ("status", pa.string()),
        ("item_name", pa.string()),
        ("_odoo_product_id", pa.int64()),
        ("_odoo_uom_id", pa.int64()),
        ("location_name", pa.string()),
        ("_odoo_location_id", pa.int64()),
        ("quantity", pa.float64()),
        ("start_date", pa.timestamp("us", tz="UTC")),
        ("end_date", pa.timestamp("us", tz="UTC")),
        ("supplier_name", pa.string()),
        ("_odoo_supplier_id", pa.int64()),
        ("_odoo_bom_id", pa.int64()),
        ("origin_location", pa.string()),
        ("destination_location", pa.string()),
        ("parent_reference", pa.string()),
        ("workcenter_name", pa.string()),
        ("_odoo_workcenter_id", pa.int64()),
        ("batch", pa.string()),
        ("remark", pa.string()),
        ("min_quantity", pa.float64()),
        ("max_quantity", pa.float64()),
        ("delay", pa.int64()),
        ("planned_lead_time_days", pa.int64()),
        ("empirical_lead_time_days", pa.int64()),
    ]
)


class DecisionRow(TypedDict):
    decision_id: str
    run_uuid: str
    decision_timestamp: datetime
    decision_type: DecisionType
    status: str
    item_name: str
    _odoo_product_id: int
    _odoo_uom_id: int
    location_name: str
    _odoo_location_id: int
    quantity: float
    start_date: datetime | None
    end_date: datetime | None
    supplier_name: str | None
    _odoo_supplier_id: int
    _odoo_bom_id: int
    origin_location: str | None
    destination_location: str | None
    parent_reference: str | None
    workcenter_name: str | None
    _odoo_workcenter_id: int
    batch: str | None
    remark: str | None
    min_quantity: float
    max_quantity: float
    delay: int
    planned_lead_time_days: int
    empirical_lead_time_days: int

