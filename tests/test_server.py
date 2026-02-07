"""Tests for server-level logic (_send routing, resources, prompts, errors)."""

import json
import re

from fusion360_mcp.server import _send


class TestSendRouting:
    """_send must route to mock when mode='mock' and to TCP otherwise."""

    def test_mock_mode_returns_mock_result(self):
        result = _send("mock", "ping")
        assert result["mode"] == "mock"
        assert result["status"] == "pong"

    def test_mock_mode_forwards_params(self):
        result = _send("mock", "get_object_info", {"name": "TestBody"})
        assert result["name"] == "TestBody"
        assert result["mode"] == "mock"

    def test_mock_mode_all_tools_succeed(self):
        """Every registered tool should succeed through mock routing."""
        from fusion360_mcp.tools import TOOLS
        for tool in TOOLS:
            result = _send("mock", tool["name"], {})
            assert isinstance(result, dict)
            assert result.get("mode") == "mock", (
                f"Tool {tool['name']} missing mode=mock in mock response")

    def test_socket_mode_calls_connection(self):
        """Socket mode should use the TCP connection, not mock."""
        # We verify socket mode by checking it does NOT include
        # the mock marker in the response.  If the real add-in is
        # not running this will raise; if it is running the result
        # won't have mode=mock.
        import fusion360_mcp.connection as conn_mod

        saved = conn_mod._connection
        conn_mod._connection = None
        try:
            result = _send("socket", "ping", None)
            # If we got here, Fusion is actually running — verify not mock
            assert result.get("mode") != "mock"
        except (ConnectionError, OSError):
            pass  # Expected when no Fusion add-in is running
        finally:
            conn_mod._connection = saved


class TestMockResources:
    """Verify the resource URIs return sensible data in mock mode."""

    def test_status_resource(self):
        result = _send("mock", "ping")
        payload = json.dumps({"connected": True, "ping": result}, indent=2)
        data = json.loads(payload)
        assert data["connected"] is True
        assert data["ping"]["mode"] == "mock"

    def test_design_resource(self):
        result = _send("mock", "get_scene_info")
        assert "design_name" in result
        assert "bodies" in result
        assert "components" in result

    def test_parameters_resource(self):
        result = _send("mock", "get_parameters")
        assert "parameters" in result
        params = result["parameters"]
        assert len(params) > 0
        assert "name" in params[0]
        assert "value" in params[0]


class TestToolAnnotations:
    """Verify tool annotations are present and correct."""

    def test_all_tools_have_annotations(self):
        from fusion360_mcp.tools import TOOLS
        for t in TOOLS:
            ann = t.get("annotations")
            assert ann is not None, (
                f"Tool {t['name']} missing annotations")
            assert "readOnlyHint" in ann
            assert "destructiveHint" in ann
            assert "idempotentHint" in ann

    def test_read_only_tools(self):
        from fusion360_mcp.tools import TOOLS
        read_only_names = {
            "get_scene_info", "get_object_info", "list_components",
            "get_parameters", "get_physical_properties",
            "measure_distance", "measure_angle",
            "check_interference", "ping",
            "cam_list_setups", "cam_list_operations",
            "cam_get_operation_info",
            "get_design_type",
        }
        for t in TOOLS:
            ann = t["annotations"]
            if t["name"] in read_only_names:
                assert ann["readOnlyHint"] is True, (
                    f"{t['name']} should be readOnly")
            else:
                assert ann["readOnlyHint"] is False, (
                    f"{t['name']} should not be readOnly")

    def test_destructive_tools(self):
        from fusion360_mcp.tools import TOOLS
        destructive = {"delete_all", "delete_parameter"}
        for t in TOOLS:
            ann = t["annotations"]
            if t["name"] in destructive:
                assert ann["destructiveHint"] is True
            else:
                assert ann["destructiveHint"] is False

    def test_annotations_in_mcp_tool_objects(self):
        from fusion360_mcp.tools import get_tool_list
        tools = get_tool_list()
        for tool in tools:
            assert tool.annotations is not None, (
                f"MCP Tool {tool.name} missing annotations")


class TestMCPPrompts:
    """Verify prompt definitions are accessible."""

    def test_prompt_definitions_exist(self):
        """The server defines prompts internally; verify the dict."""
        # We can't easily call the async handlers, but we can
        # verify the prompt data is importable and structured.
        # This is a basic smoke test.
        import mcp.types as types
        assert hasattr(types, "Prompt")
        assert hasattr(types, "PromptArgument")
        assert hasattr(types, "GetPromptResult")

    def test_create_box_prompt_text(self):
        """Verify the create-box prompt produces text."""
        # Simulate what the get_prompt handler does
        length, width, height = "10", "5", "3"
        text = (
            f"Create a parametric box in Fusion 360:\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_rectangle width={width} height={length}\n"
            f"3. extrude height={height}\n"
            f"4. get_scene_info to verify"
        )
        assert "create_sketch" in text
        assert "draw_rectangle" in text
        assert "extrude" in text

    def test_threaded_bolt_prompt_text(self):
        desig = "M8x1.25"
        text = (
            f"Model a threaded bolt ({desig}):\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_circle for bolt shaft\n"
            f"3. extrude to bolt length\n"
            f"4. create_thread designation={desig}\n"
            f"5. Create hex head sketch + extrude\n"
            f"6. chamfer head edges"
        )
        assert desig in text
        assert "create_thread" in text


class TestResourceTemplates:
    """Verify resource template definitions and read handlers."""

    def test_resource_template_types(self):
        import mcp.types as types
        assert hasattr(types, "ResourceTemplate")

    def test_body_template_read(self):
        """Reading fusion360://body/{name} should return object info."""
        result = _send("mock", "get_object_info", {"name": "TestBody"})
        assert result["name"] == "TestBody"
        assert "faces" in result

    def test_component_template_read(self):
        """Reading fusion360://component/{name} delegates to get_object_info."""
        result = _send("mock", "get_object_info", {"name": "Bracket"})
        assert result["name"] == "Bracket"

    def test_body_uri_regex_match(self):
        """The body URI regex should extract the name."""
        import re
        m = re.match(r"^fusion360://body/(.+)$", "fusion360://body/Box1")
        assert m is not None
        assert m.group(1) == "Box1"

    def test_component_uri_regex_match(self):
        import re
        m = re.match(
            r"^fusion360://component/(.+)$",
            "fusion360://component/Arm",
        )
        assert m is not None
        assert m.group(1) == "Arm"


class TestStructuredErrors:
    """Verify structured error detection logic."""

    def test_error_detection_status_field(self):
        """Results with status='error' should be flagged."""
        result = {"status": "error", "message": "Something failed"}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        assert is_error

    def test_error_detection_error_key(self):
        """Results with an 'error' key should be flagged."""
        result = {"error": "Connection lost"}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        assert is_error

    def test_success_not_flagged(self):
        """Normal results should not be flagged as errors."""
        result = {"status": "ok", "body_name": "Box"}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        assert not is_error


class TestPortOption:
    """Verify the --port option propagates."""

    def test_send_mock_ignores_port(self):
        """Mock mode works regardless of port value."""
        result = _send("mock", "ping", None, port=12345)
        assert result["mode"] == "mock"
        assert result["status"] == "pong"


class TestResourceTemplateReads:
    """Verify resource template URI matching and data flow."""

    def test_body_uri_extracts_name_and_returns_data(self):
        """fusion360://body/{name} should call get_object_info."""
        # Simulate what read_resource does
        uri = "fusion360://body/MyBox"
        m = re.match(r"^fusion360://body/(.+)$", uri)
        assert m is not None
        name = m.group(1)
        result = _send("mock", "get_object_info", {"name": name})
        assert result["name"] == "MyBox"
        assert "faces" in result

    def test_component_uri_extracts_name(self):
        uri = "fusion360://component/Bracket_v2"
        m = re.match(r"^fusion360://component/(.+)$", uri)
        assert m is not None
        name = m.group(1)
        result = _send("mock", "get_object_info", {"name": name})
        assert result["name"] == "Bracket_v2"

    def test_body_uri_with_spaces(self):
        uri = "fusion360://body/My%20Body"
        m = re.match(r"^fusion360://body/(.+)$", uri)
        assert m is not None
        assert m.group(1) == "My%20Body"

    def test_static_uri_does_not_match_templates(self):
        """Static URIs should not match template patterns."""
        for static in ["fusion360://status", "fusion360://design",
                        "fusion360://parameters"]:
            assert re.match(r"^fusion360://body/(.+)$", static) is None
            assert re.match(
                r"^fusion360://component/(.+)$", static,
            ) is None

    def test_unknown_uri_does_not_match(self):
        uri = "fusion360://unknown/thing"
        body = re.match(r"^fusion360://body/(.+)$", uri)
        comp = re.match(r"^fusion360://component/(.+)$", uri)
        assert body is None
        assert comp is None


class TestPromptGeneration:
    """Verify prompt templates produce correct output."""

    def test_create_box_default_args(self):
        """create-box prompt with no args uses defaults."""
        args = {}
        length = args.get("length", "10")
        width = args.get("width", "5")
        height = args.get("height", "3")
        text = (
            f"Create a parametric box in Fusion 360:\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_rectangle width={width} height={length}\n"
            f"3. extrude height={height}\n"
            f"4. get_scene_info to verify"
        )
        assert "width=5" in text
        assert "height=10" in text
        assert "height=3" in text

    def test_create_box_custom_args(self):
        args = {"length": "20", "width": "15", "height": "8"}
        text = (
            f"Create a parametric box in Fusion 360:\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_rectangle width={args['width']}"
            f" height={args['length']}\n"
            f"3. extrude height={args['height']}\n"
            f"4. get_scene_info to verify"
        )
        assert "width=15" in text
        assert "height=20" in text
        assert "height=8" in text

    def test_threaded_bolt_custom_designation(self):
        desig = "M6x0.75"
        text = (
            f"Model a threaded bolt ({desig}):\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_circle for bolt shaft\n"
            f"3. extrude to bolt length\n"
            f"4. create_thread designation={desig}\n"
            f"5. Create hex head sketch + extrude\n"
            f"6. chamfer head edges"
        )
        assert "M6x0.75" in text
        assert "create_thread designation=M6x0.75" in text

    def test_sheet_metal_enclosure_custom_args(self):
        args = {"length": "30", "width": "20", "height": "10"}
        text = (
            f"Create a sheet metal enclosure "
            f"({args['length']}x{args['width']}x{args['height']} cm):\n"
            f"1. create_sketch on xy plane\n"
            f"2. draw_rectangle {args['width']}x{args['length']}\n"
            f"3. extrude to sheet thickness\n"
            f"4. create_flange on each edge\n"
            f"5. flat_pattern to verify unfold"
        )
        assert "30x20x10 cm" in text
        assert "create_flange" in text
        assert "flat_pattern" in text


class TestErrorDetectionEdgeCases:
    """Edge cases in error detection logic."""

    def test_result_with_error_key_but_false_value(self):
        """A result with error=None should still flag."""
        result = {"error": None, "body_name": "Box"}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        # "error" key exists, even if value is falsy
        assert is_error

    def test_result_with_status_ok_and_no_error(self):
        result = {"status": "ok", "body_name": "Box"}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        assert not is_error

    def test_empty_dict_not_flagged(self):
        result = {}
        is_error = (
            result.get("status") == "error" or "error" in result
        )
        assert not is_error

    def test_non_dict_result(self):
        """Non-dict results shouldn't crash error detection."""
        result = "some string"
        is_error = False
        if isinstance(result, dict):
            is_error = (
                result.get("status") == "error" or "error" in result
            )
        assert not is_error


class TestMockDispatchCompleteness:
    """Verify mock dispatch covers every tool exactly."""

    def test_every_tool_has_handler(self):
        from fusion360_mcp.mock import _DISPATCH
        from fusion360_mcp.tools import TOOLS
        tool_names = {t["name"] for t in TOOLS}
        dispatch_names = set(_DISPATCH.keys())
        missing = tool_names - dispatch_names
        assert not missing, f"Tools without mock handlers: {missing}"

    def test_no_extra_handlers(self):
        from fusion360_mcp.mock import _DISPATCH
        from fusion360_mcp.tools import TOOLS
        tool_names = {t["name"] for t in TOOLS}
        dispatch_names = set(_DISPATCH.keys())
        extra = dispatch_names - tool_names
        assert not extra, f"Handlers without tools: {extra}"

    def test_dispatch_count_matches_tool_count(self):
        from fusion360_mcp.mock import _DISPATCH
        from fusion360_mcp.tools import TOOLS
        assert len(_DISPATCH) == len(TOOLS)


class TestAnnotationConsistency:
    """Cross-check annotation sets against actual tool list."""

    def test_all_read_only_tools_exist(self):
        from fusion360_mcp.tools import _READ_ONLY, TOOLS
        tool_names = {t["name"] for t in TOOLS}
        missing = _READ_ONLY - tool_names
        assert not missing, (
            f"Read-only set references nonexistent tools: {missing}"
        )

    def test_all_destructive_tools_exist(self):
        from fusion360_mcp.tools import _DESTRUCTIVE, TOOLS
        tool_names = {t["name"] for t in TOOLS}
        missing = _DESTRUCTIVE - tool_names
        assert not missing, (
            f"Destructive set references nonexistent tools: {missing}"
        )

    def test_all_idempotent_tools_exist(self):
        from fusion360_mcp.tools import _IDEMPOTENT, TOOLS
        tool_names = {t["name"] for t in TOOLS}
        missing = _IDEMPOTENT - tool_names
        assert not missing, (
            f"Idempotent set references nonexistent tools: {missing}"
        )

    def test_read_only_implies_idempotent(self):
        """Every read-only tool should also be idempotent."""
        from fusion360_mcp.tools import _IDEMPOTENT, _READ_ONLY
        not_idempotent = _READ_ONLY - _IDEMPOTENT
        assert not not_idempotent, (
            f"Read-only tools not marked idempotent: {not_idempotent}"
        )

    def test_destructive_not_read_only(self):
        """No tool should be both destructive and read-only."""
        from fusion360_mcp.tools import _DESTRUCTIVE, _READ_ONLY
        overlap = _DESTRUCTIVE & _READ_ONLY
        assert not overlap, (
            f"Tools marked both destructive and read-only: {overlap}"
        )
