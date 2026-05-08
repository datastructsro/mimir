"""Runtime context required to import server-published replenishment rules."""

from __future__ import annotations

from dataclasses import dataclass, field

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
