import math
import threading
import unittest
from playground.vision import Calibration, CameraTracker, calibrate, marker_observation


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.calibration = Calibration(((0.001, 0, 0), (0, 0.001, 0), (0, 0, 1)))

    def test_calibrated_pose_object_and_rotation(self):
        markers = {0: [[450, 450], [550, 450], [550, 550], [450, 550]],
                   1: [[700, 700], [720, 700], [720, 720], [700, 720]]}
        observation = marker_observation(markers, self.calibration, 10)
        self.assertEqual(observation["robot"], {"x": 0.5, "y": 0.5, "heading": 0.0})
        self.assertTrue(observation["tracking"]["in_bounds"])
        self.assertAlmostEqual(observation["objects"][0]["x"], 0.71)
        rotated = {0: [[550, 450], [550, 550], [450, 550], [450, 450]]}
        self.assertAlmostEqual(marker_observation(rotated, self.calibration, 10)["robot"]["heading"], math.pi/2)

    def test_missing_marker_is_invalid_and_never_fabricates_pose(self):
        result = marker_observation({}, self.calibration, 12)
        self.assertFalse(result["tracking"]["valid"])
        self.assertFalse(result["tracking"]["in_bounds"])
        self.assertIsNone(result["robot"])

    def test_singular_nonfinite_and_horizon_fail(self):
        with self.assertRaises(ValueError): Calibration(((1, 0, 0), (0, 0, 0), (0, 0, 1)))
        with self.assertRaises(ValueError): Calibration(((1, 0, 0), (0, 1, 0), (0, 0, float("nan"))))
        h = Calibration(((1, 0, 0), (0, 1, 0), (1, 0, -1)))
        with self.assertRaises(ValueError): h.point(1, 2)
        with self.assertRaises(ValueError): self.calibration.point(float("inf"), 1)

    def test_robot_footprint_must_stay_inside_arena(self):
        result = marker_observation({0: [[0, 450], [100, 450], [100, 550], [0, 550]]}, self.calibration, 12)
        self.assertTrue(result["tracking"]["valid"])
        self.assertFalse(result["tracking"]["in_bounds"])

    def test_marker_offset_reports_axle_in_both_orientations(self):
        calibration = Calibration(self.calibration.homography, marker_offset_x_m=-0.045,
                                  marker_offset_y_m=0.01)
        straight = {0: [[450,450], [550,450], [550,550], [450,550]]}
        pose = marker_observation(straight, calibration, 12)["robot"]
        self.assertAlmostEqual(pose["x"], 0.545)
        self.assertAlmostEqual(pose["y"], 0.49)
        rotated = {0: [[550,450], [550,550], [450,550], [450,450]]}
        pose = marker_observation(rotated, calibration, 12)["robot"]
        self.assertAlmostEqual(pose["x"], 0.51)
        self.assertAlmostEqual(pose["y"], 0.545)

    def test_clearance_uses_axle_after_offset_correction(self):
        calibration = Calibration(self.calibration.homography, marker_offset_x_m=-0.045)
        near_edge = {0: [[850,450], [870,450], [870,470], [850,470]]}
        result = marker_observation(near_edge, calibration, 12)
        self.assertAlmostEqual(result["robot"]["x"], 0.905)
        self.assertFalse(result["tracking"]["in_bounds"])


class OptionalOpenCVTests(unittest.TestCase):
    def test_changed_camera_resolution_invalidates_tracking(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional OpenCV dependency not installed")
        tracker = CameraTracker.__new__(CameraTracker)
        tracker.cv2 = cv2
        tracker.calibration = Calibration(((.001,0,0), (0,.001,0), (0,0,1)))
        tracker.clock = lambda: 10
        tracker.expected_frame_size = (1000,1000)
        tracker._lock, tracker._stop = threading.Lock(), threading.Event()
        class ChangedResolution:
            def read(self):
                tracker._stop.set()  # Exactly one deterministic frame iteration.
                return True, np.full((480,640), 255, dtype=np.uint8)
        tracker.capture = ChangedResolution()
        tracker._run()
        observation = tracker.observe()
        self.assertFalse(observation["tracking"]["valid"])
        self.assertIn("resolution", observation["tracking"]["reason"])
        self.assertEqual(observation["camera"], {"width_px":640,"height_px":480})

    def test_real_detector_calibration_and_jpeg_without_camera(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional OpenCV dependency not installed")
        calibration = calibrate([[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
                                [[0, 0], [1, 0], [1, 1], [0, 1]])
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        marker = cv2.aruco.generateImageMarker(dictionary, 0, 200)
        frame = np.full((1000, 1000), 255, dtype=np.uint8)
        frame[400:600, 400:600] = marker
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(frame)
        markers = {int(i): c.reshape(4, 2).tolist() for i, c in zip(ids.flatten(), corners)}
        observation = marker_observation(markers, calibration, 10)
        self.assertTrue(observation["tracking"]["valid"])
        self.assertAlmostEqual(observation["robot"]["x"], 0.5, places=2)
        tracker = CameraTracker.__new__(CameraTracker)
        tracker.cv2, tracker._lock = cv2, threading.Lock()
        tracker._frame, tracker._last = frame, observation
        jpeg, paired = tracker.snapshot(640)
        decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        self.assertEqual(decoded.shape, (640, 640))
        self.assertEqual(paired["tracking"]["timestamp"], 10)
        paired["tracking"]["timestamp"] = 99
        self.assertEqual(tracker.observe()["tracking"]["timestamp"], 10)
