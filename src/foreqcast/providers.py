"""Forecast providers for Foreqcast.

Abstracts the generation of ForecastResult objects, allowing for internal
linear regression or external data sources (Parquet files, S3, or HTTP server).
"""

from __future__ import annotations

import io
import logging
from typing import Protocol
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .config import ForeqcastConfig
from .forecaster import ForecastResult

logger = logging.getLogger(__name__)


class ForecastProvider(Protocol):
    """Protocol for forecasting demand."""

    def get_forecasts(
        self,
        config: ForeqcastConfig,
    ) -> list[ForecastResult]:
        """Generate forecasts using the provided configuration."""
        ...


# ── Shared helpers ──────────────────────────────────────────────────────


def _table_to_forecast_results(table: pa.Table) -> list[ForecastResult]:
    """Convert a validated PyArrow table to a list of ForecastResult objects."""
    # Validate required columns
    required_cols = {
        "_odoo_product_id", "_odoo_warehouse_id", "forecasted_daily_demand",
        "first_observation", "last_observation", "avg_daily_demand", "data_points"
    }
    missing = required_cols - set(table.column_names)
    if missing:
        raise ValueError(f"External forecast missing required columns: {missing}")

    pids = table.column("_odoo_product_id").to_pylist()
    wids = table.column("_odoo_warehouse_id").to_pylist()
    demands = table.column("forecasted_daily_demand").to_pylist()

    first_obs_col = table.column("first_observation").to_pylist()
    last_obs_col = table.column("last_observation").to_pylist()
    avg_daily_col = table.column("avg_daily_demand").to_pylist()
    data_points_col = table.column("data_points").to_pylist()

    confidences = None
    if "confidence" in table.column_names:
        confidences = table.column("confidence").to_pylist()

    quantile_mins = None
    if "quantile_min_qty" in table.column_names:
        quantile_mins = table.column("quantile_min_qty").to_pylist()

    quantile_maxs = None
    if "quantile_max_qty" in table.column_names:
        quantile_maxs = table.column("quantile_max_qty").to_pylist()

    forecasts: list[ForecastResult] = []

    for i in range(len(pids)):
        pid = pids[i]
        wid = wids[i]
        daily = demands[i]

        if pid is None or wid is None or daily is None:
            continue

        conf = confidences[i] if confidences else "external"
        if not conf:
            conf = "external"

        first_obs = first_obs_col[i]
        if hasattr(first_obs, "date"):
            first_obs = first_obs.date()

        last_obs = last_obs_col[i]
        if hasattr(last_obs, "date"):
            last_obs = last_obs.date()

        avg_daily = float(avg_daily_col[i]) if avg_daily_col[i] is not None else 0.0
        data_points = int(data_points_col[i]) if data_points_col[i] is not None else 0

        q_min = float(quantile_mins[i]) if quantile_mins and quantile_mins[i] is not None else None
        q_max = float(quantile_maxs[i]) if quantile_maxs and quantile_maxs[i] is not None else None

        forecasts.append(
            ForecastResult(
                product_id=pid,
                warehouse_id=wid,
                data_points=data_points,
                first_observation=first_obs,
                last_observation=last_obs,
                avg_daily_demand=round(avg_daily, 4),
                slope=0.0,
                intercept=0.0,
                r_squared=0.0,
                forecasted_daily_demand=round(float(daily), 4),
                confidence=conf,
                quantile_min_qty=q_min,
                quantile_max_qty=q_max,
            )
        )

    return forecasts


# ── Providers ───────────────────────────────────────────────────────────





class ExternalParquetForecastProvider:
    """Loads pre-computed forecasts from an external Parquet file (local or S3)."""

    def get_forecasts(
        self,
        config: ForeqcastConfig,
    ) -> list[ForecastResult]:
        """Load external forecasts and map them to ForecastResult objects."""
        uri = config.external_forecast_uri
        if not uri:
            raise ValueError("external_forecast_uri must be configured when using external forecast source")

        logger.info("Loading external forecasts from %s", uri)

        dataset = ds.dataset(uri, format="parquet")
        table = dataset.to_table()
        return _table_to_forecast_results(table)


class ExternalHttpForecastProvider:
    """Fetches pre-computed forecasts from an HTTP server (e.g. foreqcast-server).

    Expects the server to return a Parquet file as an octet-stream response.
    Authenticates via X-API-Key header using config.external_forecast_api_key.
    """

    def get_forecasts(
        self,
        config: ForeqcastConfig,
    ) -> list[ForecastResult]:
        """Fetch forecast Parquet from an HTTP endpoint."""
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        base_uri = config.external_forecast_uri
        if not base_uri:
            raise ValueError("external_forecast_uri must be configured when using external forecast source")

        api_key = config.external_forecast_api_key
        if api_key:
            import uuid
            try:
                uuid.UUID(api_key)
            except ValueError:
                raise ValueError("external_forecast_api_key must be a valid UUID.")

        params = {
            "time_bucket": config.time_bucket,
            "forecast_horizon_days": config.forecast_horizon_days,
            "min_history_days": config.min_history_days,
            "service_level": config.service_level,
            "review_period_days": config.review_period_days,
            "default_lead_time_days": config.default_lead_time_days,
            "min_data_points": config.min_data_points,
            "min_demand_threshold": config.min_demand_threshold,
        }
        if config.odoo_db:
            params["tenant_id"] = config.odoo_db

        # Determine if base_uri already has query params
        separator = "&" if "?" in base_uri else "?"
        uri = f"{base_uri}{separator}{urlencode(params)}"

        logger.info("Fetching external forecasts from %s", uri)

        req = Request(uri)
        if api_key:
            req.add_header("X-API-Key", api_key)

        with urlopen(req, timeout=60) as resp:  # noqa: S310
            data = resp.read()

        # Read the downloaded bytes as a Parquet table
        table = pq.read_table(io.BytesIO(data))
        return _table_to_forecast_results(table)


# ── Factory ─────────────────────────────────────────────────────────────


def get_forecast_provider(config: ForeqcastConfig) -> ForecastProvider:
    """Factory to get the appropriate forecast provider based on config.

    Routing logic:
      - forecast_source != 'external' → ValueError
      - forecast_source == 'external' + http(s):// URI  → ExternalHttpForecastProvider
      - forecast_source == 'external' + anything else   → ExternalParquetForecastProvider
    """
    if config.forecast_source != "external":
        raise ValueError("forecast_source must be 'external'. Internal forecasting has been removed.")

    uri = config.external_forecast_uri
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        return ExternalHttpForecastProvider()

    return ExternalParquetForecastProvider()
