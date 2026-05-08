"""Tests for building pipeline runtime context from Odoo metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from mimir.runtime_context import build_runtime_context_from_xmlrpc, extract_rule_product_ids


def _rules_table() -> pa.Table:
    return pa.table(
        {
            "product_id": pa.array(["1", "2"], type=pa.string()),
            "facility_id": pa.array(["WH10", "WH10"], type=pa.string()),
            "min_quantity": pa.array([5.0, 3.0], type=pa.float64()),
            "max_quantity": pa.array([10.0, 8.0], type=pa.float64()),
        }
    )


def test_extract_rule_product_ids_validates_ids() -> None:
    table = pa.table(
        {
            "product_id": pa.array(["1", "bad"], type=pa.string()),
            "facility_id": pa.array(["WH10", "WH10"], type=pa.string()),
            "min_quantity": pa.array([1.0, 2.0], type=pa.float64()),
            "max_quantity": pa.array([3.0, 4.0], type=pa.float64()),
        }
    )

    with pytest.raises(ValueError, match="invalid product_id 'bad'"):
        extract_rule_product_ids(table)


@patch("mimir.runtime_context.xmlrpc.client.ServerProxy")
def test_build_runtime_context_from_xmlrpc(mock_server_proxy: MagicMock) -> None:
    common = MagicMock()
    common.authenticate.return_value = 7

    models = MagicMock()

    def execute_kw(_db, _uid, _password, model, method, args, kwargs):
        if model == "stock.warehouse" and method == "read":
            assert args == [[10]]
            return [{"code": "WH10", "lot_stock_id": [1000, "WH/Stock"]}]
        if model == "product.product" and method == "search_read":
            return [{"id": 1, "name": "Product 1"}, {"id": 2, "name": "Product 2"}]
        if model == "stock.warehouse.orderpoint" and method == "search_read":
            return [{"product_id": [1, "Product 1"], "warehouse_id": [10, "WH10"]}]
        raise AssertionError(f"Unexpected XML-RPC call: {model}.{method}")

    models.execute_kw.side_effect = execute_kw
    mock_server_proxy.side_effect = [common, models]

    context = build_runtime_context_from_xmlrpc(
        odoo_url="https://odoo.example",
        odoo_db="test_db",
        odoo_user="admin",
        odoo_password="secret",
        selected_warehouse_id=10,
        minmax_table=_rules_table(),
    )

    assert context.warehouse_code == "WH10"
    assert context.location_id == 1000
    assert context.product_names == {1: "Product 1", 2: "Product 2"}
    assert context.existing_orderpoint_keys == {(1, 10)}
