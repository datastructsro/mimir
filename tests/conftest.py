"""Shared pytest fixtures for mimir tests."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
