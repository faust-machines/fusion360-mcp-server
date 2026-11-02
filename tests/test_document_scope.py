"""Document binding runs inside one Fusion main-thread command, without Fusion."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location(
    "document_scope", Path(__file__).parents[1] / "addon/server/document_scope.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DocumentScope = MODULE.DocumentScope
DocumentBindings = MODULE.DocumentBindings


class Document:
    def __init__(self, app, identity):
        self.app = app
        self.creationId = identity
        self.isValid = True
        self.name = "Untitled"
        self.changes = []

    def activate(self):
        self.app.activeDocument = self
        return True


class Documents:
    def __init__(self, values):
        self.values = values

    @property
    def count(self):
        return len(self.values)

    def item(self, index):
        return self.values[index]


class ScopeFixture(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(activeDocument=None)
        self.user = Document(self.app, "user-document")
        self.owned = Document(self.app, "owned-document")
        self.app.documents = Documents([self.user, self.owned])
        self.user.activate()
        self.bindings = DocumentBindings()
        self.token = self.bindings.add(self.owned)


class ScopeTests(ScopeFixture):
    def test_binding_resolves_identity_not_duplicate_name_and_restores_user(self):
        with DocumentScope(self.app, self.token, self.bindings) as scope:
            self.assertIs(self.app.activeDocument, self.owned)
            scope.document.changes.append("box")
            # Fusion event processing can let the user activate another tab.
            self.user.activate()
            self.assertIs(scope.document, self.owned)
            scope.activate()
            self.assertIs(self.app.activeDocument, self.owned)
        self.assertIs(self.app.activeDocument, self.user)
        self.assertEqual(self.user.changes, [])
        self.assertEqual(self.owned.changes, ["box"])

    def test_missing_document_fails_before_any_operation(self):
        with self.assertRaisesRegex(RuntimeError, "no longer open"):
            with DocumentScope(self.app, "closed-document", self.bindings):
                self.fail("must not run")
        self.assertIs(self.app.activeDocument, self.user)

    def test_handler_exception_restores_previous_document(self):
        with self.assertRaisesRegex(ValueError, "failed operation"):
            with DocumentScope(self.app, self.token, self.bindings):
                raise ValueError("failed operation")
        self.assertIs(self.app.activeDocument, self.user)

    def test_closed_bound_document_never_falls_back_to_user(self):
        with DocumentScope(self.app, self.token, self.bindings) as scope:
            self.owned.isValid = False
            self.user.activate()
            with self.assertRaisesRegex(RuntimeError, "no longer open"):
                _ = scope.document
            with self.assertRaisesRegex(RuntimeError, "no longer open"):
                scope.activate()
        self.assertIs(self.app.activeDocument, self.user)

    def test_unbound_call_preserves_legacy_active_document_behavior(self):
        with DocumentScope(self.app, None, self.bindings) as scope:
            self.assertIs(scope.document, self.user)
            self.owned.activate()
            self.assertIs(scope.document, self.owned)
        self.assertIs(self.app.activeDocument, self.owned)

    def test_invalid_identity_is_not_treated_as_unbound(self):
        for value in ["", 12, False]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    with DocumentScope(self.app, value, self.bindings):
                        self.fail("must not run")


if __name__ == "__main__":
    unittest.main()


def handler_class():
    """Run production dispatch/helpers with the Autodesk API stubbed."""
    import ast
    import logging
    import time
    import traceback

    tree = ast.parse(
        (Path(__file__).parents[1] / "addon/server/command_handler.py").read_text()
    )
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    names = {
        "execute_command",
        "_execute_bound_command",
        "_document",
        "_design",
        "_do_events",
        "new_document",
        "close_document",
        "execute_code",
        "_root",
    }
    methods = [
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(
        body=[
            ast.ClassDef(
                name="Handler", bases=[], keywords=[], body=methods, decorator_list=[]
            )
        ],
        type_ignores=[],
    )
    namespace = {
        "ast": ast,
        "io": __import__("io"),
        "math": __import__("math"),
        "redirect_stdout": __import__("contextlib").redirect_stdout,
        "BoundApplication": MODULE.BoundApplication,
        "DocumentScope": DocumentScope,
        "os": __import__("os"),
        "adsk": SimpleNamespace(
            core=SimpleNamespace(
                DocumentTypes=SimpleNamespace(FusionDesignDocumentType=1)
            ),
            fusion=SimpleNamespace(DesignTypes=SimpleNamespace(ParametricDesignType=1)),
        ),
        "time": time,
        "traceback": traceback,
        "log": logging.getLogger("test"),
        "_hints": SimpleNamespace(classify=lambda _: ("ERROR", [])),
    }
    exec(
        compile(ast.fix_missing_locations(module), "production-handler", "exec"),
        namespace,
    )
    return namespace["Handler"]


class HandlerTests(ScopeFixture):
    def test_real_dispatch_mutates_bound_design_even_after_user_switch(self):
        handler = handler_class()()
        handler.app = self.app
        handler._bindings = self.bindings
        handler._scope = None
        handler._MUTATION_COMMANDS = set()
        for doc in [self.user, self.owned]:
            doc.products = SimpleNamespace(itemByProductType=lambda _, doc=doc: doc)

        def change():
            self.user.activate()
            handler._design().changes.append("extrusion")
            return {"done": True}

        handler._COMMANDS = {"change": change}
        result = handler.execute_command(
            {"type": "change", "params": {}, "document_id": self.token}
        )
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(self.owned.changes, ["extrusion"])
        self.assertEqual(self.user.changes, [])
        self.assertIs(self.app.activeDocument, self.user)

    def test_real_dispatch_missing_target_does_not_invoke_handler(self):
        handler = handler_class()()
        handler.app = self.app
        handler._bindings = self.bindings
        handler._scope = None
        handler._MUTATION_COMMANDS = set()
        called = []
        handler._COMMANDS = {"change": lambda: called.append(True)}
        result = handler.execute_command(
            {"type": "change", "params": {}, "document_id": "missing"}
        )
        self.assertFalse(result["result"]["ok"])
        self.assertEqual(called, [])


class CreationTests(ScopeFixture):
    def test_create_returns_distinct_token_and_restores_existing_document(self):
        handler = handler_class()()
        handler.app, handler._bindings, handler._scope = self.app, self.bindings, None
        self.owned.products = SimpleNamespace(
            itemByProductType=lambda _: SimpleNamespace(designType=1)
        )

        def create(_):
            self.owned.activate()
            return self.owned

        self.app.documents.add = create
        first = handler.new_document()
        second = handler.new_document()
        self.assertNotEqual(first["document_id"], second["document_id"])
        self.assertIs(self.bindings.resolve(first["document_id"]), self.owned)
        self.assertIs(self.app.activeDocument, self.user)
        self.assertEqual(self.user.changes, [])

    def test_revision_imports_archive_to_separate_document(self):
        handler = handler_class()()
        handler.app, handler._bindings, handler._scope = self.app, self.bindings, None
        self.owned.products = SimpleNamespace(
            itemByProductType=lambda _: SimpleNamespace(designType=1)
        )
        seen = []

        def import_new(options):
            seen.append(options)
            self.owned.activate()
            return self.owned

        self.app.importManager = SimpleNamespace(
            createFusionArchiveImportOptions=lambda path: {"source": path},
            importToNewDocument=import_new,
        )
        receipt = handler.new_document("/project/current.f3d")
        self.assertEqual(seen, [{"source": "/project/current.f3d"}])
        self.assertIs(self.bindings.resolve(receipt["document_id"]), self.owned)
        self.assertIs(self.app.activeDocument, self.user)
        self.assertEqual(self.user.changes, [])


class ApplicationBindingTests(ScopeFixture):
    def test_code_application_references_stay_bound_after_ui_events(self):
        self.owned.products = SimpleNamespace(itemByProductType=lambda _: self.owned)
        with DocumentScope(self.app, self.token, self.bindings) as scope:
            application = MODULE.BoundApplication(self.app, scope)
            self.user.activate()
            self.assertIs(application.activeDocument, self.owned)
            self.assertIs(application.activeProduct, self.owned)
        self.assertIs(self.app.activeDocument, self.user)

    def test_failed_activation_restores_previous_document(self):
        def failed_activation():
            self.app.activeDocument = self.owned
            return False

        self.owned.activate = failed_activation
        with self.assertRaisesRegex(RuntimeError, "activate"):
            with DocumentScope(self.app, self.token, self.bindings):
                self.fail("must not run")
        self.assertIs(self.app.activeDocument, self.user)


class HandlerRestartTests(ScopeFixture):
    def test_new_handler_owns_dispatch_and_rejects_old_session_tokens(self):
        import ast
        import logging
        import os
        import time
        import traceback

        tree = ast.parse(
            (Path(__file__).parents[1] / "addon/server/command_handler.py").read_text()
        )
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        self.app.userInterface = None
        api = SimpleNamespace(
            core=SimpleNamespace(
                Application=SimpleNamespace(get=lambda: self.app),
                DocumentTypes=SimpleNamespace(FusionDesignDocumentType=1),
            ),
            fusion=SimpleNamespace(DesignTypes=SimpleNamespace(ParametricDesignType=1)),
        )
        namespace = {
            "DocumentScope": DocumentScope,
            "DocumentBindings": DocumentBindings,
            "BoundApplication": MODULE.BoundApplication,
            "adsk": api,
            "os": os,
            "time": time,
            "traceback": traceback,
            "log": logging.getLogger("test"),
            "_hints": SimpleNamespace(classify=lambda _: ("ERROR", [])),
        }
        exec(
            compile(ast.Module(body=[cls], type_ignores=[]), "real-handler", "exec"),
            namespace,
        )
        handler_type = namespace["CommandHandler"]

        def create(_):
            document = Document(self.app, "copied-creation-id")
            document.products = SimpleNamespace(
                itemByProductType=lambda _: SimpleNamespace(designType=1)
            )
            document.activate()
            return document

        self.app.documents.add = create
        first, second = handler_type(), handler_type()
        old = first.execute_command({"type": "new_document"})["result"]
        new = second.execute_command({"type": "new_document"})["result"]
        accepted = second.execute_command(
            {"type": "get_design_type", "document_id": new["document_id"]}
        )
        self.assertTrue(accepted["result"]["ok"], accepted)
        rejected = second.execute_command(
            {"type": "get_design_type", "document_id": old["document_id"]}
        )
        self.assertFalse(rejected["result"]["ok"])
        self.assertIs(self.app.activeDocument, self.user)


class CodeAndCloseTests(ScopeFixture):
    def test_execute_code_supplied_application_stays_bound_after_events(self):
        handler = handler_class()()
        handler.app, handler._bindings, handler._scope = self.app, self.bindings, None
        handler.ui = None
        handler._MUTATION_COMMANDS = set()
        handler._COMMANDS = {"execute_code": handler.execute_code}
        self.owned.designType = 1
        self.owned.rootComponent = self.owned
        self.owned.products = SimpleNamespace(itemByProductType=lambda _: self.owned)
        handler.execute_code.__globals__["adsk"].doEvents = self.user.activate
        result = handler.execute_command(
            {
                "type": "execute_code",
                "document_id": self.token,
                "params": {
                    "code": (
                        "adsk.doEvents()\n"
                        "app.activeDocument.changes.append('owned edit')"
                    )
                },
            }
        )
        self.assertTrue(result["result"]["ok"], result)
        self.assertEqual(self.owned.changes, ["owned edit"])
        self.assertEqual(self.user.changes, [])
        self.assertIs(self.app.activeDocument, self.user)

    def test_close_requires_owned_binding_and_never_closes_user_document(self):
        handler = handler_class()()
        handler.app, handler._bindings, handler._scope = self.app, self.bindings, None
        handler._MUTATION_COMMANDS = set()
        handler._COMMANDS = {"close_document": handler.close_document}
        calls = []

        def close(save):
            calls.append((self.owned, save))
            self.owned.isValid = False
            return True

        self.owned.close = close
        refused = handler.execute_command({"type": "close_document"})
        self.assertFalse(refused["result"]["ok"])
        self.assertEqual(calls, [])
        result = handler.execute_command(
            {"type": "close_document", "document_id": self.token}
        )
        self.assertTrue(result["result"]["ok"], result)
        self.assertEqual(calls, [(self.owned, False)])
        self.assertIs(self.app.activeDocument, self.user)


class InvalidEnvelopeTests(ScopeFixture):
    def test_null_binding_never_becomes_an_unbound_mutation(self):
        handler = handler_class()()
        handler.app, handler._bindings, handler._scope = self.app, self.bindings, None
        handler._MUTATION_COMMANDS = set()
        calls = []
        handler._COMMANDS = {"change": lambda: calls.append(True)}
        result = handler.execute_command({"type": "change", "document_id": None})
        self.assertFalse(result["result"]["ok"])
        self.assertEqual(calls, [])
