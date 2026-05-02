"""Tests for the decisions.parquet writer (temporal pipeline output)."""

import tempfile
from datetime import datetime, timezone

import pyarrow.parquet as pq

from foreqcast.calculator import ReplenishmentRule
from foreqcast.schemas import DECISIONS_SCHEMA
from foreqcast.writer import write_decisions


def _make_rule(
    product_id: int = 1,
    warehouse_id: int = 10,
    action: str = "create",
    min_qty: float = 5.0,
    max_qty: float = 15.0,
    lead_time_days: int = 7,
    skip_reason: str | None = None,
) -> ReplenishmentRule:
    return ReplenishmentRule(
        product_id=product_id,
        product_name=f"Product {product_id}",
        warehouse_id=warehouse_id,
        warehouse_code=f"WH{warehouse_id // 10}",
        location_id=warehouse_id * 10,
        min_qty=min_qty,
        max_qty=max_qty,
        lead_time_days=lead_time_days,
        service_level=0.85,
        review_period_days=7,
        forecasted_daily_demand=2.5,
        trigger="auto",
        action=action,
        skip_reason=skip_reason,
    )


def test_write_decisions_basic():
    """write_decisions produces a valid Parquet matching DECISIONS_SCHEMA."""
    rules = [
        _make_rule(product_id=1, action="create"),
        _make_rule(product_id=2, action="update"),
    ]
    run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ts = datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as d:
        path = write_decisions(rules, d, run_uuid, ts)

        assert path.exists()
        table = pq.read_table(str(path))

        # Schema matches exactly
        assert table.schema.equals(DECISIONS_SCHEMA)
        assert table.num_rows == 2

        # All rows have the same run_uuid
        uuids = table.column("run_uuid").to_pylist()
        assert all(u == run_uuid for u in uuids)

        # decision_type is ORDERPOINT
        types = table.column("decision_type").to_pylist()
        assert all(t == "ORDERPOINT" for t in types)

        # status is pending
        statuses = table.column("status").to_pylist()
        assert all(s == "pending" for s in statuses)


def test_write_decisions_skips_excluded():
    """Skip rules are excluded from decisions.parquet."""
    rules = [
        _make_rule(product_id=1, action="create"),
        _make_rule(product_id=2, action="skip", skip_reason="below_demand_threshold"),
        _make_rule(product_id=3, action="update"),
    ]
    run_uuid = "11111111-2222-3333-4444-555555555555"
    ts = datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as d:
        table = pq.read_table(str(write_decisions(rules, d, run_uuid, ts)))
        assert table.num_rows == 2  # skip rule excluded

        pids = table.column("_odoo_product_id").to_pylist()
        assert 2 not in pids  # product 2 was skipped


def test_write_decisions_field_mapping():
    """Verify correct mapping from ReplenishmentRule to DECISIONS_SCHEMA."""
    rule = _make_rule(
        product_id=42,
        warehouse_id=20,
        action="create",
        min_qty=10.5,
        max_qty=25.0,
        lead_time_days=14,
    )
    run_uuid = "abcdefab-1234-5678-9abc-def012345678"
    ts = datetime(2026, 5, 2, 12, 30, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as d:
        table = pq.read_table(str(write_decisions([rule], d, run_uuid, ts)))
        row = table.to_pydict()

        assert row["decision_id"][0] == f"foreqcast-{run_uuid[:8]}-42-20"
        assert row["item_name"][0] == "Product 42"
        assert row["_odoo_product_id"][0] == 42
        assert row["_odoo_location_id"][0] == 200  # 20 * 10
        assert row["location_name"][0] == "WH2"
        assert row["min_quantity"][0] == 10.5
        assert row["max_quantity"][0] == 25.0
        assert row["delay"][0] == 14
        assert row["remark"][0] == "action=create"


def test_write_decisions_parent_reference():
    """parent_reference contains source_run_uuid when provided."""
    rule = _make_rule()
    run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    source_uuid = "99999999-8888-7777-6666-555555555555"
    ts = datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as d:
        table = pq.read_table(str(write_decisions([rule], d, run_uuid, ts, source_run_uuid=source_uuid)))
        assert table.column("parent_reference").to_pylist()[0] == source_uuid


def test_write_decisions_empty_rules():
    """write_decisions handles empty actionable rules gracefully."""
    rules = [
        _make_rule(action="skip", skip_reason="excluded"),
    ]
    run_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ts = datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as d:
        path = write_decisions(rules, d, run_uuid, ts)
        table = pq.read_table(str(path))
        assert table.num_rows == 0
        assert table.schema.equals(DECISIONS_SCHEMA)
