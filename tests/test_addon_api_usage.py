"""Guards against add-in code calling Fusion APIs that do not exist.

The add-in cannot be imported here — it needs Fusion's ``adsk`` runtime — so
these checks read it with ``ast`` the same way ``test_addon_sync.py`` does.

Both bugs these cover shared a root cause: the failing call sat inside a bare
``except Exception: pass``, so the tool reported success while doing nothing.
Neither would have been caught by a test that only asserted on return values.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLER = REPO_ROOT / "addon" / "server" / "command_handler.py"


def _method(name: str) -> ast.FunctionDef:
    tree = ast.parse(HANDLER.read_text())
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "CommandHandler":
            continue
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
    raise AssertionError(f"CommandHandler.{name} not found in {HANDLER}")


def _called_attrs(node: ast.AST) -> set[str]:
    """Every ``x.name(...)`` attribute name called within *node*."""
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }


def _has_silent_except(node: ast.AST) -> bool:
    """True if any handler's whole body is ``pass``."""
    for handler in (n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)):
        if all(isinstance(stmt, ast.Pass) for stmt in handler.body):
            return True
    return False


class TestDeleteAll:
    def test_deletes_via_the_timeline_entity(self):
        """TimelineObject has no deleteMe(); the entity it wraps does.

        Calling deleteMe() on the TimelineObject raises AttributeError on
        every item, so delete_all removed nothing at all.
        """
        node = _method("delete_all")
        attrs = {
            n.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute)
        }
        assert "entity" in attrs, (
            "delete_all must reach the feature through TimelineObject.entity; "
            "TimelineObject itself has no deleteMe()"
        )

    def test_does_not_swallow_failures(self):
        node = _method("delete_all")
        assert not _has_silent_except(node), (
            "delete_all must not use a bare 'except: pass' — that is what let "
            "it report success while deleting nothing"
        )

    def test_reports_what_it_did(self):
        """The return value must carry enough to tell success from a no-op."""
        node = _method("delete_all")
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
        assert returns, "delete_all must return a result"
        keys: set[str] = set()
        for ret in returns:
            if isinstance(ret.value, ast.Dict):
                keys |= {
                    k.value
                    for k in ret.value.keys
                    if isinstance(k, ast.Constant)
                }
        assert "remaining" in keys, (
            "delete_all should report what is still in the design, so a "
            "partial failure is visible to the caller"
        )


class TestCheckInterference:
    def test_uses_the_design_level_api(self):
        """Component.interfere() does not exist; the API is on Design."""
        node = _method("check_interference")
        called = _called_attrs(node)
        assert "interfere" not in called, (
            "Component.interfere() does not exist in the Fusion API — use "
            "Design.createInterferenceInput() + Design.analyzeInterference()"
        )
        assert {"createInterferenceInput", "analyzeInterference"} <= called, (
            "check_interference must go through the Design-level interference "
            "API"
        )

    def test_iterates_results_as_a_collection(self):
        """analyzeInterference returns a collection with .count / .item()."""
        node = _method("check_interference")
        attrs = {
            n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
        }
        assert "interferenceResultCount" not in attrs, (
            "interferenceResultCount belongs to the old, non-existent API"
        )
