"""Fusion retains imported submodules when an add-in is stopped and run again."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class RestartTests(unittest.TestCase):
    def test_restart_loads_replaced_handler_bridge_and_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(
                Path(__file__).parents[1] / "addon/Fusion360MCP.py",
                root / "__init__.py",
            )
            server = root / "server"
            server.mkdir()
            (server / "__init__.py").write_text(
                "import logging\nLOG_PATH='test'\nget_logger=logging.getLogger\n"
            )
            for name in ("hints", "hole_geometry", "parameter_units"):
                (server / f"{name}.py").write_text("version='old'\n")
            (server / "document_scope.py").write_text("version='old'\n")
            (server / "command_handler.py").write_text(
                "from .document_scope import version\n"
                "class CommandHandler:\n    revision=version\n"
            )
            (server / "event_bridge.py").write_text(
                "class EventBridge:\n    revision='old'\n"
                "    def __init__(self,app,handler): pass\n"
                "    def stop(self): pass\n"
            )
            (server / "socket_server.py").write_text(
                "class Fusion360MCPServer:\n"
                "    def __init__(self,*args,**kwargs): pass\n"
                "    def start(self): pass\n    def stop(self): pass\n"
            )
            original = object()
            app = SimpleNamespace(activeDocument=original, userInterface=None)
            adsk = ModuleType("adsk")
            adsk.core = ModuleType("adsk.core")
            adsk.fusion = ModuleType("adsk.fusion")
            adsk.core.Application = SimpleNamespace(get=lambda: app)
            spec = importlib.util.spec_from_file_location(
                "restart_fixture", root / "__init__.py",
                submodule_search_locations=[str(root)],
            )
            module = importlib.util.module_from_spec(spec)
            try:
                with patch.dict(sys.modules, {
                    "adsk": adsk, "adsk.core": adsk.core,
                    "adsk.fusion": adsk.fusion, "restart_fixture": module,
                }):
                    spec.loader.exec_module(module)
                    # Fusion loads its add-in package with __package__ unset.
                    module.__package__ = None
                    module.run(None)
                    self.assertEqual(module._handler.revision, "old")
                    module.stop(None)
                    (server / "document_scope.py").write_text("version='binding-v1'\n")
                    path = server / "event_bridge.py"
                    path.write_text(path.read_text().replace("'old'", "'binding-v1'"))
                    module.run(None)
                    self.assertEqual(module._handler.revision, "binding-v1")
                    self.assertEqual(module._bridge.revision, "binding-v1")
                    self.assertIs(app.activeDocument, original)
                    module.stop(None)
            finally:
                for name in list(sys.modules):
                    if name.startswith("restart_fixture"):
                        del sys.modules[name]


if __name__ == "__main__":
    unittest.main()
