"""Min/max replenishment quantity calculator.

Uses quantile reorder points: min_qty and max_qty come directly from
the service-level percentile of historical lead-time demand windows.
Falls back to point-estimate formula when quantile data is unavailable.

Resolves lead times from supplier info, with category and product overrides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa

from .config import MimirConfig
from .forecaster import ForecastResult

if TYPE_CHECKING:
    from .inventory import InventoryConfig, InventoryPosition


@dataclass
class ReplenishmentRule:
    """Computed replenishment rule for one product×warehouse."""

    product_id: int
    product_name: str
    warehouse_id: int
    warehouse_code: str
    location_id: int  # lot_stock_id of the warehouse
    min_qty: float
    max_qty: float
    planned_lead_time_days: int
    empirical_lead_time_days: int
    service_level: float
    review_period_days: int
    forecasted_daily_demand: float
    trigger: str  # 'auto'
    action: str  # 'create', 'update', 'skip'
    skip_reason: str | None
    # Inventory analysis (populated when inventory_mode != 'ignore')
    on_hand_qty: float | None = None
    reserved_qty: float | None = None
    incoming_supply_qty: float | None = None
    outgoing_demand_qty: float | None = None
    net_available_qty: float | None = None
    coverage_days: float | None = None
    inventory_flag: str | None = None


def resolve_lead_time(
    product_id: int,
    category_id: int | None,
    supplier_info_table: pa.Table | None,
    config: MimirConfig,
) -> int:
    """Resolve lead time for a product.

    Priority:
    1. Product override in config
    2. Category override in config
    3. Supplier info parquet (min delay across suppliers)
    4. Global default
    """
    # 1. Product override
    if product_id in config.product_overrides:
        product_lt = config.product_overrides[product_id].get("lead_time_override_days")
        if product_lt is not None:
            return product_lt

    # 2. Category override
    if category_id and category_id in config.category_overrides:
        cat_lt = config.category_overrides[category_id].get("lead_time_override_days")
        if cat_lt is not None:
            return cat_lt

    # 3. Supplier info
    if supplier_info_table is not None and supplier_info_table.num_rows > 0:
        pids = supplier_info_table.column("_odoo_product_id").to_pylist()
        delays = supplier_info_table.column("delay").to_pylist()

        product_delays = [d for pid, d in zip(pids, delays) if pid == product_id and d is not None and d > 0]
        if product_delays:
            return min(product_delays)

    # 4. Global default
    return config.default_lead_time_days


def resolve_service_level(
    product_id: int,
    category_id: int | None,
    config: MimirConfig,
) -> float:
    """Resolve service level (quantile τ) for a product.

    Priority: product override > category override > global default.
    """
    if product_id in config.product_overrides:
        sl = config.product_overrides[product_id].get("service_level")
        if sl is not None:
            return sl

    if category_id and category_id in config.category_overrides:
        sl = config.category_overrides[category_id].get("service_level")
        if sl is not None:
            return sl

    return config.service_level


def resolve_review_period(
    product_id: int,
    category_id: int | None,
    config: MimirConfig,
) -> int:
    """Resolve review period for a product."""
    if category_id and category_id in config.category_overrides:
        rp = config.category_overrides[category_id].get("review_period_override_days")
        if rp is not None:
            return rp
    return config.review_period_days


def resolve_empirical_lead_time(
    product_id: int,
    warehouse_id: int,
    empirical_table: pa.Table | None,
    min_observations: int = 3,
) -> int:
    """Resolve empirical lead time from pre-computed parquet.

    Returns 0 if no reliable data is available.
    """
    if empirical_table is None or empirical_table.num_rows == 0:
        return 0

    pids = empirical_table.column("_odoo_product_id").to_pylist()
    wids = empirical_table.column("_odoo_warehouse_id").to_pylist()
    delays = empirical_table.column("median_delay_days").to_pylist()
    counts = empirical_table.column("observation_count").to_pylist()

    for pid, wid, delay, count in zip(pids, wids, delays, counts):
        if pid == product_id and wid == warehouse_id and count >= min_observations:
            return delay

    return 0


def _resolve_default_min_max(
    category_id: int | None,
    config: MimirConfig,
) -> tuple[float, float] | None:
    """Resolve default min/max qty for products without enough history."""
    if category_id and category_id in config.category_overrides:
        co = config.category_overrides[category_id]
        cat_min = co.get("default_min_qty")
        cat_max = co.get("default_max_qty")
        if cat_min is not None and cat_max is not None and (cat_min > 0 or cat_max > 0):
            return cat_min, cat_max

    if config.default_min_qty > 0 or config.default_max_qty > 0:
        return config.default_min_qty, config.default_max_qty

    return None


def _make_skip_rule(
    pid: int,
    wid: int,
    product_name: str,
    warehouse_code: str,
    location_id: int,
    forecasted_daily_demand: float,
    skip_reason: str,
    planned_lead_time_days: int = 0,
    empirical_lead_time_days: int = 0,
    service_level: float = 0,
    review_period_days: int = 0,
) -> ReplenishmentRule:
    return ReplenishmentRule(
        product_id=pid, product_name=product_name,
        warehouse_id=wid, warehouse_code=warehouse_code,
        location_id=location_id,
        min_qty=0, max_qty=0, planned_lead_time_days=planned_lead_time_days,
        empirical_lead_time_days=empirical_lead_time_days,
        service_level=service_level, review_period_days=review_period_days,
        forecasted_daily_demand=forecasted_daily_demand,
        trigger="auto", action="skip", skip_reason=skip_reason,
    )


def calculate_rule(
    forecast: ForecastResult,
    product_name: str,
    warehouse_code: str,
    location_id: int,
    category_id: int | None,
    supplier_info: pa.Table | None,
    existing_orderpoints: pa.Table | None,
    config: MimirConfig,
    quantile_result: tuple[float, float] | None = None,
    inventory_position: InventoryPosition | None = None,
    inventory_config: InventoryConfig | None = None,
    empirical_lead_times: pa.Table | None = None,
) -> ReplenishmentRule:
    """Calculate a single replenishment rule.

    Uses quantile_result (min_qty, max_qty) directly when available.
    Falls back to point-estimate formula when quantile data is unavailable.
    """
    pid = forecast.product_id
    wid = forecast.warehouse_id
    daily = forecast.forecasted_daily_demand

    # Check exclusions
    if pid in config.product_overrides and config.product_overrides[pid].get("excluded"):
        return _make_skip_rule(pid, wid, product_name, warehouse_code, location_id, daily, "product_excluded")

    # Check history span
    history_span_days = (forecast.last_observation - forecast.first_observation).days

    insufficient_history = (
        forecast.confidence == "insufficient_data" or
        history_span_days < config.min_history_days
    )

    below_demand = daily < config.min_demand_threshold

    if insufficient_history or below_demand:
        default_min_max = _resolve_default_min_max(category_id, config)
        if default_min_max is not None:
            min_qty, max_qty = default_min_max
            # Force the rule to be actionable, skipping quantile calculations
            quantile_result = (min_qty, max_qty)
            forecast.confidence = "default_fallback"
        else:
            reason = "insufficient_data" if insufficient_history else "below_demand_threshold"
            return _make_skip_rule(pid, wid, product_name, warehouse_code, location_id, daily, reason)

    # Resolve parameters
    planned_lead_time = resolve_lead_time(pid, category_id, supplier_info, config)
    empirical_lead_time = resolve_empirical_lead_time(pid, wid, empirical_lead_times)
    service_level = resolve_service_level(pid, category_id, config)
    review = resolve_review_period(pid, category_id, config)

    # Inventory classification and adjustment
    inv_flag = None
    inv_net = None
    inv_coverage = None
    if inventory_position is not None and inventory_config is not None:
        from .inventory import classify_inventory

        inv_flag = classify_inventory(inventory_position, daily, inventory_config)
        inv_net = inventory_position.net_position(
            inventory_config.respect_reservations,
            inventory_config.include_incoming_supply,
            inventory_config.include_outgoing_demand,
        )
        inv_coverage = inventory_position.coverage_days(
            daily,
            inventory_config.respect_reservations,
            inventory_config.include_incoming_supply,
            inventory_config.include_outgoing_demand,
        )

        if inventory_config.inventory_mode == "adjust":
            if inv_flag == "overstock" and inventory_config.overstock_skip:
                rule = _make_skip_rule(
                    pid, wid, product_name, warehouse_code, location_id, daily,
                    "overstock_skip", planned_lead_time, empirical_lead_time, service_level, review,
                )
                rule.on_hand_qty = inventory_position.on_hand_qty
                rule.reserved_qty = inventory_position.reserved_qty
                rule.incoming_supply_qty = inventory_position.total_incoming
                rule.outgoing_demand_qty = inventory_position.total_outgoing
                rule.net_available_qty = inv_net
                rule.coverage_days = inv_coverage
                rule.inventory_flag = inv_flag
                return rule
            # Bump service level for understocked products
            if inv_flag in ("understock", "at_risk", "no_stock"):
                service_level = min(service_level + inventory_config.understock_service_level_bump, 0.99)

    # Core calculation — quantile reorder points
    if quantile_result is not None:
        min_qty, max_qty = quantile_result
    else:
        # Without quantile result and no default fallback, we cannot proceed.
        # This shouldn't happen if the server operates correctly and we checked history length,
        # but as a final safety measure, we skip.
        return _make_skip_rule(pid, wid, product_name, warehouse_code, location_id, daily, "missing_quantile_data")

    # Apply product floor/ceiling overrides
    if pid in config.product_overrides:
        po = config.product_overrides[pid]
        if po.get("min_qty_floor") is not None:
            min_qty = max(min_qty, po["min_qty_floor"])
        if po.get("max_qty_ceiling") is not None:
            max_qty = min(max_qty, po["max_qty_ceiling"])

    max_qty = max(max_qty, min_qty)
    min_qty = round(min_qty, 2)
    max_qty = round(max_qty, 2)

    # Determine action: create vs update
    action = "create"
    if existing_orderpoints is not None and existing_orderpoints.num_rows > 0:
        op_pids = existing_orderpoints.column("_odoo_product_id").to_pylist()
        op_wids = existing_orderpoints.column("_odoo_warehouse_id").to_pylist()
        for op_pid, op_wid in zip(op_pids, op_wids):
            if op_pid == pid and op_wid == wid:
                action = "update"
                break

    rule = ReplenishmentRule(
        product_id=pid,
        product_name=product_name,
        warehouse_id=wid,
        warehouse_code=warehouse_code,
        location_id=location_id,
        min_qty=min_qty,
        max_qty=max_qty,
        planned_lead_time_days=planned_lead_time,
        empirical_lead_time_days=empirical_lead_time,
        service_level=service_level,
        review_period_days=review,
        forecasted_daily_demand=daily,
        trigger="auto",
        action=action,
        skip_reason=None,
    )

    if inventory_position is not None:
        rule.on_hand_qty = inventory_position.on_hand_qty
        rule.reserved_qty = inventory_position.reserved_qty
        rule.incoming_supply_qty = inventory_position.total_incoming
        rule.outgoing_demand_qty = inventory_position.total_outgoing
        rule.net_available_qty = inv_net
        rule.coverage_days = inv_coverage
        rule.inventory_flag = inv_flag

    return rule
