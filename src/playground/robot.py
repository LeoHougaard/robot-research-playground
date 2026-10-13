"""Bounded robot commands. The MCU, not this process, owns the final power cut."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import secrets
import threading
import time
from typing import Callable, Protocol


class RobotError(ValueError):
    pass


@dataclass(frozen=True)
class SafetyLimits:
    max_wheel_mps: float = 0.1
    max_action_s: float = 0.5
    heartbeat_s: float = 0.4
    tracking_s: float = 0.3
    telemetry_s: float = 0.3
    max_temperature_c: float = 55.0
    max_current_a: float = 1.5
    min_voltage_v: float = 9.0
    max_voltage_v: float = 12.6
    max_lease_s: float = 30.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("safety limits must be finite and positive")
        if self.min_voltage_v >= self.max_voltage_v:
            raise ValueError("voltage limits are reversed")


class Backend(Protocol):
    is_real: bool
    def sample(self) -> dict: ...
    def arm(self) -> None: ...
    def drive(self, left: float, right: float, duration_s: float, gripper: float | None) -> None: ...
    def heartbeat(self, tracking_ok: bool) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


def finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RobotError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise RobotError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise RobotError(f"{name} must be a finite number")
    return result


class RobotController:
    """One lease, explicit operator arm, and fail-closed telemetry/tracking checks.

    Host watchdog is defense in depth. A real backend MUST implement its own MCU
    action deadline and watchdog. Workers must never receive the operator arm API.
    """
    def __init__(self, backend: Backend, limits: SafetyLimits | None = None,
                 clock: Callable[[], float] = time.monotonic, *, start_watchdog=True):
        self.backend = backend
        self.limits = limits or SafetyLimits()
        self.clock = clock
        self.state = "DISARMED"
        self.reason = "startup"
        self._lease = None
        self._heartbeat_at = 0.0
        self._action_until = 0.0
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._thread = None
        self.backend.stop()
        if start_watchdog:
            self._thread = threading.Thread(target=self._watchdog, daemon=True, name="robot-watchdog")
            self._thread.start()

    def acquire(self, owner: str, ttl_s: float = 10.0) -> dict:
        with self._lock:
            if self._closed.is_set():
                raise RobotError("controller closed")
            self.tick()
            ttl = finite(ttl_s, "ttl_s")
            if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
                raise RobotError("owner must be a short nonempty string")
            if not 0 < ttl <= self.limits.max_lease_s:
                raise RobotError("lease duration exceeds limit")
            if self._lease is not None:
                raise RobotError("robot already leased")
            self._lease = {"lease_id": secrets.token_urlsafe(24), "owner": owner,
                           "expires_at": self.clock() + ttl}
            self._heartbeat_at = self.clock()
            return dict(self._lease)

    def _require_lease(self, lease_id: str):
        self.tick()
        if self._lease is None or not secrets.compare_digest(str(lease_id), self._lease["lease_id"]):
            raise RobotError("invalid or expired lease")

    def renew(self, lease_id: str, ttl_s: float = 10.0) -> dict:
        """Trusted supervisor renewal. A worker SDK cannot renew its own access."""
        with self._lock:
            self._require_lease(lease_id)
            ttl = finite(ttl_s, "ttl_s")
            if not 0 < ttl <= self.limits.max_lease_s:
                raise RobotError("lease duration exceeds limit")
            self._lease["expires_at"] = self.clock() + ttl
            return dict(self._lease)

    def _healthy(self, sample: dict) -> str | None:
        now = self.clock()
        try:
            t = sample["telemetry"]
            track = sample["tracking"]
            for record, timeout in ((t, self.limits.telemetry_s), (track, self.limits.tracking_s)):
                age = now - finite(record["timestamp"], "timestamp")
                if age < 0 or age > timeout:
                    return "stale telemetry or tracking"
            if track.get("valid") is not True:
                return "tracking lost"
            if t.get("ready") is not True:
                return "hardware is not ready"
            if self.state == "ARMED" and t.get("armed") is not True:
                return "backend disarmed"
            temperatures = t["temperatures_c"]
            currents = t["currents_a"]
            if len(temperatures) != 3 or len(currents) != 3:
                return "missing servo telemetry"
            if any(not 0 <= finite(v, "temperature") <= self.limits.max_temperature_c for v in temperatures):
                return "temperature limit"
            if any(not 0 <= finite(v, "current") <= self.limits.max_current_a for v in currents):
                return "current limit"
            if not self.limits.min_voltage_v <= finite(t["voltage_v"], "voltage") <= self.limits.max_voltage_v:
                return "voltage limit"
            pose = track["pose"]
            for key in ("x", "y", "theta"):
                finite(pose[key], key)
            if track.get("in_bounds") is not True:
                return "robot outside calibrated arena"
        except (KeyError, TypeError, RobotError):
            return "missing or invalid safety data"
        return None

    def _sample(self) -> dict:
        try:
            return self.backend.sample()
        except Exception:
            self.stop("backend observation failed")
            raise

    def arm(self, lease_id: str, *, human_confirmed: bool = False) -> dict:
        with self._lock:
            self._require_lease(lease_id)
            if human_confirmed is not True:
                raise RobotError("operator confirmation required")
            issue = self._healthy(self._sample())
            if issue:
                self.stop(issue)
                raise RobotError(issue)
            try:
                self.backend.arm()
            except Exception:
                self.stop("backend refused arm")
                raise
            self.state, self.reason = "ARMED", "operator armed"
            self._heartbeat_at = self.clock()
            return self.observe()

    def action(self, lease_id: str, left: float, right: float, duration_s: float,
               gripper: float | None = None) -> dict:
        with self._lock:
            self._require_lease(lease_id)
            try:
                left, right, duration_s = (finite(v, n) for v, n in
                                           ((left, "left"), (right, "right"), (duration_s, "duration_s")))
                if max(abs(left), abs(right)) > 1.0:
                    raise RobotError("normalized wheel command exceeds limit")
                if not 0 < duration_s <= self.limits.max_action_s:
                    raise RobotError("action duration exceeds limit")
                if gripper is not None:
                    gripper = finite(gripper, "gripper")
                    if not 0 <= gripper <= 1:
                        raise RobotError("gripper must be between 0 and 1")
                if self.state != "ARMED":
                    raise RobotError("robot is disarmed")
                issue = self._healthy(self._sample())
                if issue:
                    raise RobotError(issue)
                # Normalized API values map to at most 0.1m/s in both backends.
                scale = min(self.limits.max_wheel_mps, 0.1) / 0.1
                self.backend.drive(left * scale, right * scale, duration_s, gripper)
                self._action_until = self.clock() + duration_s
            except Exception:
                self.stop("action rejected or backend failed")
                raise
            return self.observe()

    def heartbeat(self, lease_id: str) -> dict:
        with self._lock:
            self._require_lease(lease_id)
            issue = self._healthy(self._sample())
            if issue:
                self.stop(issue)
                raise RobotError(issue)
            self._heartbeat_at = self.clock()
            try:
                self.backend.heartbeat(tracking_ok=True)
            except Exception:
                self.stop("backend heartbeat failed")
                raise
            return self.observe()

    def stop(self, reason: str = "operator stop") -> None:
        with self._lock:
            self.state, self.reason = "DISARMED", str(reason)
            self._action_until = 0.0
            self.backend.stop()

    def release(self, lease_id: str) -> None:
        with self._lock:
            self._require_lease(lease_id)
            try:
                self.stop("lease released")
            finally:
                self._lease = None

    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            if self._lease and now >= self._lease["expires_at"]:
                self.stop("lease expired")
                self._lease = None
            if self.state == "ARMED":
                issue = self._healthy(self._sample())
                if now - self._heartbeat_at >= self.limits.heartbeat_s:
                    issue = "heartbeat expired"
                if issue:
                    self.stop(issue)
                elif self._action_until and now >= self._action_until:
                    # Backend action deadline stops movement but preserves arming.
                    self.backend.drive(0.0, 0.0, self.limits.max_action_s, None)
                    self._action_until = 0.0

    def observe(self) -> dict:
        with self._lock:
            self.tick()
            sample = self._sample()
            lease = None if self._lease is None else {k: v for k, v in self._lease.items() if k != "lease_id"}
            return {"state": self.state, "reason": self.reason, "real": self.backend.is_real,
                    "lease": lease, "limits": asdict(self.limits), **sample}

    def _watchdog(self):
        while not self._closed.wait(0.025):
            try:
                self.tick()
            except Exception:
                try:
                    self.stop("watchdog backend failure")
                except Exception:
                    pass  # Independent MCU deadline remains authoritative.

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        with self._lock:
            try:
                self.stop("controller closed")
            finally:
                self._lease = None
                self.backend.close()


class SerialBackend:
    """USB serial MCU gateway. No direct host connection to the servo bus.

    ``tracking`` returns the last camera observation without blocking on a frame.
    Optional pyserial is imported only when opening physical hardware.
    """
    is_real = True

    def __init__(self, port: str, tracking: Callable[[], dict], *, transport=None,
                 clock: Callable[[], float] = time.monotonic):
        self.clock, self.tracking = clock, tracking
        self._seq = 0
        self._rx = bytearray()
        self._serial_lock = threading.Lock()
        self._cached = None
        self._cached_at = -float("inf")
        if transport is None:
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError("real hardware requires the hardware extra: pyserial") from exc
            transport = serial.Serial(port, 115200, timeout=0.02, write_timeout=0.05)
        self.transport = transport

    def _call(self, op: str, **kwargs) -> dict:
        import json
        with self._serial_lock:
            self._seq += 1
            seq = self._seq
            self.transport.write((json.dumps({"seq": seq, "op": op, **kwargs}, allow_nan=False) + "\n").encode())
            deadline = time.monotonic() + 0.15
            while time.monotonic() < deadline:
                raw = self.transport.readline(4096)
                if not raw:
                    continue
                self._rx.extend(raw)
                if len(self._rx) > 4096:
                    self._rx.clear()
                    raise RobotError("MCU response exceeds size limit")
                while b"\n" in self._rx:
                    message, _, remaining = self._rx.partition(b"\n")
                    self._rx = bytearray(remaining)
                    try:
                        response = json.loads(message)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(response, dict) or response.get("seq") != seq:
                        continue
                    if response.get("ok") is not True:
                        raise RobotError(str(response.get("error", "MCU rejected command")))
                    return response
            raise RobotError("MCU response deadline exceeded")

    def sample(self):
        now = self.clock()
        if now - self._cached_at >= 0.05:
            data = self._call("status")
            telemetry = data.get("telemetry", {})
            age = finite(telemetry.get("age_ms"), "MCU telemetry age") / 1000
            if age < 0:
                raise RobotError("invalid MCU telemetry age")
            # Request-start time includes USB serialization/response latency.
            telemetry["timestamp"] = now - age
            self._cached, self._cached_at = telemetry, self.clock()
        observation = self.tracking()
        track = observation.get("tracking", observation)
        pose = track.get("pose")
        robot = None if not isinstance(pose, dict) else {"x": pose.get("x"), "y": pose.get("y"), "heading": pose.get("theta")}
        return {"telemetry": dict(self._cached), "tracking": track, "robot": robot,
                "objects": observation.get("objects", [])}

    def arm(self):
        self._call("arm")
        self._cached_at = -float("inf")

    def drive(self, left, right, duration_s, gripper):
        # Repeat bounds at the final host boundary. MCU repeats them once more.
        values = [finite(v, n) for v, n in ((left, "left"), (right, "right"), (duration_s, "duration_s"))]
        if max(abs(values[0]), abs(values[1])) > 1.0 or not 0 < values[2] <= 0.5:
            raise RobotError("MCU envelope exceeded")
        if gripper is not None and not 0 <= finite(gripper, "gripper") <= 1:
            raise RobotError("invalid gripper")
        self._call("action", left=left, right=right, duration_ms=round(duration_s * 1000), gripper=gripper)

    def heartbeat(self, tracking_ok):
        self._call("heartbeat", tracking_ok=tracking_ok)

    def stop(self):
        self._call("stop")
        self._cached_at = -float("inf")

    def close(self):
        self.transport.close()
