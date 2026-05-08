"""Write forecast results and replenishment rules to Parquet files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .calculator import ReplenishmentRule
from .forecaster import ForecastResult
from .inventory import InventoryConfig, InventoryPosition
from .schemas import DECISIONS_SCHEMA, FORECAST_SCHEMA, INVENTORY_ANALYSIS_SCHEMA, REPLENISHMENT_RULES_SCHEMA


def write_forecasts(
    results: list[ForecastResult],
    output_dir: str | Path,
    bucket_type: str,
    horizon_days: int,
    product_names: dict[int, str] | None = None,
    warehouse_codes: dict[int, str] | None = None,
) -> Path:
    """Write forecast results to forecast.parquet."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cols = {f.name: [] for f in FORECAST_SCHEMA}

    for r in results:
        cols["_odoo_product_id"].append(r.product_id)
        cols["product_name"].append((product_names or {}).get(r.product_id, ""))
        cols["_odoo_warehouse_id"].append(r.warehouse_id)
        cols["warehouse_code"].append((warehouse_codes or {}).get(r.warehouse_id, ""))
        cols["bucket_type"].append(bucket_type)
        cols["data_points"].append(r.data_points)
        cols["first_observation"].append(
            datetime.combine(r.first_observation, datetime.min.time()).replace(tzinfo=timezone.utc)
        )
        cols["last_observation"].append(
            datetime.combine(r.last_observation, datetime.min.time()).replace(tzinfo=timezone.utc)
        )
        cols["avg_daily_demand"].append(r.avg_daily_demand)
        cols["trend_slope"].append(r.slope)
        cols["intercept"].append(r.intercept)
        cols["r_squared"].append(r.r_squared)
        cols["forecasted_daily_demand"].append(r.forecasted_daily_demand)
        cols["forecast_horizon_days"].append(horizon_days)
        cols["confidence"].append(r.confidence)
        cols["quantile_min_qty"].append(r.quantile_min_qty)
        cols["quantile_max_qty"].append(r.quantile_max_qty)

    table = pa.table(cols, schema=FORECAST_SCHEMA)
    path = output_dir / "forecast.parquet"
    pq.write_table(table, path, compression="snappy")
    return path


def write_rules(
    rules: list[ReplenishmentRule],
    output_dir: str | Path,
) -> Path:
    """Write replenishment rules to replenishment_rules.parquet."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cols = {f.name: [] for f in REPLENISHMENT_RULES_SCHEMA}

    for r in rules:
        cols["_odoo_product_id"].append(r.product_id)
        cols["product_name"].append(r.product_name)
        cols["_odoo_warehouse_id"].append(r.warehouse_id)
        cols["warehouse_code"].append(r.warehouse_code)
        cols["_odoo_location_id"].append(r.location_id)
        cols["product_min_qty"].append(r.min_qty)
        cols["product_max_qty"].append(r.max_qty)
        cols["planned_lead_time_days"].append(r.planned_lead_time_days)
        cols["empirical_lead_time_days"].append(r.empirical_lead_time_days)
        cols["service_level"].append(r.service_level)
        cols["review_period_days"].append(r.review_period_days)
        cols["forecasted_daily_demand"].append(r.forecasted_daily_demand)
        cols["trigger"].append(r.trigger)
        cols["action"].append(r.action)
        cols["skip_reason"].append(r.skip_reason)
        cols["on_hand_qty"].append(r.on_hand_qty)
        cols["reserved_qty"].append(r.reserved_qty)
        cols["incoming_supply_qty"].append(r.incoming_supply_qty)
        cols["outgoing_demand_qty"].append(r.outgoing_demand_qty)
        cols["net_available_qty"].append(r.net_available_qty)
        cols["coverage_days"].append(r.coverage_days)
        cols["inventory_flag"].append(r.inventory_flag)

    table = pa.table(cols, schema=REPLENISHMENT_RULES_SCHEMA)
    path = output_dir / "replenishment_rules.parquet"
    pq.write_table(table, path, compression="snappy")
    return path


def write_inventory_analysis(
    positions: dict[tuple[int, int], InventoryPosition],
    rules: list[ReplenishmentRule],
    product_names: dict[int, str],
    warehouse_codes: dict[int, str],
    config: InventoryConfig,
    output_dir: str | Path,
) -> Path:
    """Write inventory analysis to inventory_analysis.parquet."""
    from .inventory import classify_inventory

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build rule lookup for demand/min/max data
    rule_lookup = {(r.product_id, r.warehouse_id): r for r in rules}

    cols = {f.name: [] for f in INVENTORY_ANALYSIS_SCHEMA}

    for (pid, wid), pos in positions.items():
        rule = rule_lookup.get((pid, wid))
        daily = rule.forecasted_daily_demand if rule else 0.0

        cols["_odoo_product_id"].append(pid)
        cols["product_name"].append(product_names.get(pid, ""))
        cols["_odoo_warehouse_id"].append(wid)
        cols["warehouse_code"].append(warehouse_codes.get(wid, ""))
        cols["on_hand_qty"].append(pos.on_hand_qty)
        cols["reserved_qty"].append(pos.reserved_qty)
        cols["available_qty"].append(pos.available_qty)
        cols["incoming_po_qty"].append(pos.incoming_po_qty)
        cols["incoming_move_qty"].append(pos.incoming_move_qty)
        cols["incoming_mo_qty"].append(pos.incoming_mo_qty)
        cols["total_incoming"].append(pos.total_incoming)
        cols["outgoing_so_qty"].append(pos.outgoing_so_qty)
        cols["outgoing_move_qty"].append(pos.outgoing_move_qty)
        cols["outgoing_mo_consumption"].append(pos.outgoing_mo_consumption)
        cols["total_outgoing"].append(pos.total_outgoing)
        net = pos.net_position(
            config.respect_reservations, config.include_incoming_supply, config.include_outgoing_demand
        )
        cols["net_position"].append(net)
        cols["forecasted_daily_demand"].append(daily)
        cov = pos.coverage_days(
            daily, config.respect_reservations, config.include_incoming_supply, config.include_outgoing_demand
        )
        cols["coverage_days"].append(cov)
        cols["inventory_flag"].append(classify_inventory(pos, daily, config))
        cols["computed_min_qty"].append(rule.min_qty if rule else 0.0)
        cols["computed_max_qty"].append(rule.max_qty if rule else 0.0)

    table = pa.table(cols, schema=INVENTORY_ANALYSIS_SCHEMA)
    path = output_dir / "inventory_analysis.parquet"
    pq.write_table(table, path, compression="snappy")
    return path


def write_decisions(
    rules: list[ReplenishmentRule],
    output_dir: str | Path,
    run_uuid: str,
    decision_timestamp: datetime,
    source_run_uuid: str | None = None,
) -> Path:
    """Write actionable replenishment rules as decisions.parquet.

    Produces a Parquet file conforming to the parqcast DECISIONS_SCHEMA
    protocol so that parqcast-ingesters can consume it directly.

    Only non-skip rules (action in {"create", "update"}) are included.

    Args:
        rules: Computed replenishment rules from the calculator.
        output_dir: Directory to write decisions.parquet.
        run_uuid: Unique identifier for this mimir pipeline run.
        decision_timestamp: UTC time when mimir finished computing.
        source_run_uuid: The run_uuid from the parqcast manifest.json
            (establishes the causal chain back to the Odoo export).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cols: dict[str, list[object]] = {f.name: [] for f in DECISIONS_SCHEMA}

    for r in rules:
        if r.action == "skip":
            continue

        decision_id = f"mimir-{run_uuid[:8]}-{r.product_id}-{r.warehouse_id}"
        remark = r.skip_reason or f"action={r.action}"

        cols["decision_id"].append(decision_id)
        cols["run_uuid"].append(run_uuid)
        cols["decision_timestamp"].append(decision_timestamp)
        cols["decision_type"].append("ORDERPOINT")
        cols["status"].append("pending")
        cols["item_name"].append(r.product_name)
        cols["_odoo_product_id"].append(r.product_id)
        cols["_odoo_uom_id"].append(0)
        cols["location_name"].append(r.warehouse_code)
        cols["_odoo_location_id"].append(r.location_id)
        cols["quantity"].append(0.0)
        cols["start_date"].append(None)
        cols["end_date"].append(None)
        cols["supplier_name"].append(None)
        cols["_odoo_supplier_id"].append(0)
        cols["_odoo_bom_id"].append(0)
        cols["origin_location"].append(None)
        cols["destination_location"].append(None)
        cols["parent_reference"].append(source_run_uuid)
        cols["workcenter_name"].append(None)
        cols["_odoo_workcenter_id"].append(0)
        cols["batch"].append(None)
        cols["remark"].append(remark)
        cols["min_quantity"].append(r.min_qty)
        cols["max_quantity"].append(r.max_qty)
        cols["delay"].append(r.planned_lead_time_days)  # Legacy delay matches planned
        cols["planned_lead_time_days"].append(r.planned_lead_time_days)
        cols["empirical_lead_time_days"].append(r.empirical_lead_time_days)

    table = pa.table(cols, schema=DECISIONS_SCHEMA)
    path = output_dir / "decisions.parquet"
    pq.write_table(table, path, compression="snappy")
    return path
