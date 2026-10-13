import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

from playground.vision import Calibration

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("camera_setup", ROOT / "scripts/camera_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class CameraSetupTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional OpenCV dependency not installed")
        self.cv2, self.np = cv2, np
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        if hasattr(self, "temporary"): self.temporary.cleanup()

    def measurements(self):
        frame = self.np.full((1200, 1200), 255, dtype=self.np.uint8)
        image_path = self.directory / "camera.png"
        self.cv2.imwrite(str(image_path), frame)
        # These coordinates are synthetic, explicitly confined to software tests.
        points = {"schema_version": 1, "placeholder": False, "measurement_confirmed": True,
                  "measured_at": "2026-10-12T18:00:00-07:00", "image_file": "camera.png",
                  "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                  "marker_plane_height_m": 0.1, "arena_width_m": 1.0, "arena_height_m": 1.0,
                  "robot_radius_m": 0.16, "robot_marker_id": 0, "robot_mount_heading_offset_rad": 0.0,
                  "robot_tag_offset_x_m": -0.045, "robot_tag_offset_y_m": 0,
                  "fit_points": [{"pixel": p, "world_m": w} for p, w in
                                 [([100,100], [0,0]), ([1100,100], [1,0]),
                                  ([1100,1100], [1,1]), ([100,1100], [0,1])]],
                  "validation_points": [{"pixel": p, "world_m": w} for p, w in
                                        [([350,350], [.25,.25]), ([850,350], [.75,.25]),
                                         ([600,850], [.5,.75])]]}
        return points

    def run_calibration(self, points):
        path = self.directory / "measurements.json"
        path.write_text(json.dumps(points), encoding="utf-8")
        return setup.measured_calibration(path, self.directory / "calibration.json")

    def test_synthetic_calibration_reports_holdout_and_runtime_schema(self):
        report = self.run_calibration(self.measurements())
        self.assertTrue(report["passed"])
        self.assertLess(report["max_error_mm"], 1e-6)
        self.assertEqual(len(report["holdout"]), 3)
        calibration = Calibration.load(self.directory / "calibration.json")
        self.assertAlmostEqual(calibration.point(600, 600)[0], 0.5)
        fields = set(json.loads((self.directory / "calibration.json").read_text()))
        self.assertEqual(fields, {"homography", "width", "height", "robot_radius", "robot_marker_id", "heading_offset", "marker_offset_x_m", "marker_offset_y_m"})
        self.assertEqual(json.loads((self.directory / "calibration.validation.json").read_text())["marker_plane_height_m"], 0.1)
        self.assertEqual(report["calibration_sha256"], hashlib.sha256((self.directory / "calibration.json").read_bytes()).hexdigest())

    def test_holdout_gate_cannot_be_passed_by_perfect_fit_points(self):
        points = self.measurements()
        points["validation_points"][0]["world_m"][0] += 0.006
        with self.assertRaisesRegex(ValueError, "exceeds"): self.run_calibration(points)
        self.assertFalse((self.directory / "calibration.json").exists())
        self.assertFalse((self.directory / "calibration.validation.json").exists())

    def test_supplied_example_is_unusable(self):
        with self.assertRaisesRegex(ValueError, "Placeholder"):
            setup.measured_calibration(ROOT / "configs/calibration-points.example.json", self.directory / "calibration.json")
        self.assertFalse((self.directory / "calibration.json").exists())

    def test_hash_distinct_points_and_confirmation_fail_closed(self):
        for alteration in ("hash", "duplicate", "confirm", "collinear"):
            points = self.measurements()
            if alteration == "hash": points["image_sha256"] = "0" * 64
            elif alteration == "duplicate": points["validation_points"][0] = points["fit_points"][0]
            elif alteration == "confirm": points["measurement_confirmed"] = False
            else:
                points["validation_points"] = [{"pixel": [100+x*1000,600], "world_m": [x,.5]} for x in (.2,.5,.8)]
            with self.subTest(alteration=alteration):
                with self.assertRaises(ValueError): self.run_calibration(points)
                self.assertFalse((self.directory / "calibration.json").exists())

    def test_markers_real_detection_dpi_and_ruler(self):
        result = setup.generate_markers(self.directory)
        sheet = self.cv2.imread(str(self.directory / "aruco-markers-a4-300dpi.png"), self.cv2.IMREAD_GRAYSCALE)
        self.assertEqual(sheet.shape, (3508, 2480))
        detector = self.cv2.aruco.ArucoDetector(self.cv2.aruco.getPredefinedDictionary(self.cv2.aruco.DICT_4X4_50),
                                              self.cv2.aruco.DetectorParameters())
        _, ids, _ = detector.detectMarkers(sheet)
        self.assertEqual(set(ids.flatten()), set(range(6)))
        raw = (self.directory / "aruco-markers-a4-300dpi.png").read_bytes()
        self.assertEqual(raw[37:41], b"pHYs")
        x_dpi, y_dpi, unit = struct.unpack(">IIB", raw[41:50])
        self.assertEqual((x_dpi, y_dpi, unit), (11811, 11811, 1))
        self.assertEqual(result["ruler_mm"], 100)
        for marker_id in range(6):
            tile = self.cv2.imread(str(self.directory / f"marker-{marker_id}-20mm-300dpi.png"), self.cv2.IMREAD_GRAYSCALE)
            _, detected, _ = detector.detectMarkers(tile)
            self.assertEqual(int(detected[0][0]), marker_id)

    def test_capture_releases_camera_and_never_touches_servos(self):
        class FakeCapture:
            def __init__(self): self.released = False
            def isOpened(self): return True
            def set(self, *args): pass
            def read(fake): return True, self.np.full((480, 640, 3), 255, dtype=self.np.uint8)
            def release(self): self.released = True
        from unittest.mock import patch
        capture = FakeCapture()
        with patch.object(self.cv2, "VideoCapture", return_value=capture):
            result = setup.capture_still(0, self.directory / "captured.png")
        self.assertTrue(capture.released)
        self.assertFalse(result["servo_connection"])
        self.assertEqual((result["width_px"], result["height_px"]), (640, 480))
        self.assertEqual(result["image_sha256"], hashlib.sha256((self.directory / "captured.png").read_bytes()).hexdigest())
