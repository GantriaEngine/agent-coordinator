using System.Diagnostics;
using System.IO.Pipes;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;

namespace AgentCoordinator.CaptureService;

internal static class HelperTests
{
    private static object? Invoke(object Target, string Name, params object[] Args) =>
        Target.GetType().GetMethod(Name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(Target, Args);

    private static void Assert(bool Value, string Detail)
    {
        if (!Value) throw new InvalidOperationException(Detail);
    }

    public static async Task<int> Main(string[] Args)
    {
        if (Args.Contains("--lease")) { await Task.Delay(120000); return 0; }
        var Root = Path.Combine(Path.GetTempPath(), "agent-coordinator-helper-" + Guid.NewGuid());
        Directory.CreateDirectory(Root);
        try
        {
            var EvidenceRoot = Path.Combine(Root, "evidence");
            var Evidence = Path.Combine(EvidenceRoot, "run");
            Directory.CreateDirectory(Evidence);
            var Hook = Path.Combine(Root, "capture-mock.ps1");
            const string HookText = "param([string]$EvidenceDir,[string]$Action)\n" +
                "if ($Action -eq 'Stop' -and (Test-Path -LiteralPath (Join-Path $EvidenceDir 'slow-stop.txt'))) { Start-Sleep -Seconds 16 }\n" +
                "Set-Content -LiteralPath (Join-Path $EvidenceDir 'operation.txt') -Value $Action\n";
            File.WriteAllText(Hook, HookText);
            var Hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Hook)));
            var Sid = WindowsIdentity.GetCurrent().User!.Value;
            var Image = Environment.ProcessPath!;
            File.WriteAllText(Path.Combine(Root, "service.json"), JsonSerializer.Serialize(new CaptureConfig(Sid, Hook, Hash, EvidenceRoot, Image)));
            using var Service = new CaptureService(Root);
            Invoke(Service, "ReadConfig");
            var RunId = Guid.NewGuid().ToString("D");
            async Task<CaptureResponse> Request(string Operation, string? DirectoryName = null, int? Pid = null) =>
                await (Task<CaptureResponse>)Invoke(Service, "ExecuteAsync",
                    new CaptureRequest(1, Operation, DirectoryName ?? Evidence, RunId, Pid ?? Environment.ProcessId), CancellationToken.None)!;
            Assert((await Request("status")).Success, "authorized status failed");
            Assert(!(await Request("shell")).Success, "unsupported operation accepted");
            Assert(!(await Request("start", Root)).Success, "out-of-root path accepted");
            Assert(!(await Request("start", Path.Combine(EvidenceRoot, "..", "outside"))).Success, "traversal accepted");
            File.AppendAllText(Hook, "# changed");
            Assert(!(await Request("start")).Success, "wrong hook hash accepted");
            File.WriteAllText(Hook, HookText);
            Assert((await Request("stop")).Success, "hash-denied owned state reconciliation failed");
            Assert((await Request("start")).Success, "authorized capture start failed");
            Assert((await Request("status")).State == "running", "active status failed");
            Assert(!(await Request("start")).Success, "double start accepted");
            Assert((await Request("stop")).Success, "authorized stop failed");
            Assert(File.ReadAllText(Path.Combine(Evidence, "operation.txt")).Trim() == "Stop", "stop hook not invoked");
            File.WriteAllText(Path.Combine(Evidence, "slow-stop.txt"), "bounded export simulation");
            Assert((await Request("start")).Success, "slow export start failed");
            var SlowStop = Stopwatch.StartNew();
            var SlowResponse = await Request("stop");
            SlowStop.Stop();
            Assert(SlowResponse.Success && SlowResponse.State == "stopped", "slow owned export did not acknowledge completion");
            Assert(SlowStop.Elapsed >= TimeSpan.FromSeconds(16) && SlowStop.Elapsed < TimeSpan.FromSeconds(30),
                "slow owned export exceeded the service bound");
            Assert((await Request("status")).State == "idle", "slow owned export left capture active");
            File.Delete(Path.Combine(Evidence, "slow-stop.txt"));
            using var Lease = Process.Start(new ProcessStartInfo(Image, "--lease") { UseShellExecute = false, CreateNoWindow = true })!;
            try
            {
                await Task.Delay(250);
                var LeaseResponse = await Request("start", Pid: Lease.Id);
                Assert(LeaseResponse.Success, "leased start failed: " + LeaseResponse.Detail);
            }
            finally { if (!Lease.HasExited) Lease.Kill(); Lease.WaitForExit(); }
            await Task.Delay(3000);
            Assert((await Request("status")).State == "idle", "helper exit did not stop capture");
            Assert((await Request("start")).Success, "duration test start failed");
            // Exercise the real fixed 90-second bound with a harmless mock hook.
            await Task.Delay(91500);
            Assert((await Request("status")).State == "idle", "capture duration exceeded 90 second bound");
            Assert((await Request("start")).Success, "service cleanup start failed");
            Invoke(Service, "OnStop");
            Assert((await Request("status")).State == "idle", "service stop cleanup failed");
            var Limit = (TimeSpan)typeof(CaptureService).GetField("CaptureLimit", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
            Assert(Limit == TimeSpan.FromSeconds(90), "hard duration bound changed");
            var HookLimit = (TimeSpan)typeof(CaptureService).GetField("HookLimit", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
            Assert(HookLimit == TimeSpan.FromSeconds(30), "bounded hook deadline changed");
            // OS-level denial: current user is deliberately excluded from this test pipe.
            var Security = new PipeSecurity();
            Security.SetAccessRuleProtection(true, false);
            Security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            var PipeName = "agent-coordinator-denial-" + Guid.NewGuid();
            using var Server = NamedPipeServerStreamAcl.Create(PipeName, PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 1024, 1024, Security);
            using var Client = new NamedPipeClientStream(".", PipeName, PipeDirection.InOut);
            var Denied = false;
            try { Client.Connect(1000); } catch (UnauthorizedAccessException) { Denied = true; }
            Assert(Denied, "non-authorized SID opened pipe");
            Console.WriteLine("[Coordinator:HelperTests] Fixed operations, confinement, hook hash, lease cleanup, duration, service cleanup and SID pipe denial passed");
            return 0;
        }
        finally
        {
            var Resolved = Path.GetFullPath(Root);
            if (Path.GetDirectoryName(Resolved) != Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar) ||
                !Path.GetFileName(Resolved).StartsWith("agent-coordinator-helper-"))
                throw new InvalidOperationException("unsafe test cleanup path");
            Directory.Delete(Resolved, true);
        }
    }
}
