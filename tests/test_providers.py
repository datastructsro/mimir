"""Tests for the remote forecast provider compatibility layer."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mimir_core.config import MimirConfig
from mimir_core.providers import ExternalHttpForecastProvider, get_forecast_provider
from mimir_core.schemas import EXTERNAL_FORECAST_SCHEMA

from tests.mock_forecast_server import DemandPoint, MockForecastProvider


def test_factory_internal_removed() -> None:
    config = MimirConfig(forecast_source="internal")
    with pytest.raises(ValueError, match="forecast_source must be 'external'"):
        get_forecast_provider(config)


def test_factory_rejects_non_http_sources() -> None:
    config = MimirConfig(forecast_source="external", external_forecast_uri="/tmp/forecast.parquet")
    with pytest.raises(ValueError, match="remote HTTP\\(S\\) forecast service"):
        get_forecast_provider(config)


def test_factory_external_http() -> None:
    config = MimirConfig(forecast_source="external", external_forecast_uri="http://localhost:8400/forecasts/latest")
    assert isinstance(get_forecast_provider(config), ExternalHttpForecastProvider)


def test_factory_external_https() -> None:
    config = MimirConfig(forecast_source="external", external_forecast_uri="https://api.example.com/forecasts")
    assert isinstance(get_forecast_provider(config), ExternalHttpForecastProvider)


def test_mock_provider() -> None:
    config = MimirConfig(forecast_source="external")
    demand_series = {
        (1, 10): [
            DemandPoint(bucket_date=date(2023, 1, 1), total_qty=10, order_count=1),
            DemandPoint(bucket_date=date(2023, 1, 2), total_qty=15, order_count=2),
            DemandPoint(bucket_date=date(2023, 1, 3), total_qty=20, order_count=1),
            DemandPoint(bucket_date=date(2023, 1, 4), total_qty=25, order_count=2),
        ]
    }
    provider = MockForecastProvider(demand_series=demand_series)

    forecasts = provider.get_forecasts(config)
    assert len(forecasts) == 1
    assert forecasts[0].product_id == 1
    assert forecasts[0].warehouse_id == 10
    assert forecasts[0].forecasted_daily_demand > 0


def test_external_http_missing_uri() -> None:
    config = MimirConfig(forecast_source="external")
    provider = ExternalHttpForecastProvider()
    with pytest.raises(ValueError, match="external_forecast_uri"):
        provider.get_forecasts(config)


@patch("urllib.request.urlopen")
def test_external_http_query_params(mock_urlopen: MagicMock) -> None:
    import io
    from datetime import datetime, timezone

    config = MimirConfig(
        forecast_source="external",
        external_forecast_uri="http://example.com/api/forecasts",
        external_forecast_api_key="123e4567-e89b-12d3-a456-426614174000",
        time_bucket="weekly",
        forecast_horizon_days=60,
        odoo_db="test_tenant",
        min_history_days=30,
    )
    provider = ExternalHttpForecastProvider()

    now = datetime.now(timezone.utc)
    table = pa.table(
        {
            "_odoo_product_id": [1],
            "_odoo_warehouse_id": [10],
            "first_observation": [now],
            "last_observation": [now],
            "avg_daily_demand": [15.0],
            "data_points": [2],
            "forecasted_daily_demand": [42.5],
            "confidence": ["high"],
            "quantile_min_qty": [10.0],
            "quantile_max_qty": [20.0],
        },
        schema=EXTERNAL_FORECAST_SCHEMA,
    )

    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    buffer.seek(0)

    mock_response = MagicMock()
    mock_response.read.return_value = buffer.read()
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    forecasts = provider.get_forecasts(config)

    assert len(forecasts) == 1
    mock_urlopen.assert_called_once()
    request = mock_urlopen.call_args[0][0]
    assert "time_bucket=weekly" in request.full_url
    assert "forecast_horizon_days=60" in request.full_url
    assert "tenant_id=test_tenant" in request.full_url
    assert "min_history_days=30" in request.full_url
    assert request.headers.get("X-api-key") == "123e4567-e89b-12d3-a456-426614174000"
