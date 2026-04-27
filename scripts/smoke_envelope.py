"""Mock-mode smoke test: print the new envelope shapes end-to-end."""

import json

from fusion360_mcp.mock import mock_command
from fusion360_mcp.server import _format_result


def dump(label, name, params=None):
    print(f"\n=== {label} ===")
    result = mock_command(name, params or {})
    print("RAW RESULT:")
    print(
        json.dumps(
            {
                k: (f"<{len(v)} b64 chars>" if k == "image_base64" else v)
                for k, v in result.items()
            },
            indent=2,
            default=str,
        )
    )

    formatted = _format_result(name, result)
    print("\nFORMATTED (what the agent sees):")
    if hasattr(formatted, "content"):
        is_err = getattr(formatted, "isError", False)
        print(f"[isError={is_err}]")
        for block in formatted.content:
            print(block.text)
    else:
        for block in formatted:
            if block.type == "text":
                print(block.text)
            elif block.type == "image":
                print(f"<IMAGE BLOCK: {block.mimeType}, {len(block.data)} b64 chars>")


dump("1. SUCCESS with deltas (extrude)", "extrude", {"height": 3.0})
dump(
    "2. STRUCTURED ERROR with hints (forced)",
    "extrude",
    {"height": 3.0, "__mock_error__": "No profiles in sketch"},
)
dump("3. RENDER_VIEW image content", "render_view", {"view": "iso"})
dump(
    "4. NO-OP boolean (simulated)",
    "boolean_operation",
    {
        "target_body": "Body1",
        "tool_body": "Body2",
        "__mock_error__": "Boolean subtract failed — empty result",
    },
)
