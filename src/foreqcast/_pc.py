"""Typed wrappers around pyarrow.compute.

pyarrow.compute populates its namespace from Arrow kernels at import time.
Even with pyarrow-stubs installed, several overloads are incomplete or
narrower than the runtime accepts (e.g. day_of_week's stub prefers
Expression while ChunkedArray works at runtime). Reaching into pc directly
sprays `# type: ignore` comments across whichever module happens to use
compute functions — so we centralize the calls here instead.

Adding a new compute function is a single line below; call sites get a
fully typed API.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as _pc  # pyright: ignore[reportMissingTypeStubs]

ArrayLike = pa.ChunkedArray | pa.Array


def invert(arr: ArrayLike) -> ArrayLike:
    return _pc.invert(arr)  # pyright: ignore[reportCallIssue,reportArgumentType]


def is_in(arr: ArrayLike, value_set: pa.Array) -> ArrayLike:
    return _pc.is_in(arr, value_set=value_set)  # pyright: ignore[reportCallIssue,reportArgumentType]


def is_valid(arr: ArrayLike) -> ArrayLike:
    return _pc.is_valid(arr)  # pyright: ignore[reportCallIssue,reportArgumentType]


def day_of_week(arr: ArrayLike) -> ArrayLike:
    return _pc.day_of_week(arr)  # pyright: ignore[reportCallIssue,reportArgumentType]


def multiply(a: ArrayLike, b: ArrayLike | pa.Scalar) -> ArrayLike:
    return _pc.multiply(a, b)  # pyright: ignore[reportCallIssue,reportArgumentType]


def add(a: ArrayLike, b: ArrayLike) -> ArrayLike:
    return _pc.add(a, b)  # pyright: ignore[reportCallIssue,reportArgumentType]


def cast(arr: ArrayLike, target_type: pa.DataType) -> ArrayLike:
    return _pc.cast(arr, target_type)  # pyright: ignore[reportCallIssue,reportArgumentType]
