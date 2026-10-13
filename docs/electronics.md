# Electronics and power plan

Spark runs the supervisor, model, sandbox and local browser UI. Its USB port connects to a separate ESP32 DevKit based on ESP32-WROOM-32. The MCU mediates servo motion and cuts motor power if commands or telemetry go stale. Generated code has no serial device access.

| Part | Quantity | Selection and purpose |
| --- | --- | --- |
| ESP32 DevKit, ESP32-WROOM-32 | 1 | Matches the `esp32dev` firmware target; USB data cable to Spark |
| Waveshare Bus Servo Adapter A | 1 | TTL half-duplex bus interface, MCU UART control with jumper at A |
| Waveshare ST3020 servo | 3 | Two wheels plus one experiment joint; assign distinct IDs separately |
| Regulated 12 V supply | 1 | External motor power, complete supply limited to 2 A for this prototype |
| Normally open motor relay and coil driver | 1 assembly | G5CA-1A DC12 plus AO3400A, per the [cutoff circuit](watchdog-circuit.md) |
| Independent watchdog and enable logic | 1 assembly | CD74HC123E plus SN74HC08N and timing components, per the [exact BOM](../hardware/electronics/bill-of-materials.csv) |
| Latching emergency-stop switch with NC contacts | 1 | Physically opens motor-enable circuit independently of software |
| Momentary arm button | 1 | Physical authorization before host arming |
| Inline fuse, wiring, terminal blocks | As needed | Select from measured current and the smallest wire/connector rating |
| Level shifter | If required | Fit after confirming the adapter UART electrical levels |

Waveshare lists the ST3020 as a 6 to 14 V servo with default 1 Mbps communication and 2.7 A locked-rotor current at 12 V. Three stalled motors can therefore approach 8.1 A. This prototype limits the complete 12 V supply to 2 A and uses a 2 A motor-branch fuse, with lifted wheels during commissioning. Raising that limit requires a reviewed cutoff and wiring design. The firmware's 1.5 A per-servo trip does not establish aggregate hardware capacity. See the [ST3020 specifications](https://www.waveshare.com/product/modules/motors-servos/st3020-servo.htm) and [cutoff load boundary](watchdog-circuit.md).

Use 12.0 V for the adapter and servo rail. This is inside the adapter's stated 9 to 12.6 V range and the servo's range. The adapter documentation says its supply must match the servo. Motor power comes from the external supply, never Spark USB. See [Adapter A specifications](https://docs.waveshare.com/Bus_Servo_Adapter_A).

| Connection | Wire or behavior |
| --- | --- |
| Spark USB to ESP32 USB | 115200 baud host link; USB powers MCU only |
| ESP32 GPIO16 RX to adapter RX silk label | Vendor's same-label wiring, jumper A |
| ESP32 GPIO17 TX to adapter TX silk label | Vendor's same-label wiring, jumper A |
| ESP32 GND to adapter GND to supply return | Common signal reference |
| 12 V positive to 2 A motor fuse to relay NO contacts to adapter power input | Relay interrupts the servo rail |
| Adapter bus output to ST3020 daisy chain | Vendor cables and connector orientation, IDs assigned one servo at a time |
| GPIO25 to watchdog reset and enable logic | Active high; external pulldown forces off at reset |
| GPIO27 to NC E-stop sense loop to GND | Healthy is low; open or high trips firmware |
| E-stop separate NC contact to relay-coil return | Independent power cut even if firmware hangs |
| GPIO26 to momentary arm button to GND | `INPUT_PULLUP`, press required within 5 seconds before host arm |
| GPIO32 to independent watchdog pulse input | MCU toggles every 20 ms only while powered and safety conditions hold |
| GPIO33 to removable hardware authorization jumper to GND | Physical arm preparation interlock; absent jumper blocks arming |

The RX-to-RX and TX-to-TX labels are unusual but explicitly stated in [Waveshare's wiring instructions](https://docs.waveshare.com/Bus_Servo_Adapter_A/Product-Wiring-Example). Do not substitute generic crossed UART assumptions. The sources do not establish that every board revision is safe for direct 3.3 V GPIO. Keep `HARDWARE_VERIFIED=0` until adapter revision, signal voltage and return current have been measured. Add a suitable level shifter if required.

The firmware's heartbeat timeout is 400 ms, each motion action lasts at most 500 ms, and servo telemetry must be younger than 250 ms. Overtemperature above 55 C, per-servo current above 1.5 A, stale feedback, invalid mode, E-stop, or disarm drops GPIO25. A firmware timer cannot protect against a completely hung MCU. The independent watchdog watches GPIO32. The relay requires GPIO25 high, watchdog alive and the physical NC E-stop circuit closed. Follow the [pin-by-pin circuit and measurement gates](watchdog-circuit.md), including measured watchdog expiry and motor-rail cutoff within 100 ms. The circuit is specified but remains unassembled and unverified.

With the GPIO33 authorization jumper installed, a physical arm-button press permits a maximum five-second powered feedback check with torque disabled before host arm. Keep wheels lifted because torque behavior at servo power-up has not been measured. E-stop clears authorization and requires a fresh physical press. The first watchdog pulse, enable sequence and power-up transient need bench verification.

Commission one servo at a time. Verify power-off at boot, MCU reset, USB unplug, supervisor stop, stale heartbeat, E-stop press, broken E-stop wire and failed telemetry. Confirm no reverse feed from the adapter to USB. Set wheel servos to the required motor mode and the third joint to servo mode. Mount screws must not touch or bind the servo body. Repeat tests after wiring or firmware changes. The hardware remains unverified until these measurements and power-cut tests are recorded.
