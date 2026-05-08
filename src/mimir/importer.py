"""Remote artifact fetch helpers for the server-only Mimir import flow."""

from __future__ import annotations

import logging

import pyarrow as pa
import pyarrow.compute as pc

from .config import MimirConfig
from .server_client import MimirServerClient, warehouse_aliases

logger = logging.getLogger(__name__)

_MINMAX_REQUIRED_COLUMNS = {"product_id", "facility_id", "min_quantity", "max_quantity"}


def fetch_remote_minmax_table(
    config: MimirConfig,
    *,
    selected_warehouse_code: str | None = None,
) -> pa.Table:
    """Download and filter the selected warehouse's rules parquet from mimir-server."""
    warehouse_id = _selected_warehouse_id(config)
    client = _build_remote_client(config)
    remote_warehouse_ref = client.resolve_warehouse_ref(
        selected_warehouse_id=warehouse_id,
        warehouse_code=selected_warehouse_code,
    )
    table = client.download_rules_table(remote_warehouse_ref)
    _validate_minmax_columns(table)
    return filter_minmax_for_selected_warehouse(table, warehouse_id, selected_warehouse_code)


def fetch_remote_forecast_evidence_table(
    config: MimirConfig,
    *,
    selected_warehouse_code: str | None = None,
) -> pa.Table | None:
    """Download forecast evidence parquet for the selected warehouse when available."""
    warehouse_id = _selected_warehouse_id(config)
    client = _build_remote_client(config)
    remote_warehouse_ref = client.resolve_warehouse_ref(
        selected_warehouse_id=warehouse_id,
        warehouse_code=selected_warehouse_code,
    )
    try:
        table = client.download_forecast_table(remote_warehouse_ref)
    except ValueError:
        logger.warning("Remote forecast evidence fetch failed; continuing without forecast evidence", exc_info=True)
        return None
    return filter_table_for_warehouse(table, warehouse_aliases(warehouse_id, selected_warehouse_code))


def fetch_remote_empirical_lead_times_table(config: MimirConfig) -> pa.Table | None:
    """Download empirical lead times parquet when the server publishes it."""
    client = _build_remote_client(config)
    try:
        return client.download_lead_times_table()
    except ValueError:
        logger.info("Remote empirical lead times are unavailable; continuing without them", exc_info=True)
        return None


def filter_minmax_for_selected_warehouse(
    table: pa.Table,
    selected_warehouse_id: int,
    selected_warehouse_code: str | None = None,
) -> pa.Table:
    """Filter a rules table to the requested warehouse using ID and code aliases."""
    _validate_minmax_columns(table)
    facilities = _table_warehouse_values(table)
    if not facilities:
        raise ValueError("minmax data does not contain any facility_id values")

    aliases = warehouse_aliases(selected_warehouse_id, selected_warehouse_code)
    selected_key = next((alias for alias in aliases if alias in facilities), None)
    if selected_key is None:
        facility_list = ", ".join(facilities)
        warehouse_suffix = f" ({selected_warehouse_code})" if selected_warehouse_code else ""
        raise ValueError(
            "Selected warehouse "
            f"{selected_warehouse_id}{warehouse_suffix} is not present in the imported rules. "
            f"Available facilities: {facility_list}"
        )

    return filter_table_for_warehouse(table, [selected_key])


def filter_table_for_warehouse(table: pa.Table, warehouse_keys: str | list[str]) -> pa.Table:
    """Filter a table to one warehouse using either facility_id or _odoo_warehouse_id."""
    if "facility_id" in table.column_names:
        column = table.column("facility_id")
    elif "_odoo_warehouse_id" in table.column_names:
        column = table.column("_odoo_warehouse_id")
    else:
        return table

    if isinstance(warehouse_keys, str):
        normalized_keys = [warehouse_keys]
    else:
        normalized_keys = [str(key) for key in warehouse_keys if str(key)]

    if not normalized_keys:
        return table

    string_column = pc.cast(column, pa.string())
    if len(normalized_keys) == 1:
        mask = pc.equal(string_column, pa.scalar(normalized_keys[0], type=pa.string()))
    else:
        mask = pc.is_in(string_column, value_set=pa.array(normalized_keys, type=pa.string()))

    return table.filter(mask)


def _build_remote_client(config: MimirConfig) -> MimirServerClient:
    return MimirServerClient(config.external_forecast_uri, config.external_forecast_api_key)


def _selected_warehouse_id(config: MimirConfig) -> int:
    if config.selected_warehouse_id is None:
        raise ValueError("Select a warehouse before running Mimir.")
    return config.selected_warehouse_id


def _validate_minmax_columns(table: pa.Table) -> None:
    missing = _MINMAX_REQUIRED_COLUMNS.difference(table.column_names)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"minmax data is missing required columns: {missing_list}")


def _table_warehouse_values(table: pa.Table) -> list[str]:
    if "facility_id" in table.column_names:
        values = table.column("facility_id").to_pylist()
    elif "_odoo_warehouse_id" in table.column_names:
        values = table.column("_odoo_warehouse_id").to_pylist()
    else:
        return []

    return sorted({str(value) for value in values if value is not None and str(value) != ""})
