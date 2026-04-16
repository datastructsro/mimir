"""Tests for quantile reorder point computation."""

from datetime import date

from foreqcast.aggregator import DemandPoint
from foreqcast.forecaster import (
    _interpolate_to_daily,
    _rolling_quantile,
    compute_quantile_reorder_points,
)


def _make_points(quantities, start_date=date(2024, 1, 1)):
    """Create daily DemandPoints from a list of quantities."""
    return [
        DemandPoint(bucket_date=date.fromordinal(start_date.toordinal() + i), total_qty=q, order_count=1)
        for i, q in enumerate(quantities)
    ]


# --- _interpolate_to_daily tests ---

def test_interpolate_daily_no_gaps():
    """Consecutive daily points map directly."""
    points = _make_points([10, 20, 30])
    daily = _interpolate_to_daily(points, "daily")
    assert list(daily) == [10.0, 20.0, 30.0]


def test_interpolate_daily_with_gaps():
    """Missing days between points get 0."""
    points = [
        DemandPoint(bucket_date=date(2024, 1, 1), total_qty=10, order_count=1),
        DemandPoint(bucket_date=date(2024, 1, 4), total_qty=40, order_count=1),
    ]
    daily = _interpolate_to_daily(points, "daily")
    assert len(daily) == 4  # Jan 1, 2, 3, 4
    assert daily[0] == 10.0
    assert daily[1] == 0.0   # gap
    assert daily[2] == 0.0   # gap
    assert daily[3] == 40.0


def test_interpolate_weekly():
    """Weekly points spread evenly across 7 days."""
    points = [
        DemandPoint(bucket_date=date(2024, 1, 1), total_qty=70, order_count=1),
    ]
    daily = _interpolate_to_daily(points, "weekly")
    assert daily[0] == 10.0  # 70 / 7


# --- _rolling_quantile tests ---

def test_rolling_quantile_basic():
    """Rolling 3-day windows of [10, 10, 10, 10] should all sum to 30."""
    import numpy as np
    daily = np.array([10.0, 10.0, 10.0, 10.0])
    q95 = _rolling_quantile(daily, 3, 95)
    assert q95 == 30.0  # all windows are identical


def test_rolling_quantile_variable():
    """Higher percentile should give a higher value for variable demand."""
    import numpy as np
    daily = np.array([5.0, 20.0, 5.0, 20.0, 5.0, 20.0, 5.0, 20.0, 5.0, 20.0])
    q50 = _rolling_quantile(daily, 3, 50)
    q95 = _rolling_quantile(daily, 3, 95)
    assert q95 >= q50


def test_rolling_quantile_short_series():
    """Series shorter than window: scale proportionally."""
    import numpy as np
    daily = np.array([10.0, 10.0])
    result = _rolling_quantile(daily, 7, 95)
    assert result == 20.0 * 7 / 2  # 70.0


# --- compute_quantile_reorder_points tests ---

def test_quantile_reorder_steady_demand():
    """Steady demand of 10/day, lead_time=7: min should be ~70."""
    points = _make_points([10.0] * 30)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=7, service_level=0.95)
    assert result is not None
    min_qty, max_qty = result
    assert min_qty == 70.0  # 10/day * 7 days, no variability
    assert max_qty == 140.0  # 10/day * 14 days
    assert max_qty >= min_qty


def test_quantile_reorder_variable_demand():
    """Variable demand should give higher quantile than mean * lead_time."""
    # Alternating 5 and 15 → mean 10, but high percentile of 7-day windows > 70
    points = _make_points([5.0, 15.0] * 15)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=7, service_level=0.95)
    assert result is not None
    min_qty, max_qty = result
    # Mean 7-day demand = 70, but 95th percentile of windows should be >= 70
    assert min_qty >= 70.0
    assert max_qty >= min_qty


def test_quantile_reorder_intermittent_demand():
    """Intermittent demand (many zeros) should still produce sensible result."""
    demand = [0, 0, 0, 10, 0, 0, 0, 0, 15, 0, 0, 0, 5, 0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 8, 0, 0, 0, 0, 0, 12]
    points = _make_points(demand)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=7, service_level=0.95)
    assert result is not None
    min_qty, max_qty = result
    assert min_qty >= 0
    assert max_qty >= min_qty


def test_quantile_reorder_insufficient_data():
    """Too few data points returns None."""
    points = _make_points([10.0] * 3)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=7, service_level=0.95)
    assert result is None


def test_quantile_reorder_all_zero():
    """All-zero demand returns None."""
    points = _make_points([0.0] * 30)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=7, service_level=0.95)
    assert result is None


def test_quantile_max_gte_min():
    """max_qty is always >= min_qty."""
    points = _make_points([10.0, 0, 5, 0, 20, 0, 8, 0, 12, 0, 15, 0, 3, 0, 7] * 2)
    result = compute_quantile_reorder_points(points, lead_time_days=7, review_period_days=3, service_level=0.90)
    assert result is not None
    min_qty, max_qty = result
    assert max_qty >= min_qty
