import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _clear_mimir_core_modules() -> None:
    for name in list(sys.modules):
        if name == "mimir_core" or name.startswith("mimir_core."):
            sys.modules.pop(name)


def _load_core_loader_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[1] / "odoo_addon" / "tools" / "core_loader.py"
    spec = importlib.util.spec_from_file_location("mimir_odoo_core_loader", module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_mimir_core_loads_shared_package_alias() -> None:
    loader_module = _load_core_loader_module()
    expected_core_path = Path(__file__).resolve().parents[1] / "src" / "mimir"

    _clear_mimir_core_modules()
    try:
        core_package = loader_module.bootstrap_mimir_core()
        importer = importlib.import_module("mimir_core.importer")
        runtime_context = importlib.import_module("mimir_core.runtime_context")
        server_client = importlib.import_module("mimir_core.server_client")

        assert core_package is loader_module.bootstrap_mimir_core()
        assert importer.MimirServerClient is server_client.MimirServerClient
        assert runtime_context.PipelineRuntimeContext.__module__ == "mimir_core.runtime_context"
        assert str(expected_core_path) in list(core_package.__path__)
    finally:
        _clear_mimir_core_modules()
