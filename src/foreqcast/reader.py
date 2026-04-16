"""Read parqcast parquet exports as input.

Reads the specific parquet files produced by parqcast collectors:
  - sale_order_line.parquet: demand signal (product, qty, date, warehouse)
  - product.parquet: product master data (names, categories)
  - product_supplierinfo.parquet: vendor lead times
  - stock_warehouse.parquet: warehouse definitions (lot_stock_id for orderpoints)
  - orderpoint.parquet: existing orderpoints (to decide create vs update)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq


@dataclass
class ParqcastData:
    """Container for all parquet tables needed by the forecaster."""

    sale_order_lines: object  # pa.Table
    products: object  # pa.Table
    supplier_info: object | None  # pa.Table or None if file missing
    warehouses: object  # pa.Table
    orderpoints: object | None  # pa.Table or None if file missing


def read_parqcast_export(export_dir: str | Path) -> ParqcastData:
    """Read parqcast export directory and return all needed tables.

    Required files:
      - sale_order_line.parquet
      - product.parquet
      - stock_warehouse.parquet

    Optional files (gracefully handled if missing):
      - product_supplierinfo.parquet
      - orderpoint.parquet
    """
    d = Path(export_dir)

    # Required files — raise if missing
    sol_path = d / "sale_order_line.parquet"
    product_path = d / "product.parquet"
    warehouse_path = d / "stock_warehouse.parquet"

    for path in [sol_path, product_path, warehouse_path]:
        if not path.exists():
            raise FileNotFoundError(f"Required parquet file not found: {path}")

    sale_order_lines = pq.read_table(
        sol_path,
        columns=[
            "_odoo_product_id",
            "product_name",
            "product_uom_qty",
            "date_order",
            "_odoo_warehouse_id",
            "warehouse_code",
            "so_state",
        ],
    )

    products = pq.read_table(
        product_path,
        columns=[
            "_odoo_product_id",
            "name",
            "default_code",
            "_odoo_categ_id",
        ],
    )

    warehouses = pq.read_table(
        warehouse_path,
        columns=[
            "_odoo_warehouse_id",
            "code",
            "_odoo_lot_stock_id",
        ],
    )

    # Optional files
    supplier_info = None
    supinfo_path = d / "product_supplierinfo.parquet"
    if supinfo_path.exists():
        schema = pq.read_schema(supinfo_path)
        tmpl_col = "_odoo_product_tmpl_id" if "_odoo_product_tmpl_id" in schema.names else "_odoo_template_id"
        supplier_info = pq.read_table(
            supinfo_path,
            columns=[
                "_odoo_product_id",
                tmpl_col,
                "delay",
            ],
        )
        if tmpl_col != "_odoo_product_tmpl_id":
            supplier_info = supplier_info.rename_columns(
                [c if c != tmpl_col else "_odoo_product_tmpl_id" for c in supplier_info.column_names]
            )

    orderpoints = None
    op_path = d / "orderpoint.parquet"
    if op_path.exists():
        orderpoints = pq.read_table(
            op_path,
            columns=[
                "_odoo_orderpoint_id",
                "_odoo_product_id",
                "_odoo_warehouse_id",
                "product_min_qty",
                "product_max_qty",
                "active",
            ],
        )

    return ParqcastData(
        sale_order_lines=sale_order_lines,
        products=products,
        supplier_info=supplier_info,
        warehouses=warehouses,
        orderpoints=orderpoints,
    )
