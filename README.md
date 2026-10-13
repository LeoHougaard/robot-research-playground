# Robot playground

A small physical research lab running entirely on Leo's DGX Spark. Disposable agents choose experiments, predict outcomes, use a robot through bounded commands, and leave measurements and programs for successors. Model training is an optional tool.

The V1 robot has two powered wheels, a caster, and a floor-level gripper. An overhead camera observes the enclosed arena. Compute, power supply, and controller electronics remain offboard. A separate microcontroller and hardware watchdog cut motor power if Spark or control software fails.

Start with [the build plan](docs/BUILD_PLAN.md). The [handoff](docs/HANDOFF.md) records what is verified and what the next agent must finish.

The latest [week-long readiness review](docs/week-readiness.md) records eight repaired failure categories, 96 passing tests and the remaining deployment and physical gates. Unattended physical weeks are still unqualified.

The CAD package contains 40 printable part designs and six assemblies, each supplied as native FreeCAD and analytic STEP. FreeCAD retains editable primitives, dimensions and boolean construction; STEP supports Onshape direct editing. Print-ready STLs include the robot, arena, fixtures and offboard accessories. Start with [the editing guide](docs/onshape.md), [accessory guide](docs/accessories.md) and [build inventory](docs/build-inventory.md). The [independent CAD review](docs/cad-review.md) records what was checked.

## Run the software locally

Python 3.11 or newer is sufficient for simulation. Core runtime has no external Python dependencies.

```bash
python -m pip install -e .
python scripts/verify.py
python -m playground demo --state-dir state/demo
python -m playground serve --state-dir state/demo
```

Open http://127.0.0.1:8765 on the same machine for read-only findings and status. No audio, video autoplay, or physical controls are exposed by this page.

The demo is a deterministic researcher used to verify the experiment pipeline. It exercises multiple generations, immutable measurements, model fitting and unfamiliar trial evaluation. It does not use an LLM or establish physical discoveries.

## Run local model agents on Spark

Read [Spark deployment](docs/spark-deployment.md), [isolation](docs/isolation.md), and [the host audit](docs/spark-audit.md). Current Spark deployment is blocked by insufficient free disk. No files, images or services were changed on Spark.

After meeting admission requirements and configuring a reviewed local model and sandbox image:

```bash
bash scripts/spark-prepare.sh
bash scripts/spark-start.sh
bash scripts/spark-status.sh
bash scripts/spark-stop.sh
```

For a bounded week after qualification:

```bash
python -m playground run --config configs/research-week.json
```

The host supervisor mediates robot tools. Generated shell/Python runs only in offline containers with no Docker socket, devices, credentials or host-write mounts. A trusted launcher controls CPU, memory, process, output and workspace budgets. This is risk reduction, not a guarantee against all shared-kernel or GPU failures.

## Build the robot

- [Mechanical package and print order](hardware/mechanical.md), including fit coupons, CAD source and actual STL files.
- [Offboard accessories](docs/accessories.md): enclosure, camera mount, beam/tether clamps and operator panel.
- [Electronics and wiring](docs/electronics.md).
- [Concrete watchdog circuit and exact parts](docs/watchdog-circuit.md).
- [Robot control and firmware](docs/robot-control.md), including compiled disabled-gate binaries.
- [Camera setup](scripts/camera_setup.py), printable markers and measured calibration.
- [Acceptance gates](docs/acceptance.md).

Physical operation starts disarmed. Firmware ships with HARDWARE_VERIFIED=0 and refuses arming. Human assembly, measured servo/camera checks, an independent power-cut circuit and endurance testing are required before enabling physical research. Nothing here has been tested on a built robot.

## What persists

State contains a SQLite evidence archive, a findings digest, a retained motion model and isolated generation workspaces. Predictions are committed before actions. Measured outcomes come from the trusted controller. Agent-submitted findings remain hypotheses; a reviewed numerical prediction evaluation can receive a narrower verified status.

Successors can list and import predecessor artifacts by identifier. Original artifacts are hash checked and never exposed for agent writes. An OS lock prevents two supervisors from modifying one archive. Restarted incomplete trials become interrupted, never successful.

Agents can export measured trials and write their own CPU models or policy programs. The run_policy tool executes those programs in the sandbox, then validates one proposed action and commits its prediction before any movement. The example in examples/policy.py illustrates the contract. Optional GPU job recipes remain reviewed and separate.

The first real task is to predict short movements. Add object contact, dragging, the captive slider and gate after physical reliability is demonstrated. The simulation's gripper is an ideal latch; it does not verify real grasp physics.
