"""Tests for the forecast provider abstraction layer."""

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from foreqcast.aggregator import DemandPoint
from foreqcast.config import ForeqcastConfig
from foreqcast.providers import (
    ExternalHttpForecastProvider,
    ExternalParquetForecastProvider,
    InternalForecastProvider,
    get_forecast_provider,
)
from foreqcast.schemas import EXTERNAL_FORECAST_SCHEMA

# ── Factory tests ───────────────────────────────────────────────────────


def test_factory_internal():
    config = ForeqcastConfig()
    assert isinstance(get_forecast_provider(config), InternalForecastProvider)


def test_factory_external_local_path():
    config = ForeqcastConfig(forecast_source="external", external_forecast_uri="/tmp/forecast.parquet")
    assert isinstance(get_forecast_provider(config), ExternalParquetForecastProvider)


def test_factory_external_s3():
    config = ForeqcastConfig(forecast_source="external", external_forecast_uri="s3://bucket/forecast.parquet")
    assert isinstance(get_forecast_provider(config), ExternalParquetForecastProvider)


def test_factory_external_http():
    config = ForeqcastConfig(forecast_source="external", external_forecast_uri="http://localhost:8400/forecasts/latest")
    assert isinstance(get_forecast_provider(config), ExternalHttpForecastProvider)


def test_factory_external_https():
    config = ForeqcastConfig(forecast_source="external", external_forecast_uri="https://api.example.com/forecasts")
    assert isinstance(get_forecast_provider(config), ExternalHttpForecastProvider)


# ── InternalForecastProvider ────────────────────────────────────────────


def test_internal_provider():
    config = ForeqcastConfig()
    provider = InternalForecastProvider()

    demand_series = {
        (1, 10): [
            DemandPoint(bucket_date=date(2023, 1, 1), total_qty=10, order_count=1),
            DemandPoint(bucket_date=date(2023, 1, 2), total_qty=15, order_count=2),
            DemandPoint(bucket_date=date(2023, 1, 3), total_qty=20, order_count=1),
            DemandPoint(bucket_date=date(2023, 1, 4), total_qty=25, order_count=2),
        ]
    }

    forecasts = provider.get_forecasts(demand_series, config)
    assert len(forecasts) == 1
    assert forecasts[0].product_id == 1
    assert forecasts[0].warehouse_id == 10
    assert forecasts[0].forecasted_daily_demand > 0


# ── ExternalParquetForecastProvider ─────────────────────────────────────


def test_external_parquet_missing_uri():
    config = ForeqcastConfig(forecast_source="external")
    provider = ExternalParquetForecastProvider()
    with pytest.raises(ValueError, match="external_forecast_uri"):
        provider.get_forecasts({}, config)


def test_external_parquet_missing_columns(tmp_path: Path):
    config = ForeqcastConfig(
        forecast_source="external",
        external_forecast_uri=str(tmp_path / "bad.parquet"),
    )
    provider = ExternalParquetForecastProvider()

    bad_table = pa.table({"_odoo_product_id": [1]})
    pq.write_table(bad_table, tmp_path / "bad.parquet")

    with pytest.raises(ValueError, match="missing required columns"):
        provider.get_forecasts({}, config)


def test_external_parquet_valid(tmp_path: Path):
    uri = tmp_path / "external.parquet"
    config = ForeqcastConfig(
        forecast_source="external",
        external_forecast_uri=str(uri),
    )

    good_table = pa.table(
        {
            "_odoo_product_id": [1, 2],
            "_odoo_warehouse_id": [10, 10],
            "forecasted_daily_demand": [42.5, 100.0],
            "confidence": ["high", "low"],
        },
        schema=EXTERNAL_FORECAST_SCHEMA,
    )
    pq.write_table(good_table, uri)

    demand_series = {
        (1, 10): [
            DemandPoint(bucket_date=date(2023, 1, 1), total_qty=10, order_count=1),
            DemandPoint(bucket_date=date(2023, 1, 2), total_qty=20, order_count=1),
        ]
    }

    provider = ExternalParquetForecastProvider()
    forecasts = provider.get_forecasts(demand_series, config)

    assert len(forecasts) == 2

    f1 = next(f for f in forecasts if f.product_id == 1)
    assert f1.warehouse_id == 10
    assert f1.forecasted_daily_demand == 42.5
    assert f1.confidence == "high"
    assert f1.data_points == 2
    assert f1.slope == 0.0

    f2 = next(f for f in forecasts if f.product_id == 2)
    assert f2.forecasted_daily_demand == 100.0
    assert f2.confidence == "low"
    assert f2.data_points == 0  # no historical data for product 2


def test_external_parquet_without_confidence(tmp_path: Path):
    """When confidence column is missing, it should default to 'external'."""
    uri = tmp_path / "no_conf.parquet"
    config = ForeqcastConfig(
        forecast_source="external",
        external_forecast_uri=str(uri),
    )

    table = pa.table(
        {
            "_odoo_product_id": pa.array([1], type=pa.int64()),
            "_odoo_warehouse_id": pa.array([10], type=pa.int64()),
            "forecasted_daily_demand": pa.array([55.0], type=pa.float64()),
        }
    )
    pq.write_table(table, uri)

    provider = ExternalParquetForecastProvider()
    forecasts = provider.get_forecasts({}, config)

    assert len(forecasts) == 1
    assert forecasts[0].confidence == "external"


# ── ExternalHttpForecastProvider ────────────────────────────────────────


def test_external_http_missing_uri():
    config = ForeqcastConfig(forecast_source="external")
    provider = ExternalHttpForecastProvider()
    with pytest.raises(ValueError, match="external_forecast_uri"):
        provider.get_forecasts({}, config)
