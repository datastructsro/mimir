"""Aggregate demand from sale_order_line parquet into time series.

Groups by (product_id, warehouse_id, time_bucket) and sums product_uom_qty.
Produces a dict keyed by (product_id, warehouse_id) with sorted time series.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import pyarrow as pa

from . import _pc


@dataclass
class DemandPoint:
    """Single demand observation for a time bucket."""

    bucket_date: date
    total_qty: float
    order_count: int


def aggregate_demand(
    sale_order_lines: pa.Table,
    bucket: str = "daily",
    exclude_states: set[str] | None = None,
) -> dict[tuple[int, int], list[DemandPoint]]:
    """Aggregate SOL data into demand time series per (product, warehouse).

    Uses PyArrow compute for filtering and group_by for aggregation,
    converting only the (much smaller) aggregated result to Python.

    Args:
        sale_order_lines: PyArrow table with columns from parqcast SOL schema
        bucket: 'daily' or 'weekly'
        exclude_states: SO states to skip (e.g. {'cancel', 'draft'})

    Returns:
        Dict mapping (product_id, warehouse_id) to sorted list of DemandPoints
    """
    if exclude_states is None:
        exclude_states = {"cancel"}

    t = sale_order_lines

    # Pre-filter: remove excluded states
    if exclude_states:
        state_col = t.column("so_state")
        mask = _pc.invert(_pc.is_in(state_col, value_set=pa.array(list(exclude_states))))
        t = t.filter(mask)

    # Drop rows with null keys
    for col_name in ("_odoo_product_id", "_odoo_warehouse_id", "product_uom_qty", "date_order"):
        t = t.filter(_pc.is_valid(t.column(col_name)))

    if t.num_rows == 0:
        return {}

    # Extract date from timestamp (handles both tz-aware and tz-naive)
    date_col = t.column("date_order")
    if hasattr(date_col.type, "tz") and date_col.type.tz is not None:
        date_col = _pc.cast(date_col, pa.timestamp("us"))
    date_col = _pc.cast(date_col, pa.date32())

    # Weekly bucketing: snap to Monday
    if bucket == "weekly":
        dow = _pc.day_of_week(date_col)  # 0=Monday
        # Subtract day_of_week days to snap to Monday
        date_as_ts = _pc.cast(date_col, pa.timestamp("s"))
        offset = _pc.multiply(_pc.cast(dow, pa.int64()), pa.scalar(-86400, pa.int64()))
        date_col = _pc.cast(_pc.add(date_as_ts, _pc.cast(offset, pa.duration("s"))), pa.date32())

    t = t.set_column(t.schema.get_field_index("date_order"), "date_order", date_col)

    # Aggregate with PyArrow group_by
    result = t.group_by(["_odoo_product_id", "_odoo_warehouse_id", "date_order"]).aggregate(
        [("product_uom_qty", "sum"), ("product_uom_qty", "count")]
    )

    # Convert only the aggregated result (much smaller than raw rows) to Python
    pids = result.column("_odoo_product_id").to_pylist()
    wids = result.column("_odoo_warehouse_id").to_pylist()
    dates = result.column("date_order").to_pylist()
    sums = result.column("product_uom_qty_sum").to_pylist()
    counts = result.column("product_uom_qty_count").to_pylist()

    # Reorganize into time series per (product, warehouse)
    series: dict[tuple[int, int], list[DemandPoint]] = defaultdict(list)
    for i in range(len(pids)):
        key = (pids[i], wids[i])
        series[key].append(DemandPoint(bucket_date=dates[i], total_qty=float(sums[i]), order_count=int(counts[i])))

    # Sort each series by date
    for key in series:
        series[key].sort(key=lambda dp: dp.bucket_date)

    return dict(series)


def compute_daily_average(points: list[DemandPoint]) -> float:
    """Compute simple average daily demand from a time series."""
    if not points:
        return 0.0
    total_qty = sum(p.total_qty for p in points)
    span_days = (points[-1].bucket_date - points[0].bucket_date).days
    if span_days <= 0:
        return total_qty
    return total_qty / span_days
