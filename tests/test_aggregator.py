"""Tests for demand aggregation."""

from datetime import datetime, timezone

import pyarrow as pa

from foreqcast.aggregator import DemandPoint, aggregate_demand, compute_daily_average


def _make_sol_table(product_ids, warehouse_ids, quantities, dates, states=None):
    n = len(product_ids)
    if states is None:
        states = ["sale"] * n
    return pa.table({
        "_odoo_product_id": pa.array(product_ids, type=pa.int64()),
        "_odoo_warehouse_id": pa.array(warehouse_ids, type=pa.int64()),
        "product_uom_qty": pa.array(quantities, type=pa.float64()),
        "date_order": pa.array(dates, type=pa.timestamp("us", tz="UTC")),
        "so_state": pa.array(states, type=pa.string()),
    })


def _ts(year, month, day):
    """Create a UTC datetime for testing."""
    return datetime(year, month, day, tzinfo=timezone.utc)


# --- aggregate_demand tests ---

def test_daily_bucketing():
    """Three orders on the same day for one product should aggregate into one DemandPoint."""
    t = _make_sol_table(
        product_ids=[1, 1, 1],
        warehouse_ids=[10, 10, 10],
        quantities=[5.0, 3.0, 2.0],
        dates=[_ts(2024, 1, 15), _ts(2024, 1, 15), _ts(2024, 1, 15)],
    )
    result = aggregate_demand(t, bucket="daily")

    assert (1, 10) in result
    points = result[(1, 10)]
    assert len(points) == 1
    assert points[0].total_qty == 10.0
    assert points[0].order_count == 3


def test_weekly_bucketing():
    """Orders across Mon-Sun should snap to Monday. Two different weeks produce 2 points."""
    # 2024-01-15 = Monday, 2024-01-17 = Wednesday (same week)
    # 2024-01-22 = Monday (next week)
    t = _make_sol_table(
        product_ids=[1, 1, 1],
        warehouse_ids=[10, 10, 10],
        quantities=[5.0, 3.0, 7.0],
        dates=[_ts(2024, 1, 15), _ts(2024, 1, 17), _ts(2024, 1, 22)],
    )
    result = aggregate_demand(t, bucket="weekly")

    points = result[(1, 10)]
    assert len(points) == 2
    # First point: Mon Jan 15 week, qty = 5+3
    assert points[0].total_qty == 8.0
    # Second point: Mon Jan 22 week, qty = 7
    assert points[1].total_qty == 7.0


def test_cancelled_order_filtering():
    """Cancelled orders should be excluded from aggregation."""
    t = _make_sol_table(
        product_ids=[1, 1, 1],
        warehouse_ids=[10, 10, 10],
        quantities=[5.0, 3.0, 2.0],
        dates=[_ts(2024, 1, 15), _ts(2024, 1, 15), _ts(2024, 1, 15)],
        states=["sale", "cancel", "sale"],
    )
    result = aggregate_demand(t, bucket="daily")

    points = result[(1, 10)]
    assert len(points) == 1
    assert points[0].total_qty == 7.0  # 5 + 2 (cancelled 3 excluded)
    assert points[0].order_count == 2


def test_null_product_id_handled():
    """Rows with null product_id should be silently dropped."""
    t = _make_sol_table(
        product_ids=[1, None, 1],
        warehouse_ids=[10, 10, 10],
        quantities=[5.0, 3.0, 2.0],
        dates=[_ts(2024, 1, 15), _ts(2024, 1, 15), _ts(2024, 1, 15)],
    )
    result = aggregate_demand(t, bucket="daily")
    points = result[(1, 10)]
    assert points[0].total_qty == 7.0


def test_empty_table():
    """Empty table should return empty dict."""
    t = _make_sol_table([], [], [], [])
    result = aggregate_demand(t, bucket="daily")
    assert result == {}


def test_multiple_products_warehouses():
    """2 products x 2 warehouses should produce 4 keys."""
    t = _make_sol_table(
        product_ids=[1, 1, 2, 2],
        warehouse_ids=[10, 20, 10, 20],
        quantities=[5.0, 3.0, 7.0, 1.0],
        dates=[_ts(2024, 1, 15)] * 4,
    )
    result = aggregate_demand(t, bucket="daily")
    assert len(result) == 4
    assert result[(1, 10)][0].total_qty == 5.0
    assert result[(2, 20)][0].total_qty == 1.0


# --- compute_daily_average tests ---

def test_compute_daily_average_normal():
    """3 points spanning 30 days with total qty 300 = 10/day."""
    from datetime import date
    points = [
        DemandPoint(bucket_date=date(2024, 1, 1), total_qty=100, order_count=1),
        DemandPoint(bucket_date=date(2024, 1, 15), total_qty=100, order_count=1),
        DemandPoint(bucket_date=date(2024, 1, 31), total_qty=100, order_count=1),
    ]
    avg = compute_daily_average(points)
    assert avg == 300 / 30  # 10.0


def test_compute_daily_average_empty():
    assert compute_daily_average([]) == 0.0


def test_compute_daily_average_single_point():
    """Single point: span=0, returns total_qty."""
    from datetime import date
    points = [DemandPoint(bucket_date=date(2024, 1, 1), total_qty=42, order_count=1)]
    avg = compute_daily_average(points)
    assert avg == 42
