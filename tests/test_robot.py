import json
import math
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playground.robot import RobotController, RobotError, SafetyLimits, SerialBackend
from playground.robot_server import make_handler
from playground.sdk import RobotClient
from playground.simulation import SimulationBackend


class FakeClock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class RobotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.backend = SimulationBackend(clock=self.clock)
        self.controller = RobotController(self.backend, clock=self.clock, start_watchdog=False)
        self.lease = self.controller.acquire("test")["lease_id"]

    def tearDown(self): self.controller.close()

    def arm(self): self.controller.arm(self.lease, human_confirmed=True)

    def test_startup_and_lease_do_not_arm(self):
        self.assertEqual(self.controller.observe()["state"], "DISARMED")
        with self.assertRaises(RobotError): self.controller.arm(self.lease)
        with self.assertRaises(RobotError): self.controller.action(self.lease, 1, 1, 0.2)
        self.assertFalse(self.backend.armed)

    def test_single_lease_and_owner_token(self):
        with self.assertRaises(RobotError): self.controller.acquire("other")
        self.arm()
        self.controller.action(self.lease, 1, 1, 0.2)
        with self.assertRaises(RobotError): self.controller.action("wrong", 0, 0, 0.2)
        self.assertEqual(self.controller.state, "ARMED")
        self.assertNotIn("lease_id", self.controller.observe()["lease"])

    def test_action_deadline_stops_without_disarming(self):
        self.arm()
        self.controller.action(self.lease, 1, 1, 0.2)
        self.clock.advance(0.25)
        observation = self.controller.observe()
        self.assertAlmostEqual(observation["robot"]["x"], 0.52)
        self.assertEqual(observation["scene"]["wheel_mps"], [0, 0])
        self.assertEqual(observation["state"], "ARMED")

    def test_lost_host_heartbeat_stops_at_backend_deadline(self):
        self.arm()
        self.controller.action(self.lease, 1, 1, 0.5)
        self.clock.advance(2)
        observation = self.controller.observe()
        self.assertAlmostEqual(observation["robot"]["x"], 0.54)
        self.assertEqual(observation["state"], "DISARMED")
        self.assertIn("heartbeat", observation["reason"])
        self.controller.heartbeat(self.lease)
        self.assertEqual(self.controller.state, "DISARMED")

    def test_lease_expiry_and_renewal(self):
        self.arm()
        self.clock.advance(9)
        # Heartbeat safety may disarm, but it never removes a still-valid lease.
        self.controller.renew(self.lease, 30)
        self.clock.advance(20)
        self.assertIsNotNone(self.controller.observe()["lease"])
        self.clock.advance(11)
        self.assertIsNone(self.controller.observe()["lease"])
        with self.assertRaises(RobotError): self.controller.renew(self.lease)

    def test_invalid_commands_disarm_and_leave_zero_wheels(self):
        for left, right, duration, grip in [(float("nan"), 0, 0.2, None),
                                          (0, float("inf"), 0.2, None),
                                          (1.01, 0, 0.2, None), (0, 0, 0, None),
                                          (0, 0, 0.501, None), (0, 0, 0.2, -0.1),
                                          (True, 0, 0.2, None)]:
            with self.subTest(command=(left, right, duration, grip)):
                self.arm()
                self.controller.action(self.lease, 1, 1, 0.2)
                with self.assertRaises(RobotError): self.controller.action(self.lease, left, right, duration, grip)
                self.assertEqual(self.controller.state, "DISARMED")
                self.assertEqual(self.backend.sample()["scene"]["wheel_mps"], [0, 0])

    def test_missing_tracking_current_temperature_and_voltage_stop(self):
        cases = [("tracking_valid", False), ("telemetry_ready", False),
                 ("temperatures_c", [56, 25, 25]), ("currents_a", [0.1, 1.6, 0.1]),
                 ("currents_a", [0.1, float("nan"), 0.1]),
                 ("currents_a", [0.1, 0.1]), ("voltage_v", 8.9)]
        for attribute, bad in cases:
            previous = getattr(self.backend, attribute)
            self.arm()
            setattr(self.backend, attribute, bad)
            self.controller.tick()
            self.assertEqual(self.controller.state, "DISARMED")
            setattr(self.backend, attribute, previous)

    def test_stale_or_future_frame_rejects_arm(self):
        original = self.backend.sample
        for delta in (-1, 1):
            def sample():
                result = original()
                result["tracking"]["timestamp"] += delta
                return result
            self.backend.sample = sample
            with self.assertRaises(RobotError): self.arm()
            self.assertEqual(self.controller.state, "DISARMED")
        self.backend.sample = original

    def test_arena_clearance_disarms(self):
        self.arm()
        self.backend.x = 0.159
        self.controller.tick()
        self.assertEqual(self.controller.state, "DISARMED")

    def test_tighter_speed_envelope(self):
        self.controller.limits = SafetyLimits(max_wheel_mps=0.05)
        self.arm()
        self.controller.action(self.lease, 1, 1, 0.2)
        self.clock.advance(0.2)
        self.assertAlmostEqual(self.controller.observe()["robot"]["x"], 0.51)

    def test_limits_validate_configuration(self):
        with self.assertRaises(ValueError): SafetyLimits(heartbeat_s=float("nan"))
        with self.assertRaises(ValueError): SafetyLimits(min_voltage_v=13, max_voltage_v=12)

    def test_backend_observation_failure_disarms(self):
        self.arm()
        original = self.backend.sample
        def failed(): raise OSError("camera or MCU disconnected")
        self.backend.sample = failed
        with self.assertRaises(OSError): self.controller.observe()
        self.assertEqual(self.controller.state, "DISARMED")
        self.assertFalse(self.backend.armed)
        self.backend.sample = original


class GatewayTests(unittest.TestCase):
    def test_sdk_and_operator_boundary(self):
        controller = RobotController(SimulationBackend())
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(controller, "worker", "operator"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        client = RobotClient(base, "worker")
        try:
            lease = client.acquire("integration")
            data = json.dumps({"lease_id": lease["lease_id"], "human_confirmed": True}).encode()
            request = Request(base + "/robot/arm", data=data, headers={"Authorization": "Bearer worker"})
            with self.assertRaises(HTTPError) as denied: urlopen(request)
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
            request = Request(base + "/robot/arm", data=data, headers={"Authorization": "Bearer operator"})
            with urlopen(request) as response: self.assertEqual(json.load(response)["state"], "ARMED")
            self.assertEqual(client.action(0.2, 0.2)["state"], "ARMED")
            client.stop()
            self.assertEqual(client.observe()["state"], "DISARMED")
            client.release()
        finally:
            server.shutdown()
            server.server_close()
            controller.close()
            thread.join()


class FakeTransport:
    def __init__(self):
        self.pending = []
        self.armed = False
        self.ready = False
        self.age_ms = 0
        self.commands = []
    def write(self, raw):
        command = json.loads(raw)
        self.commands.append(command)
        if command["op"] == "arm": self.armed = True
        if command["op"] == "stop": self.armed = False
        self.pending.append(json.dumps({"seq": command["seq"], "ok": True,
                                       "telemetry": {"age_ms": self.age_ms, "ready": self.ready,
                                                     "armed": self.armed, "temperatures_c": [25]*3,
                                                     "currents_a": [0.1]*3, "voltage_v": 12}}).encode() + b"\n")
    def readline(self, count): return self.pending.pop(0) if self.pending else b""
    def close(self): pass


class SerialGatewayTests(unittest.TestCase):
    def test_partial_and_unrelated_serial_lines_do_not_break_framing(self):
        class PartialTransport(FakeTransport):
            def write(self, raw):
                super().write(raw)
                message = self.pending.pop()
                self.pending.extend([b'{"seq":0,"ok":true}\n', message[:20], message[20:]])
        transport = PartialTransport()
        backend = SerialBackend("unused", lambda: {}, transport=transport)
        self.assertEqual(backend.sample()["telemetry"]["voltage_v"], 12)
        backend.close()

    def test_hardware_readiness_and_camera_contract(self):
        clock = FakeClock()
        camera = {"tracking": {"timestamp": clock(), "valid": True, "in_bounds": True,
                               "pose": {"x": 0.5, "y": 0.5, "theta": 0.3}},
                  "objects": [{"id": "1", "x": 0.7, "y": 0.5}]}
        transport = FakeTransport()
        backend = SerialBackend("unused", lambda: camera, transport=transport, clock=clock)
        controller = RobotController(backend, clock=clock, start_watchdog=False)
        try:
            lease = controller.acquire("operator")["lease_id"]
            with self.assertRaises(RobotError): controller.arm(lease, human_confirmed=True)
            self.assertNotIn("arm", [c["op"] for c in transport.commands])
            transport.ready = True
            controller.arm(lease, human_confirmed=True)
            self.assertEqual(controller.observe()["robot"]["heading"], 0.3)
            self.assertEqual(controller.observe()["objects"], camera["objects"])
            transport.age_ms = 1000
            clock.advance(0.06)
            controller.tick()
            self.assertEqual(controller.state, "DISARMED")
        finally: controller.close()
