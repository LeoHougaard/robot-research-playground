# Agent programs and object predictions

An agent can write its own Python analysis and policy files inside the offline CPU sandbox. It can train a model from recorded exploration trials, preserve the resulting program and model files, and let a successor import them. The trusted supervisor remains responsible for observations, prediction records and every robot action.

The program workflow is:

1. Request `export_trials` with an integer limit from 1 to 500. This writes `trials.jsonl` in the current workspace. Each line is one completed exploration trial with its source, before observation, action, committed prediction and measured after observation. Holdout and inconclusive trials are excluded. Export size is limited to four MiB.
2. Use `run_code` to create and execute a training program in the CPU sandbox. Save the learned coefficients and the Python program in the workspace. Arbitrary generated code has no host fallback.
3. Save a policy under a simple filename such as `policy.py`. The supervisor accepts one Python filename, up to 64 filename characters before `.py`, and a file no larger than 32768 bytes. Paths and shell syntax are rejected.
4. Request `run_policy` with that filename. The supervisor writes `observation.json`, then runs `python /workspace/policy.py` in the same offline sandbox for at most ten seconds within the remaining research budget.
5. The policy prints exactly one JSON object containing `action` and `prediction`. Output is limited to 8192 bytes. The supervisor validates the proposal, commits its prediction, checks the run deadline again, and executes one action through the controller.

For example, a policy result can include the robot pose and a tracked object's expected position:

```json
{
  "action": {"left": 0.3, "right": 0.3, "duration_s": 0.2},
  "prediction": {
    "x": 0.5045,
    "y": 0.5,
    "heading": 0.0,
    "uncertainty_m": 0.05,
    "objects": [{"id": "block", "x": 0.65, "y": 0.5}]
  }
}
```

Object predictions are optional. There may be at most ten entries, each with exactly `id`, `x` and `y`. IDs are unique strings of one to 64 characters, beginning with a letter or digit; later characters may also be `_`, `.`, `:` or `-`. Positions are finite numbers in arena meters. All predicted robot and object coordinates, including robot heading in radians, must have absolute value at most 1000. Booleans and numeric strings are rejected. This loose bound prevents numerical overflow before any motion in the one-meter V1 arena. Use the IDs reported by the observation. Simulation reports `block`; physical camera markers use their numeric ID as a string.

The error result separates the robot's pose error from each requested object's position error:

```json
{
  "robot_m_equivalent": 0.003,
  "objects_m": {"block": 0.02},
  "total_m_equivalent": 0.020223748416156685
}
```

Robot error retains the existing distance metric, including a 0.16-meter weight for heading difference. Object errors are Euclidean distances in meters. The total is the square root of robot error squared plus all requested object errors squared. More requested objects contribute more terms; compare predictions with the same object set. Without object predictions, the metric is exactly the previous robot-pose metric.

Missing, duplicate or nonfinite measurements for a requested object produce an explicit `inconclusive` trial with the raw after observation and no numerical error. A healthy robot remains under its normal watchdog checks. An inconclusive trial cannot support a claimed finding and is not exported as training evidence. Failure to track the robot still triggers the existing safety stop.

This minimal training program fits forward speed from the exported straight-motion trials. An agent can replace it with another bounded CPU approach:

```python
import json, math
from pathlib import Path

terms = []
for line in Path("trials.jsonl").read_text().splitlines():
    trial = json.loads(line)
    action = trial["action"]
    if abs(action["left"] - action["right"]) > 1e-6:
        continue
    before, after = trial["before"]["robot"], trial["after"]["robot"]
    feature = action["left"] * action["duration_s"]
    if abs(feature) < 1e-6:
        continue
    displacement = ((after["x"] - before["x"]) * math.cos(before["heading"])
                    + (after["y"] - before["y"]) * math.sin(before["heading"]))
    terms.append((feature, displacement))
if len(terms) < 3:
    raise ValueError("Need three translating exploration trials")
speed = sum(x*y for x, y in terms) / sum(x*x for x, _ in terms)
Path("agent-model.json").write_text(json.dumps({"linear_mps": speed}))
```

A policy can load those coefficients and make a conservative straight-motion proposal. This example predicts translation of an already held simulation object. Stationary object predictions are hypotheses, not a contact or grasp model:

```python
import json, math
from pathlib import Path

observation = json.loads(Path("observation.json").read_text())
model = json.loads(Path("agent-model.json").read_text())
pose = observation["robot"]
action = {"left": 0.3, "right": 0.3, "duration_s": 0.2}
distance = action["left"] * action["duration_s"] * model["linear_mps"]
dx, dy = distance * math.cos(pose["heading"]), distance * math.sin(pose["heading"])
prediction = {"x": pose["x"] + dx, "y": pose["y"] + dy,
              "heading": pose["heading"], "uncertainty_m": 0.05}
prediction["objects"] = [
    {"id": obj["id"], "x": obj["x"] + (dx if obj.get("held") is True else 0),
     "y": obj["y"] + (dy if obj.get("held") is True else 0)}
    for obj in observation.get("objects", [])[:10]
]
print(json.dumps({"action": action, "prediction": prediction}, allow_nan=False))
```

The fixed simulator supports approach, grasp and carry behavior. Focused tests verify that correct object predictions score separately from robot motion and that a stationary-object prediction receives an error after a controlled grasp. These tests establish simulator behavior only. Physical object effects require camera tracking and measured physical trials.

Programs cannot arm hardware, change controller limits, contact the model server, access USB, select Docker images, mount host directories, or request GPUs. Invalid paths, failed programs, malformed output, out-of-range actions and expired deadlines prevent an action. Artifact listing and import preserve useful files for successors with hash validation. Imported filenames receive an artifact prefix; a successor can copy them to its chosen local filenames inside the sandbox before using them.

Policy tests use a fake sandbox that emits fixed reviewed JSON. A fixture policy contains a host-file write, and the tests verify that its source is never evaluated on the host. Actual Docker execution remains pending until Spark passes disk admission.
