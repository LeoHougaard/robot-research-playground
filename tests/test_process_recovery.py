"""Crash recovery exercised across a real OS process boundary, simulation only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from playground.research import ResearchSupervisor, simulation_controller

ROOT = Path(__file__).resolve().parents[1]


class ProcessRecoveryTests(unittest.TestCase):
    def test_abrupt_exit_preserves_pending_trial_and_program(self):
        source = r'''
import json, os, sys
from playground.research import ResearchSupervisor, simulation_controller
controller, clock = simulation_controller()
s = ResearchSupervisor(sys.argv[1], controller, clock=clock, config={'disk_reserve_bytes': 1})
s.start_robot(operator_confirmed=True)
run = s.archive.start_run('simulation', s.config)
s.storage.begin_generation()
generation = s.archive.start_generation(run)
s.active_generation = generation
s.current_workspace = s.state_dir / 'workspaces' / generation
s.current_workspace.mkdir(parents=True)
s.storage.active_workspace = s.current_workspace
(s.current_workspace / 'useful.py').write_text('print(42)\n')
before = controller.observe()
action = {'left': .1, 'right': .1, 'duration_s': .1}
trial = s.archive.begin_experiment(generation, 'simulation', 'explore', before, action, s.model.predict(before, action))
print(json.dumps({'trial': trial, 'generation': generation}), flush=True)
os._exit(23)  # Neither close(), rollback, nor finally handlers can run.
'''
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.run([sys.executable, '-c', source, directory],
                                   env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                                   capture_output=True, text=True, timeout=20)
            self.assertEqual(child.returncode, 23, child.stderr)
            ids = json.loads(child.stdout)
            controller, clock = simulation_controller()
            recovered = ResearchSupervisor(directory, controller, clock=clock, config={'disk_reserve_bytes': 1})
            try:
                recovered.start_robot(operator_confirmed=True)
                result = recovered.run(generations=1, steps=6, demo=True)
                self.assertEqual(result['status'], 'completed')
                old = recovered.archive.db.execute('SELECT status,after_json,error FROM experiments WHERE id=?', (ids['trial'],)).fetchone()
                self.assertEqual(tuple(old), ('interrupted', None, None))
                old_generation = recovered.archive.db.execute('SELECT status FROM generations WHERE id=?', (ids['generation'],)).fetchone()
                self.assertEqual(old_generation['status'], 'interrupted')
                artifact = recovered.archive.db.execute('SELECT path,bytes FROM artifacts WHERE generation_id=?', (ids['generation'],)).fetchone()
                self.assertEqual((Path(directory) / artifact['path']).read_text(), 'print(42)\n')
                self.assertEqual(recovered.archive.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(recovered.archive.db.execute("SELECT COUNT(*) FROM experiments WHERE status='pending'").fetchone()[0], 0)
            finally:
                recovered.close()


if __name__ == '__main__':
    unittest.main()
