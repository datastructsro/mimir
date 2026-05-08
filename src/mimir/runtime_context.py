"""Runtime context required to import server-published replenishment rules."""

from __future__ import annotations

import xmlrpc.client
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa


@dataclass(frozen=True)
class PipelineRuntimeContext:
    """Minimal Odoo metadata needed to normalize server artifacts."""

    warehouse_code: str
    location_id: int
    product_names: dict[int, str]
    existing_orderpoint_keys: set[tuple[int, int]] = field(default_factory=set)
    source_run_uuid: str | None = None

    @property
    def product_id_lookup(self) -> dict[str, int]:
        return {str(product_id): product_id for product_id in self.product_names}


def extract_rule_product_ids(minmax_table: pa.Table) -> list[int]:
    """Extract and validate unique Odoo product IDs from a rules parquet table."""
    if "product_id" not in minmax_table.column_names:
        raise ValueError("minmax data is missing required columns: product_id")

    product_ids: set[int] = set()
    for raw_value in minmax_table.column("product_id").to_pylist():
        if raw_value is None:
            continue
        try:
            product_ids.add(int(str(raw_value).strip()))
        except ValueError as exc:
            raise ValueError(f"Imported rule references invalid product_id '{raw_value}'") from exc

    return sorted(product_ids)


def build_runtime_context_from_xmlrpc(
    *,
    odoo_url: str,
    odoo_db: str,
    odoo_user: str,
    odoo_password: str,
    selected_warehouse_id: int,
    minmax_table: pa.Table,
) -> PipelineRuntimeContext:
    """Fetch minimal warehouse, product, and orderpoint metadata from Odoo via XML-RPC."""
    common = xmlrpc.client.ServerProxy(f"{odoo_url.rstrip('/')}/xmlrpc/2/common")
    uid = common.authenticate(odoo_db, odoo_user, odoo_password, {})
    if not uid or not isinstance(uid, int):
        raise RuntimeError(f"Authentication failed for {odoo_user}@{odoo_db}")

    models = xmlrpc.client.ServerProxy(f"{odoo_url.rstrip('/')}/xmlrpc/2/object")
    product_ids = extract_rule_product_ids(minmax_table)

    warehouse_records = _execute_kw(
        models,
        odoo_db,
        uid,
        odoo_password,
        "stock.warehouse",
        "read",
        [[selected_warehouse_id]],
        {"fields": ["code", "lot_stock_id"]},
    )
    if not warehouse_records:
        raise ValueError(f"Warehouse {selected_warehouse_id} was not found in Odoo")

    warehouse = warehouse_records[0]
    warehouse_code = warehouse.get("code") or ""
    lot_stock = warehouse.get("lot_stock_id")
    if not isinstance(lot_stock, (list, tuple)) or not lot_stock:
        raise ValueError(f"Warehouse {selected_warehouse_id} is missing a lot stock location in Odoo")

    product_records = _execute_kw(
        models,
        odoo_db,
        uid,
        odoo_password,
        "product.product",
        "search_read",
        [[["id", "in", product_ids]]],
        {"fields": ["name"], "limit": False},
    )
    product_names = {
        int(record["id"]): str(record.get("name") or record["id"])
        for record in product_records
        if isinstance(record.get("id"), int)
    }
    missing_product_ids = [product_id for product_id in product_ids if product_id not in product_names]
    if missing_product_ids:
        missing_text = ", ".join(str(product_id) for product_id in missing_product_ids)
        raise ValueError(f"Imported rules reference unknown Odoo product IDs: {missing_text}")

    existing_orderpoints = _execute_kw(
        models,
        odoo_db,
        uid,
        odoo_password,
        "stock.warehouse.orderpoint",
        "search_read",
        [[["warehouse_id", "=", selected_warehouse_id], ["product_id", "in", product_ids]]],
        {"fields": ["product_id", "warehouse_id"], "limit": False},
    )
    existing_orderpoint_keys: set[tuple[int, int]] = set()
    for orderpoint in existing_orderpoints:
        product = orderpoint.get("product_id")
        warehouse_value = orderpoint.get("warehouse_id")
        if not isinstance(product, (list, tuple)) or not product:
            continue
        if not isinstance(warehouse_value, (list, tuple)) or not warehouse_value:
            continue
        existing_orderpoint_keys.add((int(product[0]), int(warehouse_value[0])))

    return PipelineRuntimeContext(
        warehouse_code=str(warehouse_code),
        location_id=int(lot_stock[0]),
        product_names=product_names,
        existing_orderpoint_keys=existing_orderpoint_keys,
    )


def _execute_kw(
    models: xmlrpc.client.ServerProxy,
    db: str,
    uid: int,
    password: str,
    model: str,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = models.execute_kw(db, uid, password, model, method, args, kwargs or {})
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected XML-RPC response for {model}.{method}")
    return result
