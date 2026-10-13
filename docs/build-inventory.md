# Physical build inventory

The package supplies 40 printable designs: 27 robot/arena designs and 13 offboard accessory designs. Every design has native FreeCAD, analytic STEP, and STL files. The default manifests total 97 printed pieces, including fit coupons, optional shims, an optional E-stop ring, and an alternative overhead tie plate. That is 95 PETG pieces and two TPU tire rings. It is not a count of everything to buy.

There are six assembled CAD layouts, with matching native and STEP files. The robot/arena layout contains 74 printed instances and four purchased-component references. The five accessory layouts contain 15 printed instances. Purchased electronics, fasteners, camera, support frame, wiring, and floor materials still have to be obtained and assembled. Nothing has been physically built or tested.

## CAD and build entry points

- Open [the robot/arena FreeCAD assembly](../hardware/native/robot/robot_playground_assembly.FCStd) for the chassis, wheels, gripper, objects, gate, frame, and calibration mounts. For Onshape, import [its STEP assembly](../hardware/step/robot/robot_playground_assembly.step).
- Open the accessory assemblies for [electronics](../hardware/native/accessories/electronics_assembly.FCStd), [camera](../hardware/native/accessories/camera_assembly.FCStd), [tether](../hardware/native/accessories/tether_assembly.FCStd), [cable clamp](../hardware/native/accessories/cable_assembly.FCStd), and [operator panel](../hardware/native/accessories/operator_panel_assembly.FCStd). Matching STEP files have the same basenames under [the accessory STEP directory](../hardware/step/accessories/).
- Use [the editing/import guide](onshape.md), [robot assembly instructions](../hardware/mechanical.md), and [accessory assembly instructions](accessories.md). FreeCAD retains native feature histories. STEP carries solid geometry for Onshape direct editing, rather than the original history.
- Build the offboard cutoff from [the circuit guide](watchdog-circuit.md), [schematic](../hardware/electronics/watchdog-circuit.svg), [pin-by-pin netlist](../hardware/electronics/netlist.json), and [exact electrical BOM](../hardware/electronics/bill-of-materials.csv).
- Follow [robot control and commissioning](robot-control.md) and [acceptance gates](acceptance.md). Firmware ships with `HARDWARE_VERIFIED=0`; compiled binaries do not prove electrical or mechanical performance.

For each design below, its ID is the shared filename stem. Robot files are under `hardware/native/robot`, `hardware/step/robot`, and `hardware/prints`. Accessory files are under the corresponding `accessories` subdirectories. Use `.FCStd` to edit, `.step` to exchange solid CAD, and `.stl` to slice. Quantity and orientation sources are [the robot manifest](../hardware/prints/print_manifest.csv) and [the accessory manifest](../hardware/prints/accessories/print_manifest.csv).

## All 27 robot and arena designs

| Design ID | Qty | Physical purpose and remaining fit |
|---|---:|---|
|01_horn_fit_coupon|1|Compare all four holes and centre access to the actual stock horn|
|02_hole_clearance_coupon|1|Choose printed screw clearance using real screws|
|03_servo_body_coupon|1|Measure the actual delivered servo case before larger prints|
|04_gate_rail_coupon|1|Test the retained runner channel after removing support|
|05_gate_runner_coupon|1|Test sliding clearance with part 04|
|10_chassis|1|Robot plate; receives cradles, jaws, marker tower, caster, and tether mast|
|11_drive_servo_cradle|2|Strap-retained wheel servos; align shafts and actual track|
|12_drive_wheel|2|Bolts to purchased stock horns; measure screw engagement and shaft loading|
|13_tpu_tire|2|TPU 95A traction rings; test retention and real rolling radius|
|14_caster_spacer|1|Nominal 3.9 mm spacer for the purchased Pololu 950 caster|
|15_caster_shim_05|1|Optional 0.5 mm leveling shim|
|16_caster_shim_10|1|Optional 1 mm leveling shim|
|20_moving_jaw|1|Stock-horn-driven finger; use compatible button-head screws|
|21_fixed_jaw|1|Fixed finger below the chassis; check screw-head floor clearance|
|22_gripper_servo_tower|1|Inverted servo body mount; actual horn face must be at z=12 mm|
|30_marker_tower|1|Robot tag at nominal 74 mm height, 45 mm behind the axle|
|31_tether_mast|1|Robot cable strain relief with 10 mm cord eye|
|32_overhead_tie_plate|1|Alternative standalone overhead cord attachment; needs purchased support|
|40_puck|3|Loose 38 mm grip targets with sockets for marker stems|
|41_object_marker_cap|8|Four object stems/plates and four calibration stems/plates at 74 mm|
|42_calibration_marker_base|4|Raised reference stands; measure actual reference-centre coordinates|
|50_gate_base|1|180 x 116 mm retained slider fixture; anchor to the floor|
|51_gate_slider|1|38 mm grip knob and retained feet; 100 mm travel|
|52_gate_end_stop|4|Separate stop block on each rail at both ends|
|60_arena_wall_200|20|Default 1 m inside frame; configurable segment length|
|61_wall_joiner|16|Straight wall-segment joints, on top of outside feet|
|62_arena_corner|4|Outside frame-corner joints|

These quantities total 82. The robot/arena assembly omits the five coupons, two optional shims, and standalone overhead tie plate, leaving 74 printed instances. The accessory tether fixture is the default overhead attachment; print part 32 only if using that alternative. A wooden frame may replace the 40 printed arena pieces if its measured inside dimensions match the controller configuration.

## All 13 accessory designs

| Design ID | Qty | Physical purpose and remaining fit |
|---|---:|---|
|electronics_box|1|200 x 130 x 65 mm low-voltage box; measure boards, connectors, and insert fit|
|electronics_lid|1|Retaining lip and heat-set-insert fastening; check lid clearance|
|perfboard_carrier|1|Six adjustable M3 slot rows; board hole locations are measured inputs|
|camera_plate|1|140 x 100 mm plate with 1/4-20 mounting clearance; actual camera socket required|
|beam_clamp_lower|2|One camera pair and one tether pair; real beam dimensions and load unverified|
|beam_clamp_upper|2|Mating clamp halves; verify tightening gap and bolt stack|
|tether_anchor_plate|1|Overhead soft-cord eye, sharing two tether-clamp bolts|
|cable_clamp_lower|1|Measured cable-jacket strain relief; support bracket/strap still purchased|
|cable_clamp_upper|1|Mating cable half; nominal 6 mm channel needs actual cable measurement|
|estop_panel|1|Omron A22E cutout and dedicated nominal 16 mm arm-button opening|
|estop_clearance_ring|1|Optional low ring; remove it if E-stop press or turn-reset access is reduced|
|estop_panel_stand|1|Rear switch clearance and desk anchors; rubber feet and fasteners purchased|
|terminal_guard|1|Low-voltage terminal channel; exact terminal block and placement measured|

These quantities total 15. The enclosure, camera, tether, cable, and operator layouts are separate assemblies. They are not one installed CAD model of a particular desk or camera frame. The individual models provide mounting geometry; the support frame and its attachment still need selection and measurement.

## Purchased parts and stock materials

| Physical item | Qty | Supplied coverage | What the builder must obtain or measure |
|---|---:|---|---|
|ST3020 bus servo|3|Vendor reference geometry in robot assembly; source drawing/STEP preserved|Confirm delivered revision, shaft/horn position, plugs, modes, signs, and feedback units|
|Stock output horn|3|Printed 14 mm-pitch interface and fit coupon; vendor horn geometry|Confirm actual screw thread and safe engagement; keep the metal spline interface|
|Pololu 950 plastic ball caster|1|Vendor STEP reference, spacer, and mounting drawing|Verify screw stack, chassis level, free rolling, and rear load|
|ESP32-WROOM-32 DevKit|1|Firmware target and wiring pin map; adjustable carrier|Exact board model and hole coordinates; no board-specific printed footprint|
|Waveshare Bus Servo Adapter A|1|External wiring instructions and controller interface|Board revision, jumper A, actual UART levels, connector clearance, and mounting|
|Regulated current-limited 12 V supply|1|Circuit load boundary and wiring plan|Complete prototype supply stays at 2 A; supply model and mains enclosure remain purchased|
|USB camera|1|Tripod plate and calibration workflow|Camera model, driver, resolution, optics, tripod socket or purchased adapter|
|Rigid overhead frame/beam and desk attachment|1 structure|Measured-beam clamp geometry|Nominal 20 x 20 mm beam is a commissioning input; actual dimensions and load capacity required|
|Tripod screw or ball head|1|6.7 mm clearance slot for 1/4-20 UNC hardware|Measure camera socket depth and safe screw length|
|Soft support cord, swivel, camera retention cord|As needed|Robot and overhead cord eyes|Length, slack, independent camera retention, pull/slip checks|
|Power/data tether and servo bus leads|1 routed harness plus servo leads|Daisy-chain wiring, mast, and cable clamp|Wire/connector ratings, length, jacket diameter, current-limited load, and drag|
|USB data links|2|Host-to-MCU and host-to-camera workflows|Correct connectors, cable length, reliable transfer, and no motor power through USB|
|Low-voltage terminals, connectors, fuse holders, edge protection|As needed|Netlist, cable openings, terminal guard|Exact products and ratings; no invented terminal/connector footprints|
|Removable hardware-authorization jumper|1|GPIO33 interlock pin map|Physical header/jumper and access; missing jumper must block arming|
|Flat rear ballast|About 100 g available|Rear tie slots; balance procedure|Actual mass required depends on completed robot and puck load|
|Grip pads and clamp packing|Small sheet|Jaw and cradle clearance|Material thickness, grip force, cable clearance, and compression|
|Cable ties|About 24 for robot, plus accessories as needed|Strap and marker-foot slots|Fit, retention, and routing|
|Flat matte floor/board|1|Configurable arena frame and layout|Flatness, actual inside dimensions, anchoring, and camera-visible surface|
|Marker paper, adhesive, removable tape|As needed|Scaled ArUco sheet and calibration crosshairs|Actual print scale, complete margins, fixed mounts, and measured 74 mm plane|
|PETG and TPU 95A filament|As required by slicer|All 40 CAD designs and orientation manifests|Actual print quality, shrinkage, layer bonding, supports, and tire fit|
|Existing DGX Spark and network access|1 existing host|Deployment and admission instructions|Host/storage admission and model qualification remain separate acceptance gates|

The marker sheet has robot ID 0 and object IDs 1 through 5. The default physical fixtures use three pucks and one gate knob; choose four distinct object IDs and leave the fifth spare. Four plain calibration crosshairs avoid introducing extra tracked objects. The [printable marker sheet](../hardware/markers/aruco-markers-a4-300dpi.png) and [crosshair sheet](../hardware/prints/calibration-crosshairs-a4-300dpi.png) each have a 100 mm ruler to check.

## Exact cutoff-circuit purchases

The [electrical BOM](../hardware/electronics/bill-of-materials.csv) is the ordering reference. This inventory groups its components without replacing its pin-level notes or datasheet links.

| Item | Qty | Package/build coverage |
|---|---:|---|
|TI CD74HC123E HC retriggerable timer|1|PDIP 16, 3.3 V watchdog logic; HC rather than HCT|
|TI SN74HC08N HC AND gate|1|PDIP 14 enable logic|
|AOS AO3400A MOSFET|1|Relay-coil driver only|
|SparkFun BOB-00717 SOT23 carrier|1|Mount Q1 and verify pin-to-pad continuity|
|Omron/Aratas G5CA-1A DC12 relay|1|Normally open motor-rail cutoff; complete supply limited to 2 A|
|Vishay 1N4007-E3/54|1|Coil flyback diode; follow netlist polarity and placement|
|Omron A22E-M-02 E-stop|1|Two NC contacts; dedicated panel cutout and two independent circuits|
|Adafruit 1505 black normally-open momentary button|1|Dedicated nominal 16 mm panel opening; GPIO26-to-GND arm input; label PRESS TO AUTHORIZE and verify delivered fit|
|TDK B32529C0155J000 timing capacitor|1|1.5 uF film, 5 percent; specified timing connection|
|82 kOhm, 1 percent resistor|1|Timer R1|
|1 kOhm, 1 percent, 0.25 W minimum resistors|3|GPIO/logic/gate series resistors|
|10 kOhm, 1 percent resistors|2|Logic-input pulldowns|
|100 kOhm, 1 percent resistor|1|MOSFET gate pulldown|
|1 kOhm, 0.5 W minimum resistor|1|Switched-rail bleeder|
|330 Ohm, 0.25 W minimum resistor|1|External E-stop sense pullup|
|100 nF ceramic capacitors|2|One beside each logic IC|
|10 uF electrolytic capacitor|1|Local 3.3 V bypass|
|Littelfuse 0297002.WXNV, 2 A fuse|1|Motor branch, plus a compatible purchased holder|
|Littelfuse 0287001.PXCN, 1 A fuse|1|Coil branch, plus a compatible purchased holder|
|Isolated-pad perfboard|1|Builder lays out and solders the supplied netlist|
|DIP 16 and DIP 14 sockets|1 each|Fit the selected logic ICs|

The timer, logic, relay, diode, passive parts, and wiring have a circuit drawing and netlist, not individual mechanical CAD models. Their board layout must fit the measured carrier and enclosure. The [selected arm-button page](https://www.adafruit.com/product/1505) supplies the purchased component; CAD supplies its mounting hole and [printable panel labels](../hardware/prints/accessories/operator-panel-labels.svg) identify the controls. A fresh press opens a five-second authorization window; a trusted host arm command is still required. Physical watchdog timing, relay release, switched-rail discharge, E-stop operation, and actual motion cessation are still bench measurements.

## Fasteners and hardware that CAD does not supply

| Location | Starting purchase count | Fit/source |
|---|---|---|
|Caster|Two M2 x 12 sets|Verify final spacer/chassis/caster stack|
|Cradles, gripper tower, robot mast|Sixteen M3 x 16 sets|Eight cradle, four tower, four mast|
|Fixed jaw|Two M3 button-head x 20 sets|Heads sit in lower counterbores|
|Wheel-to-horn|Eight compatible screws and washers|About 14 mm may suit M3; actual thread and engagement decide|
|Moving-jaw-to-horn|Four compatible button-head screws and washers|About 12 mm may suit M3; measure engagement and case clearance|
|Gate stops|Four M3 button-head x 12 sets|Upward from lower base counterbores|
|Printed arena joints|Forty M3 button-head x 12 sets|Heads below floor plane, nuts above joiners|
|Camera and tether clamps|Eight M4 x 50 bolts, nuts, washers|Four per pair; two tether-plate holes share clamp bolts|
|Operator stand/panel|Four M4 x 90 bolts, nuts, washers|Actual stack and projecting ends require measurement|
|Optional E-stop ring|Two M3 sets|Omit if ring is not used|
|Enclosure lid|Four M3 screws and four heat-set inserts|Candidate 4.6 mm insert bore needs an actual insert fit check|
|Carrier mounting|Four M3 floor screws, four 8 mm standoffs, four carrier screws|Measure final lengths and solder-side clearance|
|Individual electronic boards|Board-specific standoffs and screws|Exact quantities/offsets depend on purchased boards and perfboard layout|
|Terminal guard|Two M3 sets|Carrier-slot alignment and actual terminals|
|Cable clamp|Four M3 sets|Actual jacket compression and retention|
|Operator stand desk anchors|Two M5 anchors|Desk/support attachment is purchased and measured|
|Stand rubber feet|Enough for stable support|Taller than projecting underside fasteners|

Counts are starting selections from the assembly guides. They do not certify screw thread, engagement, support strength, or wire clearance. Horn screws must not contact the servo body. The supplied CAD contains no models of loose fasteners, inserts, feet, pads, straps, or the wiring harness.

## Work and evidence still absent

No dedicated PCB layout, Gerbers, or populated watchdog board is supplied. The defined implementation is hand-wired perfboard. No exact ESP32 board footprint, camera body, complete overhead frame, purchased terminal-block footprint, or routed harness model is supplied. Those are measured purchased components and stock materials, not missing printable duplicates.

There is no separate native insert-fit coupon in the 40-design catalogue. The enclosure uses a candidate 4.6 mm bore; test a small coupon against the exact purchased insert before printing the enclosure. Board standoffs, fuse holders, connectors, desk anchors, and camera mounting hardware need their delivered dimensions and ratings checked.

Tools required include a printer and slicer, calipers and a ruler, appropriate soldering/crimping tools, a multimeter, a current-limited supply, and an oscilloscope or logic analyzer for the cutoff test. A suitable dummy load is needed before connecting servos. These tools and test fixtures are not supplied or modelled.

No physical print, assembly, fit test, load test, camera calibration, wiring measurement, power-cut trace, or endurance run has been performed. Actual Onshape account import has not been performed. Native/STEP kernel validation and software/firmware checks are documented evidence about the files, not the completed physical robot. Keep the firmware gate disabled until the mechanical, electrical, camera, and fault-test acceptance results are recorded.
