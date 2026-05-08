"""Warehouse-scoped artifact import helpers for Mimir."""

from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .config import MimirConfig
from .server_client import MimirServerClient, warehouse_aliases

logger = logging.getLogger(__name__)

_MINMAX_REQUIRED_COLUMNS = {"product_id", "facility_id", "min_quantity", "max_quantity"}
_WAREHOUSE_LEAD_TIME_REQUIRED_COLUMNS = {"facility_id", "lead_time_days", "type"}


def load_minmax_table(
    export_dir: str | Path,
    config: MimirConfig,
    warehouse_alias_lookup: dict[str, int],
    warehouse_codes: dict[int, str],
) -> tuple[pa.Table, int]:
    """Load the selected warehouse's min/max table from a local bundle or a warehouse-scoped HTTP server."""
    selected_warehouse_code = (
        warehouse_codes.get(config.selected_warehouse_id) if config.selected_warehouse_id is not None else None
    )
    if config.external_forecast_uri and config.selected_warehouse_id is not None:
        client = MimirServerClient(config.external_forecast_uri, config.external_forecast_api_key)
        remote_warehouse_ref = client.resolve_warehouse_ref(
            selected_warehouse_id=config.selected_warehouse_id,
            warehouse_code=selected_warehouse_code,
        )
        table = client.download_rules_table(remote_warehouse_ref)
        _validate_minmax_columns(table)
        return _filter_minmax_for_selected_warehouse(
            table,
            config.selected_warehouse_id,
            warehouse_alias_lookup,
            selected_warehouse_code,
        )

    path = Path(export_dir) / "minmax.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Required rule file not found: {path}")

    table = pq.read_table(path)
    _validate_minmax_columns(table)
    return _filter_minmax_for_selected_warehouse(
        table,
        config.selected_warehouse_id,
        warehouse_alias_lookup,
        selected_warehouse_code,
    )


def load_forecast_evidence_table(
    export_dir: str | Path,
    config: MimirConfig,
    warehouse_id: int,
    warehouse_code: str,
) -> pa.Table | None:
    """Load warehouse-scoped raw forecast evidence for Excel export."""
    aliases = warehouse_aliases(warehouse_id, warehouse_code)
    if config.external_forecast_uri:
        try:
            client = MimirServerClient(config.external_forecast_uri, config.external_forecast_api_key)
            remote_warehouse_ref = client.resolve_warehouse_ref(
                selected_warehouse_id=warehouse_id,
                warehouse_code=warehouse_code,
            )
            table = client.download_forecast_table(remote_warehouse_ref)
            return _filter_table_for_warehouse(table, aliases)
        except Exception:
            logger.warning(
                "Remote forecast evidence fetch failed; falling back to local demand_forecast.parquet",
                exc_info=True,
            )

    path = Path(export_dir) / "demand_forecast.parquet"
    if not path.exists():
        return None

    table = pq.read_table(path)
    return _filter_table_for_warehouse(table, aliases)


def load_warehouse_lead_times(export_dir: str | Path) -> dict[str, int]:
    """Load per-warehouse lead times from input_lead_times.parquet if present."""
    path = Path(export_dir) / "input_lead_times.parquet"
    if not path.exists():
        return {}

    table = pq.read_table(path)
    missing = _WAREHOUSE_LEAD_TIME_REQUIRED_COLUMNS.difference(table.column_names)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"input_lead_times.parquet is missing required columns: {missing_list}")

    lead_times: dict[str, int] = {}
    for row in table.to_pylist():
        if row.get("type") != "warehouse_lead_time":
            continue
        facility_id = row.get("facility_id")
        lead_time_days = row.get("lead_time_days")
        if facility_id is None or lead_time_days is None:
            continue

        lead_time = int(lead_time_days)
        if lead_time > 0:
            lead_times[str(facility_id)] = lead_time

    return lead_times


def _validate_minmax_columns(table: pa.Table) -> None:
    missing = _MINMAX_REQUIRED_COLUMNS.difference(table.column_names)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"minmax data is missing required columns: {missing_list}")


def _filter_minmax_for_selected_warehouse(
    table: pa.Table,
    selected_warehouse_id: int | None,
    warehouse_alias_lookup: dict[str, int],
    selected_warehouse_code: str | None,
) -> tuple[pa.Table, int]:
    facilities = _table_warehouse_values(table)
    if not facilities:
        raise ValueError("minmax data does not contain any facility_id values")

    if selected_warehouse_id is None:
        if len(facilities) != 1:
            facility_list = ", ".join(facilities)
            raise ValueError(f"Select a warehouse before running Mimir. Available facilities: {facility_list}")
        selected_key = facilities[0]
    else:
        matching_aliases = warehouse_aliases(selected_warehouse_id, selected_warehouse_code)
        selected_key = next((alias for alias in matching_aliases if alias in facilities), None)
        if selected_key is None:
            facility_list = ", ".join(facilities)
            warehouse_suffix = f" ({selected_warehouse_code})" if selected_warehouse_code else ""
            raise ValueError(
                "Selected warehouse "
                f"{selected_warehouse_id}{warehouse_suffix} is not present in the imported rules. "
                f"Available facilities: {facility_list}"
            )

    warehouse_id = warehouse_alias_lookup.get(selected_key)
    if warehouse_id is None:
        raise ValueError(f"Facility {selected_key} is missing from stock_warehouse.parquet")

    return _filter_table_for_warehouse(table, [selected_key]), warehouse_id


def _table_warehouse_values(table: pa.Table) -> list[str]:
    if "facility_id" in table.column_names:
        values = table.column("facility_id").to_pylist()
    elif "_odoo_warehouse_id" in table.column_names:
        values = table.column("_odoo_warehouse_id").to_pylist()
    else:
        return []

    return sorted({str(value) for value in values if value is not None and str(value) != ""})


def _filter_table_for_warehouse(table: pa.Table, warehouse_keys: str | list[str]) -> pa.Table:
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
