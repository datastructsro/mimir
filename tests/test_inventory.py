"""Tests for the inventory position module."""

import pyarrow as pa
import pyarrow.parquet as pq

from mimir.inventory import (
    InventoryConfig,
    InventoryPosition,
    classify_inventory,
    load_inventory_positions,
)


def _make_position(
    on_hand=100.0, reserved=10.0,
    incoming_po=50.0, incoming_move=0.0, incoming_mo=0.0,
    outgoing_so=20.0, outgoing_move=0.0, outgoing_mo=0.0,
) -> InventoryPosition:
    return InventoryPosition(
        product_id=1, warehouse_id=1,
        on_hand_qty=on_hand, reserved_qty=reserved,
        incoming_po_qty=incoming_po, incoming_move_qty=incoming_move,
        incoming_mo_qty=incoming_mo,
        outgoing_so_qty=outgoing_so, outgoing_move_qty=outgoing_move,
        outgoing_mo_consumption=outgoing_mo,
    )


def _default_config(**kwargs) -> InventoryConfig:
    defaults = {
        "inventory_mode": "analyze",
        "respect_reservations": True,
        "include_incoming_supply": True,
        "include_outgoing_demand": True,
        "overstock_threshold_days": 90.0,
        "understock_threshold_days": 3.0,
    }
    defaults.update(kwargs)
    return InventoryConfig(**defaults)


# --- net_position tests ---

def test_net_position_all_flags():
    """Net position with all flags enabled: on_hand - reserved + incoming - outgoing."""
    pos = _make_position(on_hand=100, reserved=10, incoming_po=50, outgoing_so=20)
    net = pos.net_position(respect_reservations=True, include_incoming=True, include_outgoing=True)
    assert net == 100 - 10 + 50 - 20  # 120


def test_net_position_no_reservations():
    """Without respect_reservations, reserved_qty is ignored."""
    pos = _make_position(on_hand=100, reserved=10, incoming_po=50, outgoing_so=20)
    net = pos.net_position(respect_reservations=False, include_incoming=True, include_outgoing=True)
    assert net == 100 + 50 - 20  # 130


def test_net_position_no_incoming():
    """Without incoming, only on_hand - reserved."""
    pos = _make_position(on_hand=100, reserved=10, incoming_po=50)
    net = pos.net_position(respect_reservations=True, include_incoming=False, include_outgoing=False)
    assert net == 100 - 10  # 90


def test_net_position_minimal():
    """All flags off = just on_hand."""
    pos = _make_position(on_hand=100, reserved=10, incoming_po=50)
    net = pos.net_position(respect_reservations=False, include_incoming=False, include_outgoing=False)
    assert net == 100


def test_net_position_negative():
    """Net position can be negative when reserved > on_hand."""
    pos = _make_position(on_hand=10, reserved=50, incoming_po=0)
    net = pos.net_position(respect_reservations=True, include_incoming=False, include_outgoing=False)
    assert net == -40


# --- coverage_days tests ---

def test_coverage_days_normal():
    pos = _make_position(on_hand=100, reserved=0, incoming_po=0)
    cov = pos.coverage_days(10.0, respect_reservations=False, include_incoming=False, include_outgoing=False)
    assert cov == 10.0


def test_coverage_days_zero_demand():
    pos = _make_position(on_hand=100)
    cov = pos.coverage_days(0.0)
    assert cov is None


def test_coverage_days_negative_demand():
    pos = _make_position(on_hand=100)
    cov = pos.coverage_days(-5.0)
    assert cov is None


def test_coverage_days_negative_position():
    """Negative net position floors coverage at 0."""
    pos = _make_position(on_hand=10, reserved=50, incoming_po=0)
    cov = pos.coverage_days(10.0, respect_reservations=True, include_incoming=False, include_outgoing=False)
    assert cov == 0  # max(0, -40/10) = 0


# --- classify_inventory tests ---

def test_classify_ok():
    config = _default_config(overstock_threshold_days=90, understock_threshold_days=3)
    pos = _make_position(on_hand=300, reserved=0, incoming_po=0)
    # coverage = 300/10 = 30 days — between 3 and 90
    flag = classify_inventory(pos, 10.0, config)
    assert flag == "ok"


def test_classify_overstock():
    config = _default_config(overstock_threshold_days=90, understock_threshold_days=3)
    pos = _make_position(on_hand=1000, reserved=0, incoming_po=0)
    # coverage = 1000/10 = 100 days > 90
    flag = classify_inventory(pos, 10.0, config)
    assert flag == "overstock"


def test_classify_understock():
    config = _default_config(overstock_threshold_days=90, understock_threshold_days=3)
    pos = _make_position(on_hand=20, reserved=0, incoming_po=0)
    # coverage = 20/10 = 2 days < 3
    flag = classify_inventory(pos, 10.0, config)
    assert flag == "understock"


def test_classify_at_risk():
    config = _default_config(overstock_threshold_days=90, understock_threshold_days=3)
    pos = _make_position(on_hand=40, reserved=0, incoming_po=0, outgoing_so=0, outgoing_move=0, outgoing_mo=0)
    # coverage = 40/10 = 4 days — above 3 but below 3*2=6
    flag = classify_inventory(pos, 10.0, config)
    assert flag == "at_risk"


def test_classify_no_stock():
    config = _default_config()
    pos = _make_position(on_hand=0, reserved=0, incoming_po=0, incoming_move=0, incoming_mo=0)
    flag = classify_inventory(pos, 10.0, config)
    assert flag == "no_stock"


def test_classify_no_demand():
    config = _default_config()
    pos = _make_position(on_hand=100)
    flag = classify_inventory(pos, 0.0, config)
    assert flag == "no_demand"


def test_classify_no_data():
    config = _default_config()
    pos = _make_position(on_hand=0, reserved=0, incoming_po=0, incoming_move=0, incoming_mo=0)
    flag = classify_inventory(pos, 0.0, config)
    assert flag == "no_data"


# --- load_inventory_positions tests ---

def test_load_from_mock_parquets(tmp_dir):
    """Load inventory data from minimal mock parquets."""
    # Create stock_quant.parquet
    quant_table = pa.table({
        "_odoo_product_id": pa.array([1, 1, 2], type=pa.int64()),
        "_odoo_warehouse_id": pa.array([10, 10, 10], type=pa.int64()),
        "quantity": pa.array([50.0, 30.0, 100.0], type=pa.float64()),
        "reserved_quantity": pa.array([5.0, 0.0, 10.0], type=pa.float64()),
    })
    pq.write_table(quant_table, tmp_dir / "stock_quant.parquet")

    config = InventoryConfig(inventory_mode="analyze")
    warehouse_locations = {10: 100}  # warehouse_id -> lot_stock_id

    positions = load_inventory_positions(tmp_dir, warehouse_locations, config)

    assert (1, 10) in positions
    assert (2, 10) in positions
    # Product 1 has two quant rows that should be summed
    assert positions[(1, 10)].on_hand_qty == 80.0  # 50 + 30
    assert positions[(1, 10)].reserved_qty == 5.0
    assert positions[(2, 10)].on_hand_qty == 100.0


def test_load_missing_parquets_graceful(tmp_dir):
    """Empty directory should return empty positions without error."""
    config = InventoryConfig(inventory_mode="analyze")
    positions = load_inventory_positions(tmp_dir, {}, config)
    assert positions == {}
