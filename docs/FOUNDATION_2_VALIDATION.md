# Foundation 2 validation and readiness

Verdict: **NEEDS MORE ARCHITECTURE WORK**. The feature remains `0.2.0.dev0` on
`codex/foundation-2-lifecycle`; no release or tag is published. v0.1.0, canonical
main and consumer deployment pins remain unchanged.

Starting canonical main: `35d6ce97e9cb255e8ccfb27b3a6d3f97543a5c45`.
Initial implementation: `f026acbe8b5828a21e792a3b0c5b4acb1a9b8b8b`.
The follow-up source adds typed final escalation for unavailable bootstrap tools.

## Infrastructure validation

Local Python passes presence expiry/replacement, run/generation isolation,
strict wake schema, durable admission, authentication/replay, idempotent starts,
startup timeout, missing/unsupported local capability, wrong expected run/role,
stale/oversized status, inert diagnostics, output bounds, owned process-tree
cleanup, typed escalation, and offline wake through normal registration/barrier/
LIVE/RESULT/cleanup. The original 36 v1 tests remain passing. Windows helper
fixed-operation, path/hook confinement, leases, 90-second duration, cleanup and
pipe SID denial tests pass using their existing harmless mock hook.

The [initial hosted run](https://github.com/GantriaEngine/agent-coordinator/actions/runs/36349459790)
passed on Windows and Linux at the implementation commit: Python, simulation,
wheel, links/JSON/whitespace, and Windows helper publish/security tests. Final
follow-up source must have its own hosted validation; consult the feature branch
checks rather than treating the initial run as final-source evidence.

The wheel includes the lifecycle modules, installed final output schema and
unchanged v1 envelope schema. No privileged service, physical capture or probe
was installed or operated during lifecycle qualification.

## Actual two-PC synthetic attempt (2026-09-27)

Run `9adfcfb3-a5d8-4120-baaa-1b86d22922c0` tested the initial implementation.
Endpoint agents were absent before wake. A coordinator on the main PC sent typed
wakes to a local CLIENT and dockerbox SERVER over loopback/SSH-forwarded lifecycle
connections. A reverse SSH forward carried existing v1 control traffic. Durable
local policies pinned the same harmless schema/catalog and their respective role.
Duplicate wakes did not spawn another agent. There was no human message relay.

Both actual Codex sessions started using each PC's own existing local ChatGPT
authentication/configuration:

| Endpoint | Installed version | Session | Observed result |
| --- | --- | --- | --- |
| Main PC CLIENT | `0.155.0-alpha.16` | `01a0e4a6-1716-7bb0-a261-7a92afc4de8f` | Fresh pull, REGISTERED, STAGED, WAITING_FOR_PEER; abort and local cleanup |
| dockerbox SERVER | `0.158.0-alpha.2` | `01a0e4a6-160f-7d30-ba6d-e26a3b817205` | Session active; tool runtime failed before helper registration |

Worker tool diagnostics reported:

```text
windows sandbox failed: timed out after 15000ms connecting runner pipe-in
```

The worker session completed without registering. The supervisor reported failure;
the coordinator aborted with `Success: false` and an empty Results list. Neither
synthetic capability ran. The client acknowledged its local failure cleanup. This
does **not** qualify the success path, and simulations do not overturn that result.

The one attempt admitted at most one agent per endpoint, 75-second startup,
120-second agent, 90-second registration, 10-second execution and 240-second
daemon budgets. Local ChatGPT quota was used; no general API key was transported
and no monetary cost claim is made. No automatic retry or policy relaxation was
performed. Both owned agents stopped, transient current tickets/status disappeared,
both bounded daemons exited, and the task-owned SSH tunnel was stopped after PID
and start-time verification. Independent test lifecycle keys were retired.

The failure also showed that an unavailable shell tool can prevent the local
typed escalation module from running. The follow-up adds an installed, closed
Codex final JSON status schema and inert NEEDS_USER handling independent of that
tool. Its deterministic regression verifies that escalation neither registers nor
invokes a capability. That follow-up is not represented as a successful two-PC
rerun; the worker's underlying runtime remains unqualified.

## Remaining work and exact next task

Fix and qualify dockerbox's installed Codex Windows sandbox/tool execution through
its supported endpoint-local setup, preserving approval and sandbox protections.
Then rerun the same single bounded two-PC synthetic workflow on final source,
requiring both REGISTERED, LIVE, RESULT, CLEANED, actual Codex completion and IDLE.
Do not replace this with bypass flags or a physical qualification.

Before broader deployment, qualify a dedicated restricted endpoint profile and
credential/configuration isolation between lifecycle controller and agent; address
the Windows Popen-to-job assignment window and POSIX abrupt-controller-death
recovery. Network Resume remains intentionally unavailable until cross-run session
context isolation is qualified. The current bounded loopback daemon is an
experimental implementation under test, not a protected production service.
