# Local capabilities

`Catalog` maps versioned symbolic IDs to installed local handlers and a mandatory
local cleanup function. JSON cannot construct a catalog. Advertisement exposes
exactly the role-required installed subset; unknown/missing versions reject.
`Invoke` checks the advertised subset, copies bounded parameters, supplies an
operation deadline and validates typed Success/evidence metadata. The host also
checks advertisements before issuing a transition. Endpoint claims are trusted
reports, not hardware attestation.

Handlers are `(Parameters, Operation) -> {Success: bool, Evidence: [...]}`.
`Operation.Check()` rejects after the monotonic deadline. Use bounded OS APIs and
an independent local watchdog/lease for resources that can outlive the callback.
The coordinator aborts on timeout; it cannot forcibly interrupt local Python code.
Capture service start/stop/status remain narrow locally approved capabilities.

A consumer may install `capture.pktmon.v1`, `capture.dumpcap.v1`, `artifact.sha256.v1`
or a probe-specific ID, but those names do not grant authority or implementation.
Do not expose `fixed-process.v1` unless its adapter binds a known installed artifact
and fixed argv/hash locally. Never accept a process path, script, shell command,
arbitrary CLI arguments or elevated code from the coordinator. Gargantuan's exact
probe and capture-hook checks remain in its own adapter.
# Lifecycle does not create capabilities

Starting Codex exposes only the installed endpoint environment. A local ticket
selects an already approved workflow/role/catalog; the wire cannot install or
name a new handler. The bootstrap and Join independently check the locally
required capability set. NEEDS_USER cannot grant a missing capability or privilege.
The harmless `lifecycle.synthetic` catalog returns empty evidence and performs no
capture, process launch, installation or privileged operation. It is qualification
code, not a general administration interface. See [lifecycle](AGENT_LIFECYCLE.md).
