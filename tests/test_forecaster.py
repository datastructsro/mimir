"""Tests for the linear regression forecaster."""

from datetime import date

from foreqcast.aggregator import DemandPoint
from tests.mock_forecast_server import fit_demand


def _make_points(demands: list[float], start: date = date(2024, 1, 1), step_days: int = 7) -> list[DemandPoint]:
    """Helper to create evenly-spaced demand points."""
    from datetime import timedelta

    return [
        DemandPoint(bucket_date=start + timedelta(days=i * step_days), total_qty=d, order_count=1)
        for i, d in enumerate(demands)
    ]


def test_constant_demand():
    """Constant demand → slope ≈ 0, good forecast."""
    points = _make_points([100, 100, 100, 100, 100, 100, 100, 100])
    result = fit_demand(1, 1, points, forecast_horizon_days=30)

    assert result is not None
    assert result.data_points == 8
    assert abs(result.slope) < 0.01  # near-zero slope
    assert result.forecasted_daily_demand > 0


def test_growing_demand():
    """Linearly growing demand → positive slope."""
    points = _make_points([10, 20, 30, 40, 50, 60, 70, 80])
    result = fit_demand(1, 1, points, forecast_horizon_days=30)

    assert result is not None
    assert result.slope > 0  # positive trend
    assert result.r_squared > 0.95  # near-perfect linear fit
    assert result.forecasted_daily_demand > 80 / 7  # above last weekly rate


def test_declining_demand():
    """Declining demand → negative slope, forecasted demand floors at 0."""
    points = _make_points([80, 70, 60, 50, 40, 30, 20, 10])
    result = fit_demand(1, 1, points, forecast_horizon_days=30)

    assert result is not None
    assert result.slope < 0  # negative trend
    assert result.forecasted_daily_demand >= 0  # never negative


def test_insufficient_data():
    """Too few points → confidence = insufficient_data."""
    points = _make_points([100, 200])
    result = fit_demand(1, 1, points, min_data_points=4)

    assert result is not None
    assert result.confidence == "insufficient_data"
    assert result.forecasted_daily_demand == 0.0


def test_weekly_bucket_normalization():
    """Weekly buckets should be normalized to daily rate."""
    points = _make_points([700, 700, 700, 700, 700], step_days=7)
    result = fit_demand(1, 1, points, bucket="weekly")

    assert result is not None
    # 700/week = 100/day, so forecasted daily should be around 100
    assert 50 < result.forecasted_daily_demand < 150


def test_single_warehouse_product():
    """Basic end-to-end: product_id and warehouse_id are preserved."""
    points = _make_points([10, 20, 30, 40, 50])
    result = fit_demand(product_id=42, warehouse_id=7, points=points)

    assert result.product_id == 42
    assert result.warehouse_id == 7
