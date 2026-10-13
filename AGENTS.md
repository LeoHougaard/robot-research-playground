# Robot playground working instructions

Preserve the goal, build decisions and verification in docs/HANDOFF.md. Read README.md and docs/acceptance.md before changing the runtime. Keep the implementation small, with Python stdlib for the core and optional hardware dependencies.

Simulation runs are authorized. Physical hardware does not exist yet. Do not claim physical fit, safe wiring, calibrated cameras, continuous servo duty or unattended endurance from software tests. Firmware remains HARDWARE_VERIFIED=0 until measured bench acceptance.

Only trusted code may arm hardware, change safety limits, control Docker or write the evidence archive. Generated agent code stays in offline bounded containers. Agent claims remain hypotheses. Raw measurements, calibration provenance and model comparisons must be preserved.

For Spark operations use Leo's n-gb10 skill. Audit the host before deployment, preserve existing workloads and enforce admission. Current disk shortage blocks image/model downloads and runtime qualification. Never prune containers, delete unrelated data or reconfigure the host as a shortcut.

Run python scripts/verify.py after meaningful software changes. Inspect real renderings when changing CAD or marker layouts, and keep print-source dimensions traceable to vendor drawings or measured hardware. Use focused tests for changed behavior. Update the handoff with completed evidence and concrete remaining work.
