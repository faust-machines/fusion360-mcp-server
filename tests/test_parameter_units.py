"""Tests for user-parameter units.

``createByReal()`` is read in Fusion's internal units -- centimetres for a
length, radians for an angle -- so handing it a value the caller meant as
millimetres inflates it tenfold while the parameter still reads "mm". The
rules live in ``addon/server/parameter_units.py``, which imports no ``adsk``
and is loaded here by path.

The AST guards cover the two call sites into Fusion's API, which unit tests
cannot reach, in the same spirit as test_addon_sync.py.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from fusion360_mcp.mock import mock_command

ADDON_SERVER = Path(__file__).resolve().parent.parent / "addon" / "server"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


parameter_units = _load(ADDON_SERVER / "parameter_units.py", "parameter_units")


class TestExpressionFor:
    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (1000, "mm", "1000 mm"),
            (1200, "mm", "1200 mm"),
            (2.5, "cm", "2.5 cm"),
            (90, "deg", "90 deg"),
            (1, "in", "1 in"),
        ],
    )
    def test_the_requested_unit_reaches_the_expression(self, value, unit, expected):
        # The unit must survive into the expression -- writing the value
        # against a fixed unit, or dropping it, is the bug being fixed.
        assert parameter_units.expression_for(value, unit) == expected

    @pytest.mark.parametrize("unit", ["", "   ", None])
    def test_a_unitless_parameter_keeps_the_bare_number(self, unit):
        assert parameter_units.expression_for(7, unit) == "7"

    def test_surrounding_whitespace_is_trimmed(self):
        assert parameter_units.expression_for(5, "  mm  ") == "5 mm"


class TestNeedsExpression:
    @pytest.mark.parametrize("unit", ["mm", "deg", " in "])
    def test_a_unit_requires_the_string_form(self, unit):
        assert parameter_units.needs_expression(unit)

    @pytest.mark.parametrize("unit", ["", "   ", None])
    def test_unitless_does_not(self, unit):
        assert not parameter_units.needs_expression(unit)


class TestNormaliseUnit:
    @pytest.mark.parametrize(
        ("given", "expected"), [("mm", "mm"), (" mm ", "mm"), (None, ""), ("", "")]
    )
    def test_normalisation(self, given, expected):
        assert parameter_units.normalise_unit(given) == expected


def _func(name: str) -> ast.FunctionDef:
    tree = ast.parse((ADDON_SERVER / "command_handler.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in command_handler.py")


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == attr
    ]


class TestBuildValueInput:
    """The choice between the two factories is the thing that was wrong."""

    @staticmethod
    def _factories():
        calls = []
        return (
            calls,
            lambda s: calls.append(("string", s)),
            lambda v: calls.append(("real", v)),
        )

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [(1000, "mm", "1000 mm"), (90, "deg", "90 deg"), (2.5, "in", "2.5 in")],
    )
    def test_a_unit_goes_through_the_string_factory(self, value, unit, expected):
        calls, from_string, from_real = self._factories()
        parameter_units.build_value_input(value, unit, from_string, from_real)
        assert calls == [("string", expected)]

    @pytest.mark.parametrize("unit", ["", "   ", None])
    def test_unitless_goes_through_the_real_factory(self, unit):
        calls, from_string, from_real = self._factories()
        parameter_units.build_value_input(7, unit, from_string, from_real)
        assert calls == [("real", 7)]

    def test_centimetres_still_carry_their_unit(self):
        # cm happens to match Fusion's internal unit, so a bare real would
        # look correct here -- it must still take the explicit path.
        calls, from_string, from_real = self._factories()
        parameter_units.build_value_input(3, "cm", from_string, from_real)
        assert calls == [("string", "3 cm")]


class TestCallSites:
    def test_create_parameter_delegates_the_choice(self):
        # Constructing a ValueInput inline here would put the branch back
        # where it cannot be tested.
        create = _func("create_parameter")
        assert _calls(create, "build_value_input"), (
            "create_parameter must go through build_value_input()"
        )
        assert not _calls(create, "createByString")
        assert not _calls(create, "createByReal")

    def test_set_parameter_writes_the_expression_not_the_value(self):
        assigned = {
            target.attr: node.value
            for node in ast.walk(_func("set_parameter"))
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
        }
        assert "value" not in assigned, "Parameter.value is read in internal units"
        assert "expression" in assigned
        built = _calls(assigned["expression"], "expression_for")
        assert built, "the expression must come from expression_for()"
        names = {n.id for n in ast.walk(built[0]) if isinstance(n, ast.Name)}
        assert {"value", "unit"} <= names


class TestMockContract:
    def test_create_parameter_reports_the_resulting_expression(self):
        result = mock_command(
            "create_parameter", {"name": "frame_length", "value": 1000, "unit": "mm"}
        )
        assert result["expression"] == "1000 mm"

    def test_create_parameter_trims_the_unit_like_the_addon(self):
        result = mock_command(
            "create_parameter", {"name": "padded", "value": 5, "unit": "  mm  "}
        )
        assert result["unit"] == "mm"
        assert result["expression"] == "5 mm"

    @pytest.mark.parametrize("unit", ["", "   "])
    def test_create_parameter_keeps_a_blank_unit_unitless(self, unit):
        # Blank means unitless in the addon; mock must not turn it into mm.
        result = mock_command(
            "create_parameter", {"name": "bare", "value": 7, "unit": unit}
        )
        assert result["unit"] == ""
        assert result["expression"] == "7"

    def test_set_parameter_does_not_invent_a_unit(self):
        # set_parameter takes no unit; mock mode holds no design, so it cannot
        # know what the target parameter declares.
        result = mock_command("set_parameter", {"name": "frame_length", "value": 1200})
        assert result["value"] == 1200
        assert result["unit"] is None
        assert result["expression"] is None
