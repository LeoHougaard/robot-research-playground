"""Held-out simulations must retain the archive's bound physics and limits."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from playground.research import ResearchSupervisor, simulation_controller
from playground.simulation import SimulationBackend


class EvaluationIdentityTests(unittest.TestCase):
    def test_custom_geometry_and_limits_preserved_in_holdout(self):
        controller, clock = simulation_controller(speed=.06)
        controller.backend.width = 2.0
        controller.backend.height = 1.5
        controller.backend.wheel_base = .2
        controller.backend.radius = .11
        controller.limits = replace(controller.limits, wheel_base_m=.2, max_temperature_c=50)
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ResearchSupervisor(directory, controller, clock=clock, config={'disk_reserve_bytes':1})
            try:
                run = supervisor.archive.start_run('simulation',supervisor.config)
                supervisor.active_generation = supervisor.archive.start_generation(run)
                supervisor.evaluate_model(count=2)
                rows = supervisor.archive.experiments(phase='holdout')
                self.assertEqual(len(rows),2)
                binding = json.loads((Path(directory)/'provenance.json').read_text())['payload']
                for row in rows:
                    before = row['before']
                    self.assertEqual(before['scene']['arena'],{'width':binding['geometry']['width'],'height':binding['geometry']['height']})
                    self.assertEqual(before['scene']['robot']['radius'],binding['geometry']['radius'])
                    self.assertEqual(before['limits'],binding['safety_limits'])
                    self.assertEqual(before['robot']['x'],1.0)
                    self.assertEqual(before['robot']['y'],.75)
                copied, _ = simulation_controller(template=controller)
                try:
                    self.assertEqual(copied.backend.wheel_base,.2)
                    self.assertEqual(copied.backend.max_wheel_mps,.06)
                finally:
                    copied.close()
            finally:
                supervisor.close()

    def test_unknown_simulation_implementation_cannot_be_replaced_silently(self):
        class DifferentPhysics(SimulationBackend):
            pass
        controller, _ = simulation_controller()
        controller.backend = DifferentPhysics()
        try:
            with self.assertRaisesRegex(ValueError,'reviewed SimulationBackend'):
                simulation_controller(template=controller)
        finally:
            controller.close()


if __name__ == '__main__':
    unittest.main()
