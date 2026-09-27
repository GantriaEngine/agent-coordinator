# Agent Coordinator

Bounded multi-agent coordination, capability-based endpoint execution, and
deterministic physical/distributed test workflows. Python 3.12+ standard library
at runtime; optional .NET 8 Windows capture service. MPL-2.0, matching Gargantuan
and GantriaEngine's telemetry infrastructure.

> The protocol coordinates capabilities; it does not transmit authority.
>
> Natural-language agent communication does not directly invoke endpoint capabilities.
>
> Agent lifecycle control may cause an authorized agent to become active, but it
> MUST NOT grant that agent capabilities or authority not already authorized
> locally by the endpoint and workflow.

A host agent instantiates an installed workflow. Worker agents pull fresh role
assignments, register locally approved capabilities, wait at a barrier, execute
typed transitions, return compact evidence metadata, and clean up. No chat timing,
source/binary courier, remote shell, generic process launcher, or elevated code API.

## Quick start

```text
python examples/simulation.py
python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir dist
dotnet publish privileged-helper/AgentCoordinator.CaptureService.csproj -c Release -r win-x64 --self-contained false -m:2 -o privileged-helper/publish
dotnet run --project tests/helper/HelperTests.csproj -c Release
```

The simulation runs two local helpers and a host, using fresh tokens and a tiny
non-Gargantuan [workflow](examples/readiness.json). It starts no capture/probe.
The helper tests use a harmless locally installed mock hook and exercise the real
90-second duration limit; they do not install a service or change NIC policy.

## Architecture and usage

Foundation 2 development adds optional [Codex lifecycle and presence](docs/AGENT_LIFECYCLE.md).
It uses fixed local bootstrap, endpoint-admitted tickets and authenticated bounded
wake/status/stop. Lifecycle version 1 is separate; control protocol v1 and the
manual workflow remain unchanged. This feature revision is `0.2.0.dev0`, not a
new release. Durable local policy can admit fresh notices without per-run files;
cross-run resume remains a qualification limit.

- `agent_coordinator.transport`: extracted bounded TCP/JSON envelope, journal,
  manifest and hash generation. Transport version 1 is unchanged.
- `agent_coordinator.legacy`: existing CLIENT/SERVER readiness barrier and endpoint
  lifecycle. Local validator and probe/capture adapter are mandatory arguments.
  Its historical readiness result classification remains compatibility metadata;
  it is not a policy or acceptance decision owned by this project.
- `agent_coordinator.workflow`: strict schema, role transitions, numeric parameter
  bounds, versioned symbolic capabilities, locally installed callable catalog.
- `agent_coordinator.control`: authenticated assignment pull, registration,
  explicit barrier, ordered execution, result and acknowledged cleanup.
- `privileged-helper`: SID-restricted capture service. The hook is selected and
  hash-pinned locally by the administrator, not by a workflow or coordinator.

Host: load an approved `Workflow`, create `Assignments` with locally configured
endpoint bootstrap identities and bounded parameters, then call `Host`. Joining
agent: read local `Config`, load the approved schema mapping and a `Catalog` from
installed adapter code, then call `Join`. The runnable simulation demonstrates
these APIs. Worker installation is a one-time local authority decision; joining
does not need a large prompt or a fresh per-run file transfer.

Schemas and capability handlers are installed separately. The wire cannot install
them. Required versions and canonical schema hashes must match exactly. Evidence
files remain local; only metadata passes through the control connection.

## Scope and maturity

v0.1.0 is an extracted infrastructure foundation with a tested local simulation,
not a generally qualified unattended remote runner. TCP v1 is unencrypted: use
only a trusted LAN/loopback, never the Internet. There is one endpoint per role,
no reconnect/retry, no bulk evidence transfer, no NAT discovery, no dynamic code
loading, and only bounded ordered workflows with numeric parameters in schema v1.
Locally approved handlers must enforce OS-level operation bounds and check the
provided deadline; Python cannot forcibly cancel an arbitrary blocked callback.
The host enforces its own hard run/operation deadline and aborts peers.

See [protocol](docs/PROTOCOL.md), [security](docs/SECURITY.md),
[workflows](docs/WORKFLOWS.md), [capabilities](docs/CAPABILITIES.md),
[migration](docs/MIGRATION.md), [provenance](PROVENANCE.json), and
[validation](docs/VALIDATION.md). Engine/POOLED_SERVICE and physical qualification
acceptance remain entirely in the consuming project.
