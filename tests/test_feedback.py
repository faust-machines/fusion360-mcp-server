"""Tests for agent-feedback additions: hints, deltas, render_view, formatting."""

import base64

import mcp.types as types
import pytest

from fusion360_mcp.hints import classify
from fusion360_mcp.mock import _MUTATION_MOCKS, mock_command
from fusion360_mcp.server import _format_result


class TestHintClassification:
    """The hint table maps exception messages to an error_kind + hints."""

    @pytest.mark.parametrize(
        "msg,expected_kind",
        [
            ("No profiles in sketch", "PROFILE_NOT_CLOSED"),
            ("No active design", "NO_ACTIVE_DESIGN"),
            ("Body SomeBody not found", "BODY_NOT_FOUND"),
            ("Sketch Sketch1 not found", "SKETCH_NOT_FOUND"),
            ("Sweep self-intersects at T=0.4", "SELF_INTERSECTION"),
            ("Regeneration failed for feature Extrude7", "REGEN_FAILED"),
            ("Boolean subtract failed — empty result", "BOOLEAN_NO_OP"),
            ("Invalid input: angle must be > 0", "INVALID_INPUT"),
            ("Unknown command: bogus", "UNKNOWN_COMMAND"),
            ("Operation timeout after 30s", "TIMEOUT"),
        ],
    )
    def test_classify_known_messages(self, msg, expected_kind):
        kind, hints = classify(msg)
        assert kind == expected_kind, f"'{msg}' classified as {kind}"
        assert hints, f"'{expected_kind}' should ship with at least one hint"

    def test_classify_unknown_falls_back(self):
        kind, hints = classify("totally novel failure mode xyz")
        assert kind == "UNKNOWN"
        assert hints == []

    def test_classify_accepts_exception(self):
        kind, hints = classify(RuntimeError("No profiles in sketch"))
        assert kind == "PROFILE_NOT_CLOSED"
        assert len(hints) >= 1


class TestMockForcedError:
    """Sending __mock_error__ in params produces an ok=False envelope."""

    def test_forced_error_shape(self):
        result = mock_command(
            "extrude",
            {
                "height": 1,
                "__mock_error__": "No profiles in sketch",
            },
        )
        assert result["ok"] is False
        assert result["error_kind"] == "PROFILE_NOT_CLOSED"
        assert "hints" in result and result["hints"]
        assert result["error_message"] == "No profiles in sketch"
        assert result["mode"] == "mock"

    def test_forced_error_unknown_kind(self):
        result = mock_command("extrude", {"__mock_error__": "something weird"})
        assert result["ok"] is False
        assert result["error_kind"] == "UNKNOWN"
        assert result["hints"] == []


class TestMockDeltas:
    """Mutation mocks carry a deltas sub-dict; read-only mocks do not."""

    def test_mutation_has_deltas(self):
        for cmd in _MUTATION_MOCKS:
            result = mock_command(cmd, {})
            assert "deltas" in result, f"{cmd} should expose deltas"
            deltas = result["deltas"]
            assert "body_count_delta" in deltas
            assert "mass_g_delta" in deltas
            assert "bbox_after" in deltas

    def test_read_only_has_no_deltas(self):
        for cmd in (
            "ping",
            "get_scene_info",
            "get_parameters",
            "measure_distance",
            "check_interference",
        ):
            result = mock_command(cmd, {})
            assert "deltas" not in result, f"{cmd} must not fabricate deltas"

    def test_success_is_ok_true(self):
        assert mock_command("ping")["ok"] is True
        assert mock_command("extrude", {"height": 1})["ok"] is True


class TestMockRenderView:
    """render_view returns image_base64 that decodes as PNG bytes."""

    def test_render_view_shape(self):
        result = mock_command("render_view", {"view": "iso"})
        assert result["ok"] is True
        assert result["image_format"] == "png"
        assert result["view"] == "iso"
        assert isinstance(result["image_base64"], str)
        decoded = base64.b64decode(result["image_base64"])
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

    def test_render_view_is_read_only(self):
        result = mock_command("render_view", {})
        assert "deltas" not in result


class TestFormatResult:
    """_format_result converts mock/addon envelopes to MCP content blocks."""

    def test_error_path_sets_isError(self):
        result = {
            "ok": False,
            "error_kind": "PROFILE_NOT_CLOSED",
            "error_message": "No profiles in sketch",
            "hints": ["close the loop", "verify coincident constraints"],
            "traceback": "Traceback (...)",
        }
        out = _format_result("extrude", result)
        assert isinstance(out, types.CallToolResult)
        assert out.isError is True
        text = out.content[0].text
        assert "PROFILE_NOT_CLOSED" in text
        assert "close the loop" in text
        assert "No profiles in sketch" in text

    def test_success_path_lists_fields(self):
        result = {
            "ok": True,
            "body_name": "Body1",
            "height": 3.0,
            "mode": "mock",
        }
        out = _format_result("extrude", result)
        assert isinstance(out, list)
        text = out[0].text
        assert "**extrude** OK" in text
        assert "body_name: Body1" in text
        assert "height: 3.0" in text
        # "ok" is a control field — it should not appear in the rendered body
        assert "\n  ok:" not in text

    def test_deltas_are_rendered(self):
        result = {
            "ok": True,
            "body_name": "Body1",
            "deltas": {
                "body_count_before": 0,
                "body_count_after": 1,
                "body_count_delta": 1,
                "mass_g_before": 0.0,
                "mass_g_after": 7.85,
                "mass_g_delta": 7.85,
                "bbox_before": None,
                "bbox_after": {"min": [0, 0, 0], "max": [1, 1, 1]},
            },
        }
        out = _format_result("extrude", result)
        text = out[0].text
        assert "deltas:" in text
        assert "body_count: 0 → 1" in text
        assert "+1" in text
        assert "7.85" in text

    def test_render_view_returns_image_content(self):
        result = mock_command("render_view", {})
        out = _format_result("render_view", result)
        assert isinstance(out, list)
        # First block is text metadata, second is the image.
        kinds = [b.type for b in out]
        assert kinds == ["text", "image"]
        assert out[1].mimeType == "image/png"
        assert out[1].data == result["image_base64"]

    def test_non_dict_result(self):
        out = _format_result("ping", "pong")
        assert isinstance(out, list)
        assert "pong" in out[0].text
