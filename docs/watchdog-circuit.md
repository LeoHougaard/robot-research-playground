# External watchdog and motor cutoff prototype

This is a concrete perfboard design for the external electronics box. It uses a
retriggerable logic timer, an AND gate and a normally open relay to interrupt the
12 V servo rail. It keeps the existing GPIO25 enable and GPIO32 pulse interface.
The supplied firmware remains `HARDWARE_VERIFIED=0`. The design has not been
assembled, measured or certified.

The [schematic](../hardware/electronics/watchdog-circuit.svg),
[parts list](../hardware/electronics/bill-of-materials.csv) and
[pin-by-pin netlist](../hardware/electronics/netlist.json) describe the same
revision. Use a soldered isolated-pad perfboard for logic and coil wiring. Motor
current must use suitable wire and terminals rather than solderless breadboard
contacts or thin perfboard copper.

## Parts and load boundary

Use TI `CD74HC123E` in DIP16 and `SN74HC08N` in DIP14, powered at 3.3 V from the
ESP32 regulator. Both are HC parts with a 2 to 6 V supply range. Their HCT variants
are unsuitable substitutes for this 3.3 V circuit.
[TI CD74HC123](https://www.ti.com/product/CD74HC123),
[TI SN74HC08](https://www.ti.com/product/SN74HC08).

Use `AO3400A` on a `SparkFun BOB-00717` carrier to switch the relay coil. The AOS
part specifies on-resistance at 2.5 V gate drive, so the design does not depend on
an uncharacterized MOSFET gate threshold. Its actual SOT23 pins are one gate,
two source and three drain. Check the carrier's trace mapping with a continuity
meter before soldering.
[AOS AO3400A](https://www.aosmd.com/products/mosfets/low-voltage-mosfets-12v-30v/ao3400a),
[SparkFun carrier](https://www.sparkfun.com/sparkfun-sot23-to-dip-adapter.html).

The exact relay is `G5CA-1A DC12`, Omron/Aratas. Its 12 V coil draws nominally
16.7 mA. The normally open contacts have a stated 10 A at 30 VDC resistive rating
and a separate 3 A DC inductive rating. Its coil uses terminals one and two;
contacts use three and four. The vendor terminal drawing is a bottom view.
[G5CA datasheet](https://omronfs.omron.com/en_US/ecb/products/pdf/en-g5ca.pdf).

The DC resistive rating is explicit; an AC-only mains rating would not establish
this use. Servo electronics have inrush, switching and regenerative behavior, so
that resistive rating does not establish safe interruption of three stalled
motors. Limit the complete 12 V supply to 2 A for this prototype and use a 2 A
motor-branch fuse. Raising that limit needs load qualification and a reviewed
switch/relay choice. The current catalogue lists the G5CA-1A in production.
[Current manufacturer catalogue](https://www.aratas.com/us-en/products/relays/G5CA).

The parts are identifiable through ordinary distribution. The orderable DIP
timer and exact relay appeared in stock when checked; availability can change.
[Timer listing](https://www.digikey.com/en/products/detail/texas-instruments/CD74HC123E/376825),
[relay listing](https://www.digikey.com/en/products/detail/omron-electronics-inc-emc-div/G5CA-1A-DC12/699986).

## Logic and timing

U1 channel one uses its B rising-edge input, pin two, with A pin one grounded.
U1 reset pin three follows the pulled-down GPIO25 enable. The watchdog output
is Q pin thirteen, not the inverted output at pin four. An 82 kOhm 1% resistor
connects pin fifteen to 3.3 V. A 1.5 uF 5% film capacitor connects between pins
fourteen and fifteen. It does not connect to ground. Tie unused channel inputs
nine, ten and eleven to ground; leave unused outputs and timing pins unconnected.
[TI timer pinout and behavior](https://www.ti.com/lit/ds/symlink/cd74hc123.pdf).

The timing capacitor is `TDK B32529C0155J000`, 1.5 uF, 63 V, 5% film, with 5 mm
lead spacing. This avoids relying on a bulk electrolytic's wider tolerance and
leakage as the time reference.
[TDK film capacitor family and ordering codes](https://www.tdk-electronics.tdk.com/inf/20/20/db/fc_2009/B32520_529.pdf).

Firmware toggles GPIO32 every 20 ms, so rising edges arrive every 40 ms. From the
timer's typical K-versus-voltage graph, K is approximately 0.48 at 3.3 V. This
gives an inferred nominal pulse of `0.48 * 82000 * 1.5e-6 = 59 ms`. The familiar
0.45 coefficient is specified at 5 V and must not be treated as a guaranteed
3.3 V timeout. Process, voltage, temperature and component variation still apply.
Require measured Q expiry between 50 and 70 ms after the last rising edge.
[TI timing graph](https://www.ti.com/lit/ds/symlink/cd74hc123.pdf).

U2 pins one and two receive enable and watchdog Q. Pin three drives the MOSFET
gate through 1 kOhm; a 100 kOhm gate-to-source resistor holds it off without logic
power. Ground every unused U2 input. Add 100 nF beside each IC's supply pins and
10 uF across the local 3.3 V rail. The logic and sense circuit require only low
current; the relay coil draws from the separate 12 V supply.
[TI AND pinout](https://www.ti.com/lit/ds/symlink/sn74hc08.pdf).

| GPIO25 enable | Watchdog Q | Physical NC circuit closed | Relay coil powered |
| --- | --- | --- | --- |
| 0 | 0 | 0 | No |
| 0 | 0 | 1 | No |
| 0 | 1 | 0 | No |
| 0 | 1 | 1 | No |
| 1 | 0 | 0 | No |
| 1 | 0 | 1 | No |
| 1 | 1 | 0 | No |
| 1 | 1 | 1 | Yes |

GPIO25 low resets U1 and disables U2. GPIO25 stuck high cannot keep the coil on
after GPIO32 stops changing. A GPIO32 wire stuck high or low produces no repeated
rising edges. The timer can generate one bounded pulse on a reset transition
under some input states; this is why startup checks must include GPIO32 held high
and enable transitions. Default pull-downs and the relay's normally open contacts
must prevent sustained motor power without healthy edges.

## Coil, emergency stop and power wiring

Feed the coil positive terminal from a separately fused 12 V branch. Connect its
negative terminal through one normally closed E-stop contact to the MOSFET drain;
source goes to common ground. The hardwired E-stop therefore opens the coil return
even if both GPIOs or the MOSFET drive logic remain high.

Place `Vishay 1N4007-E3/54` directly across the coil. Its cathode stripe connects
to coil positive; its anode connects to coil negative before the E-stop contact.
This protects the transistor and switch wiring when either the transistor or
E-stop opens. A plain flyback diode can prolong relay release, which belongs in
the measured cutoff time.
[Vishay diode datasheet](https://www.vishay.com/docs/88503/1n4001.pdf).

Use `Omron A22E-M-02`, a latching turn-reset E-stop with two NC contacts. The first
interrupts the coil; the second grounds GPIO27 while healthy. A 330 Ohm external
pullup from GPIO27 to 3.3 V gives roughly 10 mA sense current. Verify reliable
low-voltage contact behavior on the actual switch. The coil contact has a 12 V,
16.7 mA load. The manufacturer's product page identifies the exact 2NC model and
its DC switching ratings.
[Omron E-stop specifications](https://www.fa.omron.co.jp/product/item/A22E-M-02/).

Motor positive wiring is supply, 2 A fuse, K1 contact three, K1 contact four, then
adapter positive. Supply return goes directly to adapter ground, with a separate
small signal-ground connection to ESP32 ground. Do not route motor return current
through ESP32 USB ground wiring. Add a 1 kOhm 0.5 W bleeder across adapter power.
It helps discharge the disconnected rail but does not guarantee fast discharge.
The coil branch and logic remain independent of the switched servo rail.

No firmware interface changes are needed. GPIO26 remains the physical arm button;
GPIO33 remains the removable authorization interlock. Neither replaces the
hardwired E-stop. This circuit is one unmonitored cutoff channel; a welded relay
contact, shorted MOSFET or wiring bypass can defeat it. It is a prototype for a
confined, supervised bench experiment.

## Bench acceptance and falsification

Use the logic circuit and a modest dummy load first, with servos disconnected.
An oscilloscope or logic analyzer must record GPIO32, GPIO25, U1 Q, coil voltage
and switched output. A digital multimeter alone cannot establish a 100 ms cutoff.

1. Check all pin continuity against the netlist, IC orientation, grounds, diode
   polarity and capacitor placement. Verify K1 coil resistance and normally open
   contact state with all power removed.
2. Apply 12 V before USB, USB before 12 V, both together and repeated resets.
   With GPIO25 low, no sustained switched output is allowed. Repeat with GPIO32
   disconnected, held high and held low.
3. Supply normal 20 ms alternating GPIO32 levels. Q must stay high with measured
   rising edges 40 ms apart. Stop the edges at each polarity while leaving enable
   high. Q must expire between 50 and 70 ms after the final rising edge.
4. Measure final-edge-to-contact-disconnection time. It must be no more than
   100 ms, including diode-suppressed coil release and contact bounce. Record
   actual coil supply and logic voltage. Repeat near the expected operating
   temperatures and supply extremes. An out-of-range result rejects this build;
   change the timing components or suppression only through another review and
   repeat every check.
5. With logic drive held on, press the physical E-stop and break its coil-return
   wire. The coil must release and the motor contacts must open. Check the second
   contact produces a stable low GPIO27 while closed and a high level while open.
6. Check that the switched rail cannot receive power from USB or UART after K1
   opens. Measure stored charge decay; a supply disconnect does not instantly
   remove adapter and servo capacitance.
7. Only after the wiring and low-current tests pass, use a current-limited supply
   and lifted wheels with one servo. Measure inrush, contact heating, load current,
   switch-off transients and actual motion cessation. The final heartbeat-edge to
   motor cessation must also be within 100 ms. Repeat with the complete servo load
   at the 2 A total commissioning limit. A persistent rail or continued motion
   fails the gate even if Q and relay timing passed.
8. Kill the trusted host, unplug USB, stall MCU main-loop work and stop the entire
   MCU pulse source. Verify the same cutoff and fresh operator rearming behavior.

The component timing estimate, schematic review and software tests do not satisfy
these gates. Preserve measured traces, component lot/revision, load and ambient
conditions. Keep the firmware's verification gate disabled until the physical
results pass. No physical electrical experiment was performed for this design.
