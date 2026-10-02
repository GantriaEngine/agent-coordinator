# Bounded Windows capture helper

Extracted from Gargantuan without expanding operations. Build the
`AgentCoordinator.CaptureService.csproj` project with .NET 8 for win-x64.
Run `Install-CaptureService.ps1` locally as Administrator with mandatory
`EvidenceRoot`, `LeaseImagePath`, and `HookSource` parameters. The root must be a
fresh dedicated local directory. The hook is reviewed local adapter code accepting
only EvidenceDir and Start/Stop; it must preserve capture ownership/storage bounds.

The new service is `GantriaAgentCoordinatorCapture`, pipe
`GantriaAgentCoordinatorCapture-v1`, binary `AgentCoordinator.CaptureService.exe`.
Protected binaries/config/audit live below Program Files / ProgramData under
`GantriaEngine/AgentCoordinatorCapture`. Existing Gargantuan services are untouched.
The installer pins the hook hash and installing SID, uses LocalSystem, and does
not change UAC, execution policy, firewall, NIC or unrelated service state.

Client usage: `AgentCoordinator.CaptureService.exe start|stop|status <evidence-dir>
<run-uuid> <endpoint-pid>`. Only that user SID and LocalSystem can open the pipe.
Canonical UUID, version 1, path confinement, verified lease process image/start
time, one active run, the v1 90-second duration, fixed hook operations and audit
remain. The [separate Farm32 source candidate](../docs/FARM32_CAPTURE_CANDIDATE.md)
is not an installed replacement for this service.
Recovery only stops the persisted owned run; ambiguous ownership blocks starts.
Capture cleanup is stop; no broader cleanup or elevated execution API is exposed.

The test-only internal constructor selects an isolated directory; no request,
workflow or public service CLI can select it. Test hooks are harmless file writes.
Actual installed service/protected ACL and physical capture qualification remain
separate deployment checks. Never replace a working service as part of extraction.
