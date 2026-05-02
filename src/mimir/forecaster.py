"""Demand forecaster: linear regression diagnostics + quantile reorder points.

Linear regression (fit_demand) provides trend/confidence diagnostics.
Quantile computation (compute_quantile_reorder_points) directly answers
"how much stock covers the next L days at service level τ?" by taking
the τ-th percentile of rolling lead-time demand windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class ForecastResult:
    """Result of fitting a linear regression to a demand time series."""

    product_id: int
    warehouse_id: int
    data_points: int
    first_observation: date
    last_observation: date
    avg_daily_demand: float
    slope: float  # units per day (positive = growing demand)
    intercept: float
    r_squared: float
    forecasted_daily_demand: float  # projected at horizon midpoint
    confidence: str  # 'high', 'medium', 'low', 'insufficient_data'
    quantile_min_qty: float | None = None
    quantile_max_qty: float | None = None
