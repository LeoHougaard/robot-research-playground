# Independent CAD review

The final package passes independent FreeCAD 1.1.4/OpenCascade validation: 46 native/STEP pairs, covering 40 custom part designs and six assembly layouts. All 163 reopened native solids are valid, closed and positive-volume; their STEP reimports match. The native documents contain editable Box, Cylinder and boolean features, and curved surfaces remain analytic surfaces in STEP. The default manifests total 97 print pieces: 95 PETG and two TPU tire rings.

All 13 accessory print STLs pass transformed geometry and closed-mesh checks. Fourteen native dimension edits recompute and restore correctly. The final panel passes independent checks for both the physical arm-button opening and keyed E-stop cutout. Physical fit, strength and load testing remain unverified.

## Required parts and quantities

The original print manifest contains 27 distinct parts and 82 printed pieces for the default one-meter arena. Eighty pieces use PETG and two tire rings use TPU 95A. This count includes fit coupons and optional caster shims.

| Group | Distinct parts | Default printed pieces | Coverage |
| --- | ---: | ---: | --- |
| Fit coupons | 5 | 5 | Horn, screw clearance, servo body and gate rail/runner |
| Robot and tether parts | 13 | 16 | Chassis, two cradles/wheels/tires, caster spacer/shims, jaws, gripper tower, marker tower, tether mast and overhead tie plate |
| Experiment and calibration fixtures | 6 | 21 | Three pucks, eight marker caps, four calibration bases, gate base/slider and four stops |
| Arena | 3 | 40 | Twenty walls, sixteen straight joiners and four corner joiners |
| External accessories | 13 | 15 | Electronics enclosure/lid/carrier/terminal guard, camera plate, two beam clamp pairs, tether anchor, cable clamp pair and operator panel/ring/stand |

The robot/arena assembly contains 74 printed instances and four purchased reference instances: three ST3020 servos and one Pololu caster. These make 78 component instances and 108 solids because each vendor model contains multiple solids. It omits the five coupons, two optional shims and the separate overhead tie plate. The tie plate remains available to print; the overhead support is purchased. The connected assembly reaches Z=138 mm and contains no floating overhead plate.

Servos, stock horns, the purchased ball caster, fasteners, straps, pads, wiring, connectors, a flat floor, camera and rigid support structure remain purchased components or stock materials. Their reference CAD helps layout but does not replace the physical part or establish its rating. The accessory designs use configurable stock-beam and cable dimensions rather than an invented USB-camera body. Purchase and measure the rigid frame, camera adapter, board standoffs, inserts, actual cable and panel hardware before final fabrication; [accessory assembly instructions](accessories.md) specify the fastening stacks and provisional lengths.

## Geometry and editability checks

[verify_cad.py](../scripts/verify_cad.py) opens every native FCStd document, recomputes its named final object and reads its feature dependencies. It closes the native document without saving. A fresh `Part.read` then reads the written STEP. The [kernel report](../hardware/freecad/validation/kernel_report.json) records file hashes, solid counts, surface types, dimensions, volumes and feature trees. The [GUI report](../hardware/freecad/validation/gui_report.json) separately records actual desktop-application opening and final drawable visibility for all 46 documents. Both reports' hashes match the delivered files.

To repeat the kernel review from the project root:

```powershell
python scripts/run_freecad.py scripts/verify_cad.py --profile validation --timeout 180
```

The checks require matching native/STEP bounding coordinates within 0.0001 mm and volume within one part per million, with a small absolute volume floor. Those are digital consistency checks, not manufacturing tolerances. The original STL comparison allows 0.001 mm in dimensions and 0.1 percent in volume; its largest measured volume gap is 0.071435 percent. The small volume difference is expected because the old STL circles used 96 straight segments and the new CAD uses analytic circles.

Independent interface checks verify four 3.4 mm horn-clearance holes on a 14 mm pitch circle, a 6.6 mm center-access hole, and the caster spacer's two 2.5 mm holes on 13.5 mm centers. They also check the camera/clamp M4 pattern, accessible tripod slot, E-stop bore and keyed lock-ring cutout. The reviewer changes one robot coupon dimension and one driving dimension in each of the 13 accessory documents, recomputes the final solid, restores the dimension and confirms the original geometry. No source document is saved.

Each accessory STL is independently loaded as a closed mesh and compared with the native solid after the declared print rotation and translation. The lid's native coordinates extend down to Z=-4 mm; its print STL is correctly flipped 180 degrees and translated onto Z=0. All accessory print STLs start on the bed. Their bounding comparison allows 0.13 mm and volume comparison 0.5 percent for the documented 0.12 mm mesh deflection. Saved GUI metadata must contain a camera and show only the final geometry; construction tools remain in the editable tree. The parent also opens all documents in the actual FreeCAD GUI to verify drawable visibility.

The three vendor horn contact faces match their nominal assembly planes within 0.000006 mm, and the caster ball contacts Z=0. The 20 mm horn rim is 0.3 mm behind the flat mounting face because of its chamfer; the check uses the actual flat face. OpenCascade intersection checks find no positive-volume overlap between any purchased servo/caster reference and printed solids. This establishes nominal digital placement, not physical fit.

STEP provides editable solid geometry for Onshape. It does not preserve the FreeCAD construction tree. Onshape supports adding features and direct edits to imported solids; FreeCAD FCStd preserves the native primitives and boolean history. See [Onshape's imported CAD guidance](https://cad.onshape.com/help/Content/Document/working_with_imported_cad.htm). Actual Onshape account import has not been performed in this review.

The task-local FreeCAD portable runtime came from the [official 1.1.4 release](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4), with its release asset digest checked before extraction. Validation uses separate task-local user/system configuration files and changes no global preferences.

## Fit, material and load limits

| Interface or part | Nominal CAD | What still needs measurement |
| --- | --- | --- |
| Wheel/horn | 14 mm bolt circle, 3.4 mm clearance, stock horn retained | Actual horn thread, diameter, screw length and safe engagement |
| ST3020 cradle/tower | 40.2 by 20.2 mm body basis, 48 mm axial envelope; open straps and adjustment | Delivered case revision, plug clearance and horn-to-case position |
| Caster | Pololu 950, 13.5 mm hole spacing, 10.1 mm height, 3.9 mm spacer | Screw stack, chassis level and rear wheel load |
| Wheel/tire | 32.5 mm rolling radius, 154 mm track; 0.2 mm nominal radial TPU interference | Actual rolling radius, track, shrinkage, traction and tire retention |
| Gripper | 2 mm floor gap, 70 mm moving finger, initial -20 to 35 degree sweep | Floor flatness, pad thickness, low-force grip and screw-tip clearance |
| Gate | 100 mm travel, retained runners and four stops | Printed running clearance and freely moving under a 1 N hand pull |
| Marker mounts | Common 74 mm tag plane, robot tag offset 45 mm behind axle | Printed and paper height, marker margin and camera calibration |
| Beam clamp | Measured 20 mm stock beam, nominal 20.4 mm groove | Real extrusion width, tightening gap, bolt stack and long-term slip |
| Cable clamp | Measured cable diameter parameter | Compression without insulation damage and pull-load transfer |
| E-stop | 22.5 mm keyed panel opening, 3.3 mm key, 24.3 mm overall cutout, 3 mm panel | Omron A22E-M-02 locking hardware, finished cutout, unobstructed press and turn reset |
| Physical arm button | 16 mm panel opening for Adafruit 1505 normally-open momentary switch | Finished bore, washers, nut retention and terminals clear of E-stop wiring |

The vendor body measurements are inconsistent. Waveshare's product specification lists 40.2 by 20.2 by 40 mm, its drawing marks 40.1 mm body height, and its STEP housing span is about 47.3 mm along the output axis. The design uses adjustment and fit coupons instead of assuming these values describe the same surface. The modeled horn hole diameter does not establish a screw thread. See [Waveshare ST3020](https://www.waveshare.com/wiki/ST3020_Servo) and the preserved vendor files.

The E-stop cutout includes the antirotation key required by Omron's supplied locking ring, with nominal dimensions inside the published tolerance range. The 70 mm guard opening leaves nominal finger space around the 40 mm head; it still requires an actual hand-access and reset test. [Omron A22E mounting drawing and instructions](https://assets.omron.eu/downloads/latest/datasheet/en/a22e_a22ne-p_aa22ne-pd_datasheet_en.pdf).

The selected arm button's published shaft envelope reaches 15.56 mm and its full height is 29.4 mm. A nominal 16 mm bore and 70 mm rear panel space accommodate that drawing; fit and terminal routing remain physical checks. [Adafruit 1505 dimensions](https://www.adafruit.com/product/1505).

Start with the print manifest's orientations, PETG, a 0.4 mm nozzle, 0.2 mm layers and four walls. Use five walls around jaw and cradle mounting holes. Tire rings use the printer's TPU profile. Remove support from gate channels, strap windows and tether eyes before fitting. Thin shims, supported rail lips, clamp layers and long marker posts require actual print inspection.

The ST3020 vendor torque specification is 25 kg-cm at 12 V, about 2.45 N-m. A 70 mm finger can therefore see forces on the order of 35 N at that nominal stall torque. No printed jaw, clamp or chassis has been load-qualified for that condition. Software current limits and the 2 A complete prototype supply limit do not certify structural strength. Use low-force tests with motor power disconnected first, then supervised powered tests. [ST3020 specifications](https://www.waveshare.com/product/modules/motors-servos/st3020-servo.htm).

The Pololu caster is intended for small robots with only a few pounds of load, and its actual rear load depends on ballast and gripper reach. Camera and support clamps likewise need a real pull/slip test with the selected camera, cable and purchased frame. No finite-element analysis, strength test or unattended endurance run was performed. [Pololu 950](https://www.pololu.com/product/950).
