"""Load the shared ``src/mimir`` package under a non-conflicting alias for Odoo."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_CORE_PACKAGE_NAME = "mimir_core"


def bootstrap_mimir_core() -> ModuleType:
    """Load the shared Mimir core package once and return it."""
    existing = sys.modules.get(_CORE_PACKAGE_NAME)
    if existing is not None:
        return existing

    core_init = _find_core_init()
    if core_init is None:
        raise ModuleNotFoundError(
            "Could not locate the shared Mimir core package. "
            "Expected src/mimir/__init__.py somewhere above the addon directory."
        )

    spec = importlib.util.spec_from_file_location(
        _CORE_PACKAGE_NAME,
        core_init,
        submodule_search_locations=[str(core_init.parent)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import spec for {core_init}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_CORE_PACKAGE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_CORE_PACKAGE_NAME, None)
        raise
    return module


def _find_core_init() -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "src" / "mimir" / "__init__.py"
        if candidate.is_file():
            return candidate
    return None
