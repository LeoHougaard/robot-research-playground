import math
import unittest
from playground.simulation import SimulationBackend


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.backend = SimulationBackend(clock=lambda: self.now)
        self.backend.arm()

    def test_exact_turn_and_bounded_elapsed_time(self):
        self.backend.drive(-1, 1, 0.2, None)
        self.now += 0.3
        state = self.backend.sample()
        self.assertAlmostEqual(state["robot"]["x"], 0.5)
        self.assertAlmostEqual(state["robot"]["y"], 0.5)
        self.assertAlmostEqual(state["robot"]["heading"], 0.2 / 0.154 * 0.2)
        self.now += 1000
        self.assertEqual(self.backend.sample()["robot"], state["robot"])

    def test_arc_is_exact(self):
        self.backend.drive(0, 1, 0.2, None)
        self.now += 0.2
        pose = self.backend.sample()["robot"]
        angle = 0.1 / 0.154 * 0.2
        radius = 0.154 / 2
        self.assertAlmostEqual(pose["x"], 0.5 + radius * math.sin(angle))
        self.assertAlmostEqual(pose["y"], 0.5 + radius * (1 - math.cos(angle)))

    def test_grasp_and_release(self):
        self.backend.object_x = self.backend.x + 0.06
        self.backend.drive(0, 0, 0.2, 1)
        self.assertTrue(self.backend.sample()["objects"][0]["held"])
        self.backend.drive(1, 1, 0.2, None)
        self.now += 0.2
        obj = self.backend.sample()["objects"][0]
        self.assertAlmostEqual(obj["x"], 0.58)
        self.backend.drive(0, 0, 0.2, 0)
        self.assertFalse(self.backend.sample()["objects"][0]["held"])

    def test_backend_refuses_unbounded_commands_without_controller(self):
        with self.assertRaises(ValueError): self.backend.drive(2, 0, 1, None)
        self.assertFalse(self.backend.armed)

    def test_watchdog_runs_even_if_controller_never_ticks(self):
        self.backend.drive(1, 1, 0.5, None)
        self.now += 2
        state = self.backend.sample()
        self.assertAlmostEqual(state["robot"]["x"], 0.54)
        self.assertFalse(state["telemetry"]["armed"])

    def test_invalid_speed_and_clearance_configuration_rejected(self):
        with self.assertRaises(ValueError): SimulationBackend(max_wheel_mps=0.11)
        with self.assertRaises(ValueError): SimulationBackend(radius=float("nan"))
        with self.assertRaises(ValueError): SimulationBackend(radius=0.6)
