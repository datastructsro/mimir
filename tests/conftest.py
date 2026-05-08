"""Shared pytest fixtures for mimir tests."""

import sys
import tempfile
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parents[1] / "odoo_addon"
if str(ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDON_ROOT))


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
