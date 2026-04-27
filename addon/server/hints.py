"""
Error classification for Fusion API failures.

Maps exception messages from the Fusion API (and our handler code) to a
stable ``error_kind`` tag plus a small list of contextual repair hints.

Keep this file in sync with ``src/fusion360_mcp/hints.py`` — the addon
is installed into Fusion's AddIns folder at deploy time and cannot import
from the MCP server package.
"""

import re

# Each entry: (regex applied to str(exc), error_kind, hints)
# First match wins. Patterns use re.IGNORECASE.
_RULES: list[tuple[str, str, list[str]]] = [
    (
        r"no active design",
        "NO_ACTIVE_DESIGN",
        [
            "Open or create a design in Fusion before calling mutation tools.",
            "Use get_design_type to confirm the active product is a design.",
        ],
    ),
    (
        r"no profiles? in sketch",
        "PROFILE_NOT_CLOSED",
        [
            "The sketch has no closed profile to extrude/revolve.",
            "Check that your curves form a closed loop — draw_line segments "
            "must share endpoints exactly (use add_constraint coincident).",
            "Inspect the sketch with get_scene_info — a sketch with 0 profiles "
            "is never extrudable.",
        ],
    ),
    (
        r"sketch\b.*\bnot found|no sketch|sketch.*does not exist",
        "SKETCH_NOT_FOUND",
        [
            "The named sketch does not exist in the active design.",
            "List sketches via get_scene_info before referencing one by name.",
        ],
    ),
    (
        r"body\b.*\bnot found|no body|body.*does not exist",
        "BODY_NOT_FOUND",
        [
            "The named body does not exist in the active design.",
            "Use list_components or get_scene_info to see available bodies.",
        ],
    ),
    (
        r"self[- ]?intersect",
        "SELF_INTERSECTION",
        [
            "The resulting geometry self-intersects — Fusion cannot resolve it.",
            "For sweeps: check the path curvature vs. the profile size.",
            "For lofts: ensure profiles are oriented consistently.",
            "Try reducing the feature size, or split into smaller operations.",
        ],
    ),
    (
        r"regenerat(e|ion) fail|feature\s+fail(ed|ure)|rebuild fail",
        "REGEN_FAILED",
        [
            "Fusion could not regenerate the feature with these inputs.",
            "Check for references to deleted geometry upstream in the timeline.",
            "Try undo, then attempt the mutation with different parameters.",
        ],
    ),
    (
        r"boolean.*(empty|no[- ]?op|no result|failed)|subtract.*empty",
        "BOOLEAN_NO_OP",
        [
            "Boolean operation produced no change — tool and target do not "
            "intersect, or the result would be identical to the target.",
            "Verify the tool body actually overlaps the target — use "
            "check_interference.",
            "For subtract: confirm the tool body is inside or crosses the target.",
            "Consider move_body to reposition before retrying.",
        ],
    ),
    (
        r"invalid (argument|input|parameter)|bad (argument|input|value)",
        "INVALID_INPUT",
        [
            "Fusion rejected one of the parameters.",
            "Check units (all Fusion API values are in cm), enum values, and "
            "that referenced entities still exist.",
        ],
    ),
    (
        r"unknown command",
        "UNKNOWN_COMMAND",
        [
            "The add-in does not know this command — typo or version skew.",
            "Restart the addon (via reload_handler) if you recently added tools.",
        ],
    ),
    (
        r"profile.*(open|not closed|unclosed)",
        "PROFILE_NOT_CLOSED",
        [
            "The profile is open — extrude/revolve need a closed loop.",
            "Draw the missing segment or add coincident constraints at endpoints.",
        ],
    ),
    (
        r"timeout",
        "TIMEOUT",
        [
            "The operation exceeded the 30s bridge timeout.",
            "If a long operation is expected (CAM toolpath, complex fillet), "
            "split it into smaller steps.",
        ],
    ),
    (
        r"requires (?:parametric|direct[- ]edit)|"
        r"not (?:supported|permitted|allowed) in (?:parametric|direct[- ]edit)|"
        r"switch to (?:parametric|direct[- ]edit)",
        "DESIGN_TYPE_MISMATCH",
        [
            "This operation is not permitted in the current design type.",
            "Check get_design_type; some operations (like undo after direct "
            "edits) require parametric mode.",
        ],
    ),
]


def classify(exc: BaseException) -> tuple[str, list[str]]:
    """Return ``(error_kind, hints)`` for *exc*.

    Falls back to ``("UNKNOWN", [])`` if no rule matches.
    """
    msg = str(exc) or exc.__class__.__name__
    for pattern, kind, hints in _RULES:
        if re.search(pattern, msg, re.IGNORECASE):
            return kind, list(hints)
    return "UNKNOWN", []
