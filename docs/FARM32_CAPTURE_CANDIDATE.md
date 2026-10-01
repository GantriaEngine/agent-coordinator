# Farm32 capture-service candidate

This is a separate, source-qualified candidate for Gargantuan's 32-client,
five-phase Foundation 3L campaign. It is **not installed or physically
qualified**. The installed one/four-client service and hook remain pinned.

`AgentCoordinator.CaptureFarm32Service.csproj` builds a different executable
with a distinct `GantriaAgentCoordinatorCaptureFarm32` Windows service,
`GantriaAgentCoordinatorCaptureFarm32-v2` pipe, ProgramData directory and
request version 2. Its capture lease is a compile-time 600 seconds and its
Start/Stop hook deadline is 60 seconds. Neither is selected by an IPC field.
The pinned v1 build remains 90 seconds with its 30-second Stop/export hook
deadline on its original pipe.
The fixed operations, SID ACL, hash-pinned hook, process-image/start-time lease,
single active run, path/reparse confinement, persistent ownership, audit and
fail-closed recovery are shared code.
The Farm32 CLI bounds a pipe response wait to 135 seconds; the service asks
Windows Service Control Manager for up to 130 seconds of stop-pending time
when a concurrent 60-second hook and a final owned Stop can serialize.

The 600-second cap covers the current farm workflow's legal startup path
(20 + 75 + 20 + 90 = 205 seconds) and role-local 300-second total runtime,
leaving 95 seconds for capture placement and stop initiation. This is an upper
bound, not a target run duration. The host workflow's 540-second execution
timeout must be reconciled separately with when capture starts. A campaign
that exhausts all legal transition/runtime time may not leave enough workflow
time for evidence export; no PASS may be claimed from a timed-out workflow.

The Farm32 hook performs only Packet Monitor start/stop and a nonwrapping ETL
size check inside the privileged 60-second Stop. ETL loss verification and
pcapng export are an **offline** local step after Stop. A bounded supervisor
must run that step with its own deadline, preserve ETL on failure, and reject
lost events, empty export, truncation, cap reach and missing directions.
There is no service operation for arbitrary postprocessing. A 60-second Stop
is not claimed qualified at the 1-GiB ETL cap until measured on the actual
worker with a harmless controlled candidate. Offline export timing likewise
requires measurement; mock tests do not establish it.

The project Farm32 hook uses a single noncircular 1024-MiB ETL with a
960-MiB completeness rejection threshold. Start requires 2560 MiB free on
the evidence volume for ETL, pcapng and 512 MiB headroom; offline Finalize
requires 1536 MiB still free. Files and markers are Farm32-specific and
preexisting artifacts reject without replacement. Worker C: currently lacks
this reserve, so deployment needs an approved dedicated volume and evidence
root. No alternate root is selected by this source change.

Review/stage sequence:

1. Build and test both v1 and Farm32 projects; run mock hook denial tests.
2. Publish Farm32 to a versioned candidate directory. Record hashes for
   executable, all dependencies, hook, installer and endpoint adapter.
3. Verify the designated worker volume has the reserve, the root is dedicated,
   the fixed endpoint runtime image is correct, and no capture is active.
4. Run `Install-CaptureFarm32Service.ps1` only after operator review. It refuses
   an existing service/data/install/evidence path and never replaces v1.
5. Qualify pipe SID denial, hash pinning, helper exit, hard lease, Stop timing,
   full-size offline conversion, lost-event rejection, bidirectional tuples,
   crash recovery and cleanup on the actual worker before a campaign.

Rollback is side-by-side: stop and delete only
`GantriaAgentCoordinatorCaptureFarm32`, preserve its ProgramData audit and run
evidence for review, then remove only its separately recorded install directory
after verifying absolute paths and no active service. The v1 service, hook,
configuration, pipe and evidence root are never replaced or removed. An
ambiguous owned trace must be reconciled by the administrator before rollback;
do not stop a foreign Windows trace.
