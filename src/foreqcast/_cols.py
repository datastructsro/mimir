"""Null-safe column extraction from PyArrow tables.

pyarrow.ChunkedArray.to_pylist() returns list[Any | None] — any column can
contain nulls, so the static type can't promise otherwise. Call sites that
want dict[int, str] etc. used to just zip raw .to_pylist() outputs and rely
on runtime happening to have no nulls; pyarrow-stubs now surfaces the gap.

These helpers strip nulls at the table->python boundary so the rest of the
code works with concrete element types (pyright treats the yielded values
as Any, which is assignable to int/str/float/date/datetime as needed).

Null-filter semantics: a row is yielded only when every requested column
is non-null for that row. This keeps the aligned-tuple invariant intact —
filtering per-column independently would silently misalign rows whenever
different columns had nulls in different positions.
"""

from __future__ import annotations

from collections.abc import Iterator

import pyarrow as pa


def nonnull_pairs(tbl: pa.Table, key_col: str, val_col: str) -> Iterator[tuple]:
    """Yield (key, value) tuples from two columns, skipping rows where either is null.

        dict(nonnull_pairs(products, "_odoo_product_id", "name"))
    """
    keys = tbl.column(key_col).to_pylist()
    vals = tbl.column(val_col).to_pylist()
    for k, v in zip(keys, vals):
        if k is not None and v is not None:
            yield k, v


def nonnull_rows(tbl: pa.Table, *columns: str) -> Iterator[tuple]:
    """Yield tuples of values from the named columns, skipping rows with any null.

    Use for row-wise construction (e.g. dataclass init from >2 columns).
    """
    arrays = [tbl.column(c).to_pylist() for c in columns]
    for row in zip(*arrays):
        if all(v is not None for v in row):
            yield row


def nonnull_values(tbl: pa.Table, column: str) -> list:
    """Return the column's non-null values as a Python list."""
    return [v for v in tbl.column(column).to_pylist() if v is not None]
