"""Tests for create_hole's face picking and plane solving.

``addon/server/hole_geometry.py`` is loaded by path: the addon package
imports Fusion's ``adsk`` runtime, which isn't installable here, but this
module deliberately has no such dependency.

The AST guards at the bottom cover the two things that can only be seen at
the call site into Fusion's API, in the same spirit as test_addon_sync.py.
"""

from __future__ import annotations

import ast
import importlib.util
import math
from pathlib import Path

import pytest

ADDON_SERVER = Path(__file__).resolve().parent.parent / "addon" / "server"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hole_geometry = _load(ADDON_SERVER / "hole_geometry.py", "hole_geometry")


def _nz(degrees_of_tilt: float) -> float:
    return math.cos(math.radians(degrees_of_tilt))


class TestIsHorizontalFace:
    def test_accepts_the_face_pointing_the_requested_way(self):
        assert hole_geometry.is_horizontal_face(1.0, want_up=True)
        assert hole_geometry.is_horizontal_face(-1.0, want_up=False)

    def test_rejects_the_face_pointing_the_other_way(self):
        assert not hole_geometry.is_horizontal_face(-1.0, want_up=True)
        assert not hole_geometry.is_horizontal_face(1.0, want_up=False)

    def test_rejects_a_vertical_face(self):
        # The side faces of a box share the top face's bounding-box max Z,
        # which is how a "top" search used to end up drilling sideways.
        assert not hole_geometry.is_horizontal_face(0.0, want_up=True)
        assert not hole_geometry.is_horizontal_face(0.0, want_up=False)

    @pytest.mark.parametrize("tilt", [0.0, 0.5])
    def test_accepts_a_face_within_tolerance(self, tilt):
        assert hole_geometry.is_horizontal_face(_nz(tilt), want_up=True)

    @pytest.mark.parametrize("tilt", [1.0, 5.0, 45.0])
    def test_rejects_a_slanted_face(self, tilt):
        assert not hole_geometry.is_horizontal_face(_nz(tilt), want_up=True)


class TestFaceRankKey:
    def test_top_prefers_the_highest_face(self):
        keys = [hole_geometry.face_rank_key(z, 1.0, want_up=True) for z in (0.0, 5.0)]
        assert max(keys) == keys[1]

    def test_bottom_prefers_the_lowest_face(self):
        keys = [hole_geometry.face_rank_key(z, 1.0, want_up=False) for z in (0.0, 5.0)]
        assert max(keys) == keys[0]

    def test_ties_go_to_the_larger_face(self):
        small = hole_geometry.face_rank_key(2.0, 1.0, want_up=True)
        large = hole_geometry.face_rank_key(2.0, 9.0, want_up=True)
        assert max(small, large) == large


class TestPlaneZAt:
    def test_horizontal_plane_keeps_its_height_everywhere(self):
        z = hole_geometry.plane_z_at((0, 0, 1), (0, 0, 0.5), 22.0, 1.5)
        assert z == pytest.approx(0.5)

    def test_tilted_plane_z_follows_the_requested_xy(self):
        # 45 degrees about Y: the plane through the origin has z == x.
        n = (-math.sqrt(0.5), 0.0, math.sqrt(0.5))
        assert hole_geometry.plane_z_at(n, (0, 0, 0), 3.0, 99.0) == pytest.approx(3.0)

    def test_tilted_plane_z_differs_from_an_arbitrary_point_on_it(self):
        # Reusing face.pointOnFace instead of solving would put the hole
        # centre off the requested location on any non-horizontal face.
        n = (-math.sqrt(0.5), 0.0, math.sqrt(0.5))
        arbitrary_point_z = 0.0
        assert hole_geometry.plane_z_at(n, (0, 0, 0), 3.0, 0.0) != arbitrary_point_z

    def test_vertical_plane_is_rejected(self):
        with pytest.raises(ValueError):
            hole_geometry.plane_z_at((1, 0, 0), (0, 0, 0), 1.0, 1.0)


def _create_hole_ast() -> ast.FunctionDef:
    tree = ast.parse((ADDON_SERVER / "command_handler.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "create_hole":
            return node
    raise AssertionError("create_hole not found in command_handler.py")


class TestCreateHoleCallSite:
    """Guards for the Fusion API calls that unit tests can't reach."""

    def test_faces_are_ranked_on_their_own_extreme_z(self):
        # pointOnFace is an arbitrary sample point; within the tilt tolerance
        # it is not the face's highest (or lowest) point.
        ranked = [
            node
            for node in ast.walk(_create_hole_ast())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "face_rank_key"
        ]
        assert len(ranked) == 1, "expected one face_rank_key() call in create_hole"
        attrs = {
            n.attr for n in ast.walk(_create_hole_ast()) if isinstance(n, ast.Attribute)
        }
        assert {"maxPoint", "minPoint"} <= attrs
        assert "pointOnFace" not in ast.dump(ranked[0])

    def test_diameter_is_not_scaled_before_createsimpleinput(self):
        # createSimpleInput() takes the diameter, not the radius; halving it
        # here silently produced holes at half the requested size.
        for node in ast.walk(_create_hole_ast()):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "createSimpleInput"
            ):
                value = node.args[0]
                assert (
                    isinstance(value, ast.Call)
                    and len(value.args) == 1
                    and isinstance(value.args[0], ast.Name)
                    and value.args[0].id == "diameter"
                ), "createSimpleInput() must receive the diameter unmodified"
                return
        raise AssertionError("no createSimpleInput() call in create_hole")

    def test_participant_bodies_is_the_target_body(self):
        # Left unset, the hole cuts every body it happens to intersect; left
        # empty it means the same thing.
        values = [
            node.value
            for node in ast.walk(_create_hole_ast())
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute) and target.attr == "participantBodies"
        ]
        assert values, "create_hole must set participantBodies"
        assert [
            [getattr(el, "id", None) for el in v.elts]
            for v in values
            if isinstance(v, ast.List)
        ] == [["body"]], "participantBodies must be limited to the target body"

    def test_hole_centre_is_converted_from_model_space(self):
        # Feeding model XY straight into the face's sketch space put the hole
        # somewhere else entirely; the point must go through the conversion.
        converted = [
            node
            for node in ast.walk(_create_hole_ast())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "modelToSketchSpace"
        ]
        assert len(converted) == 1, "hole centre must go through sketch conversion"
        names = {n.id for n in ast.walk(converted[0]) if isinstance(n, ast.Name)}
        assert {"center_x", "center_y", "center_z"} <= names

        added = [
            node
            for node in ast.walk(_create_hole_ast())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "sketchPoints"
        ]
        assert len(added) == 1, "expected one sketchPoints.add() in create_hole"
        assert any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "modelToSketchSpace"
            for sub in ast.walk(added[0])
        ), "sketchPoints.add() must receive the converted point"
