"""Mock forecast server simulating ML computations (linear regression & quantiles) for tests."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np
import pyarrow as pa

from mimir.config import MimirConfig
from mimir.forecaster import ForecastResult


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
    """Aggregate SOL data into demand time series per (product, warehouse)."""
    import pyarrow.compute as _pc

    if exclude_states is None:
        exclude_states = {"cancel"}

    t = sale_order_lines

    if exclude_states:
        state_col = t.column("so_state")
        mask = _pc.invert(_pc.is_in(state_col, value_set=pa.array(list(exclude_states))))
        t = t.filter(mask)

    for col_name in ("_odoo_product_id", "_odoo_warehouse_id", "product_uom_qty", "date_order"):
        t = t.filter(_pc.is_valid(t.column(col_name)))

    if t.num_rows == 0:
        return {}

    date_col = t.column("date_order")
    if hasattr(date_col.type, "tz") and date_col.type.tz is not None:
        date_col = _pc.cast(date_col, pa.timestamp("us"))
    date_col = _pc.cast(date_col, pa.date32())

    if bucket == "weekly":
        dow = _pc.day_of_week(date_col)
        date_as_ts = _pc.cast(date_col, pa.timestamp("s"))
        offset = _pc.multiply(_pc.cast(dow, pa.int64()), pa.scalar(-86400, pa.int64()))
        date_col = _pc.cast(_pc.add(date_as_ts, _pc.cast(offset, pa.duration("s"))), pa.date32())

    t = t.set_column(t.schema.get_field_index("date_order"), "date_order", date_col)

    result = t.group_by(["_odoo_product_id", "_odoo_warehouse_id", "date_order"]).aggregate(
        [("product_uom_qty", "sum"), ("product_uom_qty", "count")]
    )

    series: dict[tuple[int, int], list[DemandPoint]] = defaultdict(list)
    for row in result.to_pylist():
        pid = row["_odoo_product_id"]
        wid = row["_odoo_warehouse_id"]
        d = row["date_order"]
        s = row["product_uom_qty_sum"]
        c = row["product_uom_qty_count"]
        if None not in (pid, wid, d, s, c):
            series[(pid, wid)].append(DemandPoint(bucket_date=d, total_qty=float(s), order_count=int(c)))

    for key in series:
        series[key].sort(key=lambda dp: dp.bucket_date)

    return dict(series)


def fit_demand(
    product_id: int,
    warehouse_id: int,
    points: list[DemandPoint],
    forecast_horizon_days: int = 30,
    min_data_points: int = 4,
    bucket: str = "daily",
) -> ForecastResult | None:
    """Fit linear regression to a demand time series."""
    if len(points) < min_data_points:
        return ForecastResult(
            product_id=product_id,
            warehouse_id=warehouse_id,
            data_points=len(points),
            first_observation=points[0].bucket_date if points else date.today(),
            last_observation=points[-1].bucket_date if points else date.today(),
            avg_daily_demand=0.0,
            slope=0.0,
            intercept=0.0,
            r_squared=0.0,
            forecasted_daily_demand=0.0,
            confidence="insufficient_data",
        )

    base_date = points[0].bucket_date

    # Build arrays: x = days since first observation, y = demand per bucket
    x = np.array([(p.bucket_date - base_date).days for p in points], dtype=np.float64)
    y = np.array([p.total_qty for p in points], dtype=np.float64)

    # Normalize weekly buckets to daily rate
    if bucket == "weekly":
        y = y / 7.0

    # All-zero demand: nothing to forecast
    if np.all(y == 0):
        return ForecastResult(
            product_id=product_id,
            warehouse_id=warehouse_id,
            data_points=len(points),
            first_observation=points[0].bucket_date,
            last_observation=points[-1].bucket_date,
            avg_daily_demand=0.0,
            slope=0.0,
            intercept=0.0,
            r_squared=0.0,
            forecasted_daily_demand=0.0,
            confidence="low",
        )

    # Fit linear regression: y = slope * x + intercept
    try:
        coeffs = np.polyfit(x, y, deg=1)
        slope = float(coeffs[0])
        intercept = float(coeffs[1])
        if not (np.isfinite(slope) and np.isfinite(intercept)):
            raise ValueError("Non-finite coefficients")
    except (np.linalg.LinAlgError, ValueError):
        avg = float(np.mean(y))
        slope = 0.0
        intercept = avg

    # Calculate R²
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    if not np.isfinite(r_squared):
        r_squared = 0.0

    # Average daily demand (simple)
    total_qty = sum(p.total_qty for p in points)
    span_days = max((points[-1].bucket_date - points[0].bucket_date).days, 1)
    avg_daily = total_qty / span_days

    # Project demand at the midpoint of the forecast horizon
    last_x = (points[-1].bucket_date - base_date).days
    forecast_x = last_x + forecast_horizon_days / 2.0
    projected = slope * forecast_x + intercept

    # Forecasted daily demand: use projection but floor at 0
    forecasted_daily = max(projected, 0.0)

    # Cap extreme extrapolation at 3x average
    if avg_daily > 0 and forecasted_daily > 3.0 * avg_daily:
        forecasted_daily = 3.0 * avg_daily

    # If slope is negative and projection went to zero, fall back to recent average
    if forecasted_daily < avg_daily * 0.1 and avg_daily > 0:
        # Demand declining sharply — use 50% of recent average as floor
        forecasted_daily = avg_daily * 0.5

    # Confidence classification
    confidence = _classify_confidence(r_squared, len(points), span_days)

    return ForecastResult(
        product_id=product_id,
        warehouse_id=warehouse_id,
        data_points=len(points),
        first_observation=points[0].bucket_date,
        last_observation=points[-1].bucket_date,
        avg_daily_demand=round(avg_daily, 4),
        slope=round(float(slope), 6),
        intercept=round(float(intercept), 4),
        r_squared=round(float(r_squared), 4),
        forecasted_daily_demand=round(forecasted_daily, 4),
        confidence=confidence,
    )


def _classify_confidence(r_squared: float, data_points: int, span_days: int) -> str:
    """Classify forecast confidence based on regression quality."""
    if data_points < 4 or span_days < 14:
        return "low"
    if r_squared >= 0.7 and data_points >= 20 and span_days >= 90:
        return "high"
    if r_squared >= 0.4 and data_points >= 8 and span_days >= 30:
        return "medium"
    return "low"


def compute_quantile_reorder_points(
    points: list[DemandPoint],
    lead_time_days: int,
    review_period_days: int,
    service_level: float = 0.95,
    min_data_points: int = 14,
    bucket: str = "daily",
) -> tuple[float, float] | None:
    """Compute (min_qty, max_qty) as quantiles of lead-time demand windows."""
    if len(points) < min_data_points:
        return None

    daily = _interpolate_to_daily(points, bucket)
    if len(daily) == 0 or np.all(daily == 0):
        return None

    pct = service_level * 100

    min_qty = _rolling_quantile(daily, lead_time_days, pct)
    max_qty = _rolling_quantile(daily, lead_time_days + review_period_days, pct)

    # Ensure max >= min
    max_qty = max(max_qty, min_qty)

    return round(min_qty, 2), round(max_qty, 2)


def _interpolate_to_daily(points: list[DemandPoint], bucket: str) -> np.ndarray:
    """Convert sparse DemandPoints to a gapless daily demand array (0 for missing days)."""
    if not points:
        return np.array([], dtype=np.float64)

    first = points[0].bucket_date
    last = points[-1].bucket_date
    n_days = (last - first).days + 1
    if n_days <= 0:
        return np.array([points[0].total_qty], dtype=np.float64)

    daily = np.zeros(n_days, dtype=np.float64)
    divisor = 7.0 if bucket == "weekly" else 1.0

    for p in points:
        idx = (p.bucket_date - first).days
        if bucket == "weekly":
            # Spread weekly total evenly across 7 days
            daily_rate = p.total_qty / divisor
            for d in range(min(7, n_days - idx)):
                daily[idx + d] += daily_rate
        else:
            if 0 <= idx < n_days:
                daily[idx] = p.total_qty

    return daily


def _rolling_quantile(daily: np.ndarray, window_days: int, percentile: float) -> float:
    """Compute the given percentile of rolling window sums."""
    n = len(daily)
    if n < window_days:
        # Not enough history — use the total scaled up proportionally
        return float(np.sum(daily)) * window_days / max(n, 1)

    # Rolling sum via cumsum difference (O(n), no loops)
    cumsum = np.cumsum(daily)
    cumsum = np.insert(cumsum, 0, 0.0)
    windows = cumsum[window_days:] - cumsum[:-window_days]

    return float(np.percentile(windows, percentile))


class MockForecastProvider:
    """Simulates the external mimir-server returning ForecastResults enriched with quantile computations."""

    def __init__(self, demand_series: dict[tuple[int, int], list[DemandPoint]] | None = None, input_dir: str | None = None):
        self.demand_series = demand_series or {}
        self.input_dir = input_dir

    def get_forecasts(
        self,
        config: MimirConfig,
    ) -> list[ForecastResult]:
        import uuid
        api_key = config.external_forecast_api_key
        if api_key:
            try:
                uuid.UUID(api_key)
            except ValueError:
                raise ValueError("Mock server rejected invalid UUID api key")

        series = self.demand_series
        if self.input_dir:
            from pathlib import Path

            import pyarrow.parquet as pq
            sol_path = Path(self.input_dir) / "sale_order_line.parquet"
            if sol_path.exists():
                sol = pq.read_table(sol_path)
                series = aggregate_demand(sol, bucket=config.time_bucket)

        forecasts: list[ForecastResult] = []
        for (pid, wid), points in series.items():
            result = fit_demand(
                product_id=pid,
                warehouse_id=wid,
                points=points,
                forecast_horizon_days=config.forecast_horizon_days,
                min_data_points=config.min_data_points,
                bucket=config.time_bucket,
            )
            if result:
                # Mock resolution of SL, Review, Lead Time
                qr = compute_quantile_reorder_points(
                    points=points,
                    lead_time_days=config.default_lead_time_days,
                    review_period_days=config.review_period_days,
                    service_level=config.service_level,
                    min_data_points=config.min_data_points,
                    bucket=config.time_bucket,
                )
                if qr:
                    result.quantile_min_qty, result.quantile_max_qty = qr
                forecasts.append(result)
        return forecasts
