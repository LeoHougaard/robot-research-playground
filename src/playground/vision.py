"""Optional calibrated overhead ArUco camera observations.

All coordinates are metres. Calibrate at marker height, since a floor-plane
homography cannot remove parallax for a marker above the floor.
"""
from __future__ import annotations
from dataclasses import dataclass
import copy
import json
import math
from pathlib import Path
import threading
import time


@dataclass(frozen=True)
class Calibration:
    homography: tuple
    width: float = 1.0
    height: float = 1.0
    robot_radius: float = 0.16
    robot_marker_id: int = 0
    heading_offset: float = 0.0
    marker_offset_x_m: float = 0.0
    marker_offset_y_m: float = 0.0

    def __post_init__(self):
        h = self.homography
        if len(h) != 3 or any(len(row) != 3 for row in h):
            raise ValueError("homography must be 3 by 3")
        if not all(math.isfinite(v) for row in h for v in row):
            raise ValueError("homography must be finite")
        a, b, c = h
        det = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
        if abs(det) < 1e-15:
            raise ValueError("singular homography")
        if not all(math.isfinite(v) and v > 0 for v in (self.width, self.height, self.robot_radius)):
            raise ValueError("arena dimensions and clearance must be positive")
        if self.robot_radius * 2 >= min(self.width, self.height) or not math.isfinite(self.heading_offset):
            raise ValueError("invalid robot clearance or heading offset")
        if not all(math.isfinite(v) for v in (self.marker_offset_x_m, self.marker_offset_y_m)) or math.hypot(self.marker_offset_x_m, self.marker_offset_y_m) >= self.robot_radius:
            raise ValueError("marker offset must be finite and within robot footprint")

    def point(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("pixel coordinates must be finite")
        h = self.homography
        w = h[2][0] * x + h[2][1] * y + h[2][2]
        if abs(w) < 1e-10:
            raise ValueError("point is on homography horizon")
        result = ((h[0][0] * x + h[0][1] * y + h[0][2]) / w,
                  (h[1][0] * x + h[1][1] * y + h[1][2]) / w)
        if not all(math.isfinite(v) for v in result):
            raise ValueError("invalid transformed coordinate")
        return result

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


def marker_observation(markers: dict[int, list], calibration: Calibration, timestamp: float) -> dict:
    """Convert OpenCV clockwise marker corners into the shared observation format.

    Marker edge from corner zero to one defines forward. Set heading_offset after
    measuring the mounting orientation. Marker offsets locate its centre relative
    to the wheel axle in robot-local coordinates, forward x and left y.
    """
    if not math.isfinite(timestamp):
        raise ValueError("timestamp must be finite")
    objects, robot = [], None
    for marker_id, corners in markers.items():
        if len(corners) != 4:
            raise ValueError("four marker corners required")
        world = [calibration.point(float(p[0]), float(p[1])) for p in corners]
        x, y = sum(p[0] for p in world) / 4, sum(p[1] for p in world) / 4
        dx, dy = world[1][0] - world[0][0], world[1][1] - world[0][1]
        if math.hypot(dx, dy) < 1e-8:
            raise ValueError("degenerate marker")
        if marker_id == calibration.robot_marker_id:
            heading = math.atan2(dy, dx) + calibration.heading_offset
            heading = (heading + math.pi) % (2 * math.pi) - math.pi
            offset_x, offset_y = calibration.marker_offset_x_m, calibration.marker_offset_y_m
            x -= offset_x * math.cos(heading) - offset_y * math.sin(heading)
            y -= offset_x * math.sin(heading) + offset_y * math.cos(heading)
            robot = {"x": x, "y": y, "heading": heading}
        else:
            objects.append({"id": str(marker_id), "x": x, "y": y, "held": None})
    valid = robot is not None
    in_bounds = valid and calibration.robot_radius <= robot["x"] <= calibration.width - calibration.robot_radius and calibration.robot_radius <= robot["y"] <= calibration.height - calibration.robot_radius
    return {"robot": robot, "objects": objects,
            "tracking": {"timestamp": timestamp, "valid": valid, "in_bounds": bool(in_bounds),
                         "pose": None if robot is None else {"x": robot["x"], "y": robot["y"], "theta": robot["heading"]}}}


class CameraTracker:
    """Camera reads run separately; MCU heartbeat sees the last frame's age.

    No GUI or audio. A stalled driver never creates fresh tracking timestamps.
    """
    def __init__(self, calibration: Calibration, camera=0, *, clock=time.monotonic,
                 expected_frame_size: tuple[int, int] | None = None):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("camera tracking requires the vision extra: opencv-contrib-python") from exc
        self.cv2, self.calibration, self.clock = cv2, calibration, clock
        if expected_frame_size is not None and (len(expected_frame_size) != 2 or
                any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in expected_frame_size)):
            raise ValueError("expected camera frame size must be positive width and height")
        self.expected_frame_size = expected_frame_size
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        self.capture = cv2.VideoCapture(camera)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError("camera could not be opened")
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._lock = threading.Lock()
        self._last = marker_observation({}, calibration, clock())
        self._frame = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="overhead-camera")
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            timestamp = self.clock()  # Age includes camera-driver and detection latency.
            success, frame = False, None
            try:
                success, frame = self.capture.read()
                markers = {}
                if success:
                    frame_size = (frame.shape[1], frame.shape[0])
                    if self.expected_frame_size is not None and frame_size != tuple(self.expected_frame_size):
                        raise ValueError("camera resolution differs from calibrated source image")
                    corners, ids, _ = self.detector.detectMarkers(frame)
                    if ids is not None:
                        for marker_id, points in zip(ids.flatten(), corners):
                            marker_id = int(marker_id)
                            if marker_id in markers:
                                raise ValueError("duplicate marker id")
                            markers[marker_id] = points.reshape(4, 2).tolist()
                observation = marker_observation(markers, self.calibration, timestamp)
            except Exception as exc:
                observation = marker_observation({}, self.calibration, timestamp)
                observation["tracking"]["reason"] = str(exc)[:200]
            observation["camera"] = {"width_px": None if frame is None else frame.shape[1],
                                     "height_px": None if frame is None else frame.shape[0]}
            with self._lock:
                self._last = observation
                self._frame = frame.copy() if success else None
            if self._stop.wait(0.005):
                break

    def observe(self):
        with self._lock:
            return copy.deepcopy(self._last)

    def tracking(self):
        return self.observe()["tracking"]

    def snapshot_jpeg(self, max_width=640) -> bytes | None:
        """JPEG of the last observation frame; timestamp is in observe().tracking.

        This does not fetch a new frame or refresh the safety timestamp. No camera
        driver work runs while holding the observation lock.
        """
        return self.snapshot(max_width)[0]

    def snapshot(self, max_width=640) -> tuple[bytes | None, dict]:
        """Atomically pair a JPEG with the observation of that same camera frame."""
        if not isinstance(max_width, int) or not 64 <= max_width <= 4096:
            raise ValueError("max_width must be an integer between 64 and 4096")
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            observation = copy.deepcopy(self._last)
        if frame is None:
            return None, observation
        height, width = frame.shape[:2]
        if width > max_width:
            frame = self.cv2.resize(frame, (max_width, max(1, round(height * max_width / width))))
        success, encoded = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 85])
        return (encoded.tobytes() if success else None), observation

    def close(self):
        self._stop.set()
        self.capture.release()
        self._thread.join(timeout=1.0)


def calibrate(pixel_corners, world_corners, *, width=1.0, height=1.0, **kwargs):
    """Four measured matching points, using optional OpenCV's projective fit."""
    import cv2
    import numpy as np
    pixels, world = np.asarray(pixel_corners, dtype=np.float32), np.asarray(world_corners, dtype=np.float32)
    if pixels.shape != (4, 2) or world.shape != (4, 2) or not np.isfinite(pixels).all() or not np.isfinite(world).all():
        raise ValueError("four finite 2D correspondences required")
    h = cv2.getPerspectiveTransform(pixels, world)
    return Calibration(tuple(tuple(float(v) for v in row) for row in h), width=width, height=height, **kwargs)
