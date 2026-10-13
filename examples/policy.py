"""Example program for /workspace. Run it only through run_policy.

This illustrates a nominal differential-drive predictor. Agents may replace it
with models fitted to exported measurements; the supervisor still owns motion.
"""
import json
import math
from pathlib import Path

observation = json.loads(Path("observation.json").read_text())
robot = observation["robot"]
action = {"left": .4, "right": .4, "duration_s": .2}
prediction = {"x": robot["x"] + .008 * math.cos(robot["heading"]),
              "y": robot["y"] + .008 * math.sin(robot["heading"]),
              "heading": robot["heading"], "uncertainty_m": .03}
print(json.dumps({"action": action, "prediction": prediction}))
