"""Integration tests for the end-to-end pipeline."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from foreqcast.config import ForeqcastConfig
from foreqcast.pipeline import run_pipeline


def _create_test_parquets(base_dir: Path, n_products=5, n_warehouses=2, n_days=30):
    """Create minimal parqcast export directory with known data.

    Products have linearly increasing demand: product i has base_qty = (i+1)*10.
    """
    base_date = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # Sale order lines: n_products x n_warehouses x n_days rows
    pids, wids, qtys, dates, states = [], [], [], [], []
    for p in range(1, n_products + 1):
        for w in range(1, n_warehouses + 1):
            wid = w * 10
            for d in range(n_days):
                pids.append(p)
                wids.append(wid)
                qtys.append(float(p * 10 + d))  # linearly increasing
                dates.append(base_date + timedelta(days=d))
                states.append("sale")

    sol_table = pa.table({
        "_odoo_product_id": pa.array(pids, type=pa.int64()),
        "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
        "product_uom_qty": pa.array(qtys, type=pa.float64()),
        "date_order": pa.array(dates, type=pa.timestamp("us", tz="UTC")),
        "so_state": pa.array(states, type=pa.string()),
        "product_name": pa.array([f"Product {p}" for p in pids], type=pa.string()),
        "warehouse_code": pa.array([f"WH{w // 10}" for w in wids], type=pa.string()),
        "qty_delivered": pa.array([0.0] * len(pids), type=pa.float64()),
    })
    pq.write_table(sol_table, base_dir / "sale_order_line.parquet")

    # Products
    product_table = pa.table({
        "_odoo_product_id": pa.array(list(range(1, n_products + 1)), type=pa.int64()),
        "name": pa.array([f"Product {i}" for i in range(1, n_products + 1)]),
        "default_code": pa.array([f"P{i:04d}" for i in range(1, n_products + 1)]),
        "_odoo_categ_id": pa.array([1] * n_products, type=pa.int64()),
    })
    pq.write_table(product_table, base_dir / "product.parquet")

    # Warehouses
    warehouse_table = pa.table({
        "_odoo_warehouse_id": pa.array([w * 10 for w in range(1, n_warehouses + 1)], type=pa.int64()),
        "code": pa.array([f"WH{w}" for w in range(1, n_warehouses + 1)]),
        "_odoo_lot_stock_id": pa.array([w * 100 for w in range(1, n_warehouses + 1)], type=pa.int64()),
    })
    pq.write_table(warehouse_table, base_dir / "stock_warehouse.parquet")

    # Empty supplier info and orderpoints (required by reader)
    empty_si = pa.table({
        "_odoo_product_id": pa.array([], type=pa.int64()),
        "_odoo_product_tmpl_id": pa.array([], type=pa.int64()),
        "delay": pa.array([], type=pa.int64()),
    })
    pq.write_table(empty_si, base_dir / "product_supplierinfo.parquet")

    empty_op = pa.table({
        "_odoo_orderpoint_id": pa.array([], type=pa.int64()),
        "_odoo_product_id": pa.array([], type=pa.int64()),
        "_odoo_warehouse_id": pa.array([], type=pa.int64()),
        "product_min_qty": pa.array([], type=pa.float64()),
        "product_max_qty": pa.array([], type=pa.float64()),
        "active": pa.array([], type=pa.bool_()),
    })
    pq.write_table(empty_op, base_dir / "orderpoint.parquet")


def _create_stock_quants(base_dir: Path, n_products=5, n_warehouses=2):
    """Create stock_quant.parquet with known on-hand quantities."""
    pids, wids, qtys, reserved = [], [], [], []
    for p in range(1, n_products + 1):
        for w in range(1, n_warehouses + 1):
            wid = w * 10
            pids.append(p)
            wids.append(wid)
            # Product 1 gets very high stock (overstock), others get moderate
            qtys.append(100000.0 if p == 1 else 50.0)
            reserved.append(0.0)

    quant_table = pa.table({
        "_odoo_product_id": pa.array(pids, type=pa.int64()),
        "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
        "quantity": pa.array(qtys, type=pa.float64()),
        "reserved_quantity": pa.array(reserved, type=pa.float64()),
    })
    pq.write_table(quant_table, base_dir / "stock_quant.parquet")


def test_pipeline_basic():
    """Basic pipeline run with inventory_mode=ignore produces output parquets."""
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path)

        config = ForeqcastConfig(inventory_mode="ignore")
        stats = run_pipeline(input_path, output_path, config)

        # Check stats
        assert stats["status"] == "complete"
        assert stats["products_analyzed"] == 10  # 5 products x 2 warehouses
        assert stats["products_forecasted"] == 10

        # Check output files exist
        assert (output_path / "forecast.parquet").exists()
        assert (output_path / "replenishment_rules.parquet").exists()

        # Read forecast and verify content
        forecast = pq.read_table(str(output_path / "forecast.parquet"))
        assert forecast.num_rows == 10

        # All products have increasing demand => positive slopes
        slopes = forecast.column("trend_slope").to_pylist()
        assert all(s > 0 for s in slopes)

        # Read rules and verify 22 columns (including inventory)
        rules = pq.read_table(str(output_path / "replenishment_rules.parquet"))
        assert rules.num_columns == 22
        assert rules.num_rows == 10

        # Inventory columns should be null when mode=ignore
        on_hand = rules.column("on_hand_qty").to_pylist()
        assert all(v is None for v in on_hand)

        # Product names should be populated
        names = rules.column("product_name").to_pylist()
        assert all(n != "" for n in names if n is not None)


def test_pipeline_inventory_analyze():
    """Pipeline with inventory_mode=analyze populates inventory columns."""
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path)
        _create_stock_quants(input_path)

        config = ForeqcastConfig(inventory_mode="analyze")
        stats = run_pipeline(input_path, output_path, config)

        assert stats["status"] == "complete"
        assert "inventory_mode" in stats
        assert stats["inventory_positions_loaded"] > 0

        # Rules should have inventory columns populated
        rules = pq.read_table(str(output_path / "replenishment_rules.parquet"))
        on_hand = rules.column("on_hand_qty").to_pylist()
        flags = rules.column("inventory_flag").to_pylist()
        # At least some should be non-null
        assert any(v is not None for v in on_hand)
        assert any(f is not None for f in flags)

        # inventory_analysis.parquet should exist
        assert (output_path / "inventory_analysis.parquet").exists()
        analysis = pq.read_table(str(output_path / "inventory_analysis.parquet"))
        assert analysis.num_rows > 0


def test_pipeline_inventory_adjust_overstock_skip():
    """In adjust mode, overstocked products are skipped."""
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path)
        _create_stock_quants(input_path)  # Product 1 has 100000 on-hand

        config = ForeqcastConfig(inventory_mode="adjust", overstock_skip=True)
        stats = run_pipeline(input_path, output_path, config)

        rules = pq.read_table(str(output_path / "replenishment_rules.parquet"))
        actions = rules.column("action").to_pylist()
        pids = rules.column("_odoo_product_id").to_pylist()
        skip_reasons = rules.column("skip_reason").to_pylist()

        # Product 1 (100K on-hand) should be skipped as overstock
        for i, pid in enumerate(pids):
            if pid == 1:
                assert actions[i] == "skip"
                assert skip_reasons[i] == "overstock_skip"

        assert stats["overstock_count"] > 0


def test_pipeline_empty_input():
    """Pipeline handles zero SOL rows gracefully."""
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path, n_products=0, n_warehouses=2, n_days=0)

        config = ForeqcastConfig()
        stats = run_pipeline(input_path, output_path, config)

        assert stats["products_analyzed"] == 0
        assert stats["products_forecasted"] == 0


def test_pipeline_decisions_output():
    """Pipeline produces decisions.parquet with temporal columns."""
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path)

        config = ForeqcastConfig(inventory_mode="ignore")
        stats = run_pipeline(input_path, output_path, config)

        # decisions.parquet must exist
        decisions_path = output_path / "decisions.parquet"
        assert decisions_path.exists()

        decisions = pq.read_table(str(decisions_path))

        # All rows should have the same run_uuid
        uuids = decisions.column("run_uuid").to_pylist()
        assert len(set(uuids)) == 1  # all the same
        assert uuids[0] == stats["run_uuid"]

        # decision_timestamp should be populated
        timestamps = decisions.column("decision_timestamp").to_pylist()
        assert all(t is not None for t in timestamps)

        # decision_type should be ORDERPOINT
        types = decisions.column("decision_type").to_pylist()
        assert all(t == "ORDERPOINT" for t in types)

        # Legacy outputs should still exist
        assert (output_path / "forecast.parquet").exists()
        assert (output_path / "replenishment_rules.parquet").exists()


def test_pipeline_manifest_parsing():
    """Pipeline reads manifest.json and populates parent_reference."""
    import json

    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        _create_test_parquets(input_path)

        # Write a mock manifest.json
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

        config = ForeqcastConfig(inventory_mode="ignore")
        stats = run_pipeline(input_path, output_path, config)

        assert stats.get("source_run_uuid") == source_uuid

        decisions = pq.read_table(str(output_path / "decisions.parquet"))
        parent_refs = decisions.column("parent_reference").to_pylist()
        assert all(r == source_uuid for r in parent_refs)

