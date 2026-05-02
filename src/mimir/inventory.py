"""Inventory position calculator.

Reads parqcast parquet exports for stock state and computes
the net inventory position per product x warehouse.

Three inventory modes:
  - ignore:  don't load inventory data (pure forecast-driven min/max)
  - analyze: load inventory, compute positions, add to output as analysis columns
  - adjust:  load inventory AND modify replenishment behavior (skip overstock, boost understock)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


@dataclass
class InventoryPosition:
    """Net inventory position for one product x warehouse."""

    product_id: int
    warehouse_id: int

    # Raw quantities from parquet
    on_hand_qty: float = 0.0
    reserved_qty: float = 0.0

    # Supply pipeline
    incoming_po_qty: float = 0.0       # open PO lines: ordered - received
    incoming_move_qty: float = 0.0     # stock moves TO internal locations (not done/cancel)
    incoming_mo_qty: float = 0.0       # open MO output quantity

    # Demand pipeline
    outgoing_so_qty: float = 0.0       # open SO lines: ordered - delivered
    outgoing_move_qty: float = 0.0     # stock moves FROM internal locations (not done/cancel)
    outgoing_mo_consumption: float = 0.0  # MO component demand

    # Computed
    @property
    def available_qty(self) -> float:
        """On-hand minus reserved. Can be negative."""
        return self.on_hand_qty - self.reserved_qty

    @property
    def unreserved_qty(self) -> float:
        """On-hand ignoring reservations."""
        return self.on_hand_qty

    @property
    def total_incoming(self) -> float:
        return self.incoming_po_qty + self.incoming_move_qty + self.incoming_mo_qty

    @property
    def total_outgoing(self) -> float:
        return self.outgoing_so_qty + self.outgoing_move_qty + self.outgoing_mo_consumption

    def net_position(self, respect_reservations: bool = True, include_incoming: bool = True, include_outgoing: bool = False) -> float:
        """Compute net inventory position based on config flags.

        Args:
            respect_reservations: subtract reserved_qty from on-hand
            include_incoming: add incoming supply (POs, in-transit)
            include_outgoing: subtract outgoing demand (SOs, confirmed moves)
        """
        base = self.on_hand_qty
        if respect_reservations:
            base -= self.reserved_qty

        if include_incoming:
            base += self.total_incoming

        if include_outgoing:
            base -= self.total_outgoing

        return base

    def coverage_days(self, daily_demand: float, respect_reservations: bool = True, include_incoming: bool = True, include_outgoing: bool = False) -> float | None:
        """How many days of demand does current position cover?

        Returns None if daily_demand is 0 (infinite coverage / no demand).
        """
        if daily_demand <= 0:
            return None
        net = self.net_position(respect_reservations, include_incoming, include_outgoing)
        return max(0, net / daily_demand)


@dataclass
class InventoryConfig:
    """Configuration for inventory position calculation."""

    # Master switch
    inventory_mode: str = "ignore"  # ignore | analyze | adjust

    # Position calculation flags
    respect_reservations: bool = True
    include_incoming_supply: bool = True
    include_outgoing_demand: bool = False  # False by default to avoid double-counting with forecast

    # Which supply/demand sources to include
    incoming_sources: set[str] = field(default_factory=lambda: {"purchase_orders", "incoming_moves"})
    outgoing_sources: set[str] = field(default_factory=lambda: {"sale_orders"})

    # Analysis thresholds
    overstock_threshold_days: float = 90.0
    understock_threshold_days: float = 3.0

    # Adjustment behavior (only in 'adjust' mode)
    overstock_skip: bool = True                  # skip creating orderpoints for overstocked products
    understock_service_level_bump: float = 0.03  # raise service level by this for understocked products

    # Edge cases
    negative_stock_handling: str = "zero"  # zero | allow


def classify_inventory(
    position: InventoryPosition,
    daily_demand: float,
    config: InventoryConfig,
) -> str:
    """Classify inventory health for a product x warehouse.

    Returns: 'ok', 'overstock', 'understock', 'at_risk', 'no_stock', 'no_demand', 'no_data'
    """
    if position.on_hand_qty == 0 and position.total_incoming == 0:
        if daily_demand > 0:
            return "no_stock"
        return "no_data"

    if daily_demand <= 0:
        if position.on_hand_qty > 0:
            return "no_demand"  # stock exists but no demand — potential dead stock
        return "no_data"

    coverage = position.coverage_days(
        daily_demand,
        respect_reservations=config.respect_reservations,
        include_incoming=config.include_incoming_supply,
        include_outgoing=config.include_outgoing_demand,
    )

    if coverage is None:
        return "no_data"
    if coverage > config.overstock_threshold_days:
        return "overstock"
    if coverage < config.understock_threshold_days:
        return "understock"
    if coverage < config.understock_threshold_days * 2:
        return "at_risk"
    return "ok"


def load_inventory_positions(
    export_dir: Path,
    warehouse_locations: dict[int, int],
    config: InventoryConfig,
) -> dict[tuple[int, int], InventoryPosition]:
    """Load inventory data from parqcast exports and compute positions.

    Args:
        export_dir: parqcast export directory
        warehouse_locations: mapping warehouse_id -> lot_stock_id (from reader)
        config: inventory configuration

    Returns:
        Dict mapping (product_id, warehouse_id) to InventoryPosition
    """
    positions: dict[tuple[int, int], InventoryPosition] = {}
    export_dir = Path(export_dir)

    # Reverse lookup: location_id -> warehouse_id
    location_to_warehouse = {loc: wh for wh, loc in warehouse_locations.items()}

    # 1. On-hand and reserved from stock_quant
    _load_quants(export_dir, location_to_warehouse, positions)

    # 2. Incoming supply from purchase orders
    if "purchase_orders" in config.incoming_sources:
        _load_incoming_po(export_dir, positions)

    # 3. Incoming/outgoing from stock moves
    if "incoming_moves" in config.incoming_sources or "sale_orders" in config.outgoing_sources:
        _load_moves(export_dir, location_to_warehouse, config, positions)

    # 4. Outgoing from sale order lines (alternative to moves)
    if "sale_orders" in config.outgoing_sources:
        _load_outgoing_so(export_dir, positions)

    logger.info("Loaded inventory positions for %d product x warehouse combinations", len(positions))
    return positions


def _get_or_create(positions: dict, product_id: int, warehouse_id: int) -> InventoryPosition:
    key = (product_id, warehouse_id)
    if key not in positions:
        positions[key] = InventoryPosition(product_id=product_id, warehouse_id=warehouse_id)
    return positions[key]


def _load_quants(export_dir: Path, location_to_warehouse: dict, positions: dict):
    """Load on-hand and reserved quantities from stock_quant.parquet."""
    path = export_dir / "stock_quant.parquet"
    if not path.exists():
        logger.info("stock_quant.parquet not found, skipping on-hand data")
        return

    table = pq.read_table(
        str(path),
        columns=["_odoo_product_id", "_odoo_warehouse_id", "quantity", "reserved_quantity"],
    )

    pids = table.column("_odoo_product_id").to_pylist()
    wids = table.column("_odoo_warehouse_id").to_pylist()
    qtys = table.column("quantity").to_pylist()
    reserved = table.column("reserved_quantity").to_pylist()

    for i in range(table.num_rows):
        pid, wid, qty, res = pids[i], wids[i], qtys[i], reserved[i]
        if pid is None or wid is None:
            continue

        pos = _get_or_create(positions, pid, wid)
        pos.on_hand_qty += float(qty or 0)
        pos.reserved_qty += float(res or 0)

    logger.info("Loaded %d quant rows", table.num_rows)


def _load_incoming_po(export_dir: Path, positions: dict):
    """Load incoming supply from purchase_order_line.parquet."""
    path = export_dir / "purchase_order_line.parquet"
    if not path.exists():
        logger.info("purchase_order_line.parquet not found, skipping incoming PO data")
        return

    table = pq.read_table(
        str(path),
        columns=["_odoo_product_id", "_odoo_warehouse_id", "product_qty", "qty_received", "po_state"],
    )

    pids = table.column("_odoo_product_id").to_pylist()
    wids = table.column("_odoo_warehouse_id").to_pylist()
    ordered = table.column("product_qty").to_pylist()
    received = table.column("qty_received").to_pylist()
    states = table.column("po_state").to_pylist()

    for i in range(table.num_rows):
        pid, wid = pids[i], wids[i]
        state = states[i]
        if pid is None or wid is None:
            continue
        # Only count confirmed/approved POs, not draft/cancel/done
        if state in (None, "draft", "cancel", "done"):
            continue

        remaining = float(ordered[i] or 0) - float(received[i] or 0)
        if remaining > 0:
            pos = _get_or_create(positions, pid, wid)
            pos.incoming_po_qty += remaining

    logger.info("Loaded PO line data")


def _load_moves(export_dir: Path, location_to_warehouse: dict, config: InventoryConfig, positions: dict):
    """Load open stock moves for incoming/outgoing analysis."""
    path = export_dir / "stock_move.parquet"
    if not path.exists():
        logger.info("stock_move.parquet not found, skipping move data")
        return

    table = pq.read_table(
        str(path),
        columns=[
            "_odoo_product_id", "product_uom_qty", "state",
            "_odoo_location_id", "_odoo_location_dest_id", "_odoo_warehouse_id",
        ],
    )

    pids = table.column("_odoo_product_id").to_pylist()
    qtys = table.column("product_uom_qty").to_pylist()
    states = table.column("state").to_pylist()
    src_locs = table.column("_odoo_location_id").to_pylist()
    dst_locs = table.column("_odoo_location_dest_id").to_pylist()
    wids = table.column("_odoo_warehouse_id").to_pylist()

    open_states = {"confirmed", "waiting", "assigned", "partially_available"}

    for i in range(table.num_rows):
        state = states[i]
        if state not in open_states:
            continue

        pid = pids[i]
        wid = wids[i]
        qty = float(qtys[i] or 0)
        if pid is None or wid is None or qty <= 0:
            continue

        src_wh = location_to_warehouse.get(src_locs[i])
        dst_wh = location_to_warehouse.get(dst_locs[i])

        # Incoming: move destination is an internal warehouse location
        if dst_wh is not None and "incoming_moves" in config.incoming_sources:
            pos = _get_or_create(positions, pid, dst_wh)
            pos.incoming_move_qty += qty

        # Outgoing: move source is an internal warehouse location
        if src_wh is not None and "outgoing_moves" in config.outgoing_sources:
            pos = _get_or_create(positions, pid, src_wh)
            pos.outgoing_move_qty += qty

    logger.info("Loaded stock move data")


def _load_outgoing_so(export_dir: Path, positions: dict):
    """Load outgoing demand from sale_order_line.parquet (open SOs only)."""
    path = export_dir / "sale_order_line.parquet"
    if not path.exists():
        return  # Already read for forecasting, but might not exist

    table = pq.read_table(
        str(path),
        columns=["_odoo_product_id", "_odoo_warehouse_id", "product_uom_qty", "qty_delivered", "so_state"],
    )

    pids = table.column("_odoo_product_id").to_pylist()
    wids = table.column("_odoo_warehouse_id").to_pylist()
    ordered = table.column("product_uom_qty").to_pylist()
    delivered = table.column("qty_delivered").to_pylist()
    states = table.column("so_state").to_pylist()

    for i in range(table.num_rows):
        pid, wid = pids[i], wids[i]
        state = states[i]
        if pid is None or wid is None:
            continue
        # Only count confirmed/open SOs
        if state not in ("sale", "done"):
            continue

        remaining = float(ordered[i] or 0) - float(delivered[i] or 0)
        if remaining > 0:
            pos = _get_or_create(positions, pid, wid)
            pos.outgoing_so_qty += remaining
