"""Warehouse-scoped artifact import helpers for Mimir."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .config import MimirConfig

logger = logging.getLogger(__name__)

_MINMAX_REQUIRED_COLUMNS = {"product_id", "facility_id", "min_quantity", "max_quantity"}
_WAREHOUSE_LEAD_TIME_REQUIRED_COLUMNS = {"facility_id", "lead_time_days", "type"}


def normalize_server_base_uri(uri: str) -> str:
    """Trim artifact-specific suffixes so callers can store either a base URL or an old endpoint."""
    base_uri = uri.strip().rstrip("/")
    for suffix in (
        "/forecasts/latest",
        "/forecast/latest",
        "/rules/latest",
        "/warehouses",
    ):
        if base_uri.endswith(suffix):
            base_uri = base_uri[: -len(suffix)]
            break
    return base_uri.rstrip("/")


def load_minmax_table(
    export_dir: str | Path,
    config: MimirConfig,
    warehouse_id_lookup: dict[str, int],
) -> tuple[pa.Table, int]:
    """Load the selected warehouse's min/max table from a local bundle or a warehouse-scoped HTTP server."""
    if config.external_forecast_uri and config.selected_warehouse_id is not None:
        try:
            table = fetch_remote_artifact_table(
                config.external_forecast_uri,
                config.external_forecast_api_key,
                str(config.selected_warehouse_id),
                artifact="rules",
            )
            _validate_minmax_columns(table)
            return _filter_minmax_for_selected_warehouse(table, config.selected_warehouse_id, warehouse_id_lookup)
        except Exception:
            logger.warning("Falling back to local minmax.parquet after remote rules fetch failed", exc_info=True)

    path = Path(export_dir) / "minmax.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Required rule file not found: {path}")

    table = pq.read_table(path)
    _validate_minmax_columns(table)
    return _filter_minmax_for_selected_warehouse(table, config.selected_warehouse_id, warehouse_id_lookup)


def load_forecast_evidence_table(
    export_dir: str | Path,
    config: MimirConfig,
    warehouse_id: int,
) -> pa.Table | None:
    """Load warehouse-scoped raw forecast evidence for Excel export."""
    if config.external_forecast_uri:
        try:
            table = fetch_remote_artifact_table(
                config.external_forecast_uri,
                config.external_forecast_api_key,
                str(warehouse_id),
                artifact="forecast",
            )
            return _filter_table_for_warehouse(table, str(warehouse_id))
        except Exception:
            logger.warning(
                "Remote forecast evidence fetch failed; falling back to local demand_forecast.parquet",
                exc_info=True,
            )

    path = Path(export_dir) / "demand_forecast.parquet"
    if not path.exists():
        return None

    table = pq.read_table(path)
    return _filter_table_for_warehouse(table, str(warehouse_id))


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


def fetch_remote_artifact_table(
    base_uri: str,
    api_key: str,
    warehouse_id: str,
    *,
    artifact: str,
) -> pa.Table:
    """Download a warehouse-scoped Parquet artifact from mimir-server."""
    normalized = normalize_server_base_uri(base_uri)
    uri = f"{normalized}/warehouses/{warehouse_id}/{artifact}/latest"
    logger.info("Fetching %s artifact from %s", artifact, uri)

    req = Request(uri)
    if api_key:
        req.add_header("X-API-Key", api_key)

    with urlopen(req, timeout=60) as resp:  # noqa: S310
        payload = resp.read()

    return pq.read_table(io.BytesIO(payload))


def _validate_minmax_columns(table: pa.Table) -> None:
    missing = _MINMAX_REQUIRED_COLUMNS.difference(table.column_names)
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"minmax data is missing required columns: {missing_list}")


def _filter_minmax_for_selected_warehouse(
    table: pa.Table,
    selected_warehouse_id: int | None,
    warehouse_id_lookup: dict[str, int],
) -> tuple[pa.Table, int]:
    facilities = sorted(
        {
            str(value)
            for value in table.column("facility_id").to_pylist()
            if value is not None and str(value) != ""
        }
    )
    if not facilities:
        raise ValueError("minmax data does not contain any facility_id values")

    if selected_warehouse_id is None:
        if len(facilities) != 1:
            facility_list = ", ".join(facilities)
            raise ValueError(f"Select a warehouse before running Mimir. Available facilities: {facility_list}")
        selected_key = facilities[0]
    else:
        selected_key = str(selected_warehouse_id)
        if selected_key not in facilities:
            facility_list = ", ".join(facilities)
            raise ValueError(
                "Selected warehouse "
                f"{selected_key} is not present in the imported rules. Available facilities: {facility_list}"
            )

    warehouse_id = warehouse_id_lookup.get(selected_key)
    if warehouse_id is None:
        raise ValueError(f"Facility {selected_key} is missing from stock_warehouse.parquet")

    return _filter_table_for_warehouse(table, selected_key), warehouse_id


def _filter_table_for_warehouse(table: pa.Table, warehouse_id: str) -> pa.Table:
    """Filter a table to one warehouse using either facility_id or _odoo_warehouse_id."""
    if "facility_id" in table.column_names:
        column = table.column("facility_id")
    elif "_odoo_warehouse_id" in table.column_names:
        column = table.column("_odoo_warehouse_id")
    else:
        return table

    mask = pc.equal(pc.cast(column, pa.string()), pa.scalar(str(warehouse_id), type=pa.string()))
    return table.filter(mask)
