# Foundation 2 validation and readiness

Verdict: **READY FOR EXPERIMENTAL USE** on logged-in, locally approved Windows
endpoints. The feature remains `0.2.0.dev0` on `codex/foundation-2-lifecycle`;
no release or tag is published. v0.1.0, canonical
main and consumer deployment pins remain unchanged.

Starting canonical main: `35d6ce97e9cb255e8ccfb27b3a6d3f97543a5c45`.
Initial implementation: `f026acbe8b5828a21e792a3b0c5b4acb1a9b8b8b`.
The follow-up source `03676fe32378d4150edb9e62c5691699a8b780ee` adds typed
final escalation for unavailable bootstrap tools. The final qualification did not
change the lifecycle adapter or protocol.

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
and [follow-up source run](https://github.com/GantriaEngine/agent-coordinator/actions/runs/36350220910)
passed on Windows and Linux: Python, simulation, wheel, links/JSON/whitespace,
and Windows helper publish/security tests. The final qualification documentation
commit requires its own passing PR checks before the PR can leave draft.

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
rerun; the worker's underlying runtime was unqualified at that point.

## Dockerbox sandbox diagnosis and supported correction

Both PCs use Windows 11 build 26200 and Codex's preferred native
`[windows] sandbox = "elevated"` configuration. Installed versions stayed
`codex-cli 0.155.0-alpha.16` on main and `codex-cli 0.158.0-alpha.2` on
dockerbox. A full copy of the main PC's official Codex CLI bundle ran from a
task-owned temporary directory on dockerbox; its `0.155.0-alpha.16` sandbox
command hit the same 15-second runner pipe timeout as installed `0.158.0-alpha.2`
under SSH. Version mismatch alone was not causal, so no installation, deployment
pin or adapter workaround was changed.

The exact same installed dockerbox binary and public `codex sandbox -P :read-only`
command failed under the high-integrity SSH/network token and an S4U/batch
scheduled task, but succeeded immediately under a **Limited, interactive,
medium-integrity** scheduled task for `HOSTPC\host`. The Administrators group
was deny-only in that successful token. This isolates the pipe blocker to the
launch context (session/logon token); the specific Windows runner handshake step
is not exposed by the public diagnostic. A logged-in interactive session is
therefore required for this experimental launch procedure. The task action used
hidden `pythonw.exe`, a three-to-five-minute execution limit, and only the
endpoint user's existing local profile; it did not make Codex a SYSTEM process.
The pre-existing Codex sandbox service remains the normal SYSTEM-owned service
for the preferred elevated sandbox. No firewall, Defender, pipe ACL, sandbox or
private-desktop protection was disabled.

The explicit endpoint-owned `foundation-2-endpoint` Codex profile uses
`sandbox_mode = "workspace-write"`, `approval_policy = "never"`, and
`[sandbox_workspace_write] network_access = false`, layered over the existing
`windows.sandbox = "elevated"`. The profile file lives in each endpoint user's
own `.codex` directory, outside the approved repository. On dockerbox, a
medium-integrity launch initially could not let Codex install its write/deny
ACLs because this task-owned checkout root and `.git` were owned by
`BUILTIN\Administrators`. Changing only those two owners to `HOSTPC\host`
allowed Codex's own sandbox setup to complete. The pre-existing `.lifecycle`
directory had not inherited the new sandbox write ACE; resetting inheritance on
that task-owned directory allowed a sandboxed create/delete probe and the fixed
bootstrap status write. No generic pipe ACL was modified. A separate sandboxed
attempt to create a harmless file in `HOSTPC\host`'s `.codex` directory failed
with `PermissionError`, so the agent could not edit the local profile that
controls its next launch. The selected policy bounds command writes and outbound
network access; this test did not inspect or read another user's profile.

Direct dockerbox `codex exec --json --color never --output-schema ... -` under
the limited interactive token completed both with the inherited read-only
policy and the named workspace profile. Each emitted `thread.started`, a
successful sandboxed `Get-Location` command, a typed `IDLE/NONE` final message,
`turn.completed`, exit code 0, and no remaining owned child. The lifecycle
adapter used the same executable, JSON/schema/stdin form, approved working
directory and named profile, with its normal bounded Windows Job Object cleanup.
Its output parser never treated the model's IDLE message alone as workflow
success.

## Final two-PC synthetic qualification (2026-09-27)

Run `4502c432-5648-4297-9083-e03e728c5241` used final lifecycle source
`03676fe`, fresh lifecycle keys/run/generations and the approved synthetic
catalog. Main's CLIENT ran Codex `0.155.0-alpha.16` as `aiden`; dockerbox's
SERVER ran Codex `0.158.0-alpha.2` as limited interactive `host`. Their actual
Codex thread IDs were `01a0e4f8-3fdb-7cd0-aa19-59e35b8051da` and
`01a0e4f8-3ee6-75d3-851a-d77bab463dfc` respectively. Each used its local
ChatGPT login; no OpenAI credential or general API key crossed the SSH tunnel.

The host journal records both fresh PULL_ASSIGNMENT, REGISTER/REGISTERED and
STAGED pairs, two ARMED messages, SERVER LIVE, CLIENT RESULT, two COMPLETE and
two successful CLEANED acknowledgements. Both typed results have `Success: true`;
the host result has `Success: true`; both actual Codex sessions reached
local IDLE before stop. The repeated wake did not start a second agent. The
coordinator, lifecycle clients and existing v1 control connection ran over
loopback with bounded SSH forwarding; no human message relay occurred after
start. The journal/result SHA-256 values in the local evidence manifest are
`E5C5E899E9274F5C16B30E26199EC1B6E04DA8E170D0C7D9C7346F4C36897EFE`
and `3E2AD35CD0B7075B809A436C0AF908260E2302840AAC06E2E2B57EE6E9B305F9`.
The redacted qualification report and host evidence are under
`C:\Sandbox\Codex\Artifacts\agent-coordinator-foundation-2-followup\evidence\4502c432-5648-4297-9083-e03e728c5241` on main.
Neither endpoint retained a current ticket or status file. Both owned CLI
sessions exited; the test-owned daemons, SSH tunnel and task were stopped after
the result. No physical probe, capture or Gargantuan path was used.

Two additional real Codex child crash probes used fresh generations: one child
exited before `thread.started`/registration; the other was killed after
`thread.started` and PREPARING presence but before completion. After the normal
250-ms supervision interval, both reported `FAILED / AGENT_FAILED`; duplicate
wakes returned FAILED without respawn. Their child processes exited, current
tickets were removed, and durable used-generation ledgers rejected the same
wake even after endpoint reconstruction. Existing Windows Job Object tests also
cover descendants surviving a parent exit. No orphan from either probe remained.

## Scope and next task

This qualifies experimental lifecycle control for these two logged-in, locally
approved Windows endpoints and this bounded synthetic workflow. Initial Codex
elevated-sandbox setup and correction of task-owned workspace ownership needed
local administrator maintenance; **subsequent lifecycle starts ran without an
administrator token**. The profile is endpoint-selected, and strict wake schema
tests reject peer-supplied executable, prompt, capability and security fields.
It cannot be widened by an Agent Coordinator wake. A separate populated user
profile was not used for a cross-user access probe, so this run does not claim
full multi-user host isolation.

For production service use, harden the Popen-to-Job assignment window, abrupt
supervisor-death recovery (including POSIX), protected local key/config storage,
and a supported interactive-user lifecycle service model that does not depend on
an already logged-in desktop. Network Resume remains intentionally unavailable
until cross-run session-context isolation is qualified. Keep PR #1 draft until
its final Windows/Linux CI passes and a maintainer reviews this experimental
scope; do not merge or publish a release automatically. The exact next task is
that production-hardening review, not a Foundation 3L physical qualification.
