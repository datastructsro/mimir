"""End-to-end forecast pipeline.

Orchestrates: read parquet → aggregate → forecast → calculate → write → push.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .aggregator import aggregate_demand
from .calculator import calculate_rule, resolve_lead_time, resolve_review_period, resolve_service_level
from .config import ForeqcastConfig, finish_run, start_run
from .forecaster import compute_quantile_reorder_points, fit_demand
from .inventory import InventoryConfig, load_inventory_positions
from .pusher import OdooPusher
from .reader import read_parqcast_export
from .writer import write_forecasts, write_inventory_analysis, write_rules

logger = logging.getLogger(__name__)


def run_pipeline(
    parquet_input_dir: str | Path,
    parquet_output_dir: str | Path,
    config: ForeqcastConfig,
    db_conn=None,
    push_to_odoo: bool = False,
) -> dict:
    """Execute the full forecasting pipeline.

    Args:
        parquet_input_dir: Directory containing parqcast export parquet files
        parquet_output_dir: Directory to write forecast/rules parquet files
        config: Resolved configuration
        db_conn: SQLite connection for audit trail (optional)
        push_to_odoo: Whether to push rules to Odoo after writing parquet

    Returns:
        Summary dict with statistics
    """
    start = time.monotonic()
    run_id = None
    if db_conn:
        run_id = start_run(db_conn, str(parquet_input_dir))

    try:
        # 1. Read parqcast exports
        logger.info("Reading parqcast exports from %s", parquet_input_dir)
        data = read_parqcast_export(parquet_input_dir)

        # Build lookup maps
        product_names = _build_product_names(data.products)
        product_categories = _build_product_categories(data.products)
        warehouse_locations = _build_warehouse_locations(data.warehouses)
        warehouse_codes = _build_warehouse_codes(data.warehouses)

        # 1b. Load inventory positions (if mode != 'ignore')
        inventory_positions = {}
        inv_config = None
        if config.inventory_mode != "ignore":
            inv_config = InventoryConfig(
                inventory_mode=config.inventory_mode,
                respect_reservations=config.respect_reservations,
                include_incoming_supply=config.include_incoming_supply,
                include_outgoing_demand=config.include_outgoing_demand,
                incoming_sources=config.incoming_sources,
                outgoing_sources=config.outgoing_sources,
                overstock_threshold_days=config.overstock_threshold_days,
                understock_threshold_days=config.understock_threshold_days,
                overstock_skip=config.overstock_skip,
                understock_service_level_bump=config.understock_service_level_bump,
            )
            logger.info("Loading inventory positions (mode=%s)", config.inventory_mode)
            inventory_positions = load_inventory_positions(parquet_input_dir, warehouse_locations, inv_config)
            logger.info("Loaded %d inventory positions", len(inventory_positions))

        # 2. Aggregate demand
        logger.info("Aggregating demand time series (bucket=%s)", config.time_bucket)
        demand_series = aggregate_demand(
            data.sale_order_lines,
            bucket=config.time_bucket,
        )
        logger.info("Found %d product×warehouse combinations with demand", len(demand_series))

        # 3. Forecast each series
        logger.info("Running linear regression on %d time series", len(demand_series))
        forecasts = []
        for (pid, wid), points in demand_series.items():
            result = fit_demand(
                product_id=pid,
                warehouse_id=wid,
                points=points,
                forecast_horizon_days=config.forecast_horizon_days,
                min_data_points=config.min_data_points,
                bucket=config.time_bucket,
            )
            if result:
                forecasts.append(result)

        # 4. Calculate replenishment rules (with quantile reorder points)
        logger.info("Calculating replenishment rules for %d forecasts", len(forecasts))
        rules = []
        for f in forecasts:
            cat_id = product_categories.get(f.product_id)
            loc_id = warehouse_locations.get(f.warehouse_id, 0)
            wh_code = warehouse_codes.get(f.warehouse_id, "")

            # Resolve per-product parameters for quantile computation
            lt = resolve_lead_time(f.product_id, cat_id, data.supplier_info, config)
            rp = resolve_review_period(f.product_id, cat_id, config)
            sl = resolve_service_level(f.product_id, cat_id, config)
            points = demand_series.get((f.product_id, f.warehouse_id), [])

            qr = compute_quantile_reorder_points(
                points, lt, rp, sl,
                min_data_points=config.min_data_points,
                bucket=config.time_bucket,
            )

            inv_pos = inventory_positions.get((f.product_id, f.warehouse_id))

            rule = calculate_rule(
                forecast=f,
                product_name=product_names.get(f.product_id, ""),
                warehouse_code=wh_code,
                location_id=loc_id,
                category_id=cat_id,
                supplier_info=data.supplier_info,
                existing_orderpoints=data.orderpoints,
                config=config,
                quantile_result=qr,
                inventory_position=inv_pos,
                inventory_config=inv_config,
            )
            rules.append(rule)

        # 5. Write parquet outputs
        logger.info("Writing output parquet files to %s", parquet_output_dir)
        write_forecasts(
            forecasts, parquet_output_dir, config.time_bucket, config.forecast_horizon_days,
            product_names=product_names, warehouse_codes=warehouse_codes,
        )
        write_rules(rules, parquet_output_dir)

        if config.inventory_mode != "ignore" and inventory_positions:
            write_inventory_analysis(
                positions=inventory_positions,
                rules=rules,
                product_names=product_names,
                warehouse_codes=warehouse_codes,
                config=inv_config,
                output_dir=parquet_output_dir,
            )
            logger.info("Wrote inventory_analysis.parquet")

        # 6. Push to Odoo (optional)
        push_stats = None
        if push_to_odoo and config.odoo_url:
            logger.info("Pushing rules to Odoo at %s", config.odoo_url)
            pusher = OdooPusher(config.odoo_url, config.odoo_db, config.odoo_user, config.odoo_password)
            rules_path = Path(parquet_output_dir) / "replenishment_rules.parquet"
            push_stats = pusher.push_rules(rules_path)

        # Statistics
        duration = time.monotonic() - start
        actionable = [r for r in rules if r.action != "skip"]
        skipped = [r for r in rules if r.action == "skip"]

        stats = {
            "products_analyzed": len(demand_series),
            "products_forecasted": len(forecasts),
            "rules_created": sum(1 for r in rules if r.action == "create"),
            "rules_updated": sum(1 for r in rules if r.action == "update"),
            "rules_skipped": len(skipped),
            "duration_seconds": round(duration, 2),
            "status": "complete",
        }

        if config.inventory_mode != "ignore":
            stats["inventory_mode"] = config.inventory_mode
            stats["inventory_positions_loaded"] = len(inventory_positions)
            stats["overstock_count"] = sum(1 for r in rules if r.inventory_flag == "overstock")
            stats["understock_count"] = sum(
                1 for r in rules if r.inventory_flag in ("understock", "at_risk", "no_stock")
            )

        if push_stats:
            stats["push_stats"] = push_stats

        if db_conn and run_id:
            # Only pass columns that exist in forecast_runs table
            run_cols = {
                "products_analyzed", "products_forecasted", "rules_created",
                "rules_updated", "rules_skipped", "duration_seconds", "status",
            }
            finish_run(db_conn, run_id, **{k: v for k, v in stats.items() if k in run_cols})

        logger.info(
            "Pipeline complete in %.1fs: %d analyzed, %d forecasted, %d actionable, %d skipped",
            duration, stats["products_analyzed"], stats["products_forecasted"],
            len(actionable), len(skipped),
        )
        return stats

    except Exception as e:
        if db_conn and run_id:
            finish_run(db_conn, run_id, status="error", error_message=str(e))
        raise


def _build_product_names(products_table) -> dict[int, str]:
    pids = products_table.column("_odoo_product_id").to_pylist()
    names = products_table.column("name").to_pylist()
    return dict(zip(pids, names))


def _build_product_categories(products_table) -> dict[int, int]:
    pids = products_table.column("_odoo_product_id").to_pylist()
    cats = products_table.column("_odoo_categ_id").to_pylist()
    return {p: c for p, c in zip(pids, cats) if c is not None}


def _build_warehouse_locations(warehouses_table) -> dict[int, int]:
    wids = warehouses_table.column("_odoo_warehouse_id").to_pylist()
    lids = warehouses_table.column("_odoo_lot_stock_id").to_pylist()
    return dict(zip(wids, lids))


def _build_warehouse_codes(warehouses_table) -> dict[int, str]:
    wids = warehouses_table.column("_odoo_warehouse_id").to_pylist()
    codes = warehouses_table.column("code").to_pylist()
    return dict(zip(wids, codes))
