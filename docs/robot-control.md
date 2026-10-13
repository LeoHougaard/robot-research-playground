# Robot control

The Python simulation and ESP32 firmware build without physical devices. The
physical backend starts disarmed. Firmware ships with `HARDWARE_VERIFIED=0`, which
prevents servo power and arming. Compilation is evidence about the software build;
electrical behavior, motor direction, feedback units and camera accuracy still
need bench tests.

## Control contract

`RobotController` owns a single short lease and rejects missing safety data.
`acquire(owner, ttl_s)` returns a secret `lease_id`. A trusted supervisor can call
`renew(lease_id, ttl_s)` to extend it, up to 30 seconds per renewal. Renewal does
not arm the robot. Workers receive bounded actions and observations through the
supervisor. They do not receive the operator arm API or the MCU serial device.

An operator calls `arm(lease_id, human_confirmed=True)`. On hardware, the MCU also
requires a fresh physical button press. Faults and stops require another operator
arm. A heartbeat never rearms. Simulation can use this same sequence with a fake
clock and a built-in demo's simulated operator confirmation.

`action(lease_id, left, right, duration_s, gripper=None)` is nonblocking. `left`
and `right` range from -1 to 1 and map to at most 0.1 metres per second. `gripper`
is between zero, open, and one, closed. The action lasts at most 0.5 seconds. Send
`heartbeat(lease_id)` at least every 0.2 seconds, then call `observe()` after the
duration to measure the result. The supervisor keeps heartbeats running while
an agent thinks or launches a training job, with stationary wheels after action
expiry. Call `stop(reason)` for an immediate disarm and `release(lease_id)` to
stop and relinquish control.

Observations use metres and radians. Timestamps use the host's monotonic clock.
The top-level fields are `state`, `reason`, `real`, `limits`, `lease`, `robot`,
`objects`, `telemetry` and `tracking`. The observation hides the secret lease ID.

```json
{
  "robot": {"x": 0.5, "y": 0.5, "heading": 0.0},
  "objects": [{"id": "block", "x": 0.65, "y": 0.5, "held": false}],
  "tracking": {
    "timestamp": 100.0,
    "valid": true,
    "in_bounds": true,
    "pose": {"x": 0.5, "y": 0.5, "theta": 0.0}
  },
  "telemetry": {
    "timestamp": 100.0,
    "ready": true,
    "armed": true,
    "temperatures_c": [25.0, 25.0, 25.0],
    "currents_a": [0.1, 0.1, 0.1],
    "voltage_v": 12.0
  }
}
```

Physical object IDs are marker numbers as strings; `held` is unknown until a
grasp sensor or visual test establishes it. A missing robot marker yields
`robot=null` and invalid tracking. Simulation adds `scene` for rendering. Its
object grasp is an ideal latch with no friction, slip or occlusion model.

## Host and independent cutoffs

| Condition | Default response |
| --- | --- |
| Invalid, nonfinite or oversized action | Disarm and stop |
| Host heartbeat age reaches 400 ms | MCU cuts servo power |
| Action reaches its duration | Zero wheel speed, gripper torque off on MCU |
| Main loop fails to acknowledge action stop within 25 ms | MCU safety task cuts power |
| Camera age exceeds 300 ms or marker disappears | Host disarms and stops sending healthy heartbeats |
| MCU servo feedback age reaches 250 ms | MCU cuts power |
| Host telemetry age exceeds 300 ms | Host disarms |
| Any servo temperature above 55 C or current above 1.5 A | MCU and host disarm |
| Any servo voltage outside 9.0 to 12.6 V | MCU and host disarm |
| Robot footprint leaves calibrated arena | Host disarms |
| Lease expires | Host stops and removes lease |
| MCU reset, open E-stop or authorization interlock | Motor enable low |
| Whole MCU freezes | External hardware watchdog must remove switch enable |

The MCU safety task runs separately from JSON parsing and servo reads. It toggles
GPIO32 every 20 ms while power is authorized. An external watchdog with a timeout
of at most 100 ms must gate the high-side switch. GPIO25 alone does not protect
against a completely frozen MCU. The latching physical E-stop must directly
interrupt that gate or motor power, as well as signal the MCU. Exact watchdog and
power-switch parts remain a hardware selection and bench-verification task.

## Wiring candidate

Use an ESP32-WROOM32 DevKit, the Waveshare Bus Servo Adapter A and a separate
current-limited 12.0 V servo supply. The adapter documentation says its input
must match the servo voltage and gives a 9 to 12.6 V range for ST servos.
[Waveshare adapter description](https://docs.waveshare.com/Bus_Servo_Adapter_A).

The ST3020 is a TTL serial servo with continuous rotation support and current,
temperature and voltage feedback. Its servo voltage rating is broader than the
adapter's chosen operating range.
[Waveshare ST3020](https://www.waveshare.com/wiki/ST3020_Servo).

| ESP32 pin | Connection |
| --- | --- |
| USB Serial | Spark trusted gateway, 115200 baud |
| GPIO16 RX, GPIO17 TX | Adapter UART header, 1 Mbps |
| GND | Shared signal ground, keep motor return current out of USB ground path |
| GPIO25 | Active-high external motor switch enable, with physical pulldown |
| GPIO32 | External watchdog retrigger input, toggles every 20 ms |
| GPIO26 | Momentary physical arm button to GND, input pullup |
| GPIO27 | Normally closed E-stop sense to GND; open means stop |
| GPIO33 | Physical hardware authorization interlock to GND; open means stop |

The adapter's UART instructions explicitly call for RX-to-RX and TX-to-TX using
its printed labels, with jumper A. Follow the board revision's wiring diagram.
Do not infer ordinary TX-to-RX wiring from the labels.
[Waveshare UART wiring](https://docs.waveshare.com/Bus_Servo_Adapter_A/Product-Wiring-Example).
The documentation checked here does not establish safe 3.3 V input/output levels
for every adapter revision. Measure them, obtain the revision's schematic or add
a suitable level translator before connecting ESP32 pins. Do not connect the
servo's single-wire TTL bus directly to the ESP32's separate RX/TX pins.

Servo IDs are one, left drive; two, right drive; three, gripper. Configure the
drive servos in speed-controlled wheel mode, register 33 value one, and gripper
in position mode, value zero. Firmware reads these modes and rejects mismatches.
It never rewrites persistent servo settings during startup.

The raw UART packets use the ST-series register layout and sign-magnitude speed
encoding from the vendor's implementation.
[Waveshare SCServo source](https://github.com/waveshare/Servo-Driver-with-ESP32/tree/main/SCServo).
The current conversion of 6.5 mA per count, 4096 steps per revolution, mirrored
wheel signs and gripper endpoints of 1800 and 2200 are explicit candidates.
Check them on the actual ST3020 units before changing `HARDWARE_VERIFIED`.

## Firmware build and protocol

Install PlatformIO in an isolated virtual environment. Build only, with no
physical board connected or upload command.

```text
python -m pip install platformio
python -m platformio run --project-dir firmware/robot_controller --jobs 2
```

The target is `esp32dev`, Espressif platform 6.12.0, Arduino framework and
ArduinoJson 6.21.5. Set `PLATFORMIO_CORE_DIR` to a workspace cache when you want
all downloaded tools isolated. A deeply nested Windows workspace can exceed the
GCC child-process path limit; use the workspace's existing 8.3 short path or a
short checkout. No Windows setting change is needed.

The checked disabled-gate build is included in `firmware/robot_controller/dist`
with its bootloader, partition table and source/binary hashes in
`build-evidence.json`. Its firmware uses 22,376 bytes of RAM and 279,589 bytes of
flash. These binaries cannot arm motors because the verification gate is zero.

USB messages are bounded JSON lines, maximum 768 bytes, with a positive ascending
`seq` and `op`. Replies echo `seq` and return `ok`, or `error`. Operations are
`status`, `arm`, `heartbeat`, `action` and `stop`. There is no raw servo passthrough.

```json
{"seq":1,"op":"status"}
{"seq":2,"op":"heartbeat","tracking_ok":true}
{"seq":3,"op":"arm"}
{"seq":4,"op":"action","left":0.2,"right":0.2,"duration_ms":200,"gripper":null}
{"seq":5,"op":"stop"}
```

Unhealthy tracking in a heartbeat, malformed commands, stale sequence numbers,
missing servo acknowledgements and failed reads cut power. `status` reports
feedback age in MCU milliseconds; the host converts it into a conservative
monotonic timestamp. A new host process must restart/reset a stopped MCU session
before using sequence numbers from one again, then obtain another physical arm.

After startup or any cutoff, the MCU must observe the arm button released before
accepting a fresh press. Holding it during reboot cannot authorize bus power.

The physical controller also requires `reset_tether(human_confirmed=True)` while
disarmed, after the operator inspects and unwinds the cable. The default turn
budget is a provisional pi radians, measured by unwrapping camera headings.
The next action is checked against maximum wheel speed, both wheel magnitudes,
duration and a 0.1-radian margin. Tracking gaps or implausible heading jumps latch
a stop requiring inspection/reset. Measure the 154 mm wheel track, actual speed
bound, cable allowance and tracking error before relying on these candidates.
The guard cannot detect wraps around arena fixtures or certify unattended use.

With verified hardware and authorization interlock closed, a fresh physical arm
press energizes the bus for a five-second preparation window. The MCU immediately
requests torque off and reads all servo feedback. The operator then issues host
arm while those readings are healthy. This initial power-up is a physical action;
verify that stored servo goals cannot produce a startup movement before relying
on the torque-off packet. Use wheels lifted during that check.

## Camera calibration

Install the optional hardware dependencies. `CameraTracker` opens the camera and
captures silently in a background thread; it has no window or sound. Use
`CameraTracker.observe` as the `SerialBackend` observation callback. The tracker
uses OpenCV ArUco `DICT_4X4_50`; robot marker ID defaults to zero.
[OpenCV marker detection](https://docs.opencv.org/4.12.0/d5/dae/tutorial_aruco_detection.html).

Record four measured pixel-to-world correspondences. `calibrate` fits a
homography; `Calibration.load(path)` loads a JSON matrix and arena dimensions.
Measure the robot marker's offset from the drive axle. Align its corner-zero to corner-one edge
with robot forward, or measure and set `heading_offset`. Correct lens distortion
before fitting if it changes accuracy across the arena. Calibrate at marker
height or account for parallax; a floor calibration alone is insufficient for a
marker mounted on the top shell. `marker_offset_x_m` and `marker_offset_y_m` locate
the tag centre relative to the axle in robot-local coordinates, forward x and
left y. The tracker subtracts that rotated offset so all robot observations refer
to the drive axle, matching simulation. Every pose test must include the 0.16 m default
robot clearance from the arena boundary.

`snapshot_jpeg(max_width=640)` returns the last captured frame without refreshing
its timestamp. `snapshot(max_width=640)` returns a JPEG and the observation from
that same frame as a pair for image archives. Camera-driver time and marker detection time count toward frame
age. A blocked camera thread leaves old timestamps, so it cannot keep the robot
healthy by returning stale coordinates.

## Verification before physical use

The software tests exercise lease ownership and renewal, explicit arm, action
deadlines, independent simulated heartbeat cutoff, finite inputs, missing/current/
temperature/voltage safety data, stale/future tracking, clearance, serial framing,
SDK authentication and the operator route boundary. Deterministic tests also
check straight motion, exact arcs, turns, simulated grasp/release and homography
errors. The ESP32 release firmware compiles to a binary. None of these tests prove
physical safety or camera accuracy.

Before enabling hardware, verify UART logic voltage and direction; servo IDs,
modes, step/current units and motor signs; gripper endpoints under load; startup
behavior; switch pulldown; hardwired E-stop; external watchdog loss of pulses;
motor cutoff after unplugging USB, killing the host, blocking the camera and
stalling MCU main-loop work; current and temperature faults; clean power-cycle
startup; camera mounting and clearance at several arena positions; and deliberate
operator rearming after a fault. Test without attached mechanisms first, then
with wheels lifted and a current-limited supply. Record the measured limits and
hardware revision before changing the compile-time verification gate.

## Optional loopback gateway

`python -m playground.robot_server` starts the simulation gateway on
`127.0.0.1:8766` and prints separate short-lived worker and operator tokens.
`RobotClient` uses only the worker token and has no arm or renewal method. The
operator-only routes are `/robot/arm` and `/robot/renew`. The research supervisor
normally calls `RobotController` directly and mediates every worker tool call.
Keep the operator token and MCU device outside disposable workers.

## Operator camera setup

`scripts/camera_setup.py` never opens a servo port or arms a robot. Install the
hardware extra, then capture a still with the camera fixed in its final overhead
mount. Keep the motor supply disconnected during setup.

```text
python -m pip install -e ".[hardware]"
python scripts/camera_setup.py capture --camera 0 --output setup/overhead.png
```

The command prints the image dimensions, timestamp and SHA256. No physical
camera was available for this project, so camera-driver compatibility and image
quality remain to be checked on the chosen USB camera.

Print `hardware/markers/aruco-markers-a4-300dpi.png` at 100% actual size with
fit-to-page disabled. It embeds 300 DPI in the PNG and contains a 100 mm ruler.
Measure between the end ticks before mounting markers. ID zero is the robot;
IDs one to five identify objects. Each marker's black outer square is nominally
20 mm, with a 4 mm white quiet border. Rounding at 300 DPI makes the generated
square 19.981 mm and the full tag footprint 27.94 mm. This fits the mechanical
reference's 28 mm tag plates. Individual marker PNGs and exact dimensions are in the same
directory. The sheet was visually inspected and all six IDs were detected by
OpenCV. Regenerate it with this command when needed.

```text
python scripts/camera_setup.py markers --output-dir hardware/markers --overwrite
```

Copy `configs/calibration-points.example.json` to a measurements file. The
example is deliberately unusable. It has placeholder coordinates, missing
measurements and `measurement_confirmed=false`; the setup script refuses to
turn it into runtime calibration.

Measure four matching pixel and world points in the fixed camera image. Use the
same horizontal plane as the robot's top marker, record its measured height
above the floor, and check the mapping throughout the arena. Image coordinates
are pixels; world coordinates are metres. Then measure at least three distinct
held-out points spanning the arena. None may reuse a fit point, and they must not
be collinear. These measurements test the fitted mapping, so they cannot be
computed from that mapping. Attach the source image filename, its printed SHA256
and the actual measurement timestamp with timezone.

Measure the marker's mounting heading offset in radians. Its corner-zero to
corner-one edge is forward at offset zero. Record `robot_tag_offset_x_m` and
`robot_tag_offset_y_m` relative to the drive axle, forward x and left y. The
mechanical reference places its robot tag 45 mm behind the axle at 74 mm above
the floor. The object marker plates share that 74 mm plane. Confirm the completed
assembly's values and keep all tracked tags on that plane; a raised, tilted or
displaced tag needs a different calibrated mapping or a 3D camera model. Set arena
dimensions and a conservative robot clearance radius from the completed build.
Only after recording the actual measurements set `placeholder=false` and
`measurement_confirmed=true`.

```text
python scripts/camera_setup.py calibrate --points setup/measured-points.json --output setup/calibration.json --max-error-mm 5
```

The script checks the source image hash and every coordinate, fits the four
points, and measures maximum error on the separate validation points. It saves
nothing when the error exceeds 5 mm. A tighter gate is accepted; a larger one is
refused. Existing calibration is preserved unless `--overwrite` is supplied.
The runtime JSON contains exactly the `Calibration` fields. A companion
`calibration.validation.json` records measured and predicted held-out positions,
errors, marker height and hashes of the source image, measurements and runtime
calibration. The hardware launcher should check the passed validation report and
runtime hash before accepting that file. Pass the report's `source_image_size_px`
to `CameraTracker(expected_frame_size=(width, height))`. A changed capture
resolution then produces invalid tracking and blocks motion.

Keep the camera and marker mounting fixed after calibration. Recalibrate after
moving either, changing capture resolution or correcting lens distortion. A
5 mm positional gate does not independently validate heading, marker-centre
placement, parallax at a different height or real stopping clearance. Check these
on the actual stationary robot before operator arming.
