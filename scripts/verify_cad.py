"""Independent FreeCAD/OpenCascade re-import checks for delivered CAD files.

Run inside FreeCADCmd with task-local user/system preferences. No source CAD file
is saved or changed. CAD_PROJECT_ROOT, CAD_VALIDATION_MANIFESTS and
CAD_VALIDATION_REPORT may override the defaults without FreeCAD CLI arguments.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


PROJECT = Path(os.environ.get("CAD_PROJECT_ROOT", Path(__file__).resolve().parents[1])).resolve()
EDITABLE_TYPES = {"Part::Box", "Part::Cylinder", "Part::Cone", "Part::Sphere", "Part::Torus",
                  "Part::Cut", "Part::Fuse", "Part::MultiFuse", "Part::Common", "Part::MultiCommon",
                  "Part::Compound", "Part::Extrusion", "PartDesign::Pad", "PartDesign::Pocket",
                  "PartDesign::Fillet", "PartDesign::Chamfer", "Sketcher::SketchObject"}
CURVED_ANALYTIC_TYPES = {"Cylinder", "Cone", "Sphere", "Toroid", "Torus"}


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024): digest.update(block)
    return digest.hexdigest()


def resolve_file(value, manifest):
    path = Path(value)
    if path.is_absolute(): return path
    choices = [PROJECT / path, manifest.parent / path]
    return next((choice.resolve() for choice in choices if choice.exists()), choices[0].resolve())


def vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def shape_report(shape):
    bbox = shape.BoundBox
    surfaces = Counter(type(face.Surface).__name__ for face in shape.Faces)
    cylinders = []
    for face in shape.Faces:
        surface = face.Surface
        if type(surface).__name__ == "Cylinder":
            cylinders.append({"radius_mm": float(surface.Radius),
                              "center_mm": vector(surface.Center), "axis": vector(surface.Axis)})
    solids = [{"valid": solid.isValid(), "closed": solid.isClosed(), "volume_mm3": float(solid.Volume),
               "faces": len(solid.Faces)} for solid in shape.Solids]
    return {"valid": shape.isValid(), "closed": shape.isClosed(), "shape_type": shape.ShapeType,
            "solids": solids, "solid_count": len(solids), "faces": len(shape.Faces),
            "shells": len(shape.Shells), "volume_mm3": float(shape.Volume),
            "bounds_mm": [[bbox.XMin, bbox.YMin, bbox.ZMin], [bbox.XMax, bbox.YMax, bbox.ZMax]],
            "dimensions_mm": [bbox.XLength, bbox.YLength, bbox.ZLength],
            "surface_types": dict(surfaces),
            "curved_analytic_faces": sum(count for name, count in surfaces.items() if name in CURVED_ANALYTIC_TYPES),
            "all_faces_triangles": bool(shape.Faces) and all(len(face.Edges) == 3 for face in shape.Faces),
            "cylinders": cylinders}


def valid_solid_errors(report, name):
    errors = []
    if not report["valid"]: errors.append(name + " shape is invalid")
    if report["solid_count"] < 1: errors.append(name + " contains no solids")
    for index, solid in enumerate(report["solids"]):
        if not solid["valid"] or not solid["closed"] or not math.isfinite(solid["volume_mm3"]) or solid["volume_mm3"] <= 0:
            errors.append(f"{name} solid {index} is invalid, open or nonpositive")
    if not all(math.isfinite(value) and value > 0 for value in report["dimensions_mm"]):
        errors.append(name + " dimensions are invalid")
    return errors


def compare_geometry(actual, expected, label, bbox_tolerance=.0001, volume_relative_tolerance=.000001):
    errors = []
    if "bounds_mm" in expected:
        difference = max(abs(a-b) for aa, bb in zip(actual["bounds_mm"], expected["bounds_mm"]) for a, b in zip(aa, bb))
        if difference > bbox_tolerance: errors.append(f"{label} bounds differ by {difference:.8g} mm")
    if "dimensions_mm" in expected:
        difference = max(abs(a-b) for a, b in zip(actual["dimensions_mm"], expected["dimensions_mm"]))
        if difference > bbox_tolerance: errors.append(f"{label} dimensions differ by {difference:.8g} mm")
    if "volume_mm3" in expected:
        difference = abs(actual["volume_mm3"]-expected["volume_mm3"])
        tolerance = max(.0001, abs(expected["volume_mm3"])*volume_relative_tolerance)
        if difference > tolerance: errors.append(f"{label} volume differs by {difference:.8g} mm3")
    if "solid_count" in expected and actual["solid_count"] != expected["solid_count"]:
        errors.append(f"{label} solid count differs: {actual['solid_count']} vs {expected['solid_count']}")
    return errors


def cylinder_matches(cylinders, radius, center, tolerance=.0001):
    return any(abs(item["radius_mm"]-radius) <= tolerance and
               abs(item["center_mm"][0]-center[0]) <= tolerance and
               abs(item["center_mm"][1]-center[1]) <= tolerance and
               abs(abs(item["axis"][2])-1) <= tolerance for item in cylinders)


def critical_interface_errors(identifier, report):
    """Independent V1 hole requirements, not merely values copied from exports."""
    errors = []
    if identifier in {"01_horn_fit_coupon", "12_drive_wheel", "20_moving_jaw"}:
        for center in ((7, 0), (-7, 0), (0, 7), (0, -7)):
            if not cylinder_matches(report["cylinders"], 1.7, center):
                errors.append(f"horn clearance hole missing at {center}, diameter 3.4 mm")
        if not cylinder_matches(report["cylinders"], 3.3, (0, 0)):
            errors.append("horn center access diameter is not 6.6 mm")
    if identifier in {"14_caster_spacer", "15_caster_shim_05", "16_caster_shim_10"}:
        for center in ((0, -6.75), (0, 6.75)):
            if not cylinder_matches(report["cylinders"], 1.25, center):
                errors.append("caster interface does not have 2.5 mm holes on 13.5 mm centres")
    if identifier == "camera_plate":
        for center in ((20, 31), (80, 31), (20, 69), (80, 69)):
            if not cylinder_matches(report["cylinders"], 2.25, center):
                errors.append("camera M4 hole pattern does not match the translated beam clamp")
        for center in ((108, 50), (128, 50)):
            if not cylinder_matches(report["cylinders"], 3.35, center):
                errors.append("camera tripod clearance slot is absent or inside the clamp footprint")
    if identifier in {"beam_clamp_lower", "beam_clamp_upper"}:
        for center in ((10, 8), (70, 8), (10, 46), (70, 46)):
            if not cylinder_matches(report["cylinders"], 2.25, center):
                errors.append("beam clamp M4 hole pattern differs from camera/anchor interface")
    if identifier == "estop_panel":
        if not cylinder_matches(report["cylinders"], 11.25, (60, 60)):
            errors.append("E-stop panel opening is not 22.5 mm at its required centre")
        if not cylinder_matches(report["cylinders"], 8, (60, 130)):
            errors.append("physical arm-button opening is not 16 mm at its required centre")
    return errors


def robot_reference_checks(doc):
    """Check actual vendor circles and nominal solid overlap in world coordinates."""
    result = {"placements": [], "nominal_intersections": [], "errors": []}
    references = [obj for obj in doc.Objects if hasattr(obj, "FitStatus")]
    printed = [obj for obj in doc.Objects if obj.TypeId == "App::Link"]
    for obj in references:
        bounds = obj.Shape.BoundBox
        item = {"name": obj.Name, "label": obj.Label,
                "bounds_mm": [[bounds.XMin, bounds.YMin, bounds.ZMin], [bounds.XMax, bounds.YMax, bounds.ZMax]]}
        if "ST3020" in obj.Label:
            if "gripper" in obj.Label:
                expected, axis = [360, 323, 12], [0, 0, -1]
            else:
                sign = -1 if obj.Label.endswith("-1") else 1
                expected, axis = [300, 300+sign*72, 32.5], [0, sign, 0]
            circles = []
            for edge in obj.Shape.Edges:
                try:
                    curve = edge.Curve
                except TypeError:  # Degenerate vendor edges have no curve.
                    continue
                if type(curve).__name__ == "Circle" and abs(curve.Radius-10) < .0001:
                    normal = vector(curve.Axis)
                    if abs(sum(a*b for a, b in zip(normal, axis))) > .9999:
                        circles.append(vector(curve.Center))
            if not circles:
                result["errors"].append(obj.Label + " has no actual 20 mm horn circle on the expected shaft axis")
            else:
                rim = max(circles, key=lambda point: sum(a*b for a, b in zip(point, axis)))
                # The radius-10 rim is 0.3 mm behind the contact face because
                # the vendor horn has a chamfer. Independently find the actual
                # planar contact annulus, not the chamfer's rear circle.
                contacts = []
                for face in obj.Shape.Faces:
                    if type(face.Surface).__name__ != "Plane": continue
                    normal = vector(face.Surface.Axis)
                    if abs(sum(a*b for a,b in zip(normal,axis))) < .9999: continue
                    center = vector(face.CenterOfMass)
                    offset = [a-b for a,b in zip(center,expected)]
                    axial = sum(a*b for a,b in zip(offset,axis))
                    transverse = math.sqrt(sum((a-axial*b)**2 for a,b in zip(offset,axis)))
                    bb = face.BoundBox
                    spans = [bb.XLength,bb.YLength,bb.ZLength]
                    transverse_spans = [span for span,direction in zip(spans,axis) if direction == 0]
                    if transverse < .05 and all(19 <= span <= 20.1 for span in transverse_spans):
                        contacts.append((abs(axial),center))
                error, actual = min(contacts) if contacts else (float("inf"), None)
                item.update(horn_contact_face_center_mm=actual, radius10_rim_center_mm=rim,
                            expected_horn_face_center_mm=expected, expected_axis=axis,
                            horn_contact_plane_error_mm=error)
                if error > .001: result["errors"].append(obj.Label + " has no correctly placed planar horn contact face")
        elif "caster" in obj.Label:
            item["ball_floor_z_mm"] = bounds.ZMin
            if abs(bounds.ZMin) > .001: result["errors"].append("caster does not touch the nominal floor")
            if abs(bounds.Center.x-218) > 2 or abs(bounds.Center.y-300) > 2:
                result["errors"].append("caster reference is outside the expected rear support position")
        for part in printed:
            other = part.Shape.BoundBox
            if min(bounds.XMax, other.XMax)-max(bounds.XMin, other.XMin) <= .0001: continue
            if min(bounds.YMax, other.YMax)-max(bounds.YMin, other.YMin) <= .0001: continue
            if min(bounds.ZMax, other.ZMax)-max(bounds.ZMin, other.ZMin) <= .0001: continue
            overlap = obj.Shape.common(part.Shape).Volume
            if overlap > .01:
                result["nominal_intersections"].append({"reference": obj.Label, "printed_part": part.Label,
                                                         "overlap_volume_mm3": overlap})
        result["placements"].append(item)
    if len(references) != 4: result["errors"].append("assembly must include three servo references and one caster reference")
    if result["nominal_intersections"]:
        result["errors"].append("purchased references overlap printed solids; inspect before claiming nominal fit")
    return result


def inspect_entry(entry, manifest, App, Part, legacy):
    identifier = entry.get("id", entry.get("name", "unnamed"))
    record = {"id": identifier, "manifest": str(manifest.relative_to(PROJECT)), "errors": [],
              "print_quantity": entry.get("quantity"), "material": entry.get("material")}
    native_path = resolve_file(entry["native_path"], manifest)
    step_path = resolve_file(entry["step_path"], manifest)
    record.update(native_path=str(native_path.relative_to(PROJECT)), step_path=str(step_path.relative_to(PROJECT)),
                  native_sha256=file_digest(native_path), step_sha256=file_digest(step_path))
    with zipfile.ZipFile(native_path) as archive:
        if "GuiDocument.xml" not in archive.namelist():
            record["errors"].append("native GUI presentation metadata is missing")
        else:
            gui = ET.fromstring(archive.read("GuiDocument.xml"))
            visible = [provider.attrib["name"] for provider in gui.findall("./ViewProviderData/ViewProvider")
                       if provider.find("./Properties/Property[@name='Visibility']/Bool") is not None
                       and provider.find("./Properties/Property[@name='Visibility']/Bool").attrib.get("value") == "true"]
            camera = gui.find("Camera")
            record["gui_presentation"] = {"visible_objects": visible,
                "camera_saved": camera is not None and bool(camera.attrib.get("settings"))}
            if visible != [entry["final_object"]] or not record["gui_presentation"]["camera_saved"]:
                record["errors"].append("native GUI must show only the final object and save a camera")
    doc = App.openDocument(str(native_path))
    try:
        doc.recompute()
        final = doc.getObject(entry.get("final_object", "FinalPart"))
        if final is None or not hasattr(final, "Shape"):
            raise ValueError("named native final solid is absent")
        native_shape = final.Shape.copy()
        native = shape_report(native_shape)
        features = [{"name": obj.Name, "type": obj.TypeId, "label": obj.Label,
                     "state": [str(state) for state in obj.State],
                     "dependencies": [item.Name for item in obj.OutList]} for obj in doc.Objects]
        record["native_feature_tree"] = features
        editable_count = sum(obj.TypeId in EDITABLE_TYPES for obj in doc.Objects)
        record["editable_feature_count"] = editable_count
        if editable_count == 0: record["errors"].append("native file contains no editable primitive/sketch/boolean features")
        if any(any("Invalid" in state or "Error" in state for state in feature["state"]) for feature in features):
            record["errors"].append("native feature tree reports an error after recompute")
        if identifier == "02_hole_clearance_coupon":
            driver = next(obj for obj in doc.Objects if obj.TypeId == "Part::Box")
            original_length = float(driver.Length.Value)
            try:
                driver.Length = original_length + 1
                doc.recompute()
                changed = shape_report(final.Shape)
            finally:
                driver.Length = original_length
                doc.recompute()
            restored = shape_report(final.Shape)
            demonstrated = (abs(changed["dimensions_mm"][0]-native["dimensions_mm"][0]-1) < .0001
                            and not compare_geometry(restored, native, "restored edit"))
            record["native_editability_demo"] = {"feature": driver.Name, "property": "Length", "delta_mm": 1,
                "original_length_mm": original_length, "changed_final_dimensions_mm": changed["dimensions_mm"],
                "restored_final_dimensions_mm": restored["dimensions_mm"], "saved_to_source": False, "passed": demonstrated}
            if not demonstrated: record["errors"].append("native primitive edit did not recompute and restore the expected final solid")
        if final.Name == "Final":
            dimensions = doc.getObject("Dimensions")
            properties = [name for name in dimensions.PropertiesList
                          if dimensions.getGroupOfProperty(name) == "Dimensions"]
            property_name = next((name for name in properties if name.endswith("Length")),
                                 "GuardHeight" if "GuardHeight" in properties else properties[0])
            original = float(getattr(dimensions, property_name).Value)
            try:
                setattr(dimensions, property_name, original + .25)
                doc.recompute()
                changed = shape_report(final.Shape)
            finally:
                setattr(dimensions, property_name, original)
                doc.recompute()
            restored = shape_report(final.Shape)
            demonstrated = (changed["valid"] and abs(changed["volume_mm3"]-native["volume_mm3"]) > .0001
                            and not compare_geometry(restored, native, "restored parameter edit"))
            record["native_editability_demo"] = {"feature": dimensions.Name, "property": property_name,
                "delta_mm": .25, "volume_delta_mm3": changed["volume_mm3"]-native["volume_mm3"],
                "saved_to_source": False, "passed": demonstrated}
            if not demonstrated: record["errors"].append("accessory dimension edit failed to propagate or restore")
        if identifier == "robot_playground_assembly":
            checks = robot_reference_checks(doc)
            record["vendor_reference_checks"] = checks
            record["errors"] += checks["errors"]
            if native["bounds_mm"][1][2] > 150:
                record["errors"].append("connected robot/arena assembly includes floating overhead geometry above 150 mm")
    finally:
        App.closeDocument(doc.Name)
    # A fresh OpenCascade reader reads the written STEP, independent of exporter objects.
    step_shape = Part.read(str(step_path))
    step = shape_report(step_shape)
    record["native"], record["step"] = native, step
    record["errors"] += valid_solid_errors(native, "native") + valid_solid_errors(step, "STEP")
    record["errors"] += compare_geometry(step, native, "STEP versus reopened native")
    record["errors"] += compare_geometry(step, entry, "STEP versus manifest")
    if native["curved_analytic_faces"] and not step["curved_analytic_faces"]:
        record["errors"].append("curved native geometry was replaced by planar/faceted STEP faces")
    if step["all_faces_triangles"] and step["faces"] > 6:
        record["errors"].append("STEP contains only triangular faces, a faceted-solid substitute")
    record["errors"] += critical_interface_errors(identifier, step)
    if identifier == "estop_panel":
        # Explicitly test the Omron lock-ring key outside the circular hole.
        key_clear = all(not step_shape.isInside(App.Vector(x,y,1.5),.00001,True)
                        for x,y in ((60,72.9),(61.6,72.9)))
        surrounding_solid = all(step_shape.isInside(App.Vector(x,y,1.5),.00001,True)
                                for x,y in ((61.8,72.9),(60,73.2)))
        record["estop_key_check"] = {"key_clear": key_clear, "surrounding_solid": surrounding_solid,
                                     "nominal_key_width_mm": 3.3, "nominal_overall_cutout_height_mm": 24.3}
        if not key_clear or not surrounding_solid:
            record["errors"].append("E-stop antirotation key cutout does not match the vendor lock-ring envelope")
    if identifier in legacy:
        original = legacy[identifier]
        record["legacy_stl_comparison"] = {"dimensions_mm": original["dimensions_mm"], "volume_mm3": original["volume_mm3"],
            "bbox_tolerance_mm": .001, "volume_relative_tolerance": .001}
        record["errors"] += compare_geometry(step, original, "STEP versus original STL dimensions/volume", .001, .001)
    if "print_rotation_xyz_deg" in entry:
        import Mesh
        print_shape = native_shape.copy()
        for angle, axis in zip(entry["print_rotation_xyz_deg"], ((1,0,0),(0,1,0),(0,0,1))):
            if angle: print_shape.rotate(App.Vector(0,0,0), App.Vector(*axis), angle)
        print_shape.translate(App.Vector(*entry["print_translation_mm"]))
        expected_print = shape_report(print_shape)
        stl_path = resolve_file(entry["stl_path"], manifest)
        stl_hash = file_digest(stl_path)
        mesh = Mesh.Mesh(str(stl_path))
        bb = mesh.BoundBox
        actual_print = {"bounds_mm": [[bb.XMin,bb.YMin,bb.ZMin],[bb.XMax,bb.YMax,bb.ZMax]],
                        "dimensions_mm": [bb.XLength,bb.YLength,bb.ZLength],
                        "volume_mm3": mesh.Volume, "solid_count": 1 if mesh.isSolid() else 0,
                        "closed_solid": mesh.isSolid(), "facets": mesh.CountFacets}
        record["print_stl_comparison"] = {"path": str(stl_path.relative_to(PROJECT)), "sha256": stl_hash,
            "actual": actual_print, "expected_transformed_bounds_mm": expected_print["bounds_mm"],
            "bbox_tolerance_mm": .13, "volume_relative_tolerance": .005}
        record["errors"] += compare_geometry(actual_print, expected_print, "transformed print STL", .13, .005)
        if not actual_print["closed_solid"] or abs(bb.ZMin) > .0001:
            record["errors"].append("print STL is open or not on the Z=0 print bed")
        if file_digest(stl_path) != stl_hash: record["errors"].append("print STL changed during validation")
    # Inspection never saves native documents or modifies exporter files.
    if record["native_sha256"] != file_digest(native_path) or record["step_sha256"] != file_digest(step_path):
        record["errors"].append("CAD source changed during validation")
    record["passed"] = not record["errors"]
    return record


def main():
    import FreeCAD as App
    import Part
    defaults = [PROJECT / "hardware/freecad/robot/cad_manifest.json", PROJECT / "hardware/freecad/accessories/cad_manifest.json"]
    paths = os.environ.get("CAD_VALIDATION_MANIFESTS")
    manifests = [Path(path).resolve() for path in paths.split(os.pathsep)] if paths else defaults
    legacy_path = PROJECT / "hardware/prints/mesh_report.json"
    legacy = {item["part"]: item for item in json.loads(legacy_path.read_text())} if legacy_path.exists() else {}
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "freecad_version": App.Version(),
              "kernel": "FreeCAD Part/OpenCascade", "units": "mm", "physical_fit_verified": False,
              "parts": [], "errors": []}
    ids = set()
    for manifest in manifests:
        if not manifest.exists():
            report["errors"].append("CAD manifest is missing: " + str(manifest))
            continue
        data = json.loads(manifest.read_text())
        entries = data if isinstance(data, list) else data.get("parts", []) + data.get("assemblies", [])
        if not entries: report["errors"].append("CAD manifest has no parts: " + str(manifest))
        for entry in entries:
            identifier = entry.get("id", entry.get("name", "unnamed"))
            ids.add(identifier)
            try:
                record = inspect_entry(entry, manifest, App, Part, legacy)
            except Exception as exc:
                record = {"id": identifier, "passed": False, "errors": [type(exc).__name__ + ": " + str(exc)]}
            report["parts"].append(record)
    missing = sorted(set(legacy)-ids)
    if missing: report["errors"].append("Original printable parts absent from CAD manifests: " + ", ".join(missing))
    report["passed"] = bool(report["parts"]) and not report["errors"] and all(item["passed"] for item in report["parts"])
    report["checked_files"] = len(report["parts"])
    report["custom_part_designs"] = sum(item.get("print_quantity") is not None for item in report["parts"])
    report["default_print_pieces"] = sum(item.get("print_quantity") or 0 for item in report["parts"])
    report["assembly_layouts"] = len(report["parts"])-report["custom_part_designs"]
    target = Path(os.environ.get("CAD_VALIDATION_REPORT", PROJECT / "hardware/freecad/validation/kernel_report.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(json.dumps({"passed": report["passed"], "checked_files": report["checked_files"], "report": str(target),
                      "errors": report["errors"], "failed_parts": [item["id"] for item in report["parts"] if not item["passed"]]}), flush=True)
    return 0 if report["passed"] else 1


# FreeCADCmd executes Python files with application-owned globals rather than
# consistently assigning __name__='__main__'. This file is an executable only.
result_code = main()
sys.stdout.flush()
sys.stderr.flush()
os._exit(result_code)  # FreeCAD otherwise reports Python script failures as exit 0.
