# Real runtime qualification

## Current result

Neither real Docker nor a real local model has been qualified in the cloud recovery workspace. The Docker executable and a served local model are unavailable here. HTTP fixtures and injected sandbox results test the harness, not inference or container enforcement. Physical hardware remains unverified and disabled.

`scripts/qualify_local_model.py` is an opt-in acceptance harness for the actual Linux host. It never installs software, downloads models/images, starts services, changes credentials, accesses cameras, moves physical hardware, or prunes unrelated resources. Use only after the host's disk/memory admission and real sandbox qualification pass. Existing Spark storage restrictions remain applicable.

## What it exercises

The existing `LocalModel` client requests one bounded JSON decision at a time. The trusted supervisor and simulator, rather than model claims, establish outcomes:

1. Observe the simulation through a structured tool call
2. Intentionally propose an out-of-range wheel value; verify the supervisor rejects it without movement or a new experiment record
3. Recover with a valid moving experiment, committed prediction, measured outcome, and bounded prediction error
4. Write `policy.py` through the real offline Docker sandbox, then archive its content hash
5. Start a fresh successor conversation without the predecessor's private messages
6. Discover, import, and execute the predecessor's exact program through `run_policy`; verify a second measured experiment and unchanged original artifact

The successor starts at a deliberately different, bounded simulator pose, so merely hardcoding the predecessor pose does not pass the prediction tolerance. The harness changes only simulator state. Generations use the supervisor's aggregate-storage admission and preserve the archive. Nine successful model decisions are required; malformed JSON, a wrong tool, an unexpected tool error, an exceeded deadline, or missing evidence fails the run.

This is a small guided task, not a benchmark of autonomous scientific ability, long-running reliability, contact dynamics, or physical safety. Generated source is never evaluated on the host.

## Required provenance

A successful `/models` response does not prove real inference. The exact model ID must be provided explicitly. A full run also needs an operator-reviewed JSON manifest with the fields below. Do not put passwords, tokens, or other secrets in the manifest or launch configuration.

```json
{
  "kind": "operator_attested_real_local_model",
  "model_id": "EXACT_SERVED_MODEL_ID",
  "model_revision": "REVIEWED_UPSTREAM_REVISION_OR_LOCAL_BUILD_ID",
  "runtime_version": "REVIEWED_SERVER_VERSION",
  "reviewed_by": "ACTUAL_OPERATOR",
  "reviewed_at": "ACTUAL_REVIEW_TIME_WITH_TIMEZONE",
  "acceleration": "cpu",
  "pid": 12345,
  "process_start_ticks": 123456789,
  "command_sha256": "SHA256_OF_PROC_PID_CMDLINE",
  "launch_config": {
    "context_tokens": 4096,
    "quantization": "ACTUAL_QUANTIZATION",
    "batch_size": "ACTUAL_SETTING"
  },
  "model_files": [
    {"path": "/actual/local/model-file", "sha256": "ACTUAL_SHA256"}
  ],
  "runtime_files": [
    {"path": "/actual/server/executable", "sha256": "ACTUAL_SHA256"}
  ]
}
```

This is a schema illustration with intentionally invalid placeholders, not an attestation. Include all weight shards, tokenizer/config files needed to identify the model, and relevant server source/build files. `runtime_files` must include the actual `/proc/PID/exe` target and the executable of any descendant that owns the endpoint listener. The command hash is of the raw `/proc/PID/cmdline` bytes, including NUL separators. `process_start_ticks` is field 22 of `/proc/PID/stat`, parsed after its parenthesized process name. For GPU inference set `acceleration` to `cuda`.

The harness hashes supplied local files, checks the actual process identity and command, verifies that its process tree uniquely owns the exact endpoint address, address family, and port, and requires an explicit HTTP loopback IP. Wildcard, IPv4-mapped/ambiguous dual-stack, shared-owner, and duplicate listeners are refused. Bind a reviewed single listener to the exact loopback IP for this qualification; the harness does not change the service for you. It refuses `localhost`, remote URLs, missing provenance, process replacement, stale/future timestamps, and model-ID mismatches. The listener PID/start time, socket inode, executable path, and command hash are pinned and rechecked before/after requests, during resource sampling, and at completion. Configuration, manifest, model/runtime files, sandbox evidence, and source hashes are revalidated before any pass. Operator review and Docker evidence must be no more than 24 hours old.

These checks make provenance auditable; they are not cryptographic proof that a server genuinely used the named weights. A malicious operator or a deliberately forged manifest/report is outside this trust boundary. A fixture endpoint or successful behavioral test alone never produces a real qualification pass. The in-process behavioral evaluator leaves `passed` false unless all independent gates are satisfied by the full CLI.

## Resource limits and evidence

The operator supplies positive limits appropriate to the actual host and existing workloads:

- Maximum sampled aggregate process-tree RSS
- Minimum sampled host `MemAvailable`
- Maximum sampled aggregate CUDA device memory, when CUDA is declared
- Per-decision latency, overall behavior-test duration, and prediction-error tolerance

The harness samples process RSS and host memory every 0.2 seconds. CUDA telemetry uses a bounded, read-only `nvidia-smi` query at most once per second. CUDA totals include other workloads, making the admission check conservative. Missing, nonnumeric, or `N/A` GPU memory is recorded as unknown and fails qualification, never as zero. On a unified-memory host where this telemetry is unsupported, the memory gate remains open until a separately reviewed actual-host measurement path is implemented; do not substitute invented numbers or declare CPU acceleration for GPU inference.

These are sampled stop/admission limits, not kernel-enforced memory limits for the existing model service. Sampling can miss transient peaks. A threshold breach latches failure and stops further harness work after the current bounded operation; the harness does not kill or reconfigure an existing service. Model startup/cold-load peaks are outside this warm-service test. Record and separately review startup memory before deployment.

The report records all runtime Python source hashes, the qualifier source hashes, raw and effective configuration, model identification, attested/local-file provenance, Docker daemon/image identity, per-call latency, sampled memory, detailed tool decisions/results, measured experiment IDs, artifact hashes, and a retained fresh state directory. Unknown values remain null. It always records `physical_hardware_verified=false`. Previous reports are never overwritten.

## Operator command

After reviewing the actual host and choosing real budgets, run from the project as a non-root Linux user:

```bash
python3 scripts/qualify_sandbox.py \
  --image "$PLAYGROUND_SANDBOX_IMAGE" \
  --report state/qualification/sandbox-UNIQUE.json

python3 scripts/qualify_local_model.py \
  --config configs/research.json \
  --model "$ACTUAL_MODEL_ID" \
  --image "$PLAYGROUND_SANDBOX_IMAGE" \
  --sandbox-report state/qualification/sandbox-UNIQUE.json \
  --provenance /approved/project/model-provenance.json \
  --max-process-rss-bytes "$REVIEWED_RSS_LIMIT" \
  --min-host-available-bytes "$REVIEWED_HOST_RESERVE" \
  --report state/qualification/local-model-UNIQUE.json
```

For CUDA, additionally provide `--max-device-memory-bytes "$REVIEWED_DEVICE_LIMIT"`. The model endpoint comes from the simulation config. The model ID is an explicit override and is recorded in the effective config. Defaults are 60 seconds per decision, 600 seconds for the behavior run, and 0.03 m-equivalent maximum prediction error. Preflight and final model-file hashing are outside the behavior deadline and may take time for large weights. No behavior-only CLI or mock endpoint override is provided.

Both commands must finish successfully. Keep the report, retained state, model manifest, and matching source/config together. A local-model report does not supersede host admission, Docker qualification, recovery/backup acceptance, or physical commissioning. Requalify after relevant source, configuration, model, runtime, image, or host changes. Existing launchers do not yet automatically consume this local-model report; operator acceptance is still required.

## Docker audit findings

The static review found two concrete qualifier gaps: ambient `DOCKER_CONTEXT` could override a separately inspected `DOCKER_HOST`, and any `SandboxError` could count as a successful malicious-artifact rejection. The parallel Docker qualifier review is addressing endpoint pinning and artifact-specific rejection checks. Read its final tests/report before treating those repairs as verified.

The existing paused-container `docker cp` export from tmpfs remains an actual-host compatibility question. The first real workspace roundtrip must pass; mocked tar output cannot establish this. Do not redesign the export path or claim persistence without daemon evidence. The local-model harness checks the current Docker daemon/image against the supplied report, rejects context overrides, and pins its sandbox commands to one explicitly resolved local Unix-socket endpoint.

## Cloud regression evidence

`tests/test_local_model_qualification.py` covers the complete two-generation scenario with an actual local HTTP client and reviewed sandbox fixtures, rejection without recovery, wrong-tool refusal before code execution, independent qualification gates, missing/fixture provenance, stale/future timestamps, process replacement, exact IP/address-family ownership, wildcard refusal, descendant listener replacement under a live parent, final input/file rehashing, unknown GPU memory, and preservation of prior reports. The socket-identity regressions use actual local sockets and child processes containing only reviewed inert fixture code. These tests produce no accepted real-runtime qualification report.
