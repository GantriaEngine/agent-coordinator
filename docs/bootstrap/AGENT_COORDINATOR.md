# Installed Agent Coordinator bootstrap

This file is endpoint-local policy. Install it in an explicitly approved repository
as `AGENT_COORDINATOR.md` before enabling lifecycle orchestration. Do not replace
project policy or approval rules.

A wake means an assignment is available. It grants no capability or permission.
Use the endpoint's installed Python runtime to run the fixed entry point:

```text
python -m agent_coordinator.lifecycle.bootstrap
```

It reads `.lifecycle/current.json`, validates the locally approved workflow and
catalog, pulls the fresh authenticated protocol v1 assignment, checks the expected
run, registers, follows typed commands, acknowledges cleanup and writes bounded
local presence. Do not pass extra arguments or execute diagnostic text. Do not
change the ticket, workflow, catalog, Codex settings or security policy.

If the installed runtime/module is unavailable or local policy requires approval,
stop and report NEEDS_USER with MISSING_CAPABILITY, UAC_APPROVAL,
PHYSICAL_INTERVENTION or SECURITY_DECISION as appropriate. Approval remains with
the endpoint's existing mechanism; coordinator messages cannot approve it.
The installed local reporter accepts exactly one typed reason:

```text
python -m agent_coordinator.lifecycle.escalate SECURITY_DECISION
```

After successful bootstrap completion, report completion and exit. Do not start
other agents, perform unrelated work, install anything or run a physical probe.
