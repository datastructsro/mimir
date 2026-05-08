"""Generate test parquet data for mimir pipeline testing.

Creates a realistic set of parqcast-style parquet exports with:
- 50 products across 2 warehouses
- 90 days of sale order lines (various demand patterns)
- Stock quants (on-hand inventory)
- Stock moves, purchase orders
- Supplier info, orderpoints (empty)
"""

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

OUTPUT_DIR = Path(__file__).parent.parent / "test_data"
random.seed(42)

N_PRODUCTS = 50
WAREHOUSES = [
    (1, "WH", 8, "Main Warehouse"),
    (2, "W2", 15, "Warehouse 2"),
]
N_DAYS = 90
BASE_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Product catalog
PRODUCTS = []
CATEGORIES = {1: "Beverages", 2: "Snacks", 3: "Dairy", 4: "Frozen"}
for i in range(1, N_PRODUCTS + 1):
    cat_id = (i % 4) + 1
    PRODUCTS.append(
        {
            "id": i,
            "name": f"Product {i:03d} - {CATEGORIES[cat_id]}",
            "code": f"P{i:04d}",
            "categ_id": cat_id,
        }
    )


def _demand_pattern(product_id: int, day: int) -> float:
    """Generate demand for a product on a given day. Various patterns."""
    base = (product_id % 10 + 1) * 5  # 5 to 50 base demand

    if product_id <= 10:
        # Steady demand
        return base + random.gauss(0, base * 0.1)
    elif product_id <= 20:
        # Growing trend
        return base + day * 0.5 + random.gauss(0, base * 0.15)
    elif product_id <= 30:
        # Intermittent (many zeros)
        return base * 2 if random.random() < 0.2 else 0
    elif product_id <= 40:
        # Seasonal (weekly pattern)
        weekday = day % 7
        seasonal = 1.5 if weekday < 5 else 0.3  # weekdays vs weekends
        return base * seasonal + random.gauss(0, base * 0.2)
    else:
        # Lumpy (sporadic large orders)
        if random.random() < 0.1:
            return base * random.uniform(5, 15)
        elif random.random() < 0.3:
            return base + random.gauss(0, base * 0.1)
        return 0


def generate_sale_order_lines():
    pids, wids, qtys, dates, states, pnames, wcodes, qty_delivered = [], [], [], [], [], [], [], []

    for p in PRODUCTS:
        for wh_id, wh_code, _, _ in WAREHOUSES:
            for day in range(N_DAYS):
                qty = _demand_pattern(p["id"], day)
                if qty <= 0:
                    continue
                qty = round(max(qty, 0), 2)
                dt = BASE_DATE + timedelta(days=day, hours=random.randint(8, 18))

                pids.append(p["id"])
                wids.append(wh_id)
                qtys.append(qty)
                dates.append(dt)
                states.append("sale")
                pnames.append(p["name"])
                wcodes.append(wh_code)
                qty_delivered.append(qty)  # all delivered for historical data

    table = pa.table(
        {
            "_odoo_product_id": pa.array(pids, type=pa.int64()),
            "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
            "product_uom_qty": pa.array(qtys, type=pa.float64()),
            "date_order": pa.array(dates, type=pa.timestamp("us", tz="UTC")),
            "so_state": pa.array(states, type=pa.string()),
            "product_name": pa.array(pnames, type=pa.string()),
            "warehouse_code": pa.array(wcodes, type=pa.string()),
            "qty_delivered": pa.array(qty_delivered, type=pa.float64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "sale_order_line.parquet", compression="snappy")
    print(f"  sale_order_line.parquet: {table.num_rows} rows")


def generate_products():
    table = pa.table(
        {
            "_odoo_product_id": pa.array([p["id"] for p in PRODUCTS], type=pa.int64()),
            "name": pa.array([p["name"] for p in PRODUCTS]),
            "default_code": pa.array([p["code"] for p in PRODUCTS]),
            "_odoo_categ_id": pa.array([p["categ_id"] for p in PRODUCTS], type=pa.int64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "product.parquet", compression="snappy")
    print(f"  product.parquet: {table.num_rows} rows")


def generate_warehouses():
    table = pa.table(
        {
            "_odoo_warehouse_id": pa.array([w[0] for w in WAREHOUSES], type=pa.int64()),
            "code": pa.array([w[1] for w in WAREHOUSES]),
            "_odoo_lot_stock_id": pa.array([w[2] for w in WAREHOUSES], type=pa.int64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "stock_warehouse.parquet", compression="snappy")
    print(f"  stock_warehouse.parquet: {table.num_rows} rows")


def generate_stock_quants():
    """On-hand inventory: some products overstocked, some understocked."""
    pids, wids, qtys, reserved = [], [], [], []
    for p in PRODUCTS:
        for wh_id, _, _, _ in WAREHOUSES:
            if p["id"] <= 5:
                # Heavily overstocked
                on_hand = random.uniform(5000, 10000)
            elif p["id"] <= 15:
                # Normal stock
                on_hand = random.uniform(100, 500)
            elif p["id"] <= 25:
                # Low stock
                on_hand = random.uniform(5, 30)
            elif p["id"] <= 35:
                # Intermittent — some stock
                on_hand = random.uniform(0, 50)
            else:
                # Out of stock
                on_hand = 0

            pids.append(p["id"])
            wids.append(wh_id)
            qtys.append(round(on_hand, 2))
            reserved.append(round(on_hand * random.uniform(0, 0.2), 2))

    table = pa.table(
        {
            "_odoo_product_id": pa.array(pids, type=pa.int64()),
            "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
            "quantity": pa.array(qtys, type=pa.float64()),
            "reserved_quantity": pa.array(reserved, type=pa.float64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "stock_quant.parquet", compression="snappy")
    print(f"  stock_quant.parquet: {table.num_rows} rows")


def generate_supplier_info():
    """Empty supplier info — all products use default lead time."""
    table = pa.table(
        {
            "_odoo_product_id": pa.array([], type=pa.int64()),
            "_odoo_product_tmpl_id": pa.array([], type=pa.int64()),
            "delay": pa.array([], type=pa.int64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "product_supplierinfo.parquet", compression="snappy")
    print(f"  product_supplierinfo.parquet: {table.num_rows} rows")


def generate_orderpoints():
    """Empty orderpoints — mimir will create all new."""
    table = pa.table(
        {
            "_odoo_orderpoint_id": pa.array([], type=pa.int64()),
            "_odoo_product_id": pa.array([], type=pa.int64()),
            "_odoo_warehouse_id": pa.array([], type=pa.int64()),
            "product_min_qty": pa.array([], type=pa.float64()),
            "product_max_qty": pa.array([], type=pa.float64()),
            "active": pa.array([], type=pa.bool_()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "orderpoint.parquet", compression="snappy")
    print(f"  orderpoint.parquet: {table.num_rows} rows")


def generate_stock_moves():
    """Some open stock moves for inventory analysis."""
    pids, wids, qtys, states_list, src_locs, dst_locs = [], [], [], [], [], []
    for p in PRODUCTS[:20]:
        for wh_id, _, lot_stock_id, _ in WAREHOUSES:
            if random.random() < 0.3:
                # Incoming move
                pids.append(p["id"])
                wids.append(wh_id)
                qtys.append(round(random.uniform(10, 100), 2))
                states_list.append("assigned")
                src_locs.append(4)  # supplier location
                dst_locs.append(lot_stock_id)

    table = pa.table(
        {
            "_odoo_product_id": pa.array(pids, type=pa.int64()),
            "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
            "product_uom_qty": pa.array(qtys, type=pa.float64()),
            "state": pa.array(states_list, type=pa.string()),
            "_odoo_location_id": pa.array(src_locs, type=pa.int64()),
            "_odoo_location_dest_id": pa.array(dst_locs, type=pa.int64()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "stock_move.parquet", compression="snappy")
    print(f"  stock_move.parquet: {table.num_rows} rows")


def generate_purchase_order_lines():
    """Some open PO lines for incoming supply."""
    pids, wids, ordered, received, po_states = [], [], [], [], []
    for p in PRODUCTS[:15]:
        for wh_id, _, _, _ in WAREHOUSES:
            if random.random() < 0.4:
                qty = round(random.uniform(50, 200), 2)
                pids.append(p["id"])
                wids.append(wh_id)
                ordered.append(qty)
                received.append(round(qty * random.uniform(0, 0.5), 2))
                po_states.append("purchase")

    table = pa.table(
        {
            "_odoo_product_id": pa.array(pids, type=pa.int64()),
            "_odoo_warehouse_id": pa.array(wids, type=pa.int64()),
            "product_qty": pa.array(ordered, type=pa.float64()),
            "qty_received": pa.array(received, type=pa.float64()),
            "po_state": pa.array(po_states, type=pa.string()),
        }
    )
    pq.write_table(table, OUTPUT_DIR / "purchase_order_line.parquet", compression="snappy")
    print(f"  purchase_order_line.parquet: {table.num_rows} rows")


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating test data in {OUTPUT_DIR}/")
    generate_sale_order_lines()
    generate_products()
    generate_warehouses()
    generate_stock_quants()
    generate_supplier_info()
    generate_orderpoints()
    generate_stock_moves()
    generate_purchase_order_lines()
    print("Done!")
