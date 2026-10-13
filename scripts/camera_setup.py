"""Capture camera stills, print markers and validate measured camera calibration.

This utility never opens a servo connection or arms a robot.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from playground.vision import Calibration, calibrate


def cv_modules():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError('Install camera dependencies with: python -m pip install -e ".[hardware]"') from exc
    return cv2, np


def atomic_write(path: Path, data: bytes, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise ValueError(f"Output already exists: {path}. Use --overwrite to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def png_with_dpi(frame, dpi=300):
    """Embed PNG physical resolution without another imaging dependency."""
    cv2, _ = cv_modules()
    success, encoded = cv2.imencode(".png", frame)
    if not success:
        raise RuntimeError("PNG encoding failed")
    png = encoded.tobytes()
    density = round(dpi / 0.0254)
    payload = struct.pack(">IIB", density, density, 1)
    kind = b"pHYs"
    chunk = struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    # First chunk is the 13-byte IHDR. Insert physical dimensions after it.
    return png[:33] + chunk + png[33:]


def capture_still(camera, output, *, overwrite=False):
    cv2, _ = cv_modules()
    capture = cv2.VideoCapture(camera)
    try:
        if not capture.isOpened():
            raise RuntimeError("Camera could not be opened")
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Allow exposure to settle using actual frames, with a bounded count.
        frame = None
        for _ in range(10):
            success, candidate = capture.read()
            if success:
                frame = candidate
        if frame is None:
            raise RuntimeError("Camera returned no image")
        success, encoded = cv2.imencode(".png", frame)
        if not success:
            raise RuntimeError("Camera image encoding failed")
        data = encoded.tobytes()
        atomic_write(Path(output), data, overwrite=overwrite)
        return {"image_file": str(Path(output).resolve()), "image_sha256": hashlib.sha256(data).hexdigest(),
                "captured_at": datetime.now().astimezone().isoformat(),
                "width_px": frame.shape[1], "height_px": frame.shape[0], "servo_connection": False}
    finally:
        capture.release()


def generate_markers(output_dir, *, overwrite=False):
    cv2, np = cv_modules()
    dpi, marker_mm, quiet_mm = 300, 20, 4
    px = lambda mm: round(mm / 25.4 * dpi)
    directory = Path(output_dir)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    sheet = np.full((px(297), px(210)), 255, dtype=np.uint8)
    def label(text, x, y, scale=1.2):
        cv2.putText(sheet, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, 2, cv2.LINE_AA)
    label("Robot playground | ArUco DICT_4X4_50", px(10), px(12), 1.2)
    label("A4 | 300 dpi | print at 100% | disable fit-to-page", px(10), px(20), 0.95)
    files = []
    for marker_id in range(6):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, px(marker_mm))
        border = px(quiet_mm)
        tile = np.full((px(marker_mm) + 2 * border, px(marker_mm) + 2 * border), 255, dtype=np.uint8)
        tile[border:border + marker.shape[0], border:border + marker.shape[1]] = marker
        name = f"marker-{marker_id}-20mm-300dpi.png"
        atomic_write(directory / name, png_with_dpi(tile, dpi), overwrite=overwrite)
        files.append(name)
        x, y = px(20 + marker_id % 2 * 100), px(35 + marker_id // 2 * 75)
        sheet[y:y + tile.shape[0], x:x + tile.shape[1]] = tile
        label(f"ID {marker_id}: {'robot' if marker_id == 0 else 'object'} | 20 mm", x, y + tile.shape[0] + px(5), 0.9)
    # A measured ruler catches a printer scaling error before marker mounting.
    x, y, ruler = px(25), px(275), px(100)
    cv2.line(sheet, (x, y), (x + ruler, y), 0, 3)
    for mm in range(0, 101, 10):
        tick_x = x + px(mm)
        cv2.line(sheet, (tick_x, y - px(2)), (tick_x, y + px(2)), 0, 3)
    label("Ruler: end ticks must measure 100 mm", x, y + px(8), 0.9)
    sheet_name = "aruco-markers-a4-300dpi.png"
    atomic_write(directory / sheet_name, png_with_dpi(sheet, dpi), overwrite=overwrite)
    manifest = {"dictionary": "DICT_4X4_50", "dpi": dpi, "paper_mm": [210, 297],
                "marker_outer_side_nominal_mm": marker_mm, "marker_outer_side_px": px(marker_mm),
                "marker_outer_side_actual_mm": px(marker_mm) / dpi * 25.4,
                "quiet_border_nominal_mm": quiet_mm, "ruler_mm": 100,
                "robot_id": 0, "object_ids": list(range(1, 6)), "files": files + [sheet_name],
                "printing": "Print at 100% actual size; measure ruler before use. Do not fit to page."}
    atomic_write(directory / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), overwrite=overwrite)
    return manifest


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a measured finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def measured_calibration(points_path, output, *, max_error_mm=5.0, overwrite=False):
    cv2, _ = cv_modules()
    points_path, output = Path(points_path), Path(output)
    raw = points_path.read_bytes()
    data = json.loads(raw)
    allowed = {"schema_version", "placeholder", "measurement_confirmed", "measured_at", "image_file",
               "image_sha256", "marker_plane_height_m", "arena_width_m", "arena_height_m", "robot_radius_m",
               "robot_marker_id", "robot_mount_heading_offset_rad", "robot_tag_offset_x_m", "robot_tag_offset_y_m",
               "fit_points", "validation_points", "notes"}
    if not isinstance(data, dict) or set(data) - allowed or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("Unknown calibration measurement schema")
    if data.get("placeholder") is not False or data.get("measurement_confirmed") is not True:
        raise ValueError("Placeholder points are unusable. Supply confirmed physical measurements.")
    try:
        measured_at = datetime.fromisoformat(data["measured_at"].replace("Z", "+00:00"))
        if measured_at.tzinfo is None:
            raise ValueError("measurement time must include a timezone")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("measured_at must be an actual ISO timestamp with timezone") from exc
    image_name = data.get("image_file")
    if not isinstance(image_name, str) or not image_name:
        raise ValueError("Source camera image required")
    image_path = Path(image_name)
    if not image_path.is_absolute():
        image_path = points_path.parent / image_path
    image_data = image_path.read_bytes()
    image_hash = hashlib.sha256(image_data).hexdigest()
    if data.get("image_sha256") != image_hash:
        raise ValueError("Camera image SHA256 does not match measured points")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("Source camera image cannot be decoded")
    image_height, image_width = image.shape[:2]
    width, height, radius = (number(data.get(key), key) for key in
                             ("arena_width_m", "arena_height_m", "robot_radius_m"))
    plane_height = number(data.get("marker_plane_height_m"), "marker_plane_height_m")
    heading = number(data.get("robot_mount_heading_offset_rad"), "robot_mount_heading_offset_rad")
    offset_x = number(data.get("robot_tag_offset_x_m"), "robot_tag_offset_x_m")
    offset_y = number(data.get("robot_tag_offset_y_m"), "robot_tag_offset_y_m")
    marker_id = data.get("robot_marker_id")
    if not 0 <= plane_height <= 1 or not -math.pi <= heading <= math.pi:
        raise ValueError("Invalid measured marker height or mounting heading")
    if isinstance(marker_id, bool) or not isinstance(marker_id, int) or not 0 <= marker_id < 50:
        raise ValueError("robot_marker_id must identify a DICT_4X4_50 marker")
    maximum = number(max_error_mm, "max_error_mm")
    if not 0 < maximum <= 5:
        raise ValueError("Calibration max error must be greater than zero and at most 5 mm")
    def pairs(records, name):
        if not isinstance(records, list): raise ValueError(f"{name} must be measured point records")
        result = []
        for record in records:
            if not isinstance(record, dict) or set(record) != {"pixel", "world_m"}:
                raise ValueError(f"{name} records need pixel and world_m only")
            pixel, world = record["pixel"], record["world_m"]
            if not isinstance(pixel, list) or not isinstance(world, list) or len(pixel) != 2 or len(world) != 2:
                raise ValueError("Measured coordinates must contain two numbers")
            pixel = tuple(number(v, "pixel") for v in pixel)
            world = tuple(number(v, "world_m") for v in world)
            if not 0 <= pixel[0] < image_width or not 0 <= pixel[1] < image_height:
                raise ValueError("Measured pixel lies outside source image")
            if not 0 <= world[0] <= width or not 0 <= world[1] <= height:
                raise ValueError("Measured world point lies outside arena")
            result.append((pixel, world))
        return result
    fit = pairs(data.get("fit_points"), "fit_points")
    holdout = pairs(data.get("validation_points"), "validation_points")
    if len(fit) != 4 or not 3 <= len(holdout) <= 100:
        raise ValueError("Four fit points and at least three separate validation points required")
    all_pairs = fit + holdout
    for a, b in itertools.combinations(all_pairs, 2):
        if math.dist(a[0], b[0]) < 1 or math.dist(a[1], b[1]) < 0.001:
            raise ValueError("Fit and validation measurements must be distinct")
    def area(triangle):
        a, b, c = [pair[1] for pair in triangle]
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    if max(area(tri) for tri in itertools.combinations(holdout, 3)) < 0.01 * width * height:
        raise ValueError("Validation points must span the arena and cannot be collinear")
    calibration = calibrate([pair[0] for pair in fit], [pair[1] for pair in fit],
                            width=width, height=height, robot_radius=radius,
                            robot_marker_id=marker_id, heading_offset=heading,
                            marker_offset_x_m=offset_x, marker_offset_y_m=offset_y)
    payload = (json.dumps(asdict(calibration), indent=2, allow_nan=False) + "\n").encode()
    checks = []
    for pixel, world in holdout:
        predicted = calibration.point(*pixel)
        checks.append({"pixel": pixel, "measured_world_m": world, "predicted_world_m": predicted,
                       "error_mm": math.dist(predicted, world) * 1000})
    error_mm = max(check["error_mm"] for check in checks)
    if error_mm > maximum:
        raise ValueError(f"Held-out calibration error {error_mm:.3f} mm exceeds {maximum:.3f} mm; no calibration saved")
    report = {"passed": True, "max_error_mm": error_mm, "allowed_max_error_mm": maximum,
              "measured_at": measured_at.isoformat(), "image_sha256": image_hash,
              "source_image_size_px": [image_width, image_height],
              "calibration_sha256": hashlib.sha256(payload).hexdigest(),
              "measurements_sha256": hashlib.sha256(raw).hexdigest(),
              "marker_plane_height_m": plane_height, "holdout": checks,
              "robot_tag_offset_m": [offset_x, offset_y],
              "physical_accuracy_claim": "Operator-supplied measurements; verify mounting and lens distortion on hardware."}
    report_path = output.with_suffix(".validation.json")
    if not overwrite and (output.exists() or report_path.exists()):
        raise ValueError("Calibration or validation report already exists. Use --overwrite to replace it.")
    # Re-read the strict runtime schema before publishing either artifact.
    Calibration(**json.loads(payload))
    atomic_write(report_path, (json.dumps(report, indent=2, allow_nan=False) + "\n").encode(), overwrite=overwrite)
    atomic_write(output, payload, overwrite=overwrite)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Save an overhead USB camera still, with motors untouched")
    capture.add_argument("--camera", type=int, default=0)
    capture.add_argument("--output", required=True)
    markers = commands.add_parser("markers", help="Generate 300 DPI printable ArUco markers and ruler")
    markers.add_argument("--output-dir", default="hardware/markers")
    fit = commands.add_parser("calibrate", help="Fit only confirmed measured points and validate unseen points")
    fit.add_argument("--points", required=True)
    fit.add_argument("--output", required=True)
    fit.add_argument("--max-error-mm", type=float, default=5.0)
    for command in (capture, markers, fit): command.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture": result = capture_still(args.camera, args.output, overwrite=args.overwrite)
        elif args.command == "markers": result = generate_markers(args.output_dir, overwrite=args.overwrite)
        else: result = measured_calibration(args.points, args.output, max_error_mm=args.max_error_mm, overwrite=args.overwrite)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
