"""Legacy forecast provider compatibility wrappers for server-backed forecasts."""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Protocol
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from .config import MimirConfig
from .forecaster import ForecastResult

logger = logging.getLogger(__name__)


class ForecastProvider(Protocol):
    """Protocol for loading precomputed forecast results."""

    def get_forecasts(self, config: MimirConfig) -> list[ForecastResult]:
        """Load forecasts using the provided configuration."""
        ...


def _normalize_observation_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        normalized = date_method()
        if isinstance(normalized, date):
            return normalized
    raise ValueError(f"External forecast returned an invalid observation date: {value!r}")


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str)):
        return float(value)
    float_method = getattr(value, "__float__", None)
    if callable(float_method):
        coerced = float_method()
        if isinstance(coerced, (int, float)):
            return float(coerced)
    raise ValueError(f"External forecast returned an invalid numeric value: {value!r}")


def _table_to_forecast_results(table: pa.Table) -> list[ForecastResult]:
    required_cols = {
        "_odoo_product_id",
        "_odoo_warehouse_id",
        "forecasted_daily_demand",
        "first_observation",
        "last_observation",
        "avg_daily_demand",
        "data_points",
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
    confidences = table.column("confidence").to_pylist() if "confidence" in table.column_names else None
    quantile_mins = table.column("quantile_min_qty").to_pylist() if "quantile_min_qty" in table.column_names else None
    quantile_maxs = table.column("quantile_max_qty").to_pylist() if "quantile_max_qty" in table.column_names else None

    forecasts: list[ForecastResult] = []
    for index in range(len(pids)):
        pid = pids[index]
        wid = wids[index]
        daily = demands[index]
        if pid is None or wid is None or daily is None:
            continue

        confidence = confidences[index] if confidences else "external"
        if not confidence:
            confidence = "external"

        quantile_min = _coerce_optional_float(quantile_mins[index]) if quantile_mins else None

        quantile_max = _coerce_optional_float(quantile_maxs[index]) if quantile_maxs else None

        avg_daily_value = _coerce_optional_float(avg_daily_col[index])
        daily_value = _coerce_optional_float(daily)
        if daily_value is None:
            continue
        data_points_value = data_points_col[index]
        forecasts.append(
            ForecastResult(
                product_id=int(pid),
                warehouse_id=int(wid),
                data_points=int(data_points_value) if data_points_value is not None else 0,
                first_observation=_normalize_observation_date(first_obs_col[index]),
                last_observation=_normalize_observation_date(last_obs_col[index]),
                avg_daily_demand=round(avg_daily_value if avg_daily_value is not None else 0.0, 4),
                slope=0.0,
                intercept=0.0,
                r_squared=0.0,
                forecasted_daily_demand=round(daily_value, 4),
                confidence=str(confidence),
                quantile_min_qty=quantile_min,
                quantile_max_qty=quantile_max,
            )
        )

    return forecasts


class ExternalHttpForecastProvider:
    """Fetches pre-computed forecasts from a remote HTTP endpoint."""

    def get_forecasts(self, config: MimirConfig) -> list[ForecastResult]:
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
            except ValueError as exc:
                raise ValueError("external_forecast_api_key must be a valid UUID.") from exc

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

        separator = "&" if "?" in base_uri else "?"
        uri = f"{base_uri}{separator}{urlencode(params)}"
        logger.info("Fetching external forecasts from %s", uri)

        req = Request(uri)
        if api_key:
            req.add_header("X-API-Key", api_key)

        with urlopen(req, timeout=60) as resp:  # noqa: S310
            data = resp.read()

        return _table_to_forecast_results(pq.read_table(io.BytesIO(data)))


def get_forecast_provider(config: MimirConfig) -> ForecastProvider:
    """Return the supported remote forecast provider for the configured URI."""
    if config.forecast_source != "external":
        raise ValueError("forecast_source must be 'external'. Internal forecasting has been removed.")

    parsed = urlparse(config.external_forecast_uri)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("external_forecast_uri must point to a remote HTTP(S) forecast service.")

    return ExternalHttpForecastProvider()
