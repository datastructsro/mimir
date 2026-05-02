"""Read parqcast parquet exports as input.

Reads the specific parquet files produced by parqcast collectors:
  - sale_order_line.parquet: demand signal (product, qty, date, warehouse)
  - product.parquet: product master data (names, categories)
  - product_supplierinfo.parquet: vendor lead times
  - stock_warehouse.parquet: warehouse definitions (lot_stock_id for orderpoints)
  - orderpoint.parquet: existing orderpoints (to decide create vs update)
  - manifest.json: temporal metadata (run_uuid, started_at, finished_at)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


@dataclass
class ManifestMetadata:
    """Temporal metadata parsed from a parqcast manifest.json."""

    run_uuid: str
    started_at: datetime | None
    finished_at: datetime | None


@dataclass
class ParqcastData:
    """Container for all parquet tables needed by the forecaster."""

    sale_order_lines: pa.Table
    products: pa.Table
    supplier_info: pa.Table | None  # None if file missing
    warehouses: pa.Table
    orderpoints: pa.Table | None  # None if file missing
    manifest: ManifestMetadata | None = None  # None if manifest.json missing


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

    # Manifest metadata (temporal tracking)
    manifest = _read_manifest(d)

    return ParqcastData(
        sale_order_lines=sale_order_lines,
        products=products,
        supplier_info=supplier_info,
        warehouses=warehouses,
        orderpoints=orderpoints,
        manifest=manifest,
    )


def _read_manifest(directory: Path) -> ManifestMetadata | None:
    """Parse manifest.json for temporal metadata.

    Returns None gracefully if the file is missing (backward compat
    with older parqcast exports that don't include a manifest).
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        logger.debug("No manifest.json found in %s (pre-temporal export)", directory)
        return None

    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse manifest.json: %s", e)
        return None

    run_uuid = data.get("run_uuid") or data.get("run_uuid", "")
    if not run_uuid:
        # Older manifests may lack run_uuid — try extracting from filename or skip
        logger.debug("manifest.json missing run_uuid field")
        return None

    started_at = _parse_iso(data.get("started_at"))
    finished_at = _parse_iso(data.get("finished_at"))

    logger.info(
        "Loaded manifest: run_uuid=%s, started=%s, finished=%s",
        run_uuid, started_at, finished_at,
    )
    return ManifestMetadata(
        run_uuid=run_uuid,
        started_at=started_at,
        finished_at=finished_at,
    )


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 datetime string, returning None on failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

