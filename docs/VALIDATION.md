# Validation scope

The consuming Gargantuan task's follow-up regressions are mirrored for the shared
legacy endpoint state machine: delayed FINALIZE after a completed server result
is accepted, FINALIZE while still STAGED rejects, and a live-probe abort cleans up.
Its Mellanox miniport plus TCP/IPv4 capture-layer selection, packet-direction
acceptance and idempotent local adapter cleanup remain project-owned. Changes
to those physical assumptions do not belong in the generic capability service.

Python suites preserve the extracted legacy protocol cases and add strict schema,
capability, framing/auth/replay, assignment expiry and two-endpoint simulation
coverage. Original project-specific artifact/stage/hook/child ownership tests stay
in Gargantuan and run against the extracted library after bootstrap.

Windows .NET tests use the actual service implementation with a harmless fixed
capture hook in an isolated directory. They test start/stop/status, unsupported
operations, root escape, hook hash mismatch, double-start, helper PID exit,
90-second duration, service-stop cleanup and OS pipe denial for an excluded SID.
These are local simulations, not physical capture or installed-service acceptance.
Protected installation ACLs, crash recovery under LocalSystem, physical capture
artifacts and both-PC adoption remain deployment qualification work.

CI runs Python on Windows/Linux, simulation, wheel package, docs/schema/whitespace
checks; Windows also publishes the helper and runs its mock-hook tests. CI source
and commit status are the authority; do not infer pass merely from workflow presence.
No engine builds or unrelated engine qualification are part of this extraction.
