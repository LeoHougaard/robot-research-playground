"""Deterministic differential-drive tabletop simulation, without dependencies."""
from __future__ import annotations
import math
import time
from typing import Callable


class SimulationBackend:
    is_real = False

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 arena_width=1.0, arena_height=1.0, wheel_base=0.154,
                 radius=0.16, max_wheel_mps=0.1):
        if not all(math.isfinite(v) and v > 0 for v in (arena_width, arena_height, wheel_base, radius, max_wheel_mps)):
            raise ValueError("simulation dimensions must be positive and finite")
        if max_wheel_mps > 0.1 or radius * 2 >= min(arena_width, arena_height):
            raise ValueError("simulation speed or footprint exceeds envelope")
        self.clock = clock
        self.width, self.height, self.wheel_base = arena_width, arena_height, wheel_base
        self.radius, self.max_wheel_mps = radius, max_wheel_mps
        self.x, self.y, self.theta = arena_width / 2, arena_height / 2, 0.0
        self.left = self.right = 0.0
        self.gripper = 0.0
        self.armed = False
        self._updated = clock()
        self._until = self._updated
        self._heartbeat = self._updated
        self._held = False
        self.object_x, self.object_y = self.x + 0.15, self.y
        self.tracking_valid = True
        self.telemetry_ready = True
        self.temperatures_c = [25.0] * 3
        self.currents_a = [0.1] * 3
        self.voltage_v = 12.0

    def _advance(self):
        now = self.clock()
        end = min(now, self._until, self._heartbeat + 0.4)
        dt = max(0.0, end - self._updated)
        v, omega = (self.left + self.right) / 2, (self.right - self.left) / self.wheel_base
        if abs(omega) < 1e-10:
            self.x += v * math.cos(self.theta) * dt
            self.y += v * math.sin(self.theta) * dt
        else:
            angle = self.theta + omega * dt
            self.x += v / omega * (math.sin(angle) - math.sin(self.theta))
            self.y -= v / omega * (math.cos(angle) - math.cos(self.theta))
            self.theta = angle
        self.theta = (self.theta + math.pi) % (2 * math.pi) - math.pi
        self._updated = now
        if now >= self._until:
            self.left = self.right = 0.0
        if now >= self._heartbeat + 0.4:
            self.left = self.right = 0.0
            self.armed = False
        if self._held:
            self.object_x = self.x + 0.06 * math.cos(self.theta)
            self.object_y = self.y + 0.06 * math.sin(self.theta)

    def arm(self):
        self._advance()
        self.armed = True
        self._heartbeat = self.clock()

    def drive(self, left, right, duration_s, gripper):
        self._advance()
        if not self.armed:
            raise ValueError("simulation disarmed")
        if not all(math.isfinite(v) for v in (left, right, duration_s)) or max(abs(left), abs(right)) > 1.0 or not 0 < duration_s <= 0.5:
            self.stop()
            raise ValueError("simulation action limit")
        self.left, self.right, self._until = left * self.max_wheel_mps, right * self.max_wheel_mps, self.clock() + duration_s
        if gripper is not None:
            if not math.isfinite(gripper) or not 0 <= gripper <= 1:
                self.stop()
                raise ValueError("simulation gripper limit")
            self.gripper = gripper
            if gripper < 0.5:
                self._held = False
            elif math.hypot(self.object_x - self.x - 0.06 * math.cos(self.theta),
                            self.object_y - self.y - 0.06 * math.sin(self.theta)) < 0.04:
                self._held = True

    def heartbeat(self, tracking_ok):
        self._advance()
        if not tracking_ok:
            self.stop()
        else:
            self._heartbeat = self.clock()

    def stop(self):
        self._advance()
        self.left = self.right = 0.0
        self.armed = False

    def sample(self):
        self._advance()
        now = self.clock()
        return {"telemetry": {"timestamp": now, "ready": self.telemetry_ready,
                              "temperatures_c": list(self.temperatures_c), "currents_a": list(self.currents_a),
                              "voltage_v": self.voltage_v, "armed": self.armed},
                "tracking": {"timestamp": now, "valid": self.tracking_valid,
                             "in_bounds": self.radius <= self.x <= self.width - self.radius and self.radius <= self.y <= self.height - self.radius,
                             "pose": {"x": self.x, "y": self.y, "theta": self.theta}},
                "robot": {"x": self.x, "y": self.y, "heading": self.theta},
                "objects": [{"id": "block", "x": self.object_x, "y": self.object_y, "held": self._held}],
                "scene": {"arena": {"width": self.width, "height": self.height},
                          "robot": {"x": self.x, "y": self.y, "theta": self.theta, "radius": self.radius},
                          "object": {"x": self.object_x, "y": self.object_y, "held": self._held},
                          "gripper": self.gripper, "wheel_mps": [self.left, self.right]}}

    def close(self):
        self.stop()
