"""
Fusion360 Command Handler

Executes commands using the Fusion 360 API.  Every method in this class
is called on the **main thread** (via EventBridge), so Fusion API access
is safe.
"""

import ast
import io
import math
import os
import time
import traceback
from contextlib import redirect_stdout

import adsk.cam
import adsk.core
import adsk.fusion

from . import get_logger

log = get_logger("handler")


class CommandHandler:
    """Runs Fusion API operations.  Instantiated once; reused across requests."""

    def __init__(self):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    _COMMANDS = None  # populated lazily

    def execute_command(self, command: dict) -> dict:
        """Route *command* to the correct handler; return a response dict."""
        if self._COMMANDS is None:
            self.__class__._COMMANDS = {
                # scene / query
                "get_scene_info":       self.get_scene_info,
                "get_object_info":      self.get_object_info,
                "list_components":      self.list_components,
                # sketch
                "create_sketch":        self.create_sketch,
                "draw_rectangle":       self.draw_rectangle,
                "draw_circle":          self.draw_circle,
                "draw_line":            self.draw_line,
                "draw_arc":             self.draw_arc,
                "draw_spline":          self.draw_spline,
                "create_polygon":       self.create_polygon,
                "add_constraint":       self.add_constraint,
                "add_dimension":        self.add_dimension,
                "offset_curve":         self.offset_curve,
                "trim_curve":           self.trim_curve,
                "extend_curve":         self.extend_curve,
                "project_geometry":     self.project_geometry,
                # features
                "extrude":              self.extrude,
                "revolve":              self.revolve,
                "sweep":                self.sweep,
                "loft":                 self.loft,
                "fillet":               self.fillet,
                "chamfer":              self.chamfer,
                "shell":                self.shell,
                "mirror":               self.mirror,
                "create_hole":          self.create_hole,
                "rectangular_pattern":  self.rectangular_pattern,
                "circular_pattern":     self.circular_pattern,
                "create_thread":        self.create_thread,
                "draft_faces":          self.draft_faces,
                "split_body":           self.split_body,
                "split_face":           self.split_face,
                "offset_faces":         self.offset_faces,
                "scale_body":           self.scale_body,
                "suppress_feature":     self.suppress_feature,
                "unsuppress_feature":   self.unsuppress_feature,
                # body operations
                "move_body":            self.move_body,
                "rename_body":          self.rename_body,
                "export_stl":           self.export_stl,
                "export_step":          self.export_step,
                "export_f3d":           self.export_f3d,
                "boolean_operation":    self.boolean_operation,
                "delete_all":           self.delete_all,
                "undo":                 self.undo,
                # direct primitives
                "create_box":           self.create_box,
                "create_cylinder":      self.create_cylinder,
                "create_sphere":        self.create_sphere,
                "create_torus":         self.create_torus,
                # construction geometry
                "create_construction_plane": self.create_construction_plane,
                "create_construction_axis":  self.create_construction_axis,
                # assembly
                "create_component":     self.create_component,
                "add_joint":            self.add_joint,
                "create_as_built_joint": self.create_as_built_joint,
                "create_rigid_group":   self.create_rigid_group,
                # inspection / analysis
                "measure_distance":     self.measure_distance,
                "measure_angle":        self.measure_angle,
                "get_physical_properties": self.get_physical_properties,
                "create_section_analysis": self.create_section_analysis,
                "check_interference":   self.check_interference,
                # appearance
                "set_appearance":       self.set_appearance,
                # parameters
                "get_parameters":       self.get_parameters,
                "create_parameter":     self.create_parameter,
                "set_parameter":        self.set_parameter,
                "delete_parameter":     self.delete_parameter,
                # surface operations
                "patch_surface":        self.patch_surface,
                "stitch_surfaces":      self.stitch_surfaces,
                "thicken_surface":      self.thicken_surface,
                "ruled_surface":        self.ruled_surface,
                "trim_surface":         self.trim_surface,
                # sheet metal
                "create_flange":        self.create_flange,
                "create_bend":          self.create_bend,
                "flat_pattern":         self.flat_pattern,
                "unfold":               self.unfold,
                # code execution
                "execute_code":         self.execute_code,
                # CAM
                "cam_list_setups":      self.cam_list_setups,
                "cam_list_operations":  self.cam_list_operations,
                "cam_get_operation_info": self.cam_get_operation_info,
                "cam_create_setup":     self.cam_create_setup,
                "cam_create_operation": self.cam_create_operation,
                "cam_generate_toolpath": self.cam_generate_toolpath,
                "cam_post_process":     self.cam_post_process,
                # health
                "ping":                 self.ping,
                # design type safety
                "get_design_type":      self.get_design_type,
                "set_design_type":      self.set_design_type,
            }

        cmd_type = command.get("type")
        params = command.get("params", {})

        handler = self._COMMANDS.get(cmd_type)
        if handler is None:
            return {"status": "error",
                    "message": f"Unknown command: {cmd_type}"}
        try:
            t0 = time.monotonic()
            result = handler(**params)
            elapsed = time.monotonic() - t0
            log.debug("%s completed in %.3fs", cmd_type, elapsed)
            return {"status": "success", "result": result}
        except Exception as exc:
            log.error("%s raised: %s", cmd_type, exc)
            return {"status": "error",
                    "message": f"{exc}\n{traceback.format_exc()}"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _design(self):
        d = self.app.activeProduct
        if d is None:
            raise RuntimeError("No active design")
        return d

    def _root(self):
        return self._design().rootComponent

    def _last_sketch(self):
        root = self._root()
        if root.sketches.count == 0:
            raise RuntimeError("No sketch available — create one first")
        return root.sketches.item(root.sketches.count - 1)

    def _sketch_by_name(self, name: str):
        root = self._root()
        for i in range(root.sketches.count):
            s = root.sketches.item(i)
            if s.name == name:
                return s
        raise RuntimeError(f"Sketch '{name}' not found")

    def _body_by_name(self, name: str):
        root = self._root()
        # Search root bodies first
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            if b.name == name:
                return b
        # Search bodies inside components via occurrence proxies (assembly design)
        # Returns proxy body in root coordinate space for correct boolean ops
        for occ in root.allOccurrences:
            for i in range(occ.bRepBodies.count):
                b = occ.bRepBodies.item(i)
                if b.name == name:
                    return b
        raise RuntimeError(f"Body '{name}' not found")

    def _component_by_name(self, name: str):
        root = self._root()
        if root.name == name:
            return root
        for occ in root.allOccurrences:
            if occ.component.name == name:
                return occ.component
        raise RuntimeError(f"Component '{name}' not found")

    def _construction_plane(self, plane: str):
        root = self._root()
        m = {
            "xy": root.xYConstructionPlane,
            "yz": root.yZConstructionPlane,
            "xz": root.xZConstructionPlane,
        }
        p = m.get(plane)
        if p is None:
            raise RuntimeError(f"Unknown plane '{plane}' — use xy, yz, or xz")
        return p

    def _construction_axis(self, axis: str):
        root = self._root()
        m = {
            "x": root.xConstructionAxis,
            "y": root.yConstructionAxis,
            "z": root.zConstructionAxis,
        }
        a = m.get(axis)
        if a is None:
            raise RuntimeError(f"Unknown axis '{axis}' — use x, y, or z")
        return a

    @staticmethod
    def _operation_type(name: str):
        m = {
            "new_body":   adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
            "join":       adsk.fusion.FeatureOperations.JoinFeatureOperation,
            "cut":        adsk.fusion.FeatureOperations.CutFeatureOperation,
            "intersect":  adsk.fusion.FeatureOperations.IntersectFeatureOperation,
        }
        t = m.get(name)
        if t is None:
            raise RuntimeError(
                f"Unknown operation '{name}' — use new_body/join/cut/intersect")
        return t

    def _select_edges(self, body, selection: str):
        """Return an ObjectCollection of edges based on *selection*."""
        coll = adsk.core.ObjectCollection.create()
        bbox = body.boundingBox

        if selection == "all":
            for edge in body.edges:
                coll.add(edge)
        elif selection == "top":
            threshold = bbox.maxPoint.z - 0.001
            for edge in body.edges:
                mid = edge.pointOnEdge
                if mid.z > threshold:
                    coll.add(edge)
        elif selection == "bottom":
            threshold = bbox.minPoint.z + 0.001
            for edge in body.edges:
                mid = edge.pointOnEdge
                if mid.z < threshold:
                    coll.add(edge)
        elif selection == "vertical":
            for edge in body.edges:
                sp = edge.startVertex.geometry
                ep = edge.endVertex.geometry
                if abs(sp.x - ep.x) < 0.001 and abs(sp.y - ep.y) < 0.001:
                    coll.add(edge)
        else:
            raise RuntimeError(
                f"Unknown edge_selection '{selection}' "
                "— use all/top/bottom/vertical")

        if coll.count == 0:
            raise RuntimeError(f"No edges matched selection '{selection}'")
        return coll

    def _select_faces(self, body, selection: str):
        """Return an ObjectCollection of faces based on *selection*."""
        coll = adsk.core.ObjectCollection.create()
        bbox = body.boundingBox

        if selection == "all":
            for face in body.faces:
                coll.add(face)
        elif selection == "top":
            threshold = bbox.maxPoint.z - 0.001
            for face in body.faces:
                if face.boundingBox.maxPoint.z > threshold:
                    coll.add(face)
        elif selection == "bottom":
            threshold = bbox.minPoint.z + 0.001
            for face in body.faces:
                if face.boundingBox.minPoint.z < threshold:
                    coll.add(face)
        elif selection == "vertical":
            for face in body.faces:
                # Check if face normal is roughly horizontal (vertical face)
                try:
                    _, normal_vec = face.evaluator.getNormalAtPoint(
                        face.pointOnFace)
                    if abs(normal_vec.z) < 0.1:
                        coll.add(face)
                except Exception:
                    pass
        else:
            raise RuntimeError(
                f"Unknown face_selection '{selection}' "
                "— use all/top/bottom/vertical")

        if coll.count == 0:
            raise RuntimeError(f"No faces matched selection '{selection}'")
        return coll

    # ------------------------------------------------------------------
    # Scene / Query
    # ------------------------------------------------------------------

    def get_scene_info(self):
        design = self._design()
        root = self._root()

        bodies = []
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            bodies.append({
                "name": b.name,
                "volume": b.volume,
                "area": b.area,
                "material": b.material.name if b.material else None,
                "is_visible": b.isVisible,
            })

        sketches = []
        for i in range(root.sketches.count):
            s = root.sketches.item(i)
            sketches.append({
                "name": s.name,
                "profile_count": s.profiles.count,
                "is_visible": s.isVisible,
            })

        return {
            "design_name": design.parentDocument.name,
            "design_type": design.productType,
            "bodies": bodies,
            "sketches": sketches,
            "bodies_count": root.bRepBodies.count,
            "sketches_count": root.sketches.count,
            "features_count": root.features.count,
            "timeline_count": (design.timeline.count
                               if hasattr(design, "timeline") else 0),
            "camera": self._camera_info(),
        }

    def get_object_info(self, name: str):
        root = self._root()

        # bodies
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            if b.name == name:
                return {
                    "found": True, "type": "body", "name": name,
                    "volume": b.volume, "area": b.area,
                    "material": b.material.name if b.material else None,
                    "is_visible": b.isVisible,
                    "faces_count": b.faces.count,
                    "edges_count": b.edges.count,
                    "vertices_count": b.vertices.count,
                    "bounding_box": self._bbox_dict(b.boundingBox),
                }

        # sketches
        for i in range(root.sketches.count):
            s = root.sketches.item(i)
            if s.name == name:
                return {
                    "found": True, "type": "sketch", "name": name,
                    "is_visible": s.isVisible,
                    "profile_count": s.profiles.count,
                    "curve_count": s.sketchCurves.count,
                }

        return {"found": False, "name": name}

    def list_components(self):
        root = self._root()
        components = [{"name": root.name, "is_root": True}]
        for occ in root.allOccurrences:
            components.append({
                "name": occ.component.name,
                "is_root": False,
                "is_visible": occ.isVisible,
            })
        return {"components": components, "count": len(components)}

    # ------------------------------------------------------------------
    # Sketch
    # ------------------------------------------------------------------

    def create_sketch(self, plane: str = "xy", z_offset: float = None):
        root = self._root()

        if z_offset is not None and z_offset != 0:
            # Create an offset construction plane
            planes = root.constructionPlanes
            plane_input = planes.createInput()
            offset_val = adsk.core.ValueInput.createByReal(z_offset)
            plane_input.setByOffset(self._construction_plane(plane), offset_val)
            cp = planes.add(plane_input)
            sketch = root.sketches.add(cp)
        else:
            sketch = root.sketches.add(self._construction_plane(plane))

        return {"sketch_name": sketch.name, "plane": plane,
                "z_offset": z_offset}

    def draw_rectangle(self, width: float, height: float,
                       origin_x: float = 0, origin_y: float = 0,
                       origin_z: float = 0):
        sketch = self._last_sketch()
        p1 = adsk.core.Point3D.create(origin_x, origin_y, origin_z)
        p2 = adsk.core.Point3D.create(origin_x + width,
                                       origin_y + height, origin_z)
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(p1, p2)
        return {"sketch": sketch.name, "width": width, "height": height}

    def draw_circle(self, radius: float,
                    center_x: float = 0, center_y: float = 0,
                    center_z: float = 0):
        sketch = self._last_sketch()
        c = adsk.core.Point3D.create(center_x, center_y, center_z)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(c, radius)
        return {"sketch": sketch.name, "radius": radius,
                "center": [center_x, center_y, center_z]}

    def draw_line(self, start_x: float, start_y: float,
                  end_x: float, end_y: float,
                  start_z: float = 0, end_z: float = 0):
        sketch = self._last_sketch()
        sp = adsk.core.Point3D.create(start_x, start_y, start_z)
        ep = adsk.core.Point3D.create(end_x, end_y, end_z)
        sketch.sketchCurves.sketchLines.addByTwoPoints(sp, ep)
        return {"sketch": sketch.name,
                "start": [start_x, start_y, start_z],
                "end": [end_x, end_y, end_z]}

    def draw_arc(self, center_x: float, center_y: float,
                 start_x: float, start_y: float, sweep_angle: float,
                 center_z: float = 0, start_z: float = 0):
        sketch = self._last_sketch()
        center = adsk.core.Point3D.create(center_x, center_y, center_z)
        start = adsk.core.Point3D.create(start_x, start_y, start_z)
        sweep_rad = math.radians(sweep_angle)
        sketch.sketchCurves.sketchArcs.addByCenterStartSweep(
            center, start, sweep_rad)
        return {"sketch": sketch.name, "sweep_angle": sweep_angle}

    def draw_spline(self, spline_type: str, points: list, degree: int = 3):
        sketch = self._last_sketch()
        pts = adsk.core.ObjectCollection.create()
        for p in points:
            z = p[2] if len(p) > 2 else 0
            pts.add(adsk.core.Point3D.create(p[0], p[1], z))

        if spline_type == "fit_points":
            sketch.sketchCurves.sketchFittedSplines.add(pts)
        else:  # control_points
            sketch.sketchCurves.sketchControlPointSplines.add(pts, degree)
        return {"sketch": sketch.name, "spline_type": spline_type,
                "points_count": len(points)}

    def create_polygon(self, sides: int, radius: float,
                       center_x: float = 0, center_y: float = 0,
                       center_z: float = 0):
        sketch = self._last_sketch()
        # Draw inscribed polygon
        for i in range(sides):
            angle1 = 2 * math.pi * i / sides
            angle2 = 2 * math.pi * (i + 1) / sides
            p1 = adsk.core.Point3D.create(
                center_x + radius * math.cos(angle1),
                center_y + radius * math.sin(angle1),
                center_z)
            p2 = adsk.core.Point3D.create(
                center_x + radius * math.cos(angle2),
                center_y + radius * math.sin(angle2),
                center_z)
            sketch.sketchCurves.sketchLines.addByTwoPoints(p1, p2)
        return {"sketch": sketch.name, "sides": sides, "radius": radius}

    def add_constraint(self, constraint_type: str,
                       entity_one: int = None, entity_two: int = None,
                       symmetry_line: int = None, sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        constraints = sketch.geometricConstraints
        curves = list(sketch.sketchCurves)

        e1 = curves[entity_one] if entity_one is not None else None
        e2 = curves[entity_two] if entity_two is not None else None

        constraint_map = {
            "coincident": lambda: constraints.addCoincident(e1, e2),
            "parallel": lambda: constraints.addParallel(e1, e2),
            "perpendicular": lambda: constraints.addPerpendicular(e1, e2),
            "tangent": lambda: constraints.addTangent(e1, e2),
            "equal": lambda: constraints.addEqual(e1, e2),
            "fix": lambda: constraints.addFix(e1),
            "horizontal": lambda: constraints.addHorizontal(e1),
            "vertical": lambda: constraints.addVertical(e1),
            "concentric": lambda: constraints.addConcentric(e1, e2),
            "collinear": lambda: constraints.addCollinear(e1, e2),
            "smooth": lambda: constraints.addSmooth(e1, e2),
            "midpoint": lambda: constraints.addMidPoint(
                sketch.sketchPoints.item(entity_one), e2),
            "symmetry": lambda: constraints.addSymmetry(
                e1, e2, curves[symmetry_line]),
        }

        if constraint_type not in constraint_map:
            raise RuntimeError(f"Unknown constraint type: {constraint_type}")

        constraint_map[constraint_type]()
        return {"sketch": sketch.name, "constraint_type": constraint_type}

    def add_dimension(self, dimension_type: str, value: float,
                      entity_one: int = None, entity_two: int = None,
                      sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        dims = sketch.sketchDimensions
        curves = list(sketch.sketchCurves)

        e1 = curves[entity_one] if entity_one is not None else None
        e2 = curves[entity_two] if entity_two is not None else None
        text_pt = adsk.core.Point3D.create(0, 0, 0)

        if dimension_type == "distance":
            dim = dims.addDistanceDimension(
                e1.startSketchPoint, e2.startSketchPoint,
                adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
                text_pt)
        elif dimension_type == "horizontal":
            dim = dims.addDistanceDimension(
                e1.startSketchPoint, e2.startSketchPoint,
                adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
                text_pt)
        elif dimension_type == "vertical":
            dim = dims.addDistanceDimension(
                e1.startSketchPoint, e2.startSketchPoint,
                adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
                text_pt)
        elif dimension_type == "angular":
            dim = dims.addAngularDimension(e1, e2, text_pt)
        elif dimension_type == "radial":
            dim = dims.addRadialDimension(e1, text_pt)
        elif dimension_type == "diameter":
            dim = dims.addDiameterDimension(e1, text_pt)
        else:
            raise RuntimeError(f"Unknown dimension type: {dimension_type}")

        dim.parameter.value = value
        return {"sketch": sketch.name, "dimension_type": dimension_type,
                "value": value}

    def offset_curve(self, curve_index: int, offset_distance: float,
                     direction_x: float = 1, direction_y: float = 0,
                     sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        curves = list(sketch.sketchCurves)
        curve = curves[curve_index]
        direction_pt = adsk.core.Point3D.create(direction_x, direction_y, 0)

        coll = adsk.core.ObjectCollection.create()
        coll.add(curve)
        sketch.offset(coll, direction_pt, offset_distance)
        return {"sketch": sketch.name, "offset_distance": offset_distance}

    def trim_curve(self, curve_index: int, point_x: float, point_y: float,
                   sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        curves = list(sketch.sketchCurves)
        curve = curves[curve_index]
        point = adsk.core.Point3D.create(point_x, point_y, 0)
        curve.trim(point)
        return {"sketch": sketch.name, "trimmed": True}

    def extend_curve(self, curve_index: int, point_x: float, point_y: float,
                     sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        curves = list(sketch.sketchCurves)
        curve = curves[curve_index]
        point = adsk.core.Point3D.create(point_x, point_y, 0)
        curve.extend(point)
        return {"sketch": sketch.name, "extended": True}

    def project_geometry(self, source_name: str, is_linked: bool = True,
                         sketch_name: str = None):
        sketch = (self._sketch_by_name(sketch_name) if sketch_name
                  else self._last_sketch())
        body = self._body_by_name(source_name)

        projected = []
        for edge in body.edges:
            proj = sketch.project(edge)
            projected.append(proj.count)

        return {"sketch": sketch.name, "source": source_name,
                "projected_curves": sum(projected)}

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    def extrude(self, height: float, profile_index: int = 0,
                operation: str = "new_body", direction: str = "positive"):
        root = self._root()
        sketch = self._last_sketch()
        if sketch.profiles.count == 0:
            raise RuntimeError("No profiles in sketch")
        profile = sketch.profiles.item(profile_index)

        ext_feats = root.features.extrudeFeatures
        ext_input = ext_feats.createInput(profile,
                                          self._operation_type(operation))
        dist = adsk.core.ValueInput.createByReal(height)
        if direction == "symmetric":
            ext_input.setSymmetricExtent(dist, True)
        else:
            ext_input.setDistanceExtent(direction == "negative", dist)

        feat = ext_feats.add(ext_input)
        return {"feature_name": feat.name, "height": height,
                "operation": operation, "direction": direction}

    def revolve(self, angle: float, profile_index: int = 0,
                axis_origin_x: float = 0, axis_origin_y: float = 0,
                axis_origin_z: float = 0,
                axis_direction_x: float = 1, axis_direction_y: float = 0,
                axis_direction_z: float = 0,
                operation: str = "new_body"):
        root = self._root()
        sketch = self._last_sketch()
        if sketch.profiles.count == 0:
            raise RuntimeError("No profiles in sketch")
        profile = sketch.profiles.item(profile_index)

        # Determine axis entity first (required for createInput)
        axis_entity = None
        is_x = abs(axis_direction_x) > 0.99 and abs(axis_direction_y) < 0.01
        is_y = abs(axis_direction_y) > 0.99 and abs(axis_direction_x) < 0.01
        is_z = abs(axis_direction_z) > 0.99 and abs(axis_direction_x) < 0.01
        if is_x and abs(axis_direction_z) < 0.01:
            axis_entity = root.xConstructionAxis
        elif is_y and abs(axis_direction_z) < 0.01:
            axis_entity = root.yConstructionAxis
        elif is_z and abs(axis_direction_y) < 0.01:
            axis_entity = root.zConstructionAxis
        else:
            # Create construction line in sketch
            origin = adsk.core.Point3D.create(
                axis_origin_x, axis_origin_y, axis_origin_z)
            end_pt = adsk.core.Point3D.create(
                axis_origin_x + axis_direction_x * 10,
                axis_origin_y + axis_direction_y * 10,
                axis_origin_z + axis_direction_z * 10)
            line = sketch.sketchCurves.sketchLines.addByTwoPoints(origin, end_pt)
            line.isConstruction = True
            axis_entity = line

        rev_feats = root.features.revolveFeatures
        rev_input = rev_feats.createInput(profile, axis_entity,
                                          self._operation_type(operation))

        angle_val = adsk.core.ValueInput.createByString(f"{angle} deg")
        rev_input.setAngleExtent(False, angle_val)

        feat = rev_feats.add(rev_input)
        return {"feature_name": feat.name, "angle": angle,
                "operation": operation}

    def sweep(self, profile_index: int, path_sketch_name: str,
              path_curve_index: int = 0, operation: str = "new_body"):
        root = self._root()
        sketch = self._last_sketch()
        path_sketch = self._sketch_by_name(path_sketch_name)

        if sketch.profiles.count == 0:
            raise RuntimeError("No profiles in sketch")
        profile = sketch.profiles.item(profile_index)

        path_curves = list(path_sketch.sketchCurves)
        path_curve = path_curves[path_curve_index]

        path = root.features.createPath(path_curve)

        sweep_feats = root.features.sweepFeatures
        sweep_input = sweep_feats.createInput(profile, path,
                                              self._operation_type(operation))
        feat = sweep_feats.add(sweep_input)
        return {"feature_name": feat.name, "operation": operation}

    def loft(self, profile_sketch_names: list, operation: str = "new_body"):
        root = self._root()
        loft_feats = root.features.loftFeatures
        loft_input = loft_feats.createInput(self._operation_type(operation))

        for sketch_name in profile_sketch_names:
            sketch = self._sketch_by_name(sketch_name)
            if sketch.profiles.count == 0:
                raise RuntimeError(f"No profiles in sketch '{sketch_name}'")
            loft_input.loftSections.add(sketch.profiles.item(0))

        feat = loft_feats.add(loft_input)
        return {"feature_name": feat.name, "operation": operation,
                "profile_count": len(profile_sketch_names)}

    def fillet(self, radius: float, body_name: str = None,
              body_index: int = 0, edge_selection: str = "all"):
        root = self._root()
        body = (self._body_by_name(body_name)
                if body_name else root.bRepBodies.item(body_index))
        edges = self._select_edges(body, edge_selection)

        fillets = root.features.filletFeatures
        inp = fillets.createInput()
        inp.addConstantRadiusEdgeSet(
            edges, adsk.core.ValueInput.createByReal(radius), True)
        feat = fillets.add(inp)
        return {"feature_name": feat.name, "radius": radius,
                "edges_count": edges.count}

    def chamfer(self, distance: float, body_name: str = None,
                body_index: int = 0, edge_selection: str = "all"):
        root = self._root()
        body = (self._body_by_name(body_name)
                if body_name else root.bRepBodies.item(body_index))
        edges = self._select_edges(body, edge_selection)

        chamfers = root.features.chamferFeatures
        inp = chamfers.createInput(edges, True)
        inp.setToEqualDistance(
            adsk.core.ValueInput.createByReal(distance))
        feat = chamfers.add(inp)
        return {"feature_name": feat.name, "distance": distance,
                "edges_count": edges.count}

    def shell(self, thickness: float, body_name: str = None,
              body_index: int = 0, face_selection: str = "top"):
        root = self._root()
        body = (self._body_by_name(body_name)
                if body_name else root.bRepBodies.item(body_index))

        faces = adsk.core.ObjectCollection.create()
        bbox = body.boundingBox

        if face_selection == "top":
            threshold = bbox.maxPoint.z - 0.001
            for face in body.faces:
                if face.boundingBox.maxPoint.z > threshold:
                    faces.add(face)
        elif face_selection == "bottom":
            threshold = bbox.minPoint.z + 0.001
            for face in body.faces:
                if face.boundingBox.minPoint.z < threshold:
                    faces.add(face)
        else:
            raise RuntimeError(
                f"Unknown face_selection '{face_selection}' — use top/bottom")

        if faces.count == 0:
            raise RuntimeError(f"No faces matched '{face_selection}'")

        shells = root.features.shellFeatures
        body_coll = adsk.core.ObjectCollection.create()
        body_coll.add(body)
        inp = shells.createInput(body_coll)
        inp.facesToRemove = faces
        inp.insideThickness = adsk.core.ValueInput.createByReal(thickness)
        feat = shells.add(inp)
        return {"feature_name": feat.name, "thickness": thickness,
                "faces_removed": faces.count}

    def mirror(self, mirror_plane: str, body_name: str = None,
               body_index: int = 0):
        root = self._root()
        body = (self._body_by_name(body_name)
                if body_name else root.bRepBodies.item(body_index))

        entities = adsk.core.ObjectCollection.create()
        entities.add(body)

        mirrors = root.features.mirrorFeatures
        inp = mirrors.createInput(entities,
                                  self._construction_plane(mirror_plane))
        feat = mirrors.add(inp)
        return {"feature_name": feat.name, "mirror_plane": mirror_plane}

    def create_hole(self, diameter: float, depth: float,
                    body_name: str = None, body_index: int = 0,
                    face_selection: str = "top",
                    center_x: float = 0, center_y: float = 0):
        root = self._root()
        body = (self._body_by_name(body_name)
                if body_name else root.bRepBodies.item(body_index))

        # Find the target face
        bbox = body.boundingBox
        target_face = None
        if face_selection == "top":
            threshold = bbox.maxPoint.z - 0.001
            for face in body.faces:
                if face.boundingBox.maxPoint.z > threshold:
                    target_face = face
                    break
        elif face_selection == "bottom":
            threshold = bbox.minPoint.z + 0.001
            for face in body.faces:
                if face.boundingBox.minPoint.z < threshold:
                    target_face = face
                    break

        if target_face is None:
            raise RuntimeError(f"No face found for selection '{face_selection}'")

        # Create a sketch point for the hole center
        sketch = root.sketches.add(target_face)
        center = adsk.core.Point3D.create(center_x, center_y, 0)
        sketch_pt = sketch.sketchPoints.add(center)

        # Create hole feature
        holes = root.features.holeFeatures
        hole_input = holes.createSimpleInput(
            adsk.core.ValueInput.createByReal(diameter / 2))
        hole_input.setPositionBySketchPoint(sketch_pt)
        hole_input.setDistanceExtent(adsk.core.ValueInput.createByReal(depth))

        feat = holes.add(hole_input)
        return {"feature_name": feat.name, "diameter": diameter, "depth": depth}

    def rectangular_pattern(self, body_name: str,
                            x_count: int = 1, x_spacing: float = 1.0,
                            y_count: int = 1, y_spacing: float = 1.0):
        root = self._root()
        body = self._body_by_name(body_name)

        bodies = adsk.core.ObjectCollection.create()
        bodies.add(body)

        patterns = root.features.rectangularPatternFeatures
        inp = patterns.createInput(bodies,
                                   root.xConstructionAxis,
                                   adsk.core.ValueInput.createByReal(x_count),
                                   adsk.core.ValueInput.createByReal(x_spacing),
                                   adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
        inp.setDirectionTwo(root.yConstructionAxis,
                           adsk.core.ValueInput.createByReal(y_count),
                           adsk.core.ValueInput.createByReal(y_spacing))
        feat = patterns.add(inp)
        return {"feature_name": feat.name, "x_count": x_count, "y_count": y_count}

    def circular_pattern(self, body_name: str, count: int,
                         axis: str = "z", total_angle: float = 360):
        root = self._root()
        body = self._body_by_name(body_name)

        bodies = adsk.core.ObjectCollection.create()
        bodies.add(body)

        patterns = root.features.circularPatternFeatures
        inp = patterns.createInput(bodies, self._construction_axis(axis))
        inp.quantity = adsk.core.ValueInput.createByReal(count)
        inp.totalAngle = adsk.core.ValueInput.createByString(f"{total_angle} deg")
        feat = patterns.add(inp)
        return {"feature_name": feat.name, "count": count, "total_angle": total_angle}

    def create_thread(self, body_name: str, face_index: int,
                      is_internal: bool = False,
                      thread_type: str = "ISO Metric profile",
                      thread_designation: str = "M10x1.5",
                      thread_class: str = "6g",
                      is_modeled: bool = False,
                      is_full_length: bool = True,
                      thread_length: float = None):
        root = self._root()
        body = self._body_by_name(body_name)
        face = body.faces.item(face_index)

        threads = root.features.threadFeatures
        thread_data = threads.threadDataQuery
        thread_data.threadType = thread_type

        inp = threads.createInput(face, thread_data)
        inp.isModeled = is_modeled
        inp.isFullLength = is_full_length
        if not is_full_length and thread_length:
            inp.threadLength = adsk.core.ValueInput.createByReal(thread_length)

        feat = threads.add(inp)
        return {"feature_name": feat.name, "thread_type": thread_type}

    def draft_faces(self, body_name: str, angle: float,
                    face_selection: str = "vertical",
                    pull_direction_plane: str = "xy",
                    is_tangent_chain: bool = True):
        root = self._root()
        body = self._body_by_name(body_name)
        faces = self._select_faces(body, face_selection)

        drafts = root.features.draftFeatures
        inp = drafts.createInput(faces, self._construction_plane(pull_direction_plane),
                                 adsk.core.ValueInput.createByString(f"{angle} deg"),
                                 is_tangent_chain)
        feat = drafts.add(inp)
        return {"feature_name": feat.name, "angle": angle}

    def split_body(self, body_name: str, splitting_plane: str = "xy",
                   splitting_body: str = None, extend_tool: bool = True):
        root = self._root()
        body = self._body_by_name(body_name)

        splits = root.features.splitBodyFeatures
        if splitting_body:
            tool = self._body_by_name(splitting_body)
            inp = splits.createInput(body, tool, extend_tool)
        else:
            inp = splits.createInput(body, self._construction_plane(splitting_plane),
                                     extend_tool)
        feat = splits.add(inp)
        return {"feature_name": feat.name, "splitting_plane": splitting_plane}

    def split_face(self, body_name: str, face_indices: list = None,
                   splitting_plane: str = "xy", extend_tool: bool = True):
        root = self._root()
        body = self._body_by_name(body_name)

        faces = adsk.core.ObjectCollection.create()
        if face_indices:
            for idx in face_indices:
                faces.add(body.faces.item(idx))
        else:
            for face in body.faces:
                faces.add(face)

        splits = root.features.splitFaceFeatures
        inp = splits.createInput(faces, self._construction_plane(splitting_plane),
                                 extend_tool)
        feat = splits.add(inp)
        return {"feature_name": feat.name}

    def offset_faces(self, body_name: str, distance: float,
                     face_selection: str = "top", face_indices: list = None):
        root = self._root()
        body = self._body_by_name(body_name)

        if face_indices:
            faces = adsk.core.ObjectCollection.create()
            for idx in face_indices:
                faces.add(body.faces.item(idx))
        else:
            faces = self._select_faces(body, face_selection)

        offsets = root.features.offsetFeatures
        inp = offsets.createInput(faces,
                                  adsk.core.ValueInput.createByReal(distance),
                                  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        feat = offsets.add(inp)
        return {"feature_name": feat.name, "distance": distance}

    def scale_body(self, body_name: str, scale: float,
                   scale_x: float = None, scale_y: float = None,
                   scale_z: float = None,
                   anchor_x: float = 0, anchor_y: float = 0,
                   anchor_z: float = 0):
        root = self._root()
        body = self._body_by_name(body_name)

        bodies = adsk.core.ObjectCollection.create()
        bodies.add(body)

        anchor = adsk.core.Point3D.create(anchor_x, anchor_y, anchor_z)

        scales = root.features.scaleFeatures
        if scale_x is not None and scale_y is not None and scale_z is not None:
            inp = scales.createInput(bodies, anchor,
                                     adsk.core.ValueInput.createByReal(scale_x),
                                     adsk.core.ValueInput.createByReal(scale_y),
                                     adsk.core.ValueInput.createByReal(scale_z))
        else:
            inp = scales.createInput(bodies, anchor,
                                     adsk.core.ValueInput.createByReal(scale))
        feat = scales.add(inp)
        return {"feature_name": feat.name, "scale": scale}

    def suppress_feature(self, feature_name: str):
        design = self._design()
        for i in range(design.timeline.count):
            item = design.timeline.item(i)
            has_entity = hasattr(item, 'entity') and item.entity
            if has_entity and item.entity.name == feature_name:
                item.isSuppressed = True
                return {"suppressed": True, "feature": feature_name}
        raise RuntimeError(f"Feature '{feature_name}' not found in timeline")

    def unsuppress_feature(self, feature_name: str):
        design = self._design()
        for i in range(design.timeline.count):
            item = design.timeline.item(i)
            has_entity = hasattr(item, 'entity') and item.entity
            if has_entity and item.entity.name == feature_name:
                item.isSuppressed = False
                return {"unsuppressed": True, "feature": feature_name}
        raise RuntimeError(f"Feature '{feature_name}' not found in timeline")

    # ------------------------------------------------------------------
    # Body Operations
    # ------------------------------------------------------------------

    def rename_body(self, body_name: str, new_name: str):
        body = self._body_by_name(body_name)
        old_name = body.name
        body.name = new_name
        return {"renamed": True, "old_name": old_name, "new_name": new_name}

    def move_body(self, body_name: str, x: float = 0, y: float = 0,
                  z: float = 0):
        root = self._root()
        body = self._body_by_name(body_name)

        move_feats = root.features.moveFeatures
        bodies = adsk.core.ObjectCollection.create()
        bodies.add(body)

        transform = adsk.core.Matrix3D.create()
        transform.translation = adsk.core.Vector3D.create(x, y, z)

        inp = move_feats.createInput(bodies, transform)
        feat = move_feats.add(inp)
        return {"feature_name": feat.name, "body": body_name,
                "translation": [x, y, z]}

    def export_stl(self, body_name: str, file_path: str = None):
        body = self._body_by_name(body_name)

        if file_path is None:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            file_path = os.path.join(desktop, f"{body_name}.stl")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        export_mgr = self._design().exportManager
        occ = body.assemblyContext  # None if body is at root

        if occ is None:
            stl_opts = export_mgr.createSTLExportOptions(body, file_path)
            stl_opts.meshRefinement = (
                adsk.fusion.MeshRefinementSettings.MeshRefinementMedium)
            export_mgr.execute(stl_opts)
            return {"exported": True, "body": body_name,
                    "file_path": file_path}

        # Body lives in a component occurrence: hide siblings so the
        # occurrence export only contains the target body. Identify
        # siblings by entityToken, not name, to handle same-name bodies.
        target_token = body.entityToken
        hidden = []
        for i in range(occ.bRepBodies.count):
            sibling = occ.bRepBodies.item(i)
            if sibling.entityToken != target_token and sibling.isVisible:
                sibling.isVisible = False
                hidden.append(sibling)

        try:
            stl_opts = export_mgr.createSTLExportOptions(occ, file_path)
            stl_opts.meshRefinement = (
                adsk.fusion.MeshRefinementSettings.MeshRefinementMedium)
            export_mgr.execute(stl_opts)
        finally:
            for sibling in hidden:
                sibling.isVisible = True

        return {"exported": True, "body": body_name,
                "file_path": file_path}

    def export_step(self, body_name: str, file_path: str = None):
        body = self._body_by_name(body_name)

        if file_path is None:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            file_path = os.path.join(desktop, f"{body_name}.step")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        export_mgr = self._design().exportManager
        occ = body.assemblyContext  # None if body is at root

        if occ is None:
            step_opts = export_mgr.createSTEPExportOptions(file_path, body)
            export_mgr.execute(step_opts)
            return {"exported": True, "body": body_name,
                    "file_path": file_path}

        # Body lives in a component occurrence: hide siblings so the
        # occurrence export only contains the target body. Identify
        # siblings by entityToken, not name, to handle same-name bodies.
        target_token = body.entityToken
        hidden = []
        for i in range(occ.bRepBodies.count):
            sibling = occ.bRepBodies.item(i)
            if sibling.entityToken != target_token and sibling.isVisible:
                sibling.isVisible = False
                hidden.append(sibling)

        try:
            step_opts = export_mgr.createSTEPExportOptions(file_path, occ)
            export_mgr.execute(step_opts)
        finally:
            for sibling in hidden:
                sibling.isVisible = True

        return {"exported": True, "body": body_name,
                "file_path": file_path}

    def export_f3d(self, file_path: str = None):
        design = self._design()
        doc_name = design.parentDocument.name

        if file_path is None:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            file_path = os.path.join(desktop, f"{doc_name}.f3d")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        export_mgr = design.exportManager
        f3d_opts = export_mgr.createFusionArchiveExportOptions(file_path)
        export_mgr.execute(f3d_opts)

        return {"exported": True, "file_path": file_path}

    def boolean_operation(self, target_body: str, tool_body: str,
                          operation: str = "join"):
        root = self._root()
        target = self._body_by_name(target_body)
        tool = self._body_by_name(tool_body)

        combine_feats = root.features.combineFeatures
        tool_coll = adsk.core.ObjectCollection.create()
        tool_coll.add(tool)

        op_map = {
            "join":      adsk.fusion.FeatureOperations.JoinFeatureOperation,
            "cut":       adsk.fusion.FeatureOperations.CutFeatureOperation,
            "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
        }
        op = op_map.get(operation)
        if op is None:
            raise RuntimeError(
                f"Unknown boolean op '{operation}' — use join/cut/intersect")

        inp = combine_feats.createInput(target, tool_coll)
        inp.operation = op
        feat = combine_feats.add(inp)
        return {"feature_name": feat.name, "operation": operation,
                "target": target_body, "tool": tool_body}

    def delete_all(self):
        design = self._design()
        if hasattr(design, "timeline") and design.timeline.count > 0:
            tl = design.timeline
            for i in range(tl.count - 1, -1, -1):
                try:
                    tl.item(i).deleteMe()
                except Exception:
                    pass
        return {"deleted": True}

    def undo(self):
        design = self._design()
        type_before = design.designType

        cmd_def = self.ui.commandDefinitions.itemById('UndoCommand')
        if cmd_def:
            cmd_def.execute()

        # Check if undo silently switched design type (Parametric → Direct)
        adsk.doEvents()  # let Fusion process the undo
        type_after = design.designType
        if type_before != type_after:
            # Undo the undo — redo to restore original state
            redo_def = self.ui.commandDefinitions.itemById('RedoCommand')
            if redo_def:
                redo_def.execute()
                adsk.doEvents()
            raise RuntimeError(
                f"Undo aborted: would have changed design type from "
                f"{'Parametric' if type_before == 1 else 'Direct'} to "
                f"{'Parametric' if type_after == 1 else 'Direct'}. "
                f"The undo was automatically reversed (redo). "
                f"Delete the failed feature explicitly instead."
            )

        return {"undone": True, "design_type": type_after}

    # ------------------------------------------------------------------
    # Direct Primitives (via TemporaryBRepManager)
    # ------------------------------------------------------------------

    def create_box(self, length: float, width: float, height: float,
                   center_x: float = 0, center_y: float = 0,
                   center_z: float = 0):
        root = self._root()
        temp_brep = adsk.fusion.TemporaryBRepManager.get()

        # Box orientation matrix
        orient = adsk.core.OrientedBoundingBox3D.create(
            adsk.core.Point3D.create(center_x, center_y, center_z + height/2),
            adsk.core.Vector3D.create(1, 0, 0),
            adsk.core.Vector3D.create(0, 1, 0),
            length, width, height)

        box_body = temp_brep.createBox(orient)
        base_feat = root.features.baseFeatures.add()
        base_feat.startEdit()
        root.bRepBodies.add(box_body, base_feat)
        base_feat.finishEdit()

        return {"created": True, "length": length, "width": width,
                "height": height}

    def create_cylinder(self, radius: float, height: float,
                        base_x: float = 0, base_y: float = 0,
                        base_z: float = 0, axis: str = "z"):
        root = self._root()
        temp_brep = adsk.fusion.TemporaryBRepManager.get()

        base_pt = adsk.core.Point3D.create(base_x, base_y, base_z)
        axis_vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        top_pt = adsk.core.Point3D.create(
            base_x + axis_vec[0] * height,
            base_y + axis_vec[1] * height,
            base_z + axis_vec[2] * height)

        cyl_body = temp_brep.createCylinderOrCone(base_pt, radius, top_pt, radius)

        base_feat = root.features.baseFeatures.add()
        base_feat.startEdit()
        root.bRepBodies.add(cyl_body, base_feat)
        base_feat.finishEdit()

        return {"created": True, "radius": radius, "height": height}

    def create_sphere(self, radius: float,
                      center_x: float = 0, center_y: float = 0,
                      center_z: float = 0):
        root = self._root()
        temp_brep = adsk.fusion.TemporaryBRepManager.get()

        center = adsk.core.Point3D.create(center_x, center_y, center_z)
        sphere_body = temp_brep.createSphere(center, radius)

        base_feat = root.features.baseFeatures.add()
        base_feat.startEdit()
        root.bRepBodies.add(sphere_body, base_feat)
        base_feat.finishEdit()

        return {"created": True, "radius": radius}

    def create_torus(self, major_radius: float, minor_radius: float,
                     center_x: float = 0, center_y: float = 0,
                     center_z: float = 0, axis: str = "z"):
        root = self._root()
        temp_brep = adsk.fusion.TemporaryBRepManager.get()

        center = adsk.core.Point3D.create(center_x, center_y, center_z)
        axis_vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        axis_vector = adsk.core.Vector3D.create(*axis_vec)

        torus_body = temp_brep.createTorus(center, axis_vector,
                                           major_radius, minor_radius)

        base_feat = root.features.baseFeatures.add()
        base_feat.startEdit()
        root.bRepBodies.add(torus_body, base_feat)
        base_feat.finishEdit()

        return {"created": True, "major_radius": major_radius,
                "minor_radius": minor_radius}

    # ------------------------------------------------------------------
    # Construction Geometry
    # ------------------------------------------------------------------

    def create_construction_plane(self, method: str,
                                  plane: str = None, offset: float = None,
                                  angle: float = None, edge_name: str = None,
                                  plane_one: str = None, plane_two: str = None,
                                  point_one: list = None, point_two: list = None,
                                  point_three: list = None):
        root = self._root()
        planes = root.constructionPlanes
        inp = planes.createInput()

        if method == "offset":
            inp.setByOffset(self._construction_plane(plane),
                           adsk.core.ValueInput.createByReal(offset))
        elif method == "angle":
            inp.setByAngle(self._construction_axis(edge_name or "x"),
                          adsk.core.ValueInput.createByString(f"{angle} deg"),
                          self._construction_plane(plane))
        elif method == "midplane":
            inp.setByTwoPlanes(self._construction_plane(plane_one),
                              self._construction_plane(plane_two))
        elif method == "three_points":
            p1 = adsk.core.Point3D.create(*point_one)
            p2 = adsk.core.Point3D.create(*point_two)
            p3 = adsk.core.Point3D.create(*point_three)
            inp.setByThreePoints(p1, p2, p3)
        elif method == "tangent":
            raise RuntimeError("Tangent plane needs face selection—use execute_code")
        else:
            raise RuntimeError(f"Unknown method: {method}")

        plane_obj = planes.add(inp)
        return {"created": True, "name": plane_obj.name, "method": method}

    def create_construction_axis(self, method: str,
                                 point_one: list = None, point_two: list = None,
                                 plane_one: str = None, plane_two: str = None,
                                 body_name: str = None, edge_index: int = None):
        root = self._root()
        axes = root.constructionAxes
        inp = axes.createInput()

        if method == "two_points":
            p1 = adsk.core.Point3D.create(*point_one)
            p2 = adsk.core.Point3D.create(*point_two)
            inp.setByTwoPoints(p1, p2)
        elif method == "intersection":
            inp.setByTwoPlanes(self._construction_plane(plane_one),
                              self._construction_plane(plane_two))
        elif method == "edge":
            body = self._body_by_name(body_name)
            edge = body.edges.item(edge_index)
            inp.setByEdge(edge)
        elif method == "perpendicular_at_point":
            p1 = adsk.core.Point3D.create(*point_one)
            inp.setByPerpendicularAtPoint(self._construction_plane(plane_one), p1)
        else:
            raise RuntimeError(f"Unknown method: {method}")

        axis_obj = axes.add(inp)
        return {"created": True, "name": axis_obj.name, "method": method}

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def create_component(self, name: str, parent_name: str = None):
        root = self._root()
        parent = (self._component_by_name(parent_name) if parent_name
                  else root)

        occ = parent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        occ.component.name = name
        return {"created": True, "name": name}

    def add_joint(self, component_one: str, component_two: str,
                  joint_type: str = "rigid"):
        root = self._root()

        occ1 = occ2 = None
        for occ in root.allOccurrences:
            if occ.component.name == component_one:
                occ1 = occ
            if occ.component.name == component_two:
                occ2 = occ

        if not occ1 or not occ2:
            raise RuntimeError("One or both components not found")

        joints = root.joints
        joint_types = {
            "rigid": adsk.fusion.JointTypes.RigidJointType,
            "revolute": adsk.fusion.JointTypes.RevoluteJointType,
            "slider": adsk.fusion.JointTypes.SliderJointType,
            "cylindrical": adsk.fusion.JointTypes.CylindricalJointType,
            "pin_slot": adsk.fusion.JointTypes.PinSlotJointType,
            "planar": adsk.fusion.JointTypes.PlanarJointType,
            "ball": adsk.fusion.JointTypes.BallJointType,
        }

        jt = joint_types.get(joint_type)
        if jt is None:
            raise RuntimeError(f"Unknown joint type: {joint_type}")

        # Create joint geometry from origin points
        origin1 = occ1.component.originConstructionPoint
        origin2 = occ2.component.originConstructionPoint
        geo1 = adsk.fusion.JointGeometry.createByPoint(occ1, origin1)
        geo2 = adsk.fusion.JointGeometry.createByPoint(occ2, origin2)

        inp = joints.createInput(geo1, geo2)
        if joint_type == "rigid":
            inp.setAsRigidJointMotion()
        joints.add(inp)
        return {"created": True, "joint_type": joint_type}

    def create_as_built_joint(self, component_one: str, component_two: str,
                              joint_type: str = "rigid"):
        root = self._root()

        occ1 = occ2 = None
        for occ in root.allOccurrences:
            if occ.component.name == component_one:
                occ1 = occ
            if occ.component.name == component_two:
                occ2 = occ

        if not occ1 or not occ2:
            raise RuntimeError("One or both components not found")

        as_built = root.asBuiltJoints
        inp = as_built.createInput(occ1, occ2, None)
        as_built.add(inp)
        return {"created": True, "joint_type": joint_type}

    def create_rigid_group(self, component_names: list,
                           include_children: bool = True):
        root = self._root()
        occs = adsk.core.ObjectCollection.create()

        for name in component_names:
            for occ in root.allOccurrences:
                if occ.component.name == name:
                    occs.add(occ)
                    break

        if occs.count < 2:
            raise RuntimeError("Need at least 2 components for rigid group")

        groups = root.rigidGroups
        groups.add(occs, include_children)
        return {"created": True, "component_count": occs.count}

    # ------------------------------------------------------------------
    # Inspection / Analysis
    # ------------------------------------------------------------------

    def measure_distance(self, entity_one: str, entity_two: str):
        root = self._root()

        def get_entity(name):
            # Try as body
            for i in range(root.bRepBodies.count):
                b = root.bRepBodies.item(i)
                if b.name == name:
                    return b
            # Try as point (x,y,z format)
            if "," in name:
                coords = [float(x.strip()) for x in name.split(",")]
                return adsk.core.Point3D.create(*coords)
            raise RuntimeError(f"Entity '{name}' not found")

        e1 = get_entity(entity_one)
        e2 = get_entity(entity_two)

        measure = self.app.measureManager
        result = measure.measureMinimumDistance(e1, e2)
        return {"distance": result.value,
                "point_one": [result.pointOnEntityOne.x,
                             result.pointOnEntityOne.y,
                             result.pointOnEntityOne.z],
                "point_two": [result.pointOnEntityTwo.x,
                             result.pointOnEntityTwo.y,
                             result.pointOnEntityTwo.z]}

    def measure_angle(self, entity_one: str, entity_two: str):
        root = self._root()

        def get_entity(name):
            for i in range(root.bRepBodies.count):
                b = root.bRepBodies.item(i)
                if b.name == name:
                    return b.faces.item(0)  # First face
            raise RuntimeError(f"Entity '{name}' not found")

        e1 = get_entity(entity_one)
        e2 = get_entity(entity_two)

        measure = self.app.measureManager
        result = measure.measureAngle(e1, e2)
        return {"angle_degrees": math.degrees(result.value)}

    def get_physical_properties(self, body_name: str,
                                accuracy: str = "medium"):
        body = self._body_by_name(body_name)

        accuracy_map = {
            "low": adsk.fusion.CalculationAccuracy.LowCalculationAccuracy,
            "medium": adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy,
            "high": adsk.fusion.CalculationAccuracy.HighCalculationAccuracy,
            "very_high": adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy,
        }
        acc = accuracy_map.get(accuracy, accuracy_map["medium"])

        props = body.getPhysicalProperties(acc)
        return {
            "mass": props.mass,
            "volume": props.volume,
            "area": props.area,
            "density": props.density,
            "center_of_mass": [props.centerOfMass.x,
                              props.centerOfMass.y,
                              props.centerOfMass.z],
        }

    def create_section_analysis(self, plane: str = "yz", offset: float = 0):
        root = self._root()
        analyses = root.analyses

        inp = analyses.createInput()
        inp.plane = self._construction_plane(plane)
        if offset != 0:
            inp.distance = adsk.core.ValueInput.createByReal(offset)

        analyses.add(inp)
        return {"created": True, "plane": plane, "offset": offset}

    def check_interference(self, component_names: list,
                           include_coincident_faces: bool = False):
        root = self._root()
        bodies = adsk.core.ObjectCollection.create()

        for name in component_names:
            for occ in root.allOccurrences:
                if occ.component.name == name:
                    for b in occ.bRepBodies:
                        bodies.add(b)

        if bodies.count < 2:
            raise RuntimeError("Need at least 2 components with bodies")

        interference = root.interfere(bodies, include_coincident_faces)
        results = []
        for i in range(interference.interferenceResultCount):
            result = interference.interferenceResult(i)
            results.append({
                "body_one": result.entityOne.name,
                "body_two": result.entityTwo.name,
                "volume": result.interferenceBody.volume,
            })

        return {"interferences": results, "count": len(results)}

    # ------------------------------------------------------------------
    # Appearance
    # ------------------------------------------------------------------

    def set_appearance(self, target_name: str, appearance_name: str,
                       target_type: str = "body", face_index: int = None):
        # Find appearance in library — try both known library names
        app_lib = self.app.materialLibraries.itemByName(
            "Fusion Appearance Library")
        if app_lib is None:
            app_lib = self.app.materialLibraries.itemByName(
                "Fusion 360 Appearance Library")
        if app_lib is None:
            # Fall back to searching all libraries
            for i in range(self.app.materialLibraries.count):
                lib = self.app.materialLibraries.item(i)
                if lib.appearances.count > 0:
                    app_lib = lib
                    break
        if app_lib is None:
            raise RuntimeError("No appearance library found")

        appearance = None
        for i in range(app_lib.appearances.count):
            app = app_lib.appearances.item(i)
            if app.name == appearance_name:
                appearance = app
                break

        if not appearance:
            raise RuntimeError(f"Appearance '{appearance_name}' not found")

        if target_type == "body":
            body = self._body_by_name(target_name)
            body.appearance = appearance
        elif target_type == "component":
            comp = self._component_by_name(target_name)
            comp.appearance = appearance
        elif target_type == "face":
            body = self._body_by_name(target_name)
            face = body.faces.item(face_index)
            face.appearance = appearance

        return {"applied": True, "target": target_name,
                "appearance": appearance_name}

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters(self):
        design = self._design()
        params = []
        for param in design.userParameters:
            params.append({
                "name": param.name,
                "value": param.value,
                "expression": param.expression,
                "unit": param.unit,
                "comment": param.comment,
            })
        return {"parameters": params, "count": len(params)}

    def create_parameter(self, name: str, value: float, unit: str,
                         comment: str = None):
        design = self._design()
        params = design.userParameters
        params.add(name, adsk.core.ValueInput.createByReal(value),
                   unit, comment or "")
        return {"created": True, "name": name, "value": value, "unit": unit}

    def set_parameter(self, name: str, value: float):
        design = self._design()
        param = design.userParameters.itemByName(name)
        if not param:
            raise RuntimeError(f"Parameter '{name}' not found")
        param.value = value
        return {"updated": True, "name": name, "value": value}

    def delete_parameter(self, name: str):
        design = self._design()
        param = design.userParameters.itemByName(name)
        if not param:
            raise RuntimeError(f"Parameter '{name}' not found")
        param.deleteMe()
        return {"deleted": True, "name": name}

    # ------------------------------------------------------------------
    # Surface Operations
    # ------------------------------------------------------------------

    def patch_surface(self, sketch_name: str, profile_index: int = 0,
                      continuity: str = "connected"):
        root = self._root()
        sketch = self._sketch_by_name(sketch_name)

        if sketch.profiles.count == 0:
            raise RuntimeError("No profiles in sketch")
        profile = sketch.profiles.item(profile_index)

        patches = root.features.patchFeatures
        inp = patches.createInput(profile,
                                  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

        sct = adsk.fusion.SurfaceContinuityTypes
        cont_map = {
            "connected": sct.ConnectedSurfaceContinuityType,
            "tangent": sct.TangentSurfaceContinuityType,
            "curvature": sct.CurvatureSurfaceContinuityType,
        }
        inp.boundaryContinuity = cont_map.get(continuity, cont_map["connected"])

        feat = patches.add(inp)
        return {"feature_name": feat.name, "continuity": continuity}

    def stitch_surfaces(self, body_names: list, tolerance: float = 0.01):
        root = self._root()
        bodies = adsk.core.ObjectCollection.create()
        for name in body_names:
            bodies.add(self._body_by_name(name))

        stitches = root.features.stitchFeatures
        inp = stitches.createInput(bodies,
                                   adsk.core.ValueInput.createByReal(tolerance),
                                   adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        feat = stitches.add(inp)
        return {"feature_name": feat.name, "body_count": len(body_names)}

    def thicken_surface(self, body_name: str, thickness: float,
                        direction: str = "symmetric"):
        root = self._root()
        body = self._body_by_name(body_name)

        faces = adsk.core.ObjectCollection.create()
        for face in body.faces:
            faces.add(face)

        thickens = root.features.thickenFeatures
        inp = thickens.createInput(faces,
                                   adsk.core.ValueInput.createByReal(thickness),
                                   False,
                                   adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
                                   direction == "symmetric")
        feat = thickens.add(inp)
        return {"feature_name": feat.name, "thickness": thickness}

    def ruled_surface(self, body_name: str, edge_index: int,
                      distance: float = 1.0, rule_type: str = "normal"):
        root = self._root()
        body = self._body_by_name(body_name)
        edge = body.edges.item(edge_index)

        ruled = root.features.ruledSurfaceFeatures
        inp = ruled.createInput(edge,
                                adsk.core.ValueInput.createByReal(distance))
        feat = ruled.add(inp)
        return {"feature_name": feat.name, "distance": distance}

    def trim_surface(self, body_name: str, tool_name: str):
        root = self._root()
        body = self._body_by_name(body_name)
        tool = self._body_by_name(tool_name)

        trims = root.features.trimFeatures
        inp = trims.createInput(body, tool)
        feat = trims.add(inp)
        return {"feature_name": feat.name}

    # ------------------------------------------------------------------
    # Sheet Metal
    # ------------------------------------------------------------------

    def create_flange(self, body_name: str, edge_index: int,
                      height: float = 1.0, angle: float = 90,
                      bend_radius: float = None):
        root = self._root()
        body = self._body_by_name(body_name)
        edge = body.edges.item(edge_index)

        flanges = root.features.flangeFeatures
        inp = flanges.createInput(edge, True)
        inp.angle = adsk.core.ValueInput.createByString(f"{angle} deg")
        inp.height = adsk.core.ValueInput.createByReal(height)
        if bend_radius:
            inp.bendRadius = adsk.core.ValueInput.createByReal(bend_radius)

        feat = flanges.add(inp)
        return {"feature_name": feat.name, "height": height, "angle": angle}

    def create_bend(self, body_name: str, bend_line_sketch: str = None,
                    angle: float = 90, bend_radius: float = None):
        root = self._root()
        body = self._body_by_name(body_name)

        if bend_line_sketch:
            sketch = self._sketch_by_name(bend_line_sketch)
            bend_line = sketch.sketchCurves.sketchLines.item(0)

            bends = root.features.bendFeatures
            inp = bends.createInput(body, bend_line, True)
            inp.bendAngle = adsk.core.ValueInput.createByString(f"{angle} deg")
            if bend_radius:
                inp.bendRadius = adsk.core.ValueInput.createByReal(bend_radius)

            feat = bends.add(inp)
            return {"feature_name": feat.name, "angle": angle}
        else:
            raise RuntimeError("bend_line_sketch is required")

    def flat_pattern(self, body_name: str):
        root = self._root()
        body = self._body_by_name(body_name)

        flat_patterns = root.features.flatPatternFeatures
        inp = flat_patterns.createInput(body)
        feat = flat_patterns.add(inp)
        return {"feature_name": feat.name}

    def unfold(self, body_name: str, bend_indices: list = None):
        root = self._root()
        body = self._body_by_name(body_name)

        unfolds = root.features.unfoldFeatures

        bends = adsk.core.ObjectCollection.create()
        if bend_indices:
            for idx in bend_indices:
                # Get bend faces from sheet metal body
                bends.add(body.faces.item(idx))
        else:
            # Unfold all bends
            for face in body.faces:
                bends.add(face)

        # Find stationary face (first planar face)
        stationary = None
        for face in body.faces:
            if face.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
                stationary = face
                break

        if not stationary:
            raise RuntimeError("No planar face found for stationary face")

        inp = unfolds.createInput(bends, stationary)
        feat = unfolds.add(inp)
        return {"feature_name": feat.name}

    # ------------------------------------------------------------------
    # CAM
    # ------------------------------------------------------------------

    def _get_cam(self):
        """Get the CAM product from the active document."""
        doc = self.app.activeDocument
        cam_product = doc.products.itemByProductType('CAMProductType')
        if not cam_product:
            raise RuntimeError(
                "No CAM workspace found. Open the Manufacturing workspace "
                "in Fusion 360 at least once to initialise it.")
        return cam_product

    def _find_setup(self, cam, name: str):
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            if s.name == name:
                return s
        raise RuntimeError(f"Setup '{name}' not found")

    def _find_operation(self, setup, name: str):
        for i in range(setup.operations.count):
            op = setup.operations.item(i)
            if op.name == name:
                return op
        raise RuntimeError(f"Operation '{name}' not found in setup '{setup.name}'")

    def cam_list_setups(self):
        cam = self._get_cam()
        result = []
        for i in range(cam.setups.count):
            setup = cam.setups.item(i)
            ops = []
            for j in range(setup.operations.count):
                ops.append(setup.operations.item(j).name)
            result.append({
                "name": setup.name,
                "operations": ops,
                "is_valid": setup.isValid,
            })
        return {"setups": result, "count": len(result)}

    def cam_list_operations(self, setup_name: str):
        cam = self._get_cam()
        setup = self._find_setup(cam, setup_name)
        result = []
        for i in range(setup.operations.count):
            op = setup.operations.item(i)
            result.append({
                "name": op.name,
                "has_toolpath": op.hasToolpath,
                "is_valid": op.isValid,
            })
        return {"setup": setup_name, "operations": result, "count": len(result)}

    def cam_get_operation_info(self, setup_name: str, operation_name: str):
        cam = self._get_cam()
        setup = self._find_setup(cam, setup_name)
        op = self._find_operation(setup, operation_name)

        info = {
            "name": op.name,
            "is_valid": op.isValid,
            "has_toolpath": op.hasToolpath,
        }

        if hasattr(op, 'tool') and op.tool:
            tool = op.tool
            desc = tool.description if hasattr(tool, 'description') else str(tool)
            info["tool"] = {"description": desc}

        if hasattr(op, 'parameters'):
            params = {}
            for param in op.parameters:
                try:
                    params[param.name] = param.expression
                except Exception:
                    pass
            info["parameters"] = params

        return info

    def cam_create_setup(self, body_name: str, name: str = None,
                         operation_type: str = "milling",
                         stock_mode: str = "relative_box",
                         stock_offset_sides: float = 0,
                         stock_offset_top: float = 0,
                         stock_offset_bottom: float = 0):
        cam = self._get_cam()
        body = self._body_by_name(body_name)

        op_type_map = {
            "milling": adsk.cam.OperationTypes.MillingOperation,
            "turning": adsk.cam.OperationTypes.TurningOperation,
            "cutting": adsk.cam.OperationTypes.JetOperation,
        }
        op_type = op_type_map.get(operation_type)
        if op_type is None:
            raise RuntimeError(
                f"Unknown operation_type '{operation_type}' "
                "— use milling/turning/cutting")

        setup_input = cam.setups.createInput(op_type)
        setup_input.models = [body]

        if name:
            setup_input.name = name

        setup = cam.setups.add(setup_input)
        return {"name": setup.name, "body": body_name,
                "operation_type": operation_type}

    def cam_create_operation(self, setup_name: str, strategy: str,
                              name: str = None,
                              tool_number: int = None,
                              tool_diameter: float = None,
                              stepdown: float = None,
                              stepover: float = None,
                              feed_rate: float = None,
                              spindle_speed: float = None,
                              coolant: str = "flood"):
        cam = self._get_cam()
        setup = self._find_setup(cam, setup_name)

        op_input = setup.operations.createInput(strategy)
        if name:
            op_input.name = name
        if tool_diameter:
            op_input.toolDiameter = adsk.core.ValueInput.createByReal(tool_diameter)
        if stepdown:
            op_input.maximumStepdown = adsk.core.ValueInput.createByReal(stepdown)
        if stepover:
            op_input.maximumStepover = adsk.core.ValueInput.createByReal(stepover)

        op = setup.operations.add(op_input)
        return {"name": op.name, "setup": setup_name, "strategy": strategy}

    def cam_generate_toolpath(self, setup_name: str = None,
                               operation_name: str = None,
                               generate_all: bool = False):
        cam = self._get_cam()

        if generate_all:
            future = cam.generateAllToolpaths(False)
            future.wait()
            return {"generated": True, "scope": "all"}

        if operation_name and setup_name:
            setup = self._find_setup(cam, setup_name)
            op = self._find_operation(setup, operation_name)
            future = cam.generateToolpath(op)
            future.wait()
            return {"generated": True, "scope": "operation",
                    "operation": operation_name}

        if setup_name:
            setup = self._find_setup(cam, setup_name)
            ops = adsk.core.ObjectCollection.create()
            for i in range(setup.operations.count):
                ops.add(setup.operations.item(i))
            future = cam.generateToolpath(ops)
            future.wait()
            return {"generated": True, "scope": "setup", "setup": setup_name}

        raise RuntimeError(
            "Provide setup_name, operation_name, or generate_all=true")

    def cam_post_process(self, setup_name: str, operation_name: str = None,
                          post_processor: str = "fanuc",
                          output_folder: str = None,
                          output_units: str = "mm"):
        cam = self._get_cam()
        setup = self._find_setup(cam, setup_name)

        if not output_folder:
            output_folder = os.path.join(os.path.expanduser("~"), "Desktop")

        post_config = os.path.join(
            cam.genericPostFolder, f"{post_processor}.cps")

        units = (adsk.cam.PostOutputUnitOptions.MillimetersOutput
                 if output_units == "mm"
                 else adsk.cam.PostOutputUnitOptions.InchesOutput)

        post_input = adsk.cam.PostProcessInput.create(
            setup_name, post_config, output_folder, units)
        post_input.isOpenInEditor = False

        if operation_name:
            op = self._find_operation(setup, operation_name)
            cam.postProcess(op, post_input)
        else:
            cam.postProcess(setup, post_input)

        return {"setup": setup_name, "post_processor": post_processor,
                "output_folder": output_folder, "units": output_units}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def ping(self):
        return {"pong": True}

    # ------------------------------------------------------------------
    # Design type safety
    # ------------------------------------------------------------------

    def get_design_type(self):
        """Return current design type: 'parametric' or 'direct'."""
        design = self._design()
        dt = design.designType
        is_parametric = dt == adsk.fusion.DesignTypes.ParametricDesignType
        return {
            "design_type": "parametric" if is_parametric else "direct",
            "design_type_id": dt,
        }

    def set_design_type(self, design_type: str):
        """Switch design type. Use 'parametric' to recover from accidental
        direct-mode switches (equivalent to UI 'Capture Design History')."""
        design = self._design()
        current = design.designType

        if design_type == "parametric":
            target = adsk.fusion.DesignTypes.ParametricDesignType
            if current == target:
                return {"changed": False, "design_type": "parametric",
                        "message": "Already in parametric mode"}
            design.designType = target
            adsk.doEvents()
            # Verify it actually changed
            if design.designType != target:
                raise RuntimeError(
                    "Failed to switch to parametric mode. "
                    "Try 'Capture Design History' in the Fusion UI."
                )
            return {"changed": True, "design_type": "parametric"}

        elif design_type == "direct":
            target = adsk.fusion.DesignTypes.DirectDesignType
            if current == target:
                return {"changed": False, "design_type": "direct",
                        "message": "Already in direct mode"}
            design.designType = target
            adsk.doEvents()
            return {"changed": True, "design_type": "direct"}

        else:
            raise RuntimeError(
                f"Invalid design_type '{design_type}'. "
                f"Use 'parametric' or 'direct'."
            )

    # ------------------------------------------------------------------
    # Code execution (REPL-style)
    # ------------------------------------------------------------------

    def execute_code(self, code: str):
        design = self._design()
        type_before = design.designType

        ns = {
            "adsk": adsk,
            "app": self.app,
            "ui": self.ui,
            "design": design,
            "component": self._root(),
            "math": math,
        }

        buf = io.StringIO()

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise RuntimeError(f"SyntaxError: {exc}")

        last_expr_value = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_node = tree.body.pop()
            if tree.body:
                with redirect_stdout(buf):
                    exec(compile(ast.Module(body=tree.body, type_ignores=[]),
                                 "<mcp>", "exec"), ns)
            expr_code = compile(ast.Expression(body=last_node.value),
                                "<mcp>", "eval")
            with redirect_stdout(buf):
                last_expr_value = eval(expr_code, ns)
        else:
            with redirect_stdout(buf):
                exec(compile(tree, "<mcp>", "exec"), ns)

        output = buf.getvalue()
        result = last_expr_value if last_expr_value is not None else output

        # Warn if design type changed during execution
        type_after = design.designType
        design_type_warning = None
        if type_before != type_after:
            design_type_warning = (
                f"WARNING: Design type changed from "
                f"{'parametric' if type_before == 1 else 'direct'} to "
                f"{'parametric' if type_after == 1 else 'direct'} "
                f"during code execution. Use set_design_type to recover."
            )
            log.warning(design_type_warning)
        if result is not None:
            try:
                import json as _json
                _json.dumps(result)
            except (TypeError, ValueError):
                result = str(result)

        response = {"executed": True, "result": result, "output": output}
        if design_type_warning:
            response["design_type_warning"] = design_type_warning
        return response

    # ------------------------------------------------------------------
    # Camera helper
    # ------------------------------------------------------------------

    def _camera_info(self):
        try:
            cam = self.app.activeViewport.camera
            return {
                "eye": [cam.eye.x, cam.eye.y, cam.eye.z],
                "target": [cam.target.x, cam.target.y, cam.target.z],
                "up_vector": [cam.upVector.x, cam.upVector.y, cam.upVector.z],
            }
        except Exception:
            return None

    @staticmethod
    def _bbox_dict(bbox):
        return {
            "min": [bbox.minPoint.x, bbox.minPoint.y, bbox.minPoint.z],
            "max": [bbox.maxPoint.x, bbox.maxPoint.y, bbox.maxPoint.z],
        }
