# Week-long readiness review

The system is not yet qualified to run physical research unattended for weeks. Three independent reviewers checked endurance, isolation/deployment and physical control. Their concrete reproductions led to eight repaired failure categories. Leo then asked us to stop relatively soon, so remaining work is recorded rather than silently treated as passed.

The verified baseline was commit `6a24cf2`, with 83 passing tests and the 112-trial demo. The revised suite passes 96 tests without skips and the same demo. [Baseline evidence](verification-baseline.json) and [current evidence](verification.json) preserve the matched checks. The CAD remains unchanged and retains its independent validation.

| Failure category | Revised behavior |
| --- | --- |
| Tool errors and observation-only churn appeared successful | Stop after three generations without research progress; report `no_progress` |
| Evaluation failed after promoting a model above 100 holdouts | Bound matched holdouts; commit evaluation and finding together; restore model from committed evidence |
| Interrupted agents' useful files disappeared from inheritance | Validate and register interrupted workspaces on restart |
| Older programs became undiscoverable | Paginate artifacts with a bounded cursor |
| Fixed GPU recipe imported workspace code | Use isolated Python imports; generated GPU programs remain disabled |
| Job queue continued after ambiguous container cleanup | Latch the cleanup fault and refuse further jobs |
| Repeated small turns could wind the tether indefinitely | Require operator unwind/reset and enforce measured/predicted turn guards |
| Held-at-boot button counted as fresh authorization | Require release before a fresh press; clear authorization on cutoff |

An accelerated simulation attempted 1,000 generations within 120 seconds. It completed 562 generations, 15,736 measured trials and 562 evaluations, then stopped at the wall-time budget. SQLite integrity was `ok`, with no pending trials and about 68.4 MB of state. The 1,000-generation target was not reached. This is a bounded simulation stress check, not real-model, Docker or physical endurance qualification. [Stress evidence](stress-simulation.json) records the result.

The disabled ESP32 firmware rebuilt successfully. Its gate remains `HARDWARE_VERIFIED=0`. Physical startup now requires explicit tether inspection/unwind, operator authorization, a fresh MCU button press and a disarmed tether reset before arming.

Read the [endurance review](verification-endurance.md), [isolation review](verification-isolation.md) and [physical review](verification-physical.md) for reproductions, tests and limits. The new `scripts/qualify_sandbox.py` performs actual-container checks after host admission. It pulls nothing and has no successful real-Docker report yet.

## Next work, in priority order

1. Fix stale-session recovery in Spark launchers and confirm worker survival during startup. Resolve disk admission with Leo, preserving unrelated services and data. Qualify actual Docker copyback and resource enforcement using the new tool.
2. Bound aggregate state storage before a generation starts, bind evidence to configuration/geometry/firmware/calibration, and add tested backup/recovery. The disk reserve alone cannot promise weeks of capacity. Benchmark larger archives and qualify the chosen real local model.
3. Build and commission the robot. Qualify the actual camera's exposure freshness, servo/current conversions, physical cutoff, printed fits, shaft loads and tether behavior.
4. Implement and prove trusted task resets and starting-state checks. Demonstrate runs without rescues at 15 minutes, one hour, 24 hours, 72 hours, then a week. A manual reset or tether stop means unattended acceptance has not passed.

Keep automatic physical restart and arbitrary generated GPU workloads disabled. Passing software fixtures does not replace these gates.
