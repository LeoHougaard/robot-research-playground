# Robot playground build plan

Build a robot laboratory that learns to predict unfamiliar actions and knows when its evidence is insufficient. All agents, their isolated computer environments, model inference, optional training, observation and evidence storage run on one DGX Spark. A small external microcontroller handles actuator deadlines and power-cut authority.

## Physical design

Use two independently driven wheels and a rear ball caster, three ST3020-class bus servos, and a floor-height gripper. Keep the V1 gripper without a lift so dropped objects remain on the arena floor. Mount the servo controller, watchdog, supply and emergency stop outside the robot. Suspend the power/data tether above the arena and guard against winding and wheel entanglement.

Print the fit coupons before the chassis. The mechanical package supplies editable parameters, watertight STL exports, assembly views, coupons, wheels and horn adapters, a gripper, marker mounts, pucks, a captive slider and a gate. Keep the purchased servo horns; do not print a guessed spline interface. Vendor dimension discrepancies require real measurements before committing to the full print.

Use a one metre square arena as the initial design assumption. The overhead camera must see the entire reachable area. Attach distinct markers to the robot and objects. Keep marker planes at matching heights and account for the robot marker's offset from its drive axle. Use loose objects only after the system can retrieve them; captive fixtures make resets easier.

## Runtime design

| Component | Authority |
| --- | --- |
| Local model server | Generates researcher decisions. Has no motor or Docker authority. |
| Research supervisor | Tracks budgets, leases, generations, predictions and evidence. Uses fixed reviewed tool handlers. |
| Offline CPU sandbox | Runs agent-written Python and shell in a private bounded workspace. Cannot reach the network, host devices, secrets or Docker. |
| Robot controller | Validates commands, tracking, temperature, current, voltage, action duration and lease ownership. |
| MCU and external watchdog | Enforce deadlines and actuator power cut independently of Spark. |
| Archive and viewer | Preserve evidence, code and findings; show status without physical controls. |
| Optional job queue | Runs reviewed training recipes with serialized resource admission. Generated GPU programs remain off in V1. |

Start with one researcher at a time. It receives a fresh conversation and a tool budget, then exits. Its successor inherits findings, measurements and selected programs. Parallel experiments on one robot add little value initially and complicate ownership.

The agent's tools are observations, camera frames, bounded experiments, archive queries, artifact listing/import, offline code execution, reviewed model fitting/evaluation, hypothesis submission and generation completion. Tools are enforced by code. A prompt is not the isolation boundary.

Spark uses ARM64 and unified CPU/GPU memory, so choose compatible images and measure memory needs on the actual host. [NVIDIA's platform guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html) describes those constraints. Keep generous host memory headroom and qualify model inference before running long experiments.

Docker authority is powerful; only the trusted launcher receives it. [Docker's security documentation](https://docs.docker.com/engine/security/) explains the daemon boundary. Workers never receive the socket, host SSH keys, privileged flags or arbitrary mount/image controls. Their root filesystems are read-only; workspace writes occur in a bounded tmpfs and pass a trusted extraction check before being saved.

## Research objectives

The mission is: learn to predict what this robot will do when attempting something unfamiliar, quantify uncertainty, and preserve reproducible evidence and useful tools.

Start with movement calibration. Investigate wheel asymmetry, turning, surface changes and current signals. Progress to approaching objects, dragging pucks, operating the captive slider and opening/closing the gate. Every experiment commits a prediction before action and measures the result afterward.

Useful output is a better prediction, a reusable skill, or a reproducible limitation. A claim must include conditions and existing measurement references. Automatic numerical verification is deliberately narrow. General explanations remain hypotheses until repeated independent tests support them.

Keep physical and simulation evidence separate. Hold out a portion of physical measurements from the built-in model-fitting recipe. Compare candidate predictions with the nominal model and retained best on matched trials. A simple, exact simulator is useful for infrastructure testing but cannot establish real contact mechanics.

## Implementation sequence

The local implementation includes the research runtime, compiled disabled firmware, 40 printable designs, six native/STEP assemblies, wiring diagrams and operator instructions. The software suite passed 83 tests with no skips; the simulation demo recorded 112 trials over four generations. CAD validation and source hashes are recorded separately under `hardware/freecad/validation/`. Spark runtime qualification and all physical acceptance steps remain pending. See [build inventory](build-inventory.md) for the complete physical bill and [CAD regeneration](cad-regeneration.md) for source changes.

1. Complete local simulation, evidence persistence, agent replacement and isolation tests.
2. Resolve Spark disk capacity with Leo while preserving existing work. The read-only audit found the filesystem full; deployment is currently blocked.
3. Deploy the pinned CPU sandbox and qualify the local LLM's tool use. Run a bounded simulation research session on Spark and inspect actual container limits.
4. Print coupons, confirm actuator/horn dimensions, then print and assemble the robot and fixtures.
5. Assemble the external controller and independent default-off power cutoff. Measure electrical levels, servo units and actual motion before enabling firmware arming.
6. Capture the arena, print scaled markers, calibrate measured points and verify camera accuracy on separate reference points.
7. Run supervised short physical actions and fault tests. Verify motor power cuts on host freeze, camera loss, cable loss, MCU freeze and E-stop.
8. Demonstrate automatic recovery and resets for one hour, then 24 hours. Check heat, cable winding and object reachability.
9. Run 72 hours, then one week. Review daily digests and replay selected findings. Expand task variety only after the previous gate passes.

The builder agent can finish source files, printable assets, firmware, tests and operator instructions. Leo must print, assemble and perform the physical acceptance steps. Host changes, data deletion and first physical activation must remain deliberate actions with concrete evidence.
