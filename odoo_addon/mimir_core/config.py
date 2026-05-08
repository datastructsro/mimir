"""Configuration definitions for mimir.

Stores global settings, per-category overrides, and per-product overrides.
SQLite dependency has been removed in favor of Odoo ORM integration and in-process config loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class CategoryOverride(TypedDict):
    safety_factor: float | None
    min_data_points: int | None
    lead_time_override_days: int | None
    review_period_override_days: int | None
    service_level: float | None
    default_min_qty: float | None
    default_max_qty: float | None


class ProductOverride(TypedDict):
    safety_factor: float | None
    min_qty_floor: float | None
    max_qty_ceiling: float | None
    lead_time_override_days: int | None
    excluded: bool
    service_level: float | None


@dataclass
class MimirConfig:
    """Resolved configuration values for a single run."""

    selected_warehouse_id: int | None = None

    forecast_horizon_days: int = 30
    time_bucket: str = "daily"
    min_data_points: int = 4
    min_history_days: int = 36
    service_level: float = 0.72
    review_period_days: int = 7
    default_lead_time_days: int = 7
    min_demand_threshold: float = 0.1
    default_min_qty: float = 0.0
    default_max_qty: float = 0.0
    odoo_db: str = ""

    # Inventory position settings
    inventory_mode: str = "ignore"  # ignore | analyze | adjust
    respect_reservations: bool = True
    include_incoming_supply: bool = True
    include_outgoing_demand: bool = False
    incoming_sources: set[str] = field(default_factory=lambda: {"purchase_orders", "incoming_moves"})
    outgoing_sources: set[str] = field(default_factory=lambda: {"sale_orders"})
    overstock_threshold_days: float = 90.0
    understock_threshold_days: float = 3.0
    overstock_skip: bool = True
    understock_service_level_bump: float = 0.03

    # Forecast Source
    forecast_source: str = "external"
    external_forecast_uri: str = ""
    external_forecast_api_key: str = ""

    # Loaded from override tables
    category_overrides: dict[int, CategoryOverride] = field(default_factory=dict)
    product_overrides: dict[int, ProductOverride] = field(default_factory=dict)
