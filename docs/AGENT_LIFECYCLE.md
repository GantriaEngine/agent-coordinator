# Agent lifecycle Foundation 2 (development)

Agent lifecycle control may cause an authorized agent to become active, but it
MUST NOT grant that agent capabilities or authority not already authorized
locally by the endpoint and workflow. Natural-language communication is
informational, not an execution authority.

This revision is `0.2.0.dev0`, not a released or generally qualified service.
Lifecycle request/reply version 1 is separate from unchanged control protocol v1.
Manual endpoints need no lifecycle module, daemon, Codex credential or ticket.

## Three planes

1. Lifecycle: local admission, presence, one fixed Codex start, status and stop.
2. Control: authenticated fresh assignment pull, registration, barrier, typed
   capability transitions, results and acknowledged cleanup.
3. Capability: installed local catalog and its existing approval/cleanup policy.

Presence and Codex completion are not registration or workflow success. The
bootstrap must obtain REGISTERED and complete control-plane cleanup. The host's
result remains authoritative. A compromised presence publisher cannot command
peers, select capabilities, alter workflow transitions or answer approvals.

## Official integration decision (2026-09-27)

Research checked current official documentation and installed CLI help:

| Interface | Supported surface | Decision |
| --- | --- | --- |
| [Non-interactive CLI](https://learn.chatgpt.com/docs/non-interactive-mode) | `codex exec --json`, explicit `exec resume SESSION_ID` | Canonical local adapter; standard-library Python process supervision |
| [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) | TypeScript start/resume threads; Python local app-server client with pinned runtime | Supported alternative, not added as another adapter |
| [App-server](https://learn.chatgpt.com/docs/app-server) | stdio JSON-RPC initialization, thread/start, thread/resume, turn/start, notifications, turn/interrupt, approval requests | More machinery than the fixed bootstrap requires; never exposed remotely |
| [Self-hosted executor](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted) | Agents API harness with local `codex exec-server`, outbound connection and restricted environment key | Requires separate application/environment provisioning; not used |
| MCP exposure | Current SDK docs say the old `codex mcp-server` was removed; use app-server | No invented MCP lifecycle server |

Installed versions observed: controlling PC `codex-cli 0.155.0-alpha.16`;
dockerbox `codex-cli 0.158.0-alpha.2`. Each installation must pin its exact
`--version` output locally; mismatches reject before agent start. The adapter uses
`exec --json --color never`, a locally selected `--profile`, and fixed stdin.
Only `thread.started.thread_id`, `turn.completed`, `turn.failed` and `error`
events are interpreted. Other model/tool text is discarded, never executed.

The low-level adapter supports explicit continuation of its own completed local
UUID session using `exec resume`; the network endpoint deliberately does not
expose resume. Reusing conversation context across workflow runs needs additional
qualification. No `--last`, display title, GUI automation or arbitrary prompt API.

## Local setup and bootstrap

Install the package, schema and catalog on each endpoint. Select an approved
repository, installed Codex executable/version and a locally approved Codex
profile. `existing-local-policy` explicitly inherits the user's existing Codex
configuration without overriding sandbox, approval, model or authentication
settings. Broad existing local permissions are an endpoint trust decision, not
authority supplied by a wake request. Prefer a restricted dedicated profile.
Ensure the installed Python runtime is on the agent's PATH.

Install [the small durable bootstrap](bootstrap/AGENT_COORDINATOR.md) as
`AGENT_COORDINATOR.md` in that repository. It invokes only the installed fixed
`python -m agent_coordinator.lifecycle.bootstrap` entry point. The model does not
interpret transitions, provision adapters or execute peer diagnostics.

`Endpoint.InstallTicket` is an endpoint-local administrative API, absent from the
wire. A ticket contains a notice, preconfigured control endpoint/bootstrap
identity, approved workflow file, installed catalog module and role. Catalog code
is trusted installed code; it is never downloaded or selected through wire text.
Admission validates the workflow and required local capabilities. Alternatively,
a durable endpoint-local `Policy` pins EndpointId, PeerIp, CoordinatorHost, Port,
WorkflowFile, WorkflowHash, CatalogModule and Role. An authenticated wake can
then materialize a fresh local ticket under that policy without per-run courier.
None of those authority fields is accepted in the wake. Bootstrap credentials
are independently derived with HMAC over the versioned notice/run/generation
using the endpoint-local lifecycle key, with a dedicated domain separator.
The coordinator derives the same scoped bootstrap for existing assignment pull;
the agent receives only that scoped credential, never the lifecycle key.
The bootstrap
validates again and the existing Join independently validates schema hash,
parameters, role/capability advertisement and deadlines.

A wake notice has exactly Version, EndpointId, RunId, Generation and
ExpiresUnixMs. No summary, shell, executable, scripts, security settings, API key
or capabilities can be attached. The notice must exactly match a locally admitted
ticket. A fresh UUID generation is also the v1 bootstrap identity; use a new random
bootstrap token for every generation. The fresh assignment's workflow RunId must
match the notice and its Role must match local admission. Starting is not joining.

The policy admits new notices only to its one installed workflow and role. Schema
or role changes require local owner configuration. There is no remote ticket
installation or policy change operation. Initial installation may use explicit
local tickets instead of durable policy.

Protect `.lifecycle` against unrelated writers and backups; it contains local
bootstrap secrets. It is ignored by Git. `current.json` is transient; `used.json`
is a durable consumed-generation ledger. Keep it across daemon restarts. Never
reset it while old credentials or tickets could remain usable. Rotation requires
endpoint-owner revocation and a new protected installation identity.

The bounded daemon is locally launched with `python -m
agent_coordinator.lifecycle.daemon --config <local-file>`. Its strict local config
has EndpointId, Repository, Executable, Profile, Version, Token, Port,
AgentSeconds, StartupSeconds, DaemonSeconds, Tickets and Policy (null or the fixed
local policy object). It binds loopback only.
For a two-PC deployment, establish an approved SSH local forward for lifecycle
and reverse forward for control. This keeps existing unencrypted v1 control
traffic inside SSH. The library can use verified TLS for a direct LAN lifecycle
connection; the shipping daemon does not manage certificates or expose a LAN port.
No service installation, elevation or machine-wide changes are required.

## Presence and bounds

Presence fields are endpoint ID, optional actual Codex thread UUID, independent
generation UUID, workflow run UUID, observation timestamp, lease remaining,
status, typed reason and at most 512 UTF-8 bytes of inert detail.

Statuses: OFFLINE, IDLE, PREPARING, WAITING_FOR_ASSIGNMENT, WAITING_FOR_PEER,
RUNNING, NEEDS_USER and FAILED. An owned live local process observation renews
the five-second presence lease. Expiry reports OFFLINE; a title or PID lookup
alone never proves agent identity. WAITING_FOR_PEER is published after the
validated REGISTERED acknowledgement. IDLE means the fixed bootstrap returned
success after cleanup; it does not replace host acceptance.

Duplicate start is idempotent for the current generation, including failure and
NEEDS_USER. Automatic retry count is zero. One agent may run at a time; stop must
finish before replacement. Consumed generations cannot restart, including after
daemon restart or launch failure. At most 64 generations per durable ledger,
64 pending local tickets, 256 status updates per generation, 1024 authenticated
requests per daemon, 4096-byte lifecycle frames, 10-second request freshness,
one-second server read timeout, and at most 600 seconds per daemon/agent. Startup
is separately bounded to 1..120 seconds. Output is drained and capped at 2 MiB
with a 64 KiB individual event limit. The existing workflow bounds still apply.

The status tunnel is a bounded local JSON observation file and read-only network
presence snapshots. Unknown fields, stale generations and oversized files fail
closed. Diagnostic text is inert. There is no peer ReportStatus, SendPrompt,
Approve, StartCapability or START operation on the lifecycle channel.

NEEDS_USER reasons include UAC_APPROVAL, PHYSICAL_INTERVENTION,
SECURITY_DECISION, MISSING_CAPABILITY and AUTH_REQUIRED. The fixed local reporter
accepts a typed reason only. Reporting never grants approval; the endpoint's
existing mechanism remains authoritative. CLI errors are reported as failure,
not guessed to be an approval request from model prose.

## Credentials, stop and failure

Codex auth stays in each endpoint's own user profile. The coordinator never reads,
copies or forwards OpenAI credentials. Local login/status is used only to check
availability. Rotate/revoke local ChatGPT auth with the normal Codex login/logout
mechanism, or restrict and rotate endpoint-local API credentials if using those.
Agents API application and restricted environment keys are not required here.
Use an independent random 256-bit lifecycle HMAC key; rotate it out of band.
HMAC alone is not encryption: use loopback/SSH or verified TLS, never bare LAN.

The daemon observes the owned process continuously, even without coordinator
polling. Stop kills the owned tree: Windows Job Object with kill-on-job-close;
POSIX process group. It also removes the current local ticket and status. A
normal Codex exit still terminates leftover descendants. Failure to assign the
Windows job fails the launch. The initial Popen-to-job assignment window and
host shutdown recovery need further hardening before a production service.
Endpoint/user compromise can bypass same-user filesystem/process isolation;
the prototype does not claim protection against its own administrator.

Startup timeout, malformed status, excessive output or operation failure leads
to failure/stop, not another prompt or automatic agent loop. The coordinator
still aborts and requests normal local capability cleanup on missing/failing
peers. Windows/Linux deterministic tests qualify infrastructure behavior; they
do not substitute for actual Codex or protected installation qualification.
