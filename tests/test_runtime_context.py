"""Tests for runtime-context helpers."""

from __future__ import annotations

import pyarrow as pa
import pytest
from mimir_core.runtime_context import PipelineRuntimeContext, extract_rule_product_ids


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


def test_product_id_lookup_exposes_string_key_mapping() -> None:
    context = PipelineRuntimeContext(
        warehouse_code="WH10",
        location_id=1000,
        product_names={1: "Product 1", 2: "Product 2"},
        existing_orderpoint_keys={(1, 10)},
    )

    assert context.product_id_lookup == {"1": 1, "2": 2}
