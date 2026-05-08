"""Tests for the min/max replenishment calculator."""

from datetime import date

from mimir_core.calculator import calculate_rule
from mimir_core.config import MimirConfig
from mimir_core.forecaster import ForecastResult


def _make_forecast(
    daily: float = 10.0,
    confidence: str = "medium",
    product_id: int = 1,
    warehouse_id: int = 1,
) -> ForecastResult:
    return ForecastResult(
        product_id=product_id,
        warehouse_id=warehouse_id,
        data_points=20,
        first_observation=date(2024, 1, 1),
        last_observation=date(2024, 6, 1),
        avg_daily_demand=daily,
        slope=0.01,
        intercept=daily,
        r_squared=0.8,
        forecasted_daily_demand=daily,
        confidence=confidence,
    )


def test_basic_calculation_with_quantile():
    """min/max come directly from quantile_result when provided."""
    config = MimirConfig(
        default_lead_time_days=7,
        service_level=0.95,
        review_period_days=7,
        min_demand_threshold=0.1,
    )
    forecast = _make_forecast(daily=10.0)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Test Product",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
        quantile_result=(85.0, 170.0),
    )

    assert rule.action == "create"
    assert rule.min_qty == 85.0
    assert rule.max_qty == 170.0
    assert rule.service_level == 0.95
    assert rule.trigger == "auto"


def test_fallback_without_quantile():
    """When quantile_result is None, falls back to a point-estimate formula."""
    config = MimirConfig(
        default_lead_time_days=7,
        service_level=0.95,
        review_period_days=7,
        min_demand_threshold=0.1,
    )
    forecast = _make_forecast(daily=10.0)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Test Product",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
        quantile_result=None,
    )

    assert rule.action == "skip"
    assert rule.skip_reason == "missing_quantile_data"


def test_below_threshold_skipped():
    """Products below demand threshold are skipped."""
    config = MimirConfig(min_demand_threshold=1.0)
    forecast = _make_forecast(daily=0.05)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Low Demand",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
    )

    assert rule.action == "skip"
    assert rule.skip_reason == "below_demand_threshold"


def test_insufficient_data_skipped():
    """Products with insufficient data are skipped if no default min/max."""
    config = MimirConfig()
    forecast = _make_forecast(confidence="insufficient_data")

    rule = calculate_rule(
        forecast=forecast,
        product_name="No Data",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
    )

    assert rule.action == "skip"
    assert rule.skip_reason == "insufficient_data"


def test_insufficient_data_default_fallback():
    """Products with insufficient data use default fallback if configured."""
    config = MimirConfig(
        default_min_qty=5.0,
        default_max_qty=10.0,
    )
    forecast = _make_forecast(confidence="insufficient_data")

    rule = calculate_rule(
        forecast=forecast,
        product_name="Fallback Data",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
    )

    assert rule.action == "create"
    assert rule.skip_reason is None
    assert rule.min_qty == 5.0
    assert rule.max_qty == 10.0


def test_short_history_default_fallback():
    """Products with < min_history_days use default fallback."""
    config = MimirConfig(
        min_history_days=200,
        default_min_qty=2.0,
        default_max_qty=4.0,
    )
    forecast = _make_forecast()

    rule = calculate_rule(
        forecast=forecast,
        product_name="Short History Data",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
    )

    assert rule.action == "create"
    assert rule.min_qty == 2.0
    assert rule.max_qty == 4.0


def test_product_override_floor_ceiling():
    """Product overrides enforce min floor and max ceiling."""
    config = MimirConfig(
        default_lead_time_days=7,
        service_level=0.95,
        review_period_days=7,
        product_overrides={
            1: {
                "service_level": None,
                "min_qty_floor": 200.0,
                "max_qty_ceiling": 300.0,
                "lead_time_override_days": None,
                "excluded": False,
            },
        },
    )
    forecast = _make_forecast(daily=10.0)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Floored Product",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
        quantile_result=(85.0, 170.0),  # both below floor
    )

    assert rule.min_qty == 200.0  # floored up from 85
    assert rule.max_qty == 200.0  # ceiling 300 doesn't cap, but max must >= min (200)


def test_excluded_product_skipped():
    """Excluded products are skipped."""
    config = MimirConfig(
        product_overrides={
            1: {
                "excluded": True,
                "service_level": None,
                "min_qty_floor": None,
                "max_qty_ceiling": None,
                "lead_time_override_days": None,
            },
        },
    )
    forecast = _make_forecast(daily=100.0)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Excluded",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
    )

    assert rule.action == "skip"
    assert rule.skip_reason == "product_excluded"


def test_max_always_gte_min():
    """max_qty is always >= min_qty."""
    config = MimirConfig(
        default_lead_time_days=14,
        service_level=0.99,
        review_period_days=1,
    )
    forecast = _make_forecast(daily=10.0)

    rule = calculate_rule(
        forecast=forecast,
        product_name="Test",
        warehouse_code="WH",
        location_id=5,
        category_id=None,
        supplier_info=None,
        existing_orderpoints=None,
        config=config,
        quantile_result=(150.0, 160.0),
    )

    assert rule.max_qty >= rule.min_qty
