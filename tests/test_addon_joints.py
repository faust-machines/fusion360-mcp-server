"""Guards for ``add_joint``: geometry, motion types, and no silent fallbacks.

``add_joint`` shipped two defects that a return-value test could never catch,
because both made the tool *look* like it worked:

* it called ``JointGeometry.createByPoint(occurrence, point)``. The current
  Fusion API takes a single argument, so the call raised ``TypeError`` and no
  joint was ever created.
* it only set motion for ``"rigid"``. Every other ``joint_type`` was added with
  no motion at all, so Fusion inferred one (usually rigid) while the tool
  returned ``{"created": True}``.

Both defects are structural, so these tests read the add-in with ``ast`` (it
imports Fusion's ``adsk`` runtime and cannot be imported in CI) and cross-check
the schema, which lives in the importable package.

The old geometry source is also gone on purpose: component origin points are
not valid joint geometry - ``joints.add`` rejects them with
``Invalid parameter point`` because a bare point carries no component
association. A joint therefore needs a face pair, and the schema says so.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fusion360_mcp.tools import TOOLS

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLER = REPO_ROOT / "addon" / "server" / "command_handler.py"


def _class_assign(attr: str):
    """Return the literal value of ``CommandHandler.<attr>`` parsed via AST."""
    tree = ast.parse(HANDLER.read_text())
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "CommandHandler":
            continue
        for stmt in cls.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if any(isinstance(t, ast.Name) and t.id == attr for t in stmt.targets):
                return ast.literal_eval(stmt.value)
    raise AssertionError(f"CommandHandler.{attr} not found in {HANDLER}")


def _method(name: str) -> ast.FunctionDef:
    tree = ast.parse(HANDLER.read_text())
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "CommandHandler":
            continue
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
    raise AssertionError(f"CommandHandler.{name} not found in {HANDLER}")


def _schema() -> dict:
    for tool in TOOLS:
        if tool["name"] == "add_joint":
            return tool["inputSchema"]
    raise AssertionError("add_joint schema missing from TOOLS")


def test_no_two_argument_create_by_point():
    """``createByPoint`` takes one argument; passing an occurrence is a TypeError."""
    tree = ast.parse(HANDLER.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "createByPoint":
            if len(node.args) != 1:
                offenders.append(len(node.args))
        # also catch calls recorded on the class path: JointGeometry.createByPoint(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            if func.attr == "createByPoint" and len(node.args) != 1:
                offenders.append(len(node.args))
    assert not offenders, (
        "JointGeometry.createByPoint() accepts exactly one argument (a Point3D); "
        f"found call(s) with argument counts {offenders}. The occurrence-plus-point "
        "form raises TypeError on the current Fusion API."
    )


def test_joint_motion_setters_cover_every_schema_type():
    """Every joint_type in the schema must map to a motion setter in the add-in."""
    setters = _class_assign("_JOINT_MOTION_SETTERS")
    schema_types = set(_schema()["properties"]["joint_type"]["enum"])

    assert set(setters) == schema_types, (
        "joint_type values in tools.py and _JOINT_MOTION_SETTERS drifted.\n"
        f"  only in schema: {sorted(schema_types - set(setters))}\n"
        f"  only in addon:  {sorted(set(setters) - schema_types)}"
    )
    for joint_type, (setter, _directions) in setters.items():
        assert setter.startswith("setAs") and setter.endswith("JointMotion"), (
            f"{joint_type!r} maps to {setter!r}, which is not a JointInput "
            "motion setter. A missing setter means the joint is added with no "
            "motion and Fusion infers one silently."
        )


def test_motion_setter_arities_match_the_fusion_api():
    """Direction counts were verified against Fusion: 0, 1 or 2 - never inferred."""
    setters = _class_assign("_JOINT_MOTION_SETTERS")

    assert setters["rigid"][1] == 0
    assert setters["pin_slot"][1] == 2
    assert setters["ball"][1] == 2
    for joint_type in ("revolute", "slider", "cylindrical", "planar"):
        assert setters[joint_type][1] == 1, (
            f"{joint_type} takes exactly one JointDirections argument"
        )


def test_add_joint_requires_face_geometry():
    """The handler must demand faces and the schema must mark them required."""
    schema = _schema()
    required = set(schema["required"])
    assert {"face_one", "face_two"} <= required, (
        "face_one/face_two are mandatory joint geometry and must be required in "
        "the schema, so a caller fails fast instead of getting a runtime error"
    )

    node = _method("add_joint")
    args = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
    assert {"face_one", "face_two"} <= args, "add_joint must accept face_one/face_two"

    raises = [
        n for n in ast.walk(node) if isinstance(n, ast.Raise) and n.exc is not None
    ]
    assert raises, "add_joint must raise when no joint geometry is supplied"


def test_add_joint_does_not_swallow_failures():
    """No bare ``except`` in the joint path.

    The sibling bugs in this file all shared one root cause: a failing call
    inside ``except Exception: pass``, so the tool reported success while doing
    nothing. Joint motion is exactly that shape of failure, so guard it.
    """
    node = _method("add_joint")
    for handler in (n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)):
        if handler.type is None:
            raise AssertionError("bare `except:` in add_joint")
        if (
            isinstance(handler.type, ast.Name)
            and handler.type.id == "Exception"
            and len(handler.body) == 1
            and isinstance(handler.body[0], ast.Pass)
        ):
            raise AssertionError(
                "add_joint swallows Exception with `pass`; a failed motion set "
                "would report success"
            )


def test_direction_and_quadrant_enums_match_the_schema():
    """Schema enums must mirror the add-in's lookup tables."""
    props = _schema()["properties"]

    axes = set(_class_assign("_JOINT_AXES"))
    assert set(props["axis"]["enum"]) == axes
    assert set(props["second_axis"]["enum"]) == axes

    quadrants = set(_class_assign("_JOINT_QUADRANTS"))
    assert set(props["quadrant"]["enum"]) == quadrants

    key_points = set(_class_assign("_JOINT_KEY_POINTS"))
    assert set(props["key_point"]["enum"]) == key_points


# ---------------------------------------------------------------------------
# create_as_built_joint: the non-snapping variant
# ---------------------------------------------------------------------------


def test_as_built_joint_honours_joint_type():
    """An as-built joint used to ignore joint_type and always be rigid.

    Fusion keeps the components where they are for as-built joints, which makes
    this the right tool when a design is already positioned - so a silent
    downgrade to rigid is exactly the wrong failure. Guard that the method uses
    the shared motion table rather than hard-coding ``setAsRigidJointMotion``.
    """
    node = _method("create_as_built_joint")

    used_table = any(
        (isinstance(n, ast.Name) and n.id == "_JOINT_MOTION_SETTERS")
        or (isinstance(n, ast.Attribute) and n.attr == "_JOINT_MOTION_SETTERS")
        for n in ast.walk(node)
    )
    assert used_table, (
        "create_as_built_joint must resolve motion through _JOINT_MOTION_SETTERS; "
        "hard-coding a setter means joint_type is ignored"
    )

    raises = [
        n for n in ast.walk(node) if isinstance(n, ast.Raise) and n.exc is not None
    ]
    assert raises, (
        "create_as_built_joint must raise when a non-rigid joint is requested "
        "without joint geometry (Fusion: 'Geometry should not be null if joint "
        "motion is not rigid')"
    )


def test_as_built_joint_schema_mirrors_add_joint():
    """Both joint tools take the same geometry arguments."""
    schema = {t["name"]: t["inputSchema"] for t in TOOLS}
    add = set(schema["add_joint"]["properties"])
    built = set(schema["create_as_built_joint"]["properties"])

    assert add == built, (
        "add_joint and create_as_built_joint accept different arguments, which "
        f"makes the snapping/non-snapping pair confusing.\n"
        f"  only in add_joint: {sorted(add - built)}\n"
        f"  only in create_as_built_joint: {sorted(built - add)}"
    )


def test_add_joint_reports_whether_it_moved_the_components():
    """A joint snaps components together; that must be reported, not hidden.

    On a real assembly this surfaced as a 32.6 mm displacement between the two
    halves of a hinge - the joint "worked" and quietly rearranged the design.
    """
    node = _method("add_joint")
    returned = [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
    ]
    assert returned, "add_joint must return a result dict"
    keys = {
        k.value
        for d in returned
        for k in d.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }
    assert {"snapped", "moved_components"} <= keys, (
        "add_joint must report the component displacement a snap causes "
        f"(returned keys: {sorted(keys)})"
    )
