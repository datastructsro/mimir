"""Remote-only replenishment import pipeline."""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

import pyarrow as pa
import pyarrow.parquet as pq

from .calculator import ReplenishmentRule, resolve_empirical_lead_time
from .config import MimirConfig
from .runtime_context import PipelineRuntimeContext
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
    parquet_output_dir: str | Path,
    config: MimirConfig,
    runtime_context: PipelineRuntimeContext,
    *,
    minmax_table: pa.Table,
    forecast_evidence: pa.Table | None = None,
    empirical_lead_times: pa.Table | None = None,
) -> PipelineStats:
    """Normalize server-published rules into Mimir outputs for one warehouse."""
    start = time.monotonic()
    run_uuid = str(_uuid.uuid4())
    warehouse_id = _selected_warehouse_id(config)

    output_dir = Path(parquet_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Importing remote rules for warehouse=%s", warehouse_id)

    rules = _build_imported_rules(
        minmax_table=minmax_table,
        product_id_lookup=runtime_context.product_id_lookup,
        product_names=runtime_context.product_names,
        warehouse_id=warehouse_id,
        warehouse_code=runtime_context.warehouse_code,
        location_id=runtime_context.location_id,
        existing_orderpoint_keys=runtime_context.existing_orderpoint_keys,
        empirical_lead_times=empirical_lead_times,
    )

    decision_timestamp = datetime.now(timezone.utc)
    write_rules(rules, output_dir)
    write_decisions(
        rules=rules,
        output_dir=output_dir,
        run_uuid=run_uuid,
        decision_timestamp=decision_timestamp,
        source_run_uuid=runtime_context.source_run_uuid,
    )
    logger.info("Wrote decisions.parquet (run_uuid=%s)", run_uuid)

    if forecast_evidence is not None:
        pq.write_table(forecast_evidence, output_dir / "forecast_evidence.parquet", compression="snappy")
        logger.info("Wrote forecast_evidence.parquet")

    duration = time.monotonic() - start
    stats: PipelineStats = {
        "products_analyzed": len(rules),
        "products_forecasted": forecast_evidence.num_rows if forecast_evidence is not None else 0,
        "rules_created": sum(1 for rule in rules if rule.action == "create"),
        "rules_updated": sum(1 for rule in rules if rule.action == "update"),
        "rules_skipped": sum(1 for rule in rules if rule.action == "skip"),
        "duration_seconds": round(duration, 2),
        "status": "complete",
        "run_uuid": run_uuid,
        "warehouse_id": warehouse_id,
    }
    if runtime_context.source_run_uuid:
        stats["source_run_uuid"] = runtime_context.source_run_uuid

    logger.info(
        "Pipeline complete in %.1fs: warehouse=%s, rules=%d, forecast_rows=%d",
        duration,
        warehouse_id,
        len(rules),
        stats["products_forecasted"],
    )
    return stats


def _build_imported_rules(
    *,
    minmax_table: pa.Table,
    product_id_lookup: dict[str, int],
    product_names: dict[int, str],
    warehouse_id: int,
    warehouse_code: str,
    location_id: int,
    existing_orderpoint_keys: set[tuple[int, int]],
    empirical_lead_times: pa.Table | None,
) -> list[ReplenishmentRule]:
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
                planned_lead_time_days=0,
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


def _selected_warehouse_id(config: MimirConfig) -> int:
    if config.selected_warehouse_id is None:
        raise ValueError("Select a warehouse before running Mimir.")
    return config.selected_warehouse_id
