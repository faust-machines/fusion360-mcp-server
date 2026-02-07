"""
Mock responses for every Fusion 360 MCP command.

Used when the server is started with ``--mode mock`` so the full
tool→response pipeline can be tested without a running Fusion instance.
Every response includes ``"mode": "mock"`` so callers know it's simulated.
"""

from typing import Any


def mock_command(command_type: str, params: dict[str, Any] | None = None) -> dict:
    """Return a plausible mock response for *command_type*."""
    params = params or {}
    handler = _DISPATCH.get(command_type, _default_mock)
    result = handler(params)
    result["mode"] = "mock"
    return result


# ── individual mock handlers ──────────────────────────────────────────

def _ping(_p: dict) -> dict:
    return {"status": "pong"}


def _get_scene_info(_p: dict) -> dict:
    return {
        "design_name": "MockDesign",
        "bodies": ["Body1"],
        "sketches": ["Sketch1"],
        "features": ["Extrude1"],
        "components": ["RootComponent"],
    }


def _get_object_info(p: dict) -> dict:
    name = p.get("name", "Unknown")
    return {
        "name": name,
        "type": "BRepBody",
        "faces": 6,
        "edges": 12,
        "vertices": 8,
        "bounding_box": {"min": [0, 0, 0], "max": [1, 1, 1]},
    }


def _create_sketch(p: dict) -> dict:
    plane = p.get("plane", "xy")
    return {"sketch_name": f"Sketch_mock_{plane}", "plane": plane}


def _draw_rectangle(p: dict) -> dict:
    return {
        "sketch_name": "Sketch_mock_xy",
        "width": p.get("width", 1),
        "height": p.get("height", 1),
    }


def _draw_circle(p: dict) -> dict:
    return {"sketch_name": "Sketch_mock_xy", "radius": p.get("radius", 1)}


def _draw_line(p: dict) -> dict:
    return {
        "sketch_name": "Sketch_mock_xy",
        "start": [p.get("start_x", 0), p.get("start_y", 0)],
        "end": [p.get("end_x", 1), p.get("end_y", 1)],
    }


def _extrude(p: dict) -> dict:
    return {
        "body_name": "Body_mock",
        "height": p.get("height", 1),
        "operation": p.get("operation", "new_body"),
    }


def _revolve(p: dict) -> dict:
    return {
        "body_name": "Body_mock_revolve",
        "angle": p.get("angle", 360),
        "operation": p.get("operation", "new_body"),
    }


def _fillet(p: dict) -> dict:
    return {"body_name": p.get("body_name", "Body1"), "radius": p.get("radius", 0.1)}


def _chamfer(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "distance": p.get("distance", 0.1),
    }


def _shell(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "thickness": p.get("thickness", 0.1),
    }


def _mirror(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "mirror_plane": p.get("mirror_plane", "yz"),
        "new_body_name": "Body1_mirrored",
    }


def _move_body(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "translation": [p.get("x", 0), p.get("y", 0), p.get("z", 0)],
    }


def _export_stl(p: dict) -> dict:
    name = p.get("body_name", "Body1")
    path = p.get("file_path", f"~/Desktop/{name}.stl")
    return {"body_name": name, "file_path": path}


def _boolean_operation(p: dict) -> dict:
    return {
        "target_body": p.get("target_body", "Body1"),
        "tool_body": p.get("tool_body", "Body2"),
        "operation": p.get("operation", "join"),
        "result_body": p.get("target_body", "Body1"),
    }


def _delete_all(_p: dict) -> dict:
    return {"deleted": True}


def _undo(_p: dict) -> dict:
    return {"undone": True, "design_type": 1}


def _execute_code(p: dict) -> dict:
    return {"executed": True, "code": p.get("code", ""), "result": "None", "output": ""}


# ── design type safety ───────────────────────────────────────────────

def _get_design_type(_p: dict) -> dict:
    return {"design_type": "parametric", "design_type_id": 1}


def _set_design_type(p: dict) -> dict:
    dt = p.get("design_type", "parametric")
    return {"changed": True, "design_type": dt}


# ── new geometry tools ────────────────────────────────────────────────

def _sweep(p: dict) -> dict:
    return {
        "body_name": "Body_mock_sweep",
        "path_sketch_name": p.get("path_sketch_name", "PathSketch"),
        "operation": p.get("operation", "new_body"),
    }


def _loft(p: dict) -> dict:
    return {
        "body_name": "Body_mock_loft",
        "profile_sketch_names": p.get("profile_sketch_names", []),
        "operation": p.get("operation", "new_body"),
    }


def _create_polygon(p: dict) -> dict:
    return {
        "sketch_name": "Sketch_mock_xy",
        "sides": p.get("sides", 6),
        "radius": p.get("radius", 1),
    }


def _draw_arc(p: dict) -> dict:
    return {
        "sketch_name": "Sketch_mock_xy",
        "center": [p.get("center_x", 0), p.get("center_y", 0)],
        "sweep_angle": p.get("sweep_angle", 90),
    }


def _create_hole(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "diameter": p.get("diameter", 0.5),
        "depth": p.get("depth", 1),
    }


def _rectangular_pattern(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "x_count": p.get("x_count", 1),
        "y_count": p.get("y_count", 1),
        "created_bodies": p.get("x_count", 1) * p.get("y_count", 1),
    }


def _circular_pattern(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "count": p.get("count", 3),
        "axis": p.get("axis", "z"),
        "total_angle": p.get("total_angle", 360),
    }


# ── assembly tools ────────────────────────────────────────────────────

def _create_component(p: dict) -> dict:
    return {
        "component_name": p.get("name", "Component1"),
        "parent": p.get("parent_name", "RootComponent"),
    }


def _add_joint(p: dict) -> dict:
    return {
        "component_one": p.get("component_one", "Comp1"),
        "component_two": p.get("component_two", "Comp2"),
        "joint_type": p.get("joint_type", "rigid"),
        "joint_name": "Joint_mock",
    }


def _list_components(_p: dict) -> dict:
    return {
        "components": [
            {"name": "RootComponent", "bodies": ["Body1"]},
            {"name": "SubComponent1", "bodies": []},
        ],
    }


# ── export tools ──────────────────────────────────────────────────────

def _export_step(p: dict) -> dict:
    name = p.get("body_name", "Body1")
    path = p.get("file_path", f"~/Desktop/{name}.step")
    return {"body_name": name, "file_path": path}


def _export_f3d(p: dict) -> dict:
    path = p.get("file_path", "~/Desktop/MockDesign.f3d")
    return {"file_path": path}


# ── parameter tools ───────────────────────────────────────────────────

def _get_parameters(_p: dict) -> dict:
    return {
        "parameters": [
            {"name": "width", "value": 10.0, "unit": "mm", "comment": ""},
            {"name": "height", "value": 5.0, "unit": "mm", "comment": ""},
        ],
    }


def _create_parameter(p: dict) -> dict:
    return {
        "name": p.get("name", "param1"),
        "value": p.get("value", 0),
        "unit": p.get("unit", "mm"),
        "comment": p.get("comment", ""),
    }


def _set_parameter(p: dict) -> dict:
    return {"name": p.get("name", "param1"), "value": p.get("value", 0)}


def _delete_parameter(p: dict) -> dict:
    return {"name": p.get("name", "param1"), "deleted": True}


# ── sketch constraints & dimensions ───────────────────────────────────

def _add_constraint(p: dict) -> dict:
    return {
        "constraint_type": p.get("constraint_type", "coincident"),
        "entity_one": p.get("entity_one", 0),
        "entity_two": p.get("entity_two", 1),
        "sketch_name": p.get("sketch_name", "Sketch1"),
    }


def _add_dimension(p: dict) -> dict:
    return {
        "dimension_type": p.get("dimension_type", "distance"),
        "value": p.get("value", 1.0),
        "entity_one": p.get("entity_one", 0),
        "sketch_name": p.get("sketch_name", "Sketch1"),
    }


# ── construction geometry ─────────────────────────────────────────────

def _create_construction_plane(p: dict) -> dict:
    return {
        "plane_name": "ConstructionPlane_mock",
        "method": p.get("method", "offset"),
    }


def _create_construction_axis(p: dict) -> dict:
    return {
        "axis_name": "ConstructionAxis_mock",
        "method": p.get("method", "two_points"),
    }


# ── splines ───────────────────────────────────────────────────────────

def _draw_spline(p: dict) -> dict:
    return {
        "sketch_name": "Sketch_mock_xy",
        "spline_type": p.get("spline_type", "fit_points"),
        "point_count": len(p.get("points", [])),
    }


# ── sketch curve operations ──────────────────────────────────────────

def _offset_curve(p: dict) -> dict:
    return {
        "sketch_name": p.get("sketch_name", "Sketch1"),
        "offset_distance": p.get("offset_distance", 0.5),
        "new_curve_count": 4,
    }


def _trim_curve(p: dict) -> dict:
    return {
        "sketch_name": p.get("sketch_name", "Sketch1"),
        "curve_index": p.get("curve_index", 0),
        "trimmed": True,
    }


def _extend_curve(p: dict) -> dict:
    return {
        "sketch_name": p.get("sketch_name", "Sketch1"),
        "curve_index": p.get("curve_index", 0),
        "extended": True,
    }


# ── advanced features ─────────────────────────────────────────────────

def _create_thread(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "face_index": p.get("face_index", 0),
        "is_modeled": p.get("is_modeled", False),
        "thread_designation": p.get(
            "thread_designation", "M10x1.5"
        ),
    }


def _draft_faces(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "angle": p.get("angle", 5),
        "face_count": 4,
    }


def _split_body(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "result_bodies": ["Body1", "Body1_split"],
    }


def _split_face(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "faces_split": 2,
    }


def _offset_faces(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "distance": p.get("distance", 0.5),
    }


def _scale_body(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "scale": p.get("scale", 1.0),
    }


# ── direct primitives ────────────────────────────────────────────────

def _create_box(p: dict) -> dict:
    return {
        "body_name": "Box_mock",
        "length": p.get("length", 1),
        "width": p.get("width", 1),
        "height": p.get("height", 1),
    }


def _create_cylinder(p: dict) -> dict:
    return {
        "body_name": "Cylinder_mock",
        "radius": p.get("radius", 1),
        "height": p.get("height", 1),
    }


def _create_sphere(p: dict) -> dict:
    return {
        "body_name": "Sphere_mock",
        "radius": p.get("radius", 1),
    }


def _create_torus(p: dict) -> dict:
    return {
        "body_name": "Torus_mock",
        "major_radius": p.get("major_radius", 2),
        "minor_radius": p.get("minor_radius", 0.5),
    }


# ── assembly (extended) ──────────────────────────────────────────────

def _create_as_built_joint(p: dict) -> dict:
    return {
        "component_one": p.get("component_one", "Comp1"),
        "component_two": p.get("component_two", "Comp2"),
        "joint_type": p.get("joint_type", "rigid"),
        "joint_name": "AsBuiltJoint_mock",
    }


def _create_rigid_group(p: dict) -> dict:
    return {
        "component_names": p.get("component_names", []),
        "rigid_group_name": "RigidGroup_mock",
    }


# ── inspection / analysis ────────────────────────────────────────────

def _measure_distance(p: dict) -> dict:
    return {
        "entity_one": p.get("entity_one", "Body1"),
        "entity_two": p.get("entity_two", "Body2"),
        "distance": 2.54,
        "point_one": [0, 0, 0],
        "point_two": [2.54, 0, 0],
    }


def _measure_angle(p: dict) -> dict:
    return {
        "entity_one": p.get("entity_one", "Face1"),
        "entity_two": p.get("entity_two", "Face2"),
        "angle_degrees": 90.0,
    }


def _get_physical_properties(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "mass": 0.785,
        "volume": 1.0,
        "area": 6.0,
        "density": 0.00785,
        "center_of_mass": [0.5, 0.5, 0.5],
    }


def _create_section_analysis(p: dict) -> dict:
    return {
        "plane": p.get("plane", "yz"),
        "offset": p.get("offset", 0),
        "analysis_name": "SectionAnalysis_mock",
    }


def _check_interference(p: dict) -> dict:
    return {
        "component_names": p.get("component_names", []),
        "interference_count": 0,
        "interferences": [],
    }


# ── appearance ────────────────────────────────────────────────────────

def _set_appearance(p: dict) -> dict:
    return {
        "target_name": p.get("target_name", "Body1"),
        "appearance_name": p.get(
            "appearance_name", "Steel - Satin"
        ),
        "applied": True,
    }


# ── project geometry ─────────────────────────────────────────────────

def _project_geometry(p: dict) -> dict:
    return {
        "source_name": p.get("source_name", "Body1"),
        "sketch_name": p.get("sketch_name", "Sketch1"),
        "projected_curves": 4,
    }


# ── timeline control ─────────────────────────────────────────────────

def _suppress_feature(p: dict) -> dict:
    return {
        "feature_name": p.get("feature_name", "Feature1"),
        "suppressed": True,
    }


def _unsuppress_feature(p: dict) -> dict:
    return {
        "feature_name": p.get("feature_name", "Feature1"),
        "suppressed": False,
    }


# ── surface operations ──────────────────────────────────────────────

def _patch_surface(p: dict) -> dict:
    return {
        "sketch_name": p.get("sketch_name", "Sketch1"),
        "body_name": "PatchSurface_mock",
        "continuity": p.get("continuity", "connected"),
    }


def _stitch_surfaces(p: dict) -> dict:
    return {
        "body_names": p.get("body_names", []),
        "result_body": "StitchedBody_mock",
        "tolerance": p.get("tolerance", 0.01),
    }


def _thicken_surface(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Surface1"),
        "thickness": p.get("thickness", 0.1),
        "result_body": "ThickenedBody_mock",
    }


def _ruled_surface(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Body1"),
        "edge_index": p.get("edge_index", 0),
        "distance": p.get("distance", 1.0),
        "result_body": "RuledSurface_mock",
    }


def _trim_surface(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "Surface1"),
        "tool_name": p.get("tool_name", "Tool1"),
        "trimmed": True,
    }


# ── sheet metal ─────────────────────────────────────────────────────

def _create_flange(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "SheetBody1"),
        "edge_index": p.get("edge_index", 0),
        "height": p.get("height", 1.0),
        "angle": p.get("angle", 90),
    }


def _create_bend(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "SheetBody1"),
        "angle": p.get("angle", 90),
        "bend_radius": p.get("bend_radius", 0.1),
    }


def _flat_pattern(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "SheetBody1"),
        "flat_pattern_name": "FlatPattern_mock",
    }


def _unfold(p: dict) -> dict:
    return {
        "body_name": p.get("body_name", "SheetBody1"),
        "bends_unfolded": len(p.get("bend_indices", [0, 1])),
    }


# ── CAM / manufacturing ─────────────────────────────────────────────

def _cam_create_setup(p: dict) -> dict:
    return {
        "setup_name": p.get("name", "Setup1"),
        "body_name": p.get("body_name", "Body1"),
        "operation_type": p.get("operation_type", "milling"),
        "stock_mode": p.get("stock_mode", "relative_box"),
    }


def _cam_create_operation(p: dict) -> dict:
    return {
        "setup_name": p.get("setup_name", "Setup1"),
        "operation_name": p.get("name", "Operation1"),
        "strategy": p.get("strategy", "2d_contour"),
        "tool_diameter": p.get("tool_diameter", 0.6),
    }


def _cam_generate_toolpath(p: dict) -> dict:
    return {
        "setup_name": p.get("setup_name", "Setup1"),
        "operation_name": p.get("operation_name"),
        "generated": True,
        "toolpath_count": 1,
    }


def _cam_post_process(p: dict) -> dict:
    setup = p.get("setup_name", "Setup1")
    post = p.get("post_processor", "fanuc")
    return {
        "setup_name": setup,
        "post_processor": post,
        "output_file": f"~/Desktop/{setup}.nc",
        "output_units": p.get("output_units", "mm"),
    }


def _cam_list_setups(_p: dict) -> dict:
    return {
        "setups": [
            {
                "name": "Setup1",
                "operation_type": "milling",
                "operation_count": 2,
            },
        ],
    }


def _cam_list_operations(p: dict) -> dict:
    return {
        "setup_name": p.get("setup_name", "Setup1"),
        "operations": [
            {
                "name": "Face1",
                "strategy": "face",
                "has_toolpath": True,
            },
            {
                "name": "2D Contour1",
                "strategy": "2d_contour",
                "has_toolpath": False,
            },
        ],
    }


def _cam_get_operation_info(p: dict) -> dict:
    return {
        "setup_name": p.get("setup_name", "Setup1"),
        "operation_name": p.get("operation_name", "Face1"),
        "strategy": "face",
        "tool_diameter": 0.6,
        "stepdown": 0.1,
        "feed_rate": 100.0,
        "spindle_speed": 10000,
        "has_toolpath": True,
        "toolpath_valid": True,
    }


# ── default fallback ─────────────────────────────────────────────────

def _default_mock(p: dict) -> dict:
    return {"warning": "no mock handler for this command", "params_received": p}


# ── dispatch table ────────────────────────────────────────────────────

_DISPATCH: dict[str, Any] = {
    "ping": _ping,
    "get_scene_info": _get_scene_info,
    "get_object_info": _get_object_info,
    "create_sketch": _create_sketch,
    "draw_rectangle": _draw_rectangle,
    "draw_circle": _draw_circle,
    "draw_line": _draw_line,
    "extrude": _extrude,
    "revolve": _revolve,
    "fillet": _fillet,
    "chamfer": _chamfer,
    "shell": _shell,
    "mirror": _mirror,
    "move_body": _move_body,
    "export_stl": _export_stl,
    "boolean_operation": _boolean_operation,
    "delete_all": _delete_all,
    "undo": _undo,
    "execute_code": _execute_code,
    "sweep": _sweep,
    "loft": _loft,
    "create_polygon": _create_polygon,
    "draw_arc": _draw_arc,
    "create_hole": _create_hole,
    "rectangular_pattern": _rectangular_pattern,
    "circular_pattern": _circular_pattern,
    "create_component": _create_component,
    "add_joint": _add_joint,
    "list_components": _list_components,
    "export_step": _export_step,
    "export_f3d": _export_f3d,
    "get_parameters": _get_parameters,
    "create_parameter": _create_parameter,
    "set_parameter": _set_parameter,
    "delete_parameter": _delete_parameter,
    # sketch constraints & dimensions
    "add_constraint": _add_constraint,
    "add_dimension": _add_dimension,
    # construction geometry
    "create_construction_plane": _create_construction_plane,
    "create_construction_axis": _create_construction_axis,
    # splines
    "draw_spline": _draw_spline,
    # sketch curve operations
    "offset_curve": _offset_curve,
    "trim_curve": _trim_curve,
    "extend_curve": _extend_curve,
    # advanced features
    "create_thread": _create_thread,
    "draft_faces": _draft_faces,
    "split_body": _split_body,
    "split_face": _split_face,
    "offset_faces": _offset_faces,
    "scale_body": _scale_body,
    # direct primitives
    "create_box": _create_box,
    "create_cylinder": _create_cylinder,
    "create_sphere": _create_sphere,
    "create_torus": _create_torus,
    # assembly (extended)
    "create_as_built_joint": _create_as_built_joint,
    "create_rigid_group": _create_rigid_group,
    # inspection / analysis
    "measure_distance": _measure_distance,
    "measure_angle": _measure_angle,
    "get_physical_properties": _get_physical_properties,
    "create_section_analysis": _create_section_analysis,
    "check_interference": _check_interference,
    # appearance
    "set_appearance": _set_appearance,
    # project geometry
    "project_geometry": _project_geometry,
    # timeline control
    "suppress_feature": _suppress_feature,
    "unsuppress_feature": _unsuppress_feature,
    # surface operations
    "patch_surface": _patch_surface,
    "stitch_surfaces": _stitch_surfaces,
    "thicken_surface": _thicken_surface,
    "ruled_surface": _ruled_surface,
    "trim_surface": _trim_surface,
    # sheet metal
    "create_flange": _create_flange,
    "create_bend": _create_bend,
    "flat_pattern": _flat_pattern,
    "unfold": _unfold,
    # CAM / manufacturing
    "cam_create_setup": _cam_create_setup,
    "cam_create_operation": _cam_create_operation,
    "cam_generate_toolpath": _cam_generate_toolpath,
    "cam_post_process": _cam_post_process,
    "cam_list_setups": _cam_list_setups,
    "cam_list_operations": _cam_list_operations,
    "cam_get_operation_info": _cam_get_operation_info,
    # design type safety
    "get_design_type": _get_design_type,
    "set_design_type": _set_design_type,
}
