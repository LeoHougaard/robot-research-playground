# Physical/control review

Reviewed the controller, serial protocol, firmware, camera tracker, simulation,
mechanical/accessory interfaces and electrical cutoff netlist. The robot does not
exist yet. No actuation, upload, hardware authorization or physical qualification
occurred. CAD reports establish closed solids and nominal interfaces only.

## Software defects corrected

- High severity: modulo heading and individually bounded commands allowed
  unlimited cumulative cable rotation. Physical controllers now require an
  operator tether inspection/reset while disarmed before the first arm. They
  unwrap measured camera yaw, stop before a provisional pi-radian turn budget,
  and check the next action using both wheel magnitudes, maximum wheel speed and
  duration. A tracking gap over 300 ms, backwards timestamp, changed heading at
  the same timestamp, or a turn inconsistent with the speed/track bound latches
  an operator-reset requirement. Failed reset does not permit arming. Workers
  receive no tether-reset operation. Simulation may opt into the same guard.
- High severity: firmware counted an arm button already held at startup as a
  fresh authorization. Release must now be observed before a press after startup
  or cutoff. Cutoff also clears the unconsumed physical authorization. The build
  flag remains HARDWARE_VERIFIED=0.

Focused verification ran 21 controller/serial/gateway tests with no failures,
including modulo crossing, repeated bounded turns, tracking ambiguity,
predictive rejection and disarmed-only operator reset. These are software tests.
The disabled ESP32 firmware compiled and linked using the pinned Xtensa toolchain
after switching to the existing short Windows workspace path. It uses 22,376
bytes RAM and 279,589 bytes flash. Updated binaries and source/binary hashes are
in firmware/robot_controller/dist. The firmware release-before-press behavior
still needs the physical bench tests.

## Unmet gates for unattended physical weeks

| Severity | Evidence and consequence | Minimum next work |
| --- | --- | --- |
| Blocking | No physical task reset or checked puck/gate starting-state routine exists. Firmware disables gripper torque after every action; the simulator's persistent ideal latch cannot prove that physical tasks remain usable. | Design and demonstrate trusted reset/postcondition routines. Log rescues and reject unattended qualification if any manual reset is needed. |
| Blocking | Tether budget uses measured camera yaw but cannot observe wraps around fixtures, mast routing, vertical pull or startup twist. The pi-radian budget, 0.1-radian tracking margin, 154 mm track and speed bound are software candidates. | Measure wheel radius/track and true maximum speed. Prove cable pull, slack, direction signs, allowable twist and untwisting procedure with Leo present. Stop for operator unwind; do not guess an autonomous recovery. |
| High | CameraTracker timestamps each successful read, not the physical exposure. A driver returning old or frozen frames can produce fresh valid tracking. Setting OpenCV buffer size to one does not certify capture freshness. | Qualify the exact camera/backend with a changing physical reference and retained capture timestamps. Add source freshness checks before unattended use. Identical-frame checks alone cannot detect stale content with changing overlays/noise. |
| Blocking | Servo register conversion, startup stored goals, mirrored directions, jaw endpoints and continuous wheel/shaft duty remain unmeasured. Acknowledged writes do not prove motion cessation. | Raised-wheel commissioning, instrumented current/voltage/temperature checks, actual stopping measurements and measured endpoint calibration. Preserve raw evidence and the disabled binary. |
| Blocking | The relay/timer circuit is one unmonitored channel. The netlist is consistent with the described Q output, reset/AND gating, separate E-stop coil contact, diode and default-off wiring. Welded contacts or a bypass can defeat cutoff. | Scope the entire cutoff chain under actual load and temperature, including rail decay and motor cessation. Stay within the 2 A total prototype load boundary. Decide whether a monitored/redundant cutoff is necessary for the intended unattended environment. |
| Blocking | Native/STEP/STL geometry cannot establish horn screw engagement, servo radial-load rating, PETG creep, tether/clamp retention, E-stop reach or camera mount stability. | Print fit coupons, measure purchased interfaces and physically inspect complete assembly. Validate calibration again after support loading or any bump. |

The existing independent watchdog can stop a frozen MCU only after its circuit,
relay release and actual motor cessation pass the documented measurement gates.
Host tracking loss also includes detection delay, host serial-stop latency or the
400 ms MCU heartbeat fallback. The external 100 ms pulse-loss limit is not a
100 ms guarantee for every camera or host failure.

Official source review used [OpenCV VideoCapture](https://docs.opencv.org/4.13.0/d8/dfe/classcv_1_1VideoCapture.html)
and [TI CD74HC123](https://www.ti.com/lit/ds/symlink/cd74hc123.pdf).
OpenCV documents backend-dependent properties and warns that property acceptance
does not prove device acceptance. TI specifies timer logic and timing behavior;
the project's 3.3 V timeout estimate still requires the recorded scope tests.

Firmware, physical fit, calibration, cutoff, endurance and unattended operation
remain unverified. Advance through docs/acceptance.md with Leo present. Short
supervised trials, 15 minutes, one hour, 24 hours, 72 hours and a week remain
distinct gates. No software result in this review passes a physical gate.
