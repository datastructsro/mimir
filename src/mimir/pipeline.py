"""End-to-end forecast pipeline.

Orchestrates: read parquet → import rules → write outputs.
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

import pyarrow as pa
import pyarrow.parquet as pq

from . import _cols
from .calculator import ReplenishmentRule, resolve_empirical_lead_time
from .config import MimirConfig
from .importer import load_forecast_evidence_table, load_minmax_table, load_warehouse_lead_times
from .reader import read_parqcast_export
from .writer import write_decisions, write_rules

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
    warehouse_id: NotRequired[int]


def run_pipeline(
    parquet_input_dir: str | Path,
    parquet_output_dir: str | Path,
    config: MimirConfig,
) -> PipelineStats:
    """Execute the warehouse-scoped rule import pipeline.

    Args:
        parquet_input_dir: Directory containing parqcast export parquet files
        parquet_output_dir: Directory to write rule/output parquet files
        config: Resolved configuration

    Returns:
        Summary dict with statistics
    """
    start = time.monotonic()
    run_uuid = str(_uuid.uuid4())

    try:
        output_dir = Path(parquet_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Read parqcast exports
        logger.info("Reading parqcast exports from %s", parquet_input_dir)
        data = read_parqcast_export(parquet_input_dir)

        source_run_uuid: str | None = None
        if data.manifest:
            source_run_uuid = data.manifest.run_uuid
            logger.info(
                "Temporal chain: source_run=%s (finished=%s) → mimir_run=%s",
                source_run_uuid,
                data.manifest.finished_at,
                run_uuid,
            )

        product_names = _build_product_names(data.products)
        warehouse_locations = _build_warehouse_locations(data.warehouses)
        warehouse_codes = _build_warehouse_codes(data.warehouses)
        warehouse_alias_lookup = _build_warehouse_alias_lookup(data.warehouses)
        product_id_lookup = {str(product_id): product_id for product_id in product_names}
        lead_times_by_facility = load_warehouse_lead_times(parquet_input_dir)

        # 2. Load the selected warehouse's imported rules and optional raw forecast evidence.
        minmax_table, warehouse_id = load_minmax_table(
            parquet_input_dir,
            config,
            warehouse_alias_lookup,
            warehouse_codes,
        )
        forecast_evidence = load_forecast_evidence_table(
            parquet_input_dir,
            config,
            warehouse_id,
            warehouse_codes.get(warehouse_id, ""),
        )
        rules = _build_imported_rules(
            minmax_table=minmax_table,
            product_id_lookup=product_id_lookup,
            product_names=product_names,
            warehouse_id=warehouse_id,
            warehouse_locations=warehouse_locations,
            warehouse_codes=warehouse_codes,
            existing_orderpoints=data.orderpoints,
            lead_times_by_facility=lead_times_by_facility,
            empirical_lead_times=data.empirical_lead_times,
        )

        # 3. Persist normalized outputs.
        decision_timestamp = datetime.now(timezone.utc)
        logger.info("Writing output parquet files to %s", parquet_output_dir)
        write_rules(rules, parquet_output_dir)
        write_decisions(
            rules=rules,
            output_dir=parquet_output_dir,
            run_uuid=run_uuid,
            decision_timestamp=decision_timestamp,
            source_run_uuid=source_run_uuid,
        )
        logger.info("Wrote decisions.parquet (run_uuid=%s)", run_uuid)
        if forecast_evidence is not None:
            pq.write_table(forecast_evidence, output_dir / "forecast_evidence.parquet", compression="snappy")
            logger.info("Wrote forecast_evidence.parquet")

        duration = time.monotonic() - start

        stats: PipelineStats = {
            "products_analyzed": len(rules),
            "products_forecasted": forecast_evidence.num_rows if forecast_evidence is not None else 0,
            "rules_created": sum(1 for r in rules if r.action == "create"),
            "rules_updated": sum(1 for r in rules if r.action == "update"),
            "rules_skipped": sum(1 for r in rules if r.action == "skip"),
            "duration_seconds": round(duration, 2),
            "status": "complete",
            "run_uuid": run_uuid,
            "warehouse_id": warehouse_id,
        }

        if source_run_uuid:
            stats["source_run_uuid"] = source_run_uuid

        logger.info(
            "Pipeline complete in %.1fs: warehouse=%s, rules=%d, forecast_rows=%d",
            duration,
            warehouse_id,
            len(rules),
            stats["products_forecasted"],
        )
        return stats

    except Exception:
        raise


def _build_product_names(products_table: pa.Table) -> dict[int, str]:
    return dict(_cols.nonnull_pairs(products_table, "_odoo_product_id", "name"))


def _build_warehouse_locations(warehouses_table: pa.Table) -> dict[int, int]:
    return dict(_cols.nonnull_pairs(warehouses_table, "_odoo_warehouse_id", "_odoo_lot_stock_id"))


def _build_warehouse_codes(warehouses_table: pa.Table) -> dict[int, str]:
    return dict(_cols.nonnull_pairs(warehouses_table, "_odoo_warehouse_id", "code"))


def _build_warehouse_alias_lookup(warehouses_table: pa.Table) -> dict[str, int]:
    aliases: dict[str, int] = {}
    for warehouse_id, warehouse_code in _cols.nonnull_pairs(warehouses_table, "_odoo_warehouse_id", "code"):
        aliases[str(warehouse_id)] = warehouse_id
        if warehouse_code:
            aliases[str(warehouse_code)] = warehouse_id
    return aliases


def _build_imported_rules(
    *,
    minmax_table: pa.Table,
    product_id_lookup: dict[str, int],
    product_names: dict[int, str],
    warehouse_id: int,
    warehouse_locations: dict[int, int],
    warehouse_codes: dict[int, str],
    existing_orderpoints: pa.Table | None,
    lead_times_by_facility: dict[str, int],
    empirical_lead_times: pa.Table | None,
) -> list[ReplenishmentRule]:
    location_id = warehouse_locations.get(warehouse_id)
    if location_id is None:
        raise ValueError(f"Warehouse {warehouse_id} is missing lot stock location data")

    warehouse_code = warehouse_codes.get(warehouse_id, "")
    existing_orderpoint_keys = _build_existing_orderpoint_keys(existing_orderpoints)
    planned_lead_time_days = _resolve_planned_lead_time(lead_times_by_facility, warehouse_id, warehouse_code)

    rules: list[ReplenishmentRule] = []
    for row in minmax_table.to_pylist():
        product_key = str(row["product_id"])
        product_id = product_id_lookup.get(product_key)
        if product_id is None:
            raise ValueError(f"Imported rule references unknown product_id '{product_key}'")

        empirical_lead_time = resolve_empirical_lead_time(product_id, warehouse_id, empirical_lead_times)
        action = "update" if (product_id, warehouse_id) in existing_orderpoint_keys else "create"

        rules.append(
            ReplenishmentRule(
                product_id=product_id,
                product_name=product_names.get(product_id, product_key),
                warehouse_id=warehouse_id,
                warehouse_code=warehouse_code,
                location_id=location_id,
                min_qty=round(float(row["min_quantity"]), 2),
                max_qty=round(float(row["max_quantity"]), 2),
                planned_lead_time_days=planned_lead_time_days,
                empirical_lead_time_days=empirical_lead_time,
                service_level=0.0,
                review_period_days=0,
                forecasted_daily_demand=0.0,
                trigger="auto",
                action=action,
                skip_reason=None,
            )
        )

    return rules


def _resolve_planned_lead_time(lead_times_by_facility: dict[str, int], warehouse_id: int, warehouse_code: str) -> int:
    if str(warehouse_id) in lead_times_by_facility:
        return lead_times_by_facility[str(warehouse_id)]
    if warehouse_code and warehouse_code in lead_times_by_facility:
        return lead_times_by_facility[warehouse_code]
    return 0


def _build_existing_orderpoint_keys(existing_orderpoints: pa.Table | None) -> set[tuple[int, int]]:
    if existing_orderpoints is None or existing_orderpoints.num_rows == 0:
        return set()

    keys: set[tuple[int, int]] = set()
    product_ids = existing_orderpoints.column("_odoo_product_id").to_pylist()
    warehouse_ids = existing_orderpoints.column("_odoo_warehouse_id").to_pylist()

    for product_id, warehouse_id in zip(product_ids, warehouse_ids, strict=False):
        if isinstance(product_id, int) and isinstance(warehouse_id, int):
            keys.add((product_id, warehouse_id))

    return keys
