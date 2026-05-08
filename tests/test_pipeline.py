"""Tests for the remote-only replenishment import pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimir.config import MimirConfig
from mimir.pipeline import run_pipeline
from mimir.runtime_context import PipelineRuntimeContext


def _runtime_context() -> PipelineRuntimeContext:
    return PipelineRuntimeContext(
        warehouse_code="WH10",
        location_id=1000,
        product_names={1: "Product 1", 2: "Product 2", 3: "Product 3"},
        existing_orderpoint_keys={(1, 10)},
    )


def _rules_table() -> pa.Table:
    return pa.table(
        {
            "product_id": pa.array(["1", "2", "3"], type=pa.string()),
            "facility_id": pa.array(["WH10", "WH10", "WH10"], type=pa.string()),
            "min_quantity": pa.array([5.0, 3.0, 7.0], type=pa.float64()),
            "max_quantity": pa.array([10.0, 8.0, 12.0], type=pa.float64()),
        }
    )


def _forecast_table() -> pa.Table:
    return pa.table(
        {
            "product_id": pa.array(["1", "1", "2"], type=pa.string()),
            "facility_id": pa.array(["WH10", "WH10", "WH10"], type=pa.string()),
            "date": pa.array(["2026-01-01", "2026-01-02", "2026-01-01"], type=pa.string()),
            "qty_fc": pa.array([1.0, 2.0, 3.0], type=pa.float64()),
            "lower_80_qty_fc": pa.array([0.0, 1.0, 2.0], type=pa.float64()),
            "upper_80_qty_fc": pa.array([2.0, 3.0, 4.0], type=pa.float64()),
        }
    )


def _empirical_lead_times() -> pa.Table:
    return pa.table(
        {
            "_odoo_product_id": pa.array([1, 2], type=pa.int64()),
            "_odoo_warehouse_id": pa.array([10, 10], type=pa.int64()),
            "median_delay_days": pa.array([4, 5], type=pa.int64()),
            "observation_count": pa.array([3, 1], type=pa.int64()),
        }
    )


def test_pipeline_imports_remote_rules_for_selected_warehouse() -> None:
    with tempfile.TemporaryDirectory() as output_dir:
        output_path = Path(output_dir)
        stats = run_pipeline(
            parquet_output_dir=output_path,
            config=MimirConfig(selected_warehouse_id=10),
            runtime_context=_runtime_context(),
            minmax_table=_rules_table(),
            forecast_evidence=_forecast_table(),
            empirical_lead_times=_empirical_lead_times(),
        )

        assert stats["status"] == "complete"
        assert stats["warehouse_id"] == 10
        assert stats["products_analyzed"] == 3
        assert stats["products_forecasted"] == 3
        assert stats["rules_created"] == 2
        assert stats["rules_updated"] == 1
        assert stats["rules_skipped"] == 0

        rules = pq.read_table(output_path / "replenishment_rules.parquet").to_pylist()
        assert len(rules) == 3
        assert all(rule["_odoo_warehouse_id"] == 10 for rule in rules)
        assert all(rule["_odoo_location_id"] == 1000 for rule in rules)
        assert all(rule["warehouse_code"] == "WH10" for rule in rules)
        assert all(rule["planned_lead_time_days"] == 0 for rule in rules)

        product_1 = next(rule for rule in rules if rule["_odoo_product_id"] == 1)
        assert product_1["action"] == "update"
        assert product_1["empirical_lead_time_days"] == 4

        product_2 = next(rule for rule in rules if rule["_odoo_product_id"] == 2)
        assert product_2["action"] == "create"
        assert product_2["empirical_lead_time_days"] == 0

        decisions = pq.read_table(output_path / "decisions.parquet")
        assert decisions.num_rows == 3

        forecast_evidence = pq.read_table(output_path / "forecast_evidence.parquet").to_pylist()
        assert len(forecast_evidence) == 3
        assert all(row["facility_id"] == "WH10" for row in forecast_evidence)


def test_pipeline_requires_selected_warehouse() -> None:
    with tempfile.TemporaryDirectory() as output_dir:
        with pytest.raises(ValueError, match="Select a warehouse before running Mimir"):
            run_pipeline(
                parquet_output_dir=output_dir,
                config=MimirConfig(),
                runtime_context=_runtime_context(),
                minmax_table=_rules_table(),
            )


def test_pipeline_raises_for_unknown_product_ids() -> None:
    bad_rules = pa.table(
        {
            "product_id": pa.array(["999"], type=pa.string()),
            "facility_id": pa.array(["WH10"], type=pa.string()),
            "min_quantity": pa.array([1.0], type=pa.float64()),
            "max_quantity": pa.array([2.0], type=pa.float64()),
        }
    )

    with tempfile.TemporaryDirectory() as output_dir:
        with pytest.raises(ValueError, match="unknown product_id '999'"):
            run_pipeline(
                parquet_output_dir=output_dir,
                config=MimirConfig(selected_warehouse_id=10),
                runtime_context=_runtime_context(),
                minmax_table=bad_rules,
            )
