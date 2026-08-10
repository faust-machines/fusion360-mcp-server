"""Unit handling for user parameters.

Fusion reads a bare real in its internal units -- centimetres for a length,
radians for an angle -- so a value the caller meant as millimetres has to be
handed over as a unit-bearing string instead. Kept free of ``adsk`` imports
so the rules can be unit-tested without a running Fusion instance.
"""

from __future__ import annotations


def normalise_unit(unit: str | None) -> str:
    """Trim a unit to its canonical form; blank means unitless."""
    return (unit or "").strip()


def expression_for(value: float, unit: str | None) -> str:
    """The expression text that stores *value* as *unit*.

    A unitless parameter has nothing to interpolate, so it keeps the bare
    number -- Fusion then reads it as internal units, which is what unitless
    means.
    """
    unit = normalise_unit(unit)
    return f"{value} {unit}" if unit else f"{value}"


def needs_expression(unit: str | None) -> bool:
    """True when the value must go through a string rather than a real."""
    return bool(normalise_unit(unit))


def build_value_input(value: float, unit: str | None, from_string, from_real):
    """Build the ValueInput that preserves *unit*.

    The two factories are ``ValueInput.createByString`` and
    ``ValueInput.createByReal``; they are passed in so the choice between
    them -- the thing that was wrong -- can be tested without ``adsk``.
    """
    if needs_expression(unit):
        return from_string(expression_for(value, unit))
    return from_real(value)
