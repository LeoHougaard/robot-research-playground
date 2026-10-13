# Robot playground handoff

The project is in C:\Users\Leo\.t3\scratch\2026-10-13-i-have-been-thinking-that-c2783154. Build a physical playground where disposable local agents learn to predict unfamiliar robot actions, test explanations, and preserve measurements and programs. Training a model is one optional tool. All runtime compute belongs on Leo's DGX Spark, with external low-level electronics and an overhead camera.

Leo asked for implementation using parallel GPT-6.1-Sol agents at high reasoning effort. He does not know when the announced usage reset will occur. The work is saved here; regenerate the portable package with python scripts/package.py. Read README.md and docs/BUILD_PLAN.md first.

Latest steering requires CAD for the complete build, ready to edit in Onshape or FreeCAD. The package now contains 40 printable part designs and six assemblies as native FreeCAD and analytic STEP, plus print-ready STLs. The robot/arena assembly contains 78 instances and 108 solids, including authentic servo and caster references. Five offboard assemblies cover electronics, camera, tether, cable clamp and operator panel. See docs/build-inventory.md, docs/onshape.md and docs/accessories.md for entrypoints, quantities and purchased items.

FreeCAD 1.1.4 is available as a checksum-pinned task-local portable runtime in .tools/freecad-runtime.json. scripts/bootstrap_freecad.py downloads it on Windows; scripts/run_freecad.py runs trusted builder scripts with isolated settings. The runtime is excluded from the handoff package. Native trees use editable CSG primitives, rather than sketch/pad histories. STEP supports Onshape solid editing but does not transfer the native tree. After regeneration, apply scripts/cad_visibility.py to both CAD manifests to save clean display state; run scripts/verify_cad.py inside FreeCADCmd and review hardware/freecad/validation/kernel_report.json.

Follow docs/cad-regeneration.md for the complete rebuild order. The arm-button mount is dedicated to Adafruit 1505 at a nominal 16 mm bore, with supplied PRESS TO AUTHORIZE labels. A fresh press opens the five-second MCU authorization window; holding does not renew it. The host must still arm separately. No physical operation was enabled.

## Implemented

- Three-servo differential-drive robot and arena mechanical package, 27 printable designs, analytic CAD, vendor references, fit coupons, print manifest and inspected assembly renders.
- Thirteen offboard accessory designs, editable dimensions, five self-contained assemblies, oriented STL exports, measured-interface guidance and inspected previews.
- ESP32 firmware, compiled actuator-disabled binaries and build hashes. Firmware ships with HARDWARE_VERIFIED=0.
- Bounded controller with single lease, operator arming, telemetry/tracking checks and host watchdog; external hardware cutoff is part of the electronics design.
- Overhead ArUco tracker, printable markers and camera capture/calibration CLI. Calibration checks independent measured points, source hashes, marker height, tag offset and camera resolution.
- Python stdlib simulation, SQLite evidence archive, predictions committed before actions, agent replacement, artifact hashes and inheritance, model fitting/evaluation and retained best-model checkpoint.
- Local OpenAI-compatible client with loopback restriction, bounded decisions/responses and socket deadlines. No cloud account is required at runtime.
- Offline non-root Docker sandbox with bounded tmpfs, CPU/memory/process/output/time limits, safe artifact extraction and exact cleanup. Workers never receive Docker, USB, host-write mounts, SSH credentials or arming authority.
- Tools for observations, images, experiments, archive/artifact access, dataset export, agent-written CPU programs, bounded policy proposals, model fitting and claim submission. Object prediction can supplement robot pose prediction.
- Optional fixed CPU/GPU jobs. Arbitrary generated GPU programs remain disabled because shared drivers and memory cannot guarantee zero host disruption.
- Spark prepare/start/stop/status scripts, optional model-service template, read-only loopback viewer, ordinary and week-long research configurations.
- Build, wiring, watchdog, isolation, deployment, review and acceptance documentation.

## Verification

The integrated software suite passed 83 tests in .build-venv with actual OpenCV installed and no skips. The machine-readable report is docs/verification.json. Re-run python scripts/verify.py for the current count. The four-generation demo passed 112 recorded trials and four matched prediction evaluations. These describe the simple simulator, not physical learning.

Local HTTP fixtures verified the structured tool loop, agent replacement, inherited programs, rejected fabricated evidence, camera/output quotas, response/header deadlines, prohibited redirects, corrupted artifacts and exclusive supervisor ownership. The fixture is not a qualified real model.

ESP32 firmware compiled with the Xtensa toolchain. Binaries and evidence are in firmware/robot_controller/dist. HARDWARE_VERIFIED=0 is intentional. CAD topology, intersections, grasp poses and source/STL hashes are recorded in hardware/prints/build_evidence.json. All 27 meshes are watertight single solids and 1,633 nominal checks passed.

Final independent FreeCAD/OpenCascade validation passed all 46 native/STEP pairs with 163 valid, closed, positive-volume solids across parts and layouts. Thirteen accessory STLs passed transformed geometry and print-bed checks. Fourteen native dimension edits recomputed and restored correctly. The keyed E-stop and 16 mm arm-button cutouts passed independent geometry checks; vendor references have no unintended printed-part overlap. All 46 documents also passed actual FreeCAD GUI opening and final-geometry visibility checks without changing source files. Reports and file hashes are in hardware/freecad/validation/. These checks do not establish manufacturing fit or strength.

The local viewer was opened in the T3 browser and its rendered DOM/status checked without overflow. Preview screenshot capture failed at the client. CAD renders and marker sheets were inspected separately. No media auto-plays.

## Blocking conditions

The read-only Spark audit found leo on ARM64, Docker 29.2.1 and GB10 driver 580.173.02, but the filesystem reports 100% usage and approximately 2.7 GiB available. Runtime admission requires at least 20 GiB free plus model/image needs. No remote files, images, containers, services or host configuration were changed. Existing Pixel, local-model and Isaac Lab work must remain untouched.

Additional read-only inventory found /mnt/gb10-ssd1 with 683 GiB free and /mnt/gb10-ssd2 with 916 GiB free. Both mount roots are root-owned; inspect existing writable children or arrange one approved project directory. Docker still stores data on the full root filesystem. See docs/storage-options.md for reviewed options and shared storage accounting. Docker's reclaimable labels do not authorize deleting those resources.

The robot is unbuilt. Physical fit, strength, servo shaft loading, wiring, register units, camera calibration, grasp behavior, continuous duty and unattended endurance remain untested. The vendor STEP and drawing disagree on servo height. Physical activation requires measured acceptance with Leo present.

Real Docker execution, the selected local model, GPU recipes and ARM64 hardware dependencies require Spark qualification after disk admission passes. Mocked isolation checks do not establish actual enforcement.

## Next steps for the dot agent

1. Re-run acceptance and review docs/review.md, docs/acceptance.md and the latest verification report. Preserve existing work and safety boundaries.
2. Discuss the Spark disk blocker with Leo. Present concrete storage options from a read-only audit. Do not delete data, prune Docker or stop unrelated workloads. Use n-gb10 for remote operations.
3. Once space exists, deploy the pinned CPU image and qualify real container limits. Check model memory headroom and structured-tool reliability. Optional Compose stays a template until its exact image/model is verified.
4. Print coupons, measure actual servos/horns, then print and assemble the mechanical package. Follow quantities and orientations in the manifest.
5. Assemble and bench-test the external controller, power cutoff, watchdog and E-stop. Measure electrical levels and firmware units before changing HARDWARE_VERIFIED. Preserve the disabled build.
6. Print markers at actual scale and create a measured calibration plus validation report. Use adequate resolution for 20 mm tags; sheet detection does not establish arena-distance detection.
7. With Leo present, run physical fault tests and short actions. Reboot stays disarmed. Use a new state directory after changing geometry, firmware, wheel settings or calibration to avoid silently mixing configurations.
8. Prove automatic resets, tether management and endurance before 72-hour or week-long research. Follow the gates rather than treating simulation as completion.

## Commands

```bash
python -m pip install -e .
python scripts/verify.py
python -m playground demo --state-dir state/demo
python -m playground serve --state-dir state/demo
python -m playground run --config configs/research.json
python -m playground run --config configs/research-week.json
python scripts/package.py
```

Physical operation uses the explicit hardware CLI only after bench acceptance. It requires a validated calibration companion report, serial port, human terminal confirmation and MCU physical authorization. See docs/robot-control.md and docs/acceptance.md.

Do not ship .build-venv, .pio-core, nested .pio, hardware/cad/_deps, state, CAD backups, preview tessellation intermediates or caches. The packager excludes them. Keep source, native/STEP/print assets, compiled disabled firmware and verification reports.
