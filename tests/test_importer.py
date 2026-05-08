"""Tests for remote artifact fetching helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from mimir.config import MimirConfig
from mimir.importer import (
    fetch_remote_empirical_lead_times_table,
    fetch_remote_forecast_evidence_table,
    fetch_remote_minmax_table,
)


def _config() -> MimirConfig:
    return MimirConfig(
        selected_warehouse_id=10,
        external_forecast_uri="https://mimir.example",
        external_forecast_api_key="123e4567-e89b-12d3-a456-426614174000",
    )


def test_fetch_remote_minmax_table_downloads_and_filters_selected_warehouse() -> None:
    remote_rules = pa.table(
        {
            "product_id": pa.array(["1", "2", "3"], type=pa.string()),
            "facility_id": pa.array(["WH10", "WH10", "WH20"], type=pa.string()),
            "min_quantity": pa.array([5.0, 3.0, 7.0], type=pa.float64()),
            "max_quantity": pa.array([10.0, 8.0, 12.0], type=pa.float64()),
        }
    )

    with patch("mimir.importer.MimirServerClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.resolve_warehouse_ref.return_value = "WH10"
        mock_client.download_rules_table.return_value = remote_rules
        mock_client_cls.return_value = mock_client

        result = fetch_remote_minmax_table(_config(), selected_warehouse_code="WH10")

    assert result.to_pylist() == remote_rules.slice(0, 2).to_pylist()
    mock_client.download_rules_table.assert_called_once_with("WH10")


def test_fetch_remote_minmax_table_does_not_fallback_when_remote_server_fails() -> None:
    with patch("mimir.importer.MimirServerClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.resolve_warehouse_ref.return_value = "WH10"
        mock_client.download_rules_table.side_effect = ValueError("Remote rules are unavailable")
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError, match="Remote rules are unavailable"):
            fetch_remote_minmax_table(_config(), selected_warehouse_code="WH10")


def test_fetch_remote_forecast_evidence_table_is_optional() -> None:
    with patch("mimir.importer.MimirServerClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.resolve_warehouse_ref.return_value = "WH10"
        mock_client.download_forecast_table.side_effect = ValueError("Remote forecast missing")
        mock_client_cls.return_value = mock_client

        result = fetch_remote_forecast_evidence_table(_config(), selected_warehouse_code="WH10")

    assert result is None


def test_fetch_remote_empirical_lead_times_table_is_optional() -> None:
    with patch("mimir.importer.MimirServerClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.download_lead_times_table.side_effect = ValueError("No lead times published")
        mock_client_cls.return_value = mock_client

        result = fetch_remote_empirical_lead_times_table(_config())

    assert result is None
