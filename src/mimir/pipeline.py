"""End-to-end forecast pipeline.

Orchestrates: read parquet → aggregate → forecast → calculate → write → push.
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

import pyarrow as pa

from . import _cols
from .calculator import calculate_rule
from .config import MimirConfig
from .inventory import InventoryConfig, load_inventory_positions
from .providers import get_forecast_provider
from .pusher import OdooPusher, PushStats
from .reader import read_parqcast_export
from .writer import write_decisions, write_forecasts, write_inventory_analysis, write_rules

logger = logging.getLogger(__name__)


class PipelineStats(TypedDict):
    products_analyzed: int
    products_forecasted: int
    rules_created: int
    rules_updated: int
    rules_skipped: int
    duration_seconds: float
    status: str
    run_uuid: NotRequired[str]
    source_run_uuid: NotRequired[str]
    inventory_mode: NotRequired[str]
    inventory_positions_loaded: NotRequired[int]
    overstock_count: NotRequired[int]
    understock_count: NotRequired[int]
    push_stats: NotRequired[PushStats]


def run_pipeline(
    parquet_input_dir: str | Path,
    parquet_output_dir: str | Path,
    config: MimirConfig,
    push_to_odoo: bool = False,
) -> PipelineStats:
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
    run_uuid = str(_uuid.uuid4())


    try:
        # 1. Read parqcast exports
        logger.info("Reading parqcast exports from %s", parquet_input_dir)
        data = read_parqcast_export(parquet_input_dir)

        # Extract source temporal metadata from manifest
        source_run_uuid: str | None = None
        if data.manifest:
            source_run_uuid = data.manifest.run_uuid
            logger.info(
                "Temporal chain: source_run=%s (finished=%s) → mimir_run=%s",
                source_run_uuid, data.manifest.finished_at, run_uuid,
            )

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
            inventory_positions = load_inventory_positions(Path(parquet_input_dir), warehouse_locations, inv_config)
            logger.info("Loaded %d inventory positions", len(inventory_positions))

        # 2. Demand aggregation is now handled by the server.

        # 3. Forecast each series
        logger.info("Generating forecasts using %s source", config.forecast_source)
        provider = get_forecast_provider(config)
        forecasts = provider.get_forecasts(config)
        logger.info("Generated %d forecasts", len(forecasts))
        # 4. Calculate replenishment rules (with quantile reorder points)
        logger.info("Calculating replenishment rules for %d forecasts", len(forecasts))
        rules = []
        for f in forecasts:
            cat_id = product_categories.get(f.product_id)
            loc_id = warehouse_locations.get(f.warehouse_id, 0)
            wh_code = warehouse_codes.get(f.warehouse_id, "")

            qr = None
            if f.quantile_min_qty is not None and f.quantile_max_qty is not None:
                qr = (f.quantile_min_qty, f.quantile_max_qty)

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
        decision_timestamp = datetime.now(timezone.utc)
        logger.info("Writing output parquet files to %s", parquet_output_dir)
        write_forecasts(
            forecasts, parquet_output_dir, config.time_bucket, config.forecast_horizon_days,
            product_names=product_names, warehouse_codes=warehouse_codes,
        )
        write_rules(rules, parquet_output_dir)

        # Write decisions.parquet (temporal pipeline output)
        write_decisions(
            rules=rules,
            output_dir=parquet_output_dir,
            run_uuid=run_uuid,
            decision_timestamp=decision_timestamp,
            source_run_uuid=source_run_uuid,
        )
        logger.info("Wrote decisions.parquet (run_uuid=%s)", run_uuid)

        if config.inventory_mode != "ignore" and inventory_positions:
            assert inv_config is not None
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

        stats: PipelineStats = {
            "products_analyzed": len(forecasts),
            "products_forecasted": len(forecasts),
            "rules_created": sum(1 for r in rules if r.action == "create"),
            "rules_updated": sum(1 for r in rules if r.action == "update"),
            "rules_skipped": len(skipped),
            "duration_seconds": round(duration, 2),
            "status": "complete",
            "run_uuid": run_uuid,
        }

        if source_run_uuid:
            stats["source_run_uuid"] = source_run_uuid

        if config.inventory_mode != "ignore":
            stats["inventory_mode"] = config.inventory_mode
            stats["inventory_positions_loaded"] = len(inventory_positions)
            stats["overstock_count"] = sum(1 for r in rules if r.inventory_flag == "overstock")
            stats["understock_count"] = sum(
                1 for r in rules if r.inventory_flag in ("understock", "at_risk", "no_stock")
            )

        if push_stats:
            stats["push_stats"] = push_stats



        logger.info(
            "Pipeline complete in %.1fs: %d analyzed, %d forecasted, %d actionable, %d skipped",
            duration, stats["products_analyzed"], stats["products_forecasted"],
            len(actionable), len(skipped),
        )
        return stats

    except Exception:
        raise


def _build_product_names(products_table: pa.Table) -> dict[int, str]:
    return dict(_cols.nonnull_pairs(products_table, "_odoo_product_id", "name"))


def _build_product_categories(products_table: pa.Table) -> dict[int, int]:
    return dict(_cols.nonnull_pairs(products_table, "_odoo_product_id", "_odoo_categ_id"))


def _build_warehouse_locations(warehouses_table: pa.Table) -> dict[int, int]:
    return dict(_cols.nonnull_pairs(warehouses_table, "_odoo_warehouse_id", "_odoo_lot_stock_id"))


def _build_warehouse_codes(warehouses_table: pa.Table) -> dict[int, str]:
    return dict(_cols.nonnull_pairs(warehouses_table, "_odoo_warehouse_id", "code"))
