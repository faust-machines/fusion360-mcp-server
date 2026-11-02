"""Bind one main-thread command to an exact, session-owned document reference."""

from uuid import uuid4


class DocumentBindings:
    """Tokens die with the handler; names and copied creation IDs are not identities."""

    def __init__(self):
        self._documents = {}

    def add(self, document):
        self._documents = {
            token: doc for token, doc in self._documents.items() if doc.isValid
        }
        token = str(uuid4())
        self._documents[token] = document
        return token

    def resolve(self, token):
        if not isinstance(token, str) or not token:
            raise ValueError("document_id must be a nonempty session token")
        document = self._documents.get(token)
        if document is None or not document.isValid:
            raise RuntimeError("The bound Fusion document is no longer open")
        return document


class DocumentScope:
    def __init__(self, app, document_id, bindings):
        self.app = app
        self.document_id = document_id
        self.bindings = bindings
        self.previous = None

    @property
    def document(self):
        if self.document_id is None:
            return self.app.activeDocument
        return self.bindings.resolve(self.document_id)

    def activate(self):
        if self.document_id is not None:
            document = self.document
            if not document.activate():
                raise RuntimeError("Could not activate the bound Fusion document")

    def __enter__(self):
        self.previous = self.app.activeDocument
        try:
            self.activate()
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_):
        if self.document_id is not None and self.previous is not None:
            if self.previous.isValid:
                self.previous.activate()


class BoundApplication:
    """Keep execute_code's ordinary app references tied to its document.

    This is document context, not a Python sandbox: explicit global Autodesk
    access remains available to trusted scripts, as it is for unbound calls.
    """

    def __init__(self, application, scope):
        self._application = application
        self._scope = scope

    @property
    def activeDocument(self):
        return self._scope.document

    @property
    def activeProduct(self):
        design = self.activeDocument.products.itemByProductType("DesignProductType")
        if design is None:
            raise RuntimeError("The bound document has no Design product")
        return design

    @property
    def activeViewport(self):
        self._scope.activate()
        return self._application.activeViewport

    def __getattr__(self, name):
        return getattr(self._application, name)
