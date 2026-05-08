"""Tests for the warehouse-scoped mimir-server HTTP client."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimir.server_client import MimirServerClient, normalize_server_base_uri, warehouse_aliases


def _parquet_bytes(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def _response_with_bytes(payload: bytes | str) -> MagicMock:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    response = MagicMock()
    response.read.return_value = data
    response.__enter__.return_value = response
    return response


def test_normalize_server_base_uri_trims_known_artifact_routes() -> None:
    assert normalize_server_base_uri("https://mimir.example") == "https://mimir.example"
    assert normalize_server_base_uri("https://mimir.example/forecasts/latest") == "https://mimir.example"
    assert normalize_server_base_uri("https://mimir.example/warehouses/WH10/rules/latest") == "https://mimir.example"
    assert normalize_server_base_uri("https://mimir.example/api/lead-times/latest") == "https://mimir.example/api"


def test_warehouse_aliases_include_numeric_id_and_code() -> None:
    assert warehouse_aliases(10, "WH10") == ["10", "WH10"]
    assert warehouse_aliases(10, None) == ["10"]
    assert warehouse_aliases(None, "WH10") == ["WH10"]


@patch("mimir.server_client.urlopen")
def test_preflight_resolves_selected_warehouse_code_alias(mock_urlopen: MagicMock) -> None:
    responses = {
        "https://mimir.example/health": _response_with_bytes(json.dumps({"status": "ok"})),
        "https://mimir.example/warehouses": _response_with_bytes(json.dumps({"warehouses": ["WH10", "WH20"]})),
    }
    mock_urlopen.side_effect = lambda req, timeout: responses[req.full_url]

    client = MimirServerClient("https://mimir.example", "123e4567-e89b-12d3-a456-426614174000")
    result = client.preflight_warehouse(selected_warehouse_id=10, warehouse_code="WH10")

    assert result.health_status == "ok"
    assert result.available_warehouses == ["WH10", "WH20"]
    assert result.selected_warehouse_ref == "WH10"


@patch("mimir.server_client.urlopen")
def test_preflight_raises_when_remote_server_has_no_artifacts(mock_urlopen: MagicMock) -> None:
    responses = {
        "https://mimir.example/health": _response_with_bytes(json.dumps({"status": "ok"})),
        "https://mimir.example/warehouses": _response_with_bytes(json.dumps({"warehouses": []})),
    }
    mock_urlopen.side_effect = lambda req, timeout: responses[req.full_url]

    client = MimirServerClient("https://mimir.example", "123e4567-e89b-12d3-a456-426614174000")

    with pytest.raises(ValueError, match="no published warehouse-scoped artifacts"):
        client.preflight_warehouse(selected_warehouse_id=10, warehouse_code="WH10")


@patch("mimir.server_client.urlopen")
def test_download_rules_table_uses_api_key_header_and_expected_route(mock_urlopen: MagicMock) -> None:
    table = pa.table(
        {
            "product_id": pa.array(["1"], type=pa.string()),
            "facility_id": pa.array(["WH10"], type=pa.string()),
            "min_quantity": pa.array([5.0], type=pa.float64()),
            "max_quantity": pa.array([10.0], type=pa.float64()),
        }
    )
    mock_urlopen.return_value = _response_with_bytes(_parquet_bytes(table))

    client = MimirServerClient("https://mimir.example", "123e4567-e89b-12d3-a456-426614174000")
    result = client.download_rules_table("WH10")

    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://mimir.example/warehouses/WH10/rules/latest"
    assert req.headers.get("X-api-key") == "123e4567-e89b-12d3-a456-426614174000"
    assert result.to_pylist() == table.to_pylist()


@patch("mimir.server_client.urlopen")
def test_remote_http_errors_include_server_detail(mock_urlopen: MagicMock) -> None:
    error = HTTPError(
        url="https://mimir.example/warehouses/WH10/rules/latest",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(b'{"detail":"No rules found for warehouse WH10"}'),
    )
    mock_urlopen.side_effect = error

    client = MimirServerClient("https://mimir.example", "123e4567-e89b-12d3-a456-426614174000")

    with pytest.raises(ValueError, match="No rules found for warehouse WH10"):
        client.download_rules_table("WH10")
