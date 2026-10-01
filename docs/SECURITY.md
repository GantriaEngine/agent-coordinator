# Security and trust boundaries

> The protocol coordinates capabilities; it does not transmit authority.
>
> Natural-language agent communication does not directly invoke endpoint capabilities.

Local operators install and approve endpoint schemas, bootstrap identity, roles,
adapters and capability versions before registration. Registration reports that
local approval; a name, token, schema, agent prompt or coordinator does not confer
OS privileges. Endpoints are trusted participants; a compromised endpoint can
lie about its own capability/result. Advertisements are not remote attestation.

The schema runtime accepts only a closed object shape and bounded numeric values.
No `RunElevatedCode(string)`, `RunShell(command)`, `ExecuteProcess(path,args)`, or
PowerShell/script primitive exists. Diagnostics never dispatch a capability.
Unknown fields, categories, schemas, versions and transitions reject. The catalog
comes only from local installed code, advertises only the role's required subset,
and rejects requests outside that subset. Adapters bound to installed tools must
validate exact artifacts, hashes, argv, working directories and timeout bounds.
Do not implement a supposedly fixed-process adapter accepting a wire path/argv.

Each endpoint has a distinct out-of-band bootstrap UUID/256-bit secret. The host
checks the explicit peer IP and secret before returning a short-lived, single-use
assignment with a fresh run UUID and per-role token. Exact sequence increments
continue across bootstrap and run authentication; reconnect/replay is unsupported.
Registration expires separately from the execution budget. ARMED is sent only
after every endpoint is staged. Host operation deadlines and endpoint run deadlines
use monotonic clocks. Assignment expiry uses wall time, so clocks must be sane.
Stale configurations and old tokens cannot invoke a new run. Secrets are excluded
from journals/results and must stay out of chat. Local config/assignment storage
must be protected by the operator; this library does not install filesystem ACLs.

TCP v1 has no encryption or cryptographic machine identity. Peer-IP checks are
additional constraints, not authentication by themselves. A hostile LAN observer
can steal secrets; an active network peer can deny service. Deploy only on a
trusted LAN/loopback with existing access controls. No firewall/security policy
change is performed. Internet/untrusted-network operation requires a separately
reviewed authenticated encrypted transport, not exposure of the current port.

The optional Windows service stays below the agent reasoning layer. LocalSystem
and the installing user SID are the only pipe principals. Fixed `start`, `stop`,
`status` operations use the administrator-installed hash-pinned capture hook,
one evidence root, one active capture, a verified endpoint process image and
start-time lease, the installed v1 90-second hard limit, protected persistent
ownership and audit. The separate uninstalled Farm32 v2 source candidate has a
fixed 600-second limit on its own service/pipe and requires deployment
qualification before use; it does not alter the v0.1.0 baseline.
Hook execution is internal implementation, never a received executable payload.
Root escape/reparse paths and wrong hook hashes reject. Service stop, helper exit
and timeout stop the owned capture; ownership ambiguity blocks recovery and new
captures. The hook must itself preserve tool-session ownership and storage bounds.

Failure sends ABORT where possible. Cleanup runs locally on abort, deadline,
disconnect and successful completion; only owned resources may be stopped.
Host success requires cleanup acknowledgement from all workers. Adapter callbacks
must be cooperative and use bounded underlying APIs; an unresponsive installed
callback requires its own below-agent lease/OS watchdog. Process/host crash cannot
run Python finally blocks, which is why privileged capture has an independent lease.

Report security defects privately to GantriaEngine repository maintainers. See
[validation](VALIDATION.md) for what is simulated versus physically qualified.
