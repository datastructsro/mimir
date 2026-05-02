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

    products: pa.Table
    supplier_info: pa.Table | None  # None if file missing
    warehouses: pa.Table
    orderpoints: pa.Table | None  # None if file missing
    manifest: ManifestMetadata | None = None  # None if manifest.json missing


def read_parqcast_export(export_dir: str | Path) -> ParqcastData:
    """Read parqcast export directory and return all needed tables.

    Required files:
      - product.parquet
      - stock_warehouse.parquet

    Optional files (gracefully handled if missing):
      - product_supplierinfo.parquet
      - orderpoint.parquet
    """
    d = Path(export_dir)

    # Load required core tables
    tables = {}
    req_files = {
        "products": (d / "product.parquet", ["_odoo_product_id", "name", "default_code", "_odoo_categ_id"]),
        "warehouses": (d / "stock_warehouse.parquet", ["_odoo_warehouse_id", "code", "_odoo_lot_stock_id"]),
    }

    for key, (path, cols) in req_files.items():
        if not path.exists():
            raise FileNotFoundError(f"Required parquet file not found: {path}")
        tables[key] = pq.read_table(path, columns=cols)

    # Optional files
    supinfo_path = d / "product_supplierinfo.parquet"
    if supinfo_path.exists():
        schema = pq.read_schema(supinfo_path)
        tmpl_col = "_odoo_product_tmpl_id" if "_odoo_product_tmpl_id" in schema.names else "_odoo_template_id"
        table = pq.read_table(
            supinfo_path,
            columns=[
                "_odoo_product_id",
                tmpl_col,
                "delay",
            ],
        )
        if tmpl_col != "_odoo_product_tmpl_id":
            table = table.rename_columns(
                [c if c != tmpl_col else "_odoo_product_tmpl_id" for c in table.column_names]
            )
        tables["supplier_info"] = table

    op_path = d / "orderpoint.parquet"
    if op_path.exists():
        tables["orderpoints"] = pq.read_table(
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
        products=tables["products"],
        warehouses=tables["warehouses"],
        supplier_info=tables.get("supplier_info"),
        orderpoints=tables.get("orderpoints"),
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

