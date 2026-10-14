# Physical commissioning and trusted task-reset contract

Status: **planning only; every physical gate is unqualified**. The robot is
unbuilt. This document, its example JSON, and the offline validator add no
actuation, reset, automatic rearm or autonomous untwisting capability. They do
not change `HARDWARE_VERIFIED=0`, and cannot authorize changing that flag.

Use [acceptance](acceptance.md), [physical review](verification-physical.md),
[control](robot-control.md), [cutoff circuit](watchdog-circuit.md) and
[electronics](electronics.md) together. This procedure refines their evidence
requirements; it does not replace them. Spark admission, real-container and
real-model qualification remain separate prerequisites for research.

## 1. Freeze the specimen and retain evidence

Create a separate commissioning bundle and a new attempt ID before each test.
Keep the original example unchanged. Copy `configs/commissioning-evidence.example.json`
into the bundle and fill only fields supported by actual observations. Do not
turn a design value or simulator result into a measured result.

- **Planned:** intended method, candidate limit or future test. Provides no
  qualification evidence. The example contains only planned work.
- **Observed:** an operator-observed fact, with time, identity, method and raw
  evidence. A power LED or acknowledged stop is not measured motor cessation.
- **Measured:** raw readings with units, calibrated instrument identity,
  uncertainty, conditions, method and timing. Preserve failed readings too.
- **Reviewed gate:** a named reviewer interprets the measurements against the
  predeclared limits. A JSON status or hash is not independent proof of truth.

Bind each bundle to the run's immutable `provenance.json`: retain its exact bytes,
SHA256 and configuration fingerprint. The trusted runtime builds the fingerprint
from effective configuration, actual backend and limits, runtime source hashes,
geometry and byte-hashed inputs. Supply real files using `evidence_inputs`; a
user-written hash alone is insufficient. Retain copies of the exact `geometry`,
`firmware`, `calibration` and `calibration_validation` inputs and verify their
hashes and byte counts against the manifest. Include the task contract and any
future reviewed reset routine as additional inputs before creating its run.
Do not use a simulation manifest as physical provenance.

Also preserve measured hardware identity: servo IDs/revisions, adapter revision,
relay/timer/component lot, wiring revision, printed-material/part measurements,
camera serial/backend/driver/settings, instrument IDs/calibration dates, supply
limit/fuse, firmware build evidence, board identity and verified flash/readback
procedure. A firmware-file hash is **not** an attestation of what is flashed.
The current serial protocol does not supply that attestation.

Give every record an actual timezone-qualified timestamp and raw artifact hash.
Record monotonic clock identity, exposure-clock conversion and uncertainty when
comparing latencies; wall time alone is unsuitable for subsecond latency checks.
Seal completed bundles read-only using the trusted operator/evidence path and
keep an independently checked backup. Workers must not write them. Hash checking
detects changes relative to a retained manifest, but is not a digital signature,
write protection or proof that a measurement occurred. Preserve originals and
create a new attempt after any geometry, firmware, calibration, safety-limit,
camera, reset implementation or hardware change; do not silently mix runs.

The stdlib checker is read-only and opens no camera, serial device or network:

```text
python scripts/validate_commissioning.py configs/commissioning-evidence.example.json
python scripts/validate_commissioning.py /path/to/bundle/evidence.json --require-stage camera_freshness
```

The first command checks a planning document and reports it unqualified. The
second requires complete measured records, matching local input/artifact bytes,
and reviewed prerequisites through that stage. File paths are relative to the
bundle directory and may not escape it or traverse symlinks. Success means only
that the **record is internally consistent for review**, never permission to arm
or proof of safety. The checker cannot inspect the hardware, authenticate an
operator, attest a flashed binary or independently assess trace interpretation.

### Record fields

Artifact references have exactly `path` (bundle-relative), `sha256` (lowercase
SHA256 of actual bytes) and `bytes`. A non-template bundle needs actual
`attempt_id`, `recorded_at`, `operator` and all provenance references.

Each `instruments` entry has a unique `id`, `description`, `calibrated_at`,
`uncertainty` (method and units) and an `artifact` retaining its calibration
record. Each `records` entry has a unique `id`, `kind` (`observed` or `measured`),
`recorded_at`, `operator`, Boolean `operator_present`, `method`, `instrument_ids`,
`values` (readings, units, conditions and uncertainty) and raw `artifact`. Measured
records require an instrument reference. Plans stay in `planned_measurements`
and cannot satisfy a gate. A passed bench gate needs operator-present measured
evidence plus its `reviewer`, `reviewed_at` and `record_ids`; all earlier gates
must already have reviewed passing records. Actual document, calibration,
observation, measurement, review and trial timestamps may not be in the future
relative to the validator's trusted wall clock. Calibration must precede its
linked measurements; reviews must follow linked observations/measurements and
any endurance attempt's completion. Each endurance attempt must begin after its
prerequisite review. Fix an unreliable clock and preserve the original records
rather than inventing dates to satisfy these checks.

When implemented in a future change, `start_state_contract` and `reset_routine`
need `implementation="implemented"` and their actual `artifact`; the same bytes
must be inputs with those exact labels in the runtime manifest. The example
correctly says `not_implemented`. Neither contract authorizes rearm or unwinding.

Each `endurance_attempts` entry has `stage` (for example `endurance_15m`), a unique
`attempt_id`, `status`, matching manifest `fingerprint`, `started_at`, `ended_at`,
`continuous_elapsed_s`, `manual_rescues`, `disarms`, `automatic_rearms`, `restarts`,
`observation_gaps` and `record_ids`. A passing record requires sufficient
continuous duration inside the retained wall-clock interval, zero for every
intervention/gap count, gate-linked measured records and a passed reviewed stage.
Document any clock discontinuity as a failed/incomplete attempt. Do not copy the
counts from a different attempt or interpret missing values as zero.

## 2. Operator-present bench gates

Leo and a competent operator must be physically present for bench acceptance,
with an accessible hardwired E-stop. Stop on any unexpected movement, current,
heating, missing evidence or failed cutoff. Keep the motor supply disconnected
for mechanical inspection and camera setup. Test logic and a modest dummy load
before connecting mechanisms, then one servo with wheels raised, then the full
load. Any powered commissioning requires a separately reviewed physical test
setup; this software preparation supplies no enabled firmware or test driver.
Preserve and test the shipped disabled binary's refusal to arm before considering
a separately reviewed future firmware build.

1. **Mechanical fit.** Measure coupons, servo/horn and screw engagement, wheel
   track/radius, caster clearance, gripper travel, shaft loads, clamps, PETG
   creep and fixture retention. Check swept robot/object clearance, tether slack
   and overhead-mount deflection. Revalidate the camera after support loading.
2. **Electrical cutoff.** Verify UART voltage and board-specific labeling,
   reference/return wiring, default-off switch, no USB/UART backfeed, E-stop's
   independent coil contact, authorization interlock and startup sequencing.
   Follow every fault and startup combination in the cutoff circuit document.
   Retain synchronized GPIO32/GPIO25/Q/coil/switched-rail traces and independent
   motion evidence. Q expiry must be 50–70 ms after the last rising edge; contact
   disconnection and actual motor cessation must each be within 100 ms of that
   edge under tested load/temperature conditions. Log rail decay, bounce and
   uncertainty. This is the external pulse-loss test, **not a blanket 100 ms
   guarantee for host/camera failures**; measure those complete paths separately.
3. **Servo motion.** Measure register conversions (6.5 mA/count and 4096 steps/rev
   are candidates), directions, wheel speed bound, gripper endpoints, startup
   stored-goal behavior, real stop distance, current and temperature under load.
   Verify torque off after each action. The ideal simulator latch does not prove
   a real grasp persists. Reject a task relying on an unobserved held state.
4. **Camera calibration.** Retain the actual camera image, independently
   measured fit/held-out points, marker-plane height/offsets, resolution,
   calibration and companion validation report. Maximum independent position
   error must be at most 5 mm or the stricter chosen gate. Check heading,
   parallax, occlusion and clearance separately; positional fit alone is not
   sufficient. Use [camera setup](../scripts/camera_setup.py) for file checks.
5. **Camera exposure freshness.** Use a changing physical reference in the
   observed scene and independently time the reference transition/exposure,
   acquisition completion and controller decision. Retain video/raw frames,
   sequence/exposure metadata and external timing traces. Test driver buffering,
   process stalls, disconnect/reconnect, covered markers, repeated/frozen frames
   and prerecorded/stale frames even with changing overlays/noise. Bound worst
   exposure-to-decision age plus uncertainty within the selected tracking budget
   (currently at most 300 ms), and measure fault-to-power-off/motion-cessation.
   Current `CameraTracker` records read timing, not proven physical exposure
   time; a successful read or requested buffer size of one cannot pass this gate.
   An actual source-freshness implementation and fault tests are required before
   unattended use. Do not infer freshness from nonidentical pixels alone.

**Electrical load boundary:** the entire 12 V supply remains limited to 2 A,
with a 2 A motor-branch fuse. The per-servo 1.5 A software threshold is not an
aggregate-capacity guarantee; three stalled servos can exceed this prototype's
capacity. Do not raise the limit or fuse rating to make a trial pass. A change
requires a reviewed wiring/cutoff design and new measured evidence. The single
unmonitored relay channel can fail welded or bypassed. Decide and document
whether monitored/redundant cutoff is needed for the intended unattended setting;
software acceptance cannot waive that hardware limitation.

## 3. Trusted reset and starting-state contract (not implemented)

A **task reset** restores a declared robot/object/fixture state using a reviewed
bounded routine. It is separate from `reset_tether(human_confirmed=True)`, which
requires a disarmed robot and an operator's physical cable inspection/unwind.
No task reset may reset the turn budget, clear a latched fault or impersonate an
operator. Cable wrapping around a fixture may happen without accumulated yaw;
the provisional pi-radian guard is not an autonomous untwisting solution.

The future trusted supervisor, never generated worker code, owns these checks.
A worker may propose a named task and bounded parameters; it cannot provide the
truth of the start-state checks or run a custom reset program on hardware.
The task contract and reset routine must be versioned and hashed into provenance.
Initial task scope should be empty-arena short movements. Object dragging,
captive slider and gate tasks stay ineligible until their own resets qualify.

### Eligibility inputs and decision

A trusted `check_start_state(task_id, attempt_id)` should consume one coherent,
trusted observation window and return only `eligible` or `ineligible` plus
reason codes, the manifest fingerprint, task/routine hashes, observation IDs and
expiry. This is a specification, not an implemented API or reusable arm token.
Unknown, missing, nonfinite, future, stale or conflicting data means ineligible.

The checked contract must include:

- Measured robot start-pose bounds and heading tolerance, footprint/swept-path
  clearance, permitted object IDs with individual pose/orientation tolerances,
  and explicit slider/gate endpoint states. Match the complete expected set;
  missing, extra, duplicated, occluded or ambiguous objects fail closed.
- Fresh source-exposure and telemetry times in a common verified clock domain,
  uncertainty included in age; acceptable frame sequence, camera resolution,
  calibration/marker-plane identity and mounting. A host read timestamp alone
  cannot satisfy this input. Recheck before every motion, not once at startup.
- A measured settling window with bounded translation/rotation and no unknown
  contact or grip state. The gripper must be in a sensed/visually verified state
  that remains valid with torque removed. No success inferred from commands.
- Healthy supply/current/temperature/feedback, lease ownership and remaining
  action/time budget, unchanged provenance, no pending stop or unresolved trial,
  and a known safe controller state. The disarmed preflight may request operator
  arming; ordinary between-trial resets retain only an already-valid arm/lease.
  Any disarm/fault requires fresh operator authorization, never automatic rearm.
- A current operator-established cable baseline, continuous trusted yaw history,
  measured clearance/slack and margin for the full reset plus next action.
  Unobserved wrapping, ambiguous yaw, a tracking gap or exhausted margin requires
  a latched stop and operator inspection. Do not zero the budget between trials.

Eligibility expires on any new action, timeout, observation gap, scene change,
process restart, provenance drift or loss of health. It is not permission to
bypass controller, MCU or independent cutoff checks. Pin the exact observed
sequence immediately before the trial and reject a changed/stale snapshot.

### Bounded reset state machine

`CHECK -> RESETTING -> SETTLE -> CHECK_POSTCONDITIONS -> READY` is the proposed
normal path. A reset begins only with qualified preconditions, an existing
operator-authorized session and remaining bounds for distance, duration, retries,
energy/current, thermal headroom and cable turn. Do not implement retries until
these bounds are physically measured. Every command uses the existing trusted
bounded controller; workers never get serial, arm, fault-clear or limit APIs.

A failed check, timeout, lost grip, unobservable state, contact uncertainty,
tracking/telemetry failure or exceeded bound takes `STOPPED_NEEDS_OPERATOR`.
Stop and disarm; mark the trial interrupted/invalid and retain all evidence.
Never retry blindly, move to search for missing markers, clear a fault or unwind
a tether autonomously. The operator's rescue and fresh authorization begin a
new attempt, with the old failure preserved.

A reset counts as successful only when independent observations satisfy the
same start-state contract for the entire settling window and a fresh check
passes before the next trial. Qualify adversarial cases: shifted/stuck objects,
wrong marker/height, gripper slip, failed release, half-open gate, fixture contact,
stale exposure, cable near limit, restart midway, repeat-call/replay and exhausted
budget. Store every attempt and reason; simulation demonstrations are useful
software fixtures but cannot qualify this physical contract.

## 4. Rescue-free endurance and release decision

After supervised bounded actions and the task-specific reset gates pass, advance
in order through **15 minutes, 1 hour, 24 hours, 72 hours, then 1 week**. Each is a
separate complete attempt with an immutable manifest and continuous raw logs.
The early bench stages are operator-present; longer trials require a separately
reviewed containment, cutoff, monitoring and operator-response plan before they
start. This plan does not authorize leaving the current prototype unattended.

Predeclare task mix, cycle/reset success criteria, ambient/load range, maximum
observability gaps and evidence frequency. Log temperatures, total/per-servo
current, voltage, missed feedback, exposure age/uncertainty, decision latency,
reset success/failure, cable yaw/slack/wrap observations, grip/release failures,
free storage, faults, interventions and real continuous elapsed time. Preserve
exposure-freshness coverage throughout, not just a calibration clip at startup.

Any manual reset, reposition, cable unwind, rescue, human rearm, automatic rearm,
power-cycle/restart, missing raw interval or unresolved fault invalidates that
attempt for unattended acceptance. Keep its measured duration and failure; do
not subtract intervention time, join partial runs or silently restart its clock.
An operator can stop and make the setup safe, but that does not convert the run
to a pass. Remedy the cause and restart the affected stage from zero; changes to
hardware/software/configuration also invalidate affected prerequisite evidence.

A passed earlier stage never implies a later one. A one-week simulator run or
accelerated stress test is not a physical week. The example's null durations and
rescue counts mean unknown, never zero. Review raw evidence, provenance and the
actual hardware with Leo before a separate physical release decision. Until all
relevant measured gates and reset/source-freshness implementations exist,
unattended physical research remains unqualified.
