"""Integration tests for the warehouse-scoped import pipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimir.config import MimirConfig
from mimir.pipeline import run_pipeline


def _create_core_tables(base_dir: Path) -> None:
    product_table = pa.table(
        {
            "_odoo_product_id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["Product 1", "Product 2", "Product 3"], type=pa.string()),
            "default_code": pa.array(["P001", "P002", "P003"], type=pa.string()),
            "_odoo_categ_id": pa.array([1, 1, 2], type=pa.int64()),
        }
    )
    pq.write_table(product_table, base_dir / "product.parquet")

    warehouse_table = pa.table(
        {
            "_odoo_warehouse_id": pa.array([10, 20], type=pa.int64()),
            "code": pa.array(["WH10", "WH20"], type=pa.string()),
            "_odoo_lot_stock_id": pa.array([1000, 2000], type=pa.int64()),
        }
    )
    pq.write_table(warehouse_table, base_dir / "stock_warehouse.parquet")


def _write_orderpoints(base_dir: Path) -> None:
    orderpoint_table = pa.table(
        {
            "_odoo_orderpoint_id": pa.array([99], type=pa.int64()),
            "_odoo_product_id": pa.array([1], type=pa.int64()),
            "_odoo_warehouse_id": pa.array([10], type=pa.int64()),
            "product_min_qty": pa.array([2.0], type=pa.float64()),
            "product_max_qty": pa.array([5.0], type=pa.float64()),
            "active": pa.array([True], type=pa.bool_()),
        }
    )
    pq.write_table(orderpoint_table, base_dir / "orderpoint.parquet")


def _write_minmax(base_dir: Path) -> None:
    minmax_table = pa.table(
        {
            "product_id": pa.array(["1", "2", "3"], type=pa.string()),
            "facility_id": pa.array(["10", "10", "20"], type=pa.string()),
            "min_quantity": pa.array([5.0, 3.0, 7.0], type=pa.float64()),
            "max_quantity": pa.array([10.0, 8.0, 12.0], type=pa.float64()),
        }
    )
    pq.write_table(minmax_table, base_dir / "minmax.parquet")


def _write_forecast(base_dir: Path) -> None:
    forecast_table = pa.table(
        {
            "product_id": pa.array(["1", "1", "2", "3"], type=pa.string()),
            "facility_id": pa.array(["10", "10", "10", "20"], type=pa.string()),
            "date": pa.array(["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-01"], type=pa.string()),
            "qty_fc": pa.array([1.0, 2.0, 3.0, 4.0], type=pa.float64()),
            "lower_80_qty_fc": pa.array([0.0, 1.0, 2.0, 3.0], type=pa.float64()),
            "upper_80_qty_fc": pa.array([2.0, 3.0, 4.0, 5.0], type=pa.float64()),
        }
    )
    pq.write_table(forecast_table, base_dir / "demand_forecast.parquet")


def _write_lead_times(base_dir: Path) -> None:
    lead_time_table = pa.table(
        {
            "facility_id": pa.array(["10", "20"], type=pa.string()),
            "lead_time_days": pa.array([6, 12], type=pa.int64()),
            "type": pa.array(["warehouse_lead_time", "warehouse_lead_time"], type=pa.string()),
        }
    )
    pq.write_table(lead_time_table, base_dir / "input_lead_times.parquet")


def _write_empirical_lead_times(base_dir: Path) -> None:
    empirical_table = pa.table(
        {
            "_odoo_product_id": pa.array([1, 2], type=pa.int64()),
            "_odoo_warehouse_id": pa.array([10, 10], type=pa.int64()),
            "median_delay_days": pa.array([4, 5], type=pa.int64()),
            "observation_count": pa.array([3, 1], type=pa.int64()),
        }
    )
    pq.write_table(empirical_table, base_dir / "empirical_lead_times.parquet")


def _create_bundle(base_dir: Path) -> None:
    _create_core_tables(base_dir)
    _write_orderpoints(base_dir)
    _write_minmax(base_dir)
    _write_forecast(base_dir)
    _write_lead_times(base_dir)
    _write_empirical_lead_times(base_dir)


def test_pipeline_imports_selected_warehouse_only() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_bundle(input_path)

        config = MimirConfig(selected_warehouse_id=10)
        stats = run_pipeline(input_path, output_path, config)

        assert stats["status"] == "complete"
        assert stats["warehouse_id"] == 10
        assert stats["products_analyzed"] == 2
        assert stats["products_forecasted"] == 3
        assert stats["rules_created"] == 1
        assert stats["rules_updated"] == 1
        assert stats["rules_skipped"] == 0

        rules = pq.read_table(str(output_path / "replenishment_rules.parquet")).to_pylist()
        assert len(rules) == 2
        assert all(rule["_odoo_warehouse_id"] == 10 for rule in rules)
        assert {rule["_odoo_product_id"] for rule in rules} == {1, 2}

        product_1 = next(rule for rule in rules if rule["_odoo_product_id"] == 1)
        assert product_1["action"] == "update"
        assert product_1["planned_lead_time_days"] == 6
        assert product_1["empirical_lead_time_days"] == 4

        product_2 = next(rule for rule in rules if rule["_odoo_product_id"] == 2)
        assert product_2["action"] == "create"
        assert product_2["empirical_lead_time_days"] == 0

        decisions = pq.read_table(str(output_path / "decisions.parquet"))
        assert decisions.num_rows == 2
        assert (output_path / "forecast_evidence.parquet").exists()

        forecast_evidence = pq.read_table(str(output_path / "forecast_evidence.parquet")).to_pylist()
        assert len(forecast_evidence) == 3
        assert all(row["facility_id"] == "10" for row in forecast_evidence)


def test_pipeline_requires_explicit_warehouse_for_multi_facility_rules() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        _create_bundle(input_path)

        with pytest.raises(ValueError, match="Select a warehouse before running Mimir"):
            run_pipeline(input_path, output_dir, MimirConfig())


def test_pipeline_infers_single_warehouse_when_rules_are_single_facility() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_core_tables(input_path)
        _write_orderpoints(input_path)
        _write_lead_times(input_path)

        minmax_table = pa.table(
            {
                "product_id": pa.array(["1", "2"], type=pa.string()),
                "facility_id": pa.array(["10", "10"], type=pa.string()),
                "min_quantity": pa.array([5.0, 3.0], type=pa.float64()),
                "max_quantity": pa.array([10.0, 8.0], type=pa.float64()),
            }
        )
        pq.write_table(minmax_table, input_path / "minmax.parquet")

        stats = run_pipeline(input_path, output_path, MimirConfig())
        assert stats["warehouse_id"] == 10
        assert stats["products_analyzed"] == 2


def test_pipeline_matches_warehouse_codes_in_local_artifacts() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_core_tables(input_path)
        _write_orderpoints(input_path)
        _write_empirical_lead_times(input_path)

        minmax_table = pa.table(
            {
                "product_id": pa.array(["1", "2", "3"], type=pa.string()),
                "facility_id": pa.array(["WH10", "WH10", "WH20"], type=pa.string()),
                "min_quantity": pa.array([5.0, 3.0, 7.0], type=pa.float64()),
                "max_quantity": pa.array([10.0, 8.0, 12.0], type=pa.float64()),
            }
        )
        pq.write_table(minmax_table, input_path / "minmax.parquet")

        forecast_table = pa.table(
            {
                "product_id": pa.array(["1", "1", "2", "3"], type=pa.string()),
                "facility_id": pa.array(["WH10", "WH10", "WH10", "WH20"], type=pa.string()),
                "date": pa.array(["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-01"], type=pa.string()),
                "qty_fc": pa.array([1.0, 2.0, 3.0, 4.0], type=pa.float64()),
                "lower_80_qty_fc": pa.array([0.0, 1.0, 2.0, 3.0], type=pa.float64()),
                "upper_80_qty_fc": pa.array([2.0, 3.0, 4.0, 5.0], type=pa.float64()),
            }
        )
        pq.write_table(forecast_table, input_path / "demand_forecast.parquet")

        lead_time_table = pa.table(
            {
                "facility_id": pa.array(["WH10", "WH20"], type=pa.string()),
                "lead_time_days": pa.array([6, 12], type=pa.int64()),
                "type": pa.array(["warehouse_lead_time", "warehouse_lead_time"], type=pa.string()),
            }
        )
        pq.write_table(lead_time_table, input_path / "input_lead_times.parquet")

        stats = run_pipeline(input_path, output_path, MimirConfig(selected_warehouse_id=10))

        assert stats["warehouse_id"] == 10
        assert stats["products_analyzed"] == 2
        assert stats["products_forecasted"] == 3

        rules = pq.read_table(str(output_path / "replenishment_rules.parquet")).to_pylist()
        assert len(rules) == 2
        assert all(rule["_odoo_warehouse_id"] == 10 for rule in rules)
        assert all(rule["planned_lead_time_days"] == 6 for rule in rules)

        forecast_evidence = pq.read_table(str(output_path / "forecast_evidence.parquet")).to_pylist()
        assert len(forecast_evidence) == 3
        assert all(row["facility_id"] == "WH10" for row in forecast_evidence)


def test_pipeline_manifest_parsing() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_bundle(input_path)

        source_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        manifest = {
            "version": "0.1.0",
            "run_uuid": source_uuid,
            "started_at": "2026-05-02T08:00:00+00:00",
            "finished_at": "2026-05-02T08:00:15+00:00",
            "company": "Test Co",
            "company_id": 1,
            "odoo_version": "18.0",
            "files": [],
        }
        (input_path / "manifest.json").write_text(json.dumps(manifest))

        stats = run_pipeline(input_path, output_path, MimirConfig(selected_warehouse_id=10))

        assert stats.get("source_run_uuid") == source_uuid

        decisions = pq.read_table(str(output_path / "decisions.parquet"))
        parent_refs = decisions.column("parent_reference").to_pylist()
        assert all(reference == source_uuid for reference in parent_refs)


def test_pipeline_raises_for_unknown_product_ids() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        _create_core_tables(input_path)

        bad_minmax = pa.table(
            {
                "product_id": pa.array(["999"], type=pa.string()),
                "facility_id": pa.array(["10"], type=pa.string()),
                "min_quantity": pa.array([1.0], type=pa.float64()),
                "max_quantity": pa.array([2.0], type=pa.float64()),
            }
        )
        pq.write_table(bad_minmax, input_path / "minmax.parquet")

        with pytest.raises(ValueError, match="unknown product_id '999'"):
            run_pipeline(input_path, output_dir, MimirConfig(selected_warehouse_id=10))


def test_pipeline_does_not_fallback_to_local_rules_when_remote_server_is_configured() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_bundle(input_path)

        config = MimirConfig(
            selected_warehouse_id=10,
            external_forecast_uri="https://mimir.example",
            external_forecast_api_key="123e4567-e89b-12d3-a456-426614174000",
        )

        with patch("mimir.importer.MimirServerClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.resolve_warehouse_ref.side_effect = ValueError(
                "Remote server is healthy but has no published warehouse-scoped artifacts"
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="no published warehouse-scoped artifacts"):
                run_pipeline(input_path, output_path, config)


def test_pipeline_falls_back_to_local_forecast_when_remote_forecast_fetch_fails() -> None:
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_bundle(input_path)

        config = MimirConfig(
            selected_warehouse_id=10,
            external_forecast_uri="https://mimir.example",
            external_forecast_api_key="123e4567-e89b-12d3-a456-426614174000",
        )
        remote_rules = pq.read_table(input_path / "minmax.parquet")

        with patch("mimir.importer.MimirServerClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.resolve_warehouse_ref.return_value = "WH10"
            mock_client.download_rules_table.return_value = remote_rules
            mock_client.download_forecast_table.side_effect = ValueError("Remote forecast missing")
            mock_client_cls.return_value = mock_client

            stats = run_pipeline(input_path, output_path, config)

        assert stats["status"] == "complete"
        assert stats["products_forecasted"] == 3
        forecast_evidence = pq.read_table(str(output_path / "forecast_evidence.parquet")).to_pylist()
        assert len(forecast_evidence) == 3
        assert all(row["facility_id"] == "10" for row in forecast_evidence)
