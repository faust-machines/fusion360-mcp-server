"""Tests for the mock command dispatch."""

import pytest

from fusion360_mcp.mock import _DISPATCH, mock_command
from fusion360_mcp.tools import TOOLS


class TestMockDispatchCoverage:
    """Every tool defined in tools.py must have a mock handler."""

    def test_every_tool_has_a_mock_handler(self):
        tool_names = {t["name"] for t in TOOLS}
        dispatch_names = set(_DISPATCH.keys())
        missing = tool_names - dispatch_names
        assert not missing, f"Tools without mock handlers: {missing}"


class TestMockResponseStructure:
    """Every mock response must be a dict and include 'mode': 'mock'."""

    @pytest.mark.parametrize("tool_name", [t["name"] for t in TOOLS])
    def test_response_is_dict_with_mode_mock(self, tool_name):
        result = mock_command(tool_name, {})
        assert isinstance(result, dict)
        assert result["mode"] == "mock"


class TestMockPing:
    def test_ping_returns_pong(self):
        result = mock_command("ping")
        assert result["status"] == "pong"
        assert result["mode"] == "mock"


class TestMockSceneQuery:
    def test_get_scene_info(self):
        result = mock_command("get_scene_info")
        assert "design_name" in result
        assert "bodies" in result
        assert "sketches" in result

    def test_get_object_info(self):
        result = mock_command("get_object_info", {"name": "TestBody"})
        assert result["name"] == "TestBody"
        assert "faces" in result


class TestMockSketch:
    def test_create_sketch(self):
        result = mock_command("create_sketch", {"plane": "yz"})
        assert "yz" in result["sketch_name"]
        assert result["plane"] == "yz"

    def test_draw_rectangle(self):
        result = mock_command("draw_rectangle", {"width": 5, "height": 3})
        assert result["width"] == 5
        assert result["height"] == 3

    def test_draw_circle(self):
        result = mock_command("draw_circle", {"radius": 2.5})
        assert result["radius"] == 2.5

    def test_draw_line(self):
        result = mock_command("draw_line", {
            "start_x": 0, "start_y": 0, "end_x": 10, "end_y": 5,
        })
        assert result["start"] == [0, 0]
        assert result["end"] == [10, 5]


class TestMockFeatures:
    def test_extrude(self):
        result = mock_command("extrude", {"height": 3, "operation": "cut"})
        assert result["height"] == 3
        assert result["operation"] == "cut"
        assert "body_name" in result

    def test_revolve(self):
        result = mock_command("revolve", {"angle": 180})
        assert result["angle"] == 180

    def test_fillet(self):
        result = mock_command("fillet", {"radius": 0.5, "body_name": "Box"})
        assert result["body_name"] == "Box"
        assert result["radius"] == 0.5

    def test_chamfer(self):
        result = mock_command("chamfer", {"distance": 0.2, "body_name": "Box"})
        assert result["body_name"] == "Box"

    def test_shell(self):
        result = mock_command("shell", {"thickness": 0.3, "body_name": "Box"})
        assert result["body_name"] == "Box"

    def test_mirror(self):
        result = mock_command("mirror", {"mirror_plane": "xz", "body_name": "Box"})
        assert result["mirror_plane"] == "xz"
        assert "new_body_name" in result


class TestMockBodyOps:
    def test_move_body(self):
        result = mock_command("move_body", {"body_name": "B1", "x": 5, "z": 3})
        assert result["body_name"] == "B1"
        assert result["translation"] == [5, 0, 3]

    def test_export_stl(self):
        result = mock_command("export_stl", {"body_name": "B1"})
        assert result["body_name"] == "B1"
        assert "file_path" in result

    def test_boolean_operation(self):
        result = mock_command("boolean_operation", {
            "target_body": "A", "tool_body": "B", "operation": "cut",
        })
        assert result["target_body"] == "A"
        assert result["operation"] == "cut"


class TestMockSceneControl:
    def test_delete_all(self):
        result = mock_command("delete_all")
        assert result["deleted"] is True

    def test_undo(self):
        result = mock_command("undo")
        assert result["undone"] is True
        assert "design_type" in result


class TestMockExecuteCode:
    def test_execute_code(self):
        result = mock_command("execute_code", {"code": "1 + 1"})
        assert result["code"] == "1 + 1"
        assert "result" in result


class TestMockNewGeometry:
    def test_sweep(self):
        result = mock_command("sweep", {
            "profile_index": 0, "path_sketch_name": "PathSketch1",
        })
        assert result["path_sketch_name"] == "PathSketch1"
        assert "body_name" in result

    def test_loft(self):
        result = mock_command("loft", {
            "profile_sketch_names": ["Top", "Bottom"],
        })
        assert result["profile_sketch_names"] == ["Top", "Bottom"]

    def test_create_polygon(self):
        result = mock_command("create_polygon", {"sides": 8, "radius": 2})
        assert result["sides"] == 8
        assert result["radius"] == 2

    def test_draw_arc(self):
        result = mock_command("draw_arc", {
            "center_x": 0, "center_y": 0,
            "start_x": 1, "start_y": 0,
            "sweep_angle": 90,
        })
        assert result["sweep_angle"] == 90

    def test_create_hole(self):
        result = mock_command("create_hole", {
            "diameter": 0.5, "depth": 2, "body_name": "Plate",
        })
        assert result["diameter"] == 0.5
        assert result["depth"] == 2
        assert result["body_name"] == "Plate"

    def test_rectangular_pattern(self):
        result = mock_command("rectangular_pattern", {
            "body_name": "Pin", "x_count": 3, "y_count": 2,
        })
        assert result["x_count"] == 3
        assert result["y_count"] == 2
        assert result["created_bodies"] == 6

    def test_circular_pattern(self):
        result = mock_command("circular_pattern", {
            "body_name": "Lug", "count": 4, "axis": "z",
        })
        assert result["count"] == 4
        assert result["axis"] == "z"


class TestMockAssembly:
    def test_create_component(self):
        result = mock_command("create_component", {"name": "Bracket"})
        assert result["component_name"] == "Bracket"

    def test_add_joint(self):
        result = mock_command("add_joint", {
            "component_one": "A", "component_two": "B", "joint_type": "revolute",
        })
        assert result["joint_type"] == "revolute"
        assert "joint_name" in result

    def test_list_components(self):
        result = mock_command("list_components")
        assert "components" in result
        assert len(result["components"]) > 0


class TestMockExport:
    def test_export_step(self):
        result = mock_command("export_step", {"body_name": "Shaft"})
        assert result["body_name"] == "Shaft"
        assert "file_path" in result

    def test_export_f3d(self):
        result = mock_command("export_f3d", {})
        assert "file_path" in result


class TestMockParameters:
    def test_get_parameters(self):
        result = mock_command("get_parameters")
        assert "parameters" in result
        assert len(result["parameters"]) > 0
        assert "name" in result["parameters"][0]
        assert "value" in result["parameters"][0]

    def test_create_parameter(self):
        result = mock_command("create_parameter", {
            "name": "wall_thickness", "value": 2.0, "unit": "mm",
        })
        assert result["name"] == "wall_thickness"
        assert result["value"] == 2.0
        assert result["unit"] == "mm"

    def test_set_parameter(self):
        result = mock_command("set_parameter", {
            "name": "wall_thickness", "value": 3.0,
        })
        assert result["name"] == "wall_thickness"
        assert result["value"] == 3.0

    def test_delete_parameter(self):
        result = mock_command("delete_parameter", {"name": "wall_thickness"})
        assert result["name"] == "wall_thickness"
        assert result["deleted"] is True


class TestMockConstraints:
    def test_add_constraint_coincident(self):
        result = mock_command("add_constraint", {
            "constraint_type": "coincident",
            "entity_one": 0, "entity_two": 3,
        })
        assert result["constraint_type"] == "coincident"
        assert result["entity_one"] == 0
        assert result["entity_two"] == 3

    def test_add_dimension_distance(self):
        result = mock_command("add_dimension", {
            "dimension_type": "distance", "value": 5.0,
            "entity_one": 0, "entity_two": 1,
        })
        assert result["dimension_type"] == "distance"
        assert result["value"] == 5.0


class TestMockConstructionGeometry:
    def test_create_construction_plane_offset(self):
        result = mock_command("create_construction_plane", {
            "method": "offset", "plane": "xy", "offset": 5,
        })
        assert result["method"] == "offset"
        assert "plane_name" in result

    def test_create_construction_axis_two_points(self):
        result = mock_command("create_construction_axis", {
            "method": "two_points",
            "point_one": [0, 0, 0], "point_two": [1, 0, 0],
        })
        assert result["method"] == "two_points"
        assert "axis_name" in result


class TestMockSplines:
    def test_draw_spline_fit_points(self):
        pts = [[0, 0], [1, 2], [3, 1], [5, 0]]
        result = mock_command("draw_spline", {
            "spline_type": "fit_points", "points": pts,
        })
        assert result["spline_type"] == "fit_points"
        assert result["point_count"] == 4

    def test_draw_spline_control_points(self):
        pts = [[0, 0, 0], [1, 2, 0], [3, 1, 0]]
        result = mock_command("draw_spline", {
            "spline_type": "control_points", "points": pts, "degree": 3,
        })
        assert result["spline_type"] == "control_points"


class TestMockCurveOps:
    def test_offset_curve(self):
        result = mock_command("offset_curve", {
            "curve_index": 0, "offset_distance": 0.5,
        })
        assert result["offset_distance"] == 0.5
        assert "new_curve_count" in result

    def test_trim_curve(self):
        result = mock_command("trim_curve", {
            "curve_index": 2, "point_x": 1.0, "point_y": 0.5,
        })
        assert result["curve_index"] == 2
        assert result["trimmed"] is True

    def test_extend_curve(self):
        result = mock_command("extend_curve", {
            "curve_index": 1, "point_x": 5.0, "point_y": 0,
        })
        assert result["curve_index"] == 1
        assert result["extended"] is True


class TestMockAdvancedFeatures:
    def test_create_thread(self):
        result = mock_command("create_thread", {
            "body_name": "Bolt", "face_index": 0,
            "thread_designation": "M8x1.25", "is_modeled": True,
        })
        assert result["body_name"] == "Bolt"
        assert result["thread_designation"] == "M8x1.25"
        assert result["is_modeled"] is True

    def test_draft_faces(self):
        result = mock_command("draft_faces", {
            "body_name": "Housing", "angle": 3,
        })
        assert result["body_name"] == "Housing"
        assert result["angle"] == 3

    def test_split_body(self):
        result = mock_command("split_body", {"body_name": "Block"})
        assert result["body_name"] == "Block"
        assert len(result["result_bodies"]) == 2

    def test_split_face(self):
        result = mock_command("split_face", {"body_name": "Block"})
        assert result["body_name"] == "Block"
        assert result["faces_split"] > 0

    def test_offset_faces(self):
        result = mock_command("offset_faces", {
            "body_name": "Box", "distance": 0.2,
        })
        assert result["body_name"] == "Box"
        assert result["distance"] == 0.2

    def test_scale_body(self):
        result = mock_command("scale_body", {
            "body_name": "Widget", "scale": 2.0,
        })
        assert result["body_name"] == "Widget"
        assert result["scale"] == 2.0


class TestMockDirectPrimitives:
    def test_create_box(self):
        result = mock_command("create_box", {
            "length": 10, "width": 5, "height": 3,
        })
        assert result["length"] == 10
        assert result["width"] == 5
        assert result["height"] == 3
        assert "body_name" in result

    def test_create_cylinder(self):
        result = mock_command("create_cylinder", {
            "radius": 2, "height": 8,
        })
        assert result["radius"] == 2
        assert result["height"] == 8

    def test_create_sphere(self):
        result = mock_command("create_sphere", {"radius": 3})
        assert result["radius"] == 3

    def test_create_torus(self):
        result = mock_command("create_torus", {
            "major_radius": 5, "minor_radius": 1,
        })
        assert result["major_radius"] == 5
        assert result["minor_radius"] == 1


class TestMockAssemblyExtended:
    def test_create_as_built_joint(self):
        result = mock_command("create_as_built_joint", {
            "component_one": "Arm",
            "component_two": "Base",
            "joint_type": "revolute",
        })
        assert result["component_one"] == "Arm"
        assert result["joint_type"] == "revolute"
        assert "joint_name" in result

    def test_create_rigid_group(self):
        result = mock_command("create_rigid_group", {
            "component_names": ["A", "B", "C"],
        })
        assert result["component_names"] == ["A", "B", "C"]
        assert "rigid_group_name" in result


class TestMockInspection:
    def test_measure_distance(self):
        result = mock_command("measure_distance", {
            "entity_one": "Body1", "entity_two": "Body2",
        })
        assert result["entity_one"] == "Body1"
        assert isinstance(result["distance"], float)
        assert "point_one" in result

    def test_measure_angle(self):
        result = mock_command("measure_angle", {
            "entity_one": "TopFace", "entity_two": "SideFace",
        })
        assert result["entity_one"] == "TopFace"
        assert "angle_degrees" in result

    def test_get_physical_properties(self):
        result = mock_command("get_physical_properties", {
            "body_name": "Bracket",
        })
        assert result["body_name"] == "Bracket"
        assert "mass" in result
        assert "volume" in result
        assert "area" in result
        assert "center_of_mass" in result

    def test_create_section_analysis(self):
        result = mock_command("create_section_analysis", {
            "plane": "xz", "offset": 2.5,
        })
        assert result["plane"] == "xz"
        assert result["offset"] == 2.5
        assert "analysis_name" in result

    def test_check_interference(self):
        result = mock_command("check_interference", {
            "component_names": ["Gear1", "Gear2"],
        })
        assert result["component_names"] == ["Gear1", "Gear2"]
        assert "interference_count" in result


class TestMockAppearance:
    def test_set_appearance(self):
        result = mock_command("set_appearance", {
            "target_name": "Housing",
            "appearance_name": "Aluminum - Anodized Red",
        })
        assert result["target_name"] == "Housing"
        assert result["appearance_name"] == "Aluminum - Anodized Red"
        assert result["applied"] is True


class TestMockProjectGeometry:
    def test_project_geometry(self):
        result = mock_command("project_geometry", {
            "source_name": "TopEdge",
            "sketch_name": "Sketch2",
        })
        assert result["source_name"] == "TopEdge"
        assert result["sketch_name"] == "Sketch2"
        assert "projected_curves" in result


class TestMockTimelineControl:
    def test_suppress_feature(self):
        result = mock_command("suppress_feature", {"feature_name": "Extrude1"})
        assert result["feature_name"] == "Extrude1"
        assert result["suppressed"] is True

    def test_unsuppress_feature(self):
        result = mock_command("unsuppress_feature", {"feature_name": "Extrude1"})
        assert result["feature_name"] == "Extrude1"
        assert result["suppressed"] is False


class TestMockSurfaceOps:
    def test_patch_surface(self):
        result = mock_command("patch_surface", {"sketch_name": "Boundary1"})
        assert result["sketch_name"] == "Boundary1"
        assert "body_name" in result

    def test_stitch_surfaces(self):
        result = mock_command("stitch_surfaces", {
            "body_names": ["Surf1", "Surf2"],
        })
        assert result["body_names"] == ["Surf1", "Surf2"]
        assert "result_body" in result

    def test_thicken_surface(self):
        result = mock_command("thicken_surface", {
            "body_name": "Surf1", "thickness": 0.2,
        })
        assert result["body_name"] == "Surf1"
        assert result["thickness"] == 0.2
        assert "result_body" in result

    def test_ruled_surface(self):
        result = mock_command("ruled_surface", {
            "body_name": "Body1", "edge_index": 2, "distance": 1.5,
        })
        assert result["edge_index"] == 2
        assert result["distance"] == 1.5

    def test_trim_surface(self):
        result = mock_command("trim_surface", {
            "body_name": "Surf1", "tool_name": "Cutter",
        })
        assert result["body_name"] == "Surf1"
        assert result["tool_name"] == "Cutter"
        assert result["trimmed"] is True


class TestMockSheetMetal:
    def test_create_flange(self):
        result = mock_command("create_flange", {
            "body_name": "Sheet1", "edge_index": 0, "height": 2.0,
        })
        assert result["body_name"] == "Sheet1"
        assert result["height"] == 2.0

    def test_create_bend(self):
        result = mock_command("create_bend", {
            "body_name": "Sheet1", "angle": 45,
        })
        assert result["body_name"] == "Sheet1"
        assert result["angle"] == 45

    def test_flat_pattern(self):
        result = mock_command("flat_pattern", {"body_name": "Sheet1"})
        assert result["body_name"] == "Sheet1"
        assert "flat_pattern_name" in result

    def test_unfold(self):
        result = mock_command("unfold", {
            "body_name": "Sheet1", "bend_indices": [0, 2, 3],
        })
        assert result["body_name"] == "Sheet1"
        assert result["bends_unfolded"] == 3


class TestMockCAM:
    def test_cam_create_setup(self):
        result = mock_command("cam_create_setup", {
            "body_name": "Part1", "name": "MySetup",
        })
        assert result["setup_name"] == "MySetup"
        assert result["body_name"] == "Part1"
        assert result["operation_type"] == "milling"

    def test_cam_create_operation(self):
        result = mock_command("cam_create_operation", {
            "setup_name": "Setup1",
            "strategy": "2d_adaptive",
            "name": "Rough1",
        })
        assert result["strategy"] == "2d_adaptive"
        assert result["operation_name"] == "Rough1"

    def test_cam_generate_toolpath(self):
        result = mock_command("cam_generate_toolpath", {
            "setup_name": "Setup1",
        })
        assert result["generated"] is True
        assert result["toolpath_count"] >= 1

    def test_cam_post_process(self):
        result = mock_command("cam_post_process", {
            "setup_name": "Setup1",
            "post_processor": "grbl",
        })
        assert result["post_processor"] == "grbl"
        assert "output_file" in result

    def test_cam_list_setups(self):
        result = mock_command("cam_list_setups")
        assert "setups" in result
        assert len(result["setups"]) > 0
        assert "name" in result["setups"][0]

    def test_cam_list_operations(self):
        result = mock_command("cam_list_operations", {
            "setup_name": "Setup1",
        })
        assert result["setup_name"] == "Setup1"
        assert "operations" in result
        assert len(result["operations"]) > 0

    def test_cam_get_operation_info(self):
        result = mock_command("cam_get_operation_info", {
            "setup_name": "Setup1",
            "operation_name": "Face1",
        })
        assert result["operation_name"] == "Face1"
        assert "strategy" in result
        assert "tool_diameter" in result
        assert "has_toolpath" in result


class TestMockDesignTypeSafety:
    def test_get_design_type(self):
        result = mock_command("get_design_type")
        assert result["design_type"] == "parametric"
        assert result["design_type_id"] == 1
        assert result["mode"] == "mock"

    def test_set_design_type_parametric(self):
        result = mock_command("set_design_type", {"design_type": "parametric"})
        assert result["changed"] is True
        assert result["design_type"] == "parametric"

    def test_set_design_type_direct(self):
        result = mock_command("set_design_type", {"design_type": "direct"})
        assert result["changed"] is True
        assert result["design_type"] == "direct"


class TestMockFallback:
    def test_unknown_command_returns_warning(self):
        result = mock_command("totally_unknown_command", {"x": 1})
        assert "warning" in result
        assert result["mode"] == "mock"
        assert result["params_received"] == {"x": 1}
