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
            const string HookBody = "param([string]$EvidenceDir,[string]$Action)\n" +
                "if ($Action -eq 'Stop' -and (Test-Path -LiteralPath (Join-Path $EvidenceDir 'slow-stop.txt'))) { Start-Sleep -Seconds 16 }\n" +
                "Set-Content -LiteralPath (Join-Path $EvidenceDir 'operation.txt') -Value $Action\n";
            var HookText = HookBody;
#if FARM32_CAPTURE
            // A CRLF hook larger than the EncodedCommand command-line limit
            // must still execute through the fixed, pinned Farm32 file path.
            HookText = HookBody.Replace("\n", "\r\n") +
                String.Concat(Enumerable.Repeat("#" + new string('x', 180) + "\r\n", 100));
            var LongEvidenceDirectory = @"C:\" + new string('x', 509);
            var Launch = CaptureService.CreateFarm32HookStartInfo("powershell.exe", Hook,
                LongEvidenceDirectory, "Start", Root);
            Assert(LongEvidenceDirectory.Length == 512 &&
                Launch.ArgumentList.SequenceEqual(new[] { "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                    Hook, "-EvidenceDir", LongEvidenceDirectory, "-Action", "Start" }) &&
                Launch.UseShellExecute == false && Launch.CreateNoWindow &&
                Launch.RedirectStandardOutput && Launch.RedirectStandardError &&
                Launch.WorkingDirectory == Root, "Farm32 fixed long-path hook launch changed");
            var OldEncoded = Convert.ToBase64String(System.Text.Encoding.Unicode.GetBytes(
                "& {\n" + HookText + "\n} '" + LongEvidenceDirectory + "' Start"));
            Assert(OldEncoded.Length > 32767 &&
                String.Join(' ', Launch.ArgumentList).Length < 2048,
                "Farm32 hook command still depends on encoded source length");
            var DeniedAction = false;
            try { CaptureService.CreateFarm32HookStartInfo("powershell.exe", Hook,
                LongEvidenceDirectory, "Finalize", Root); }
            catch (InvalidDataException) { DeniedAction = true; }
            Assert(DeniedAction, "Farm32 hook launch accepted an arbitrary operation");
#endif
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
                    new CaptureRequest(CaptureProfile.ProtocolVersion, Operation, DirectoryName ?? Evidence, RunId, Pid ?? Environment.ProcessId), CancellationToken.None)!;
            Assert((await Request("status")).Success, "authorized status failed");
            var WrongVersion = CaptureProfile.ProtocolVersion == 1 ? 2 : 1;
            var DeniedVersion = await (Task<CaptureResponse>)Invoke(Service, "ExecuteAsync",
                new CaptureRequest(WrongVersion, "start", Evidence, RunId, Environment.ProcessId), CancellationToken.None)!;
            Assert(!DeniedVersion.Success, "wrong capture profile request version accepted");
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
#if FARM32_CAPTURE
            // Test the deadline transition without occupying the test host for ten minutes.
            // The literal 600-second profile is asserted below; no capture tool is launched.
            var ActiveField = typeof(CaptureService).GetField("Active", BindingFlags.Instance | BindingFlags.NonPublic)!;
            var Current = (ActiveCapture)ActiveField.GetValue(Service)!;
            ActiveField.SetValue(Service, Current with { DeadlineUtc = DateTime.UtcNow.AddSeconds(-1) });
            await Task.Delay(1200);
            Assert((await Request("status")).State == "idle", "farm deadline did not stop capture");
#else
            // Exercise the real fixed 90-second bound with a harmless mock hook.
            await Task.Delay(91500);
            Assert((await Request("status")).State == "idle", "capture duration exceeded 90 second bound");
#endif
            Assert((await Request("start")).Success, "service cleanup start failed");
            var Lock = (SemaphoreSlim)typeof(CaptureService).GetField("StateLock", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(Service)!;
            await Lock.WaitAsync();
            var StopTask = Task.Run(() => Invoke(Service, "OnStop"));
            await Task.Delay(250);
            Lock.Release();
            await StopTask;
            Assert((await Request("status")).State == "idle", "service stop cleanup failed");
            var Limit = (TimeSpan)typeof(CaptureService).GetField("CaptureLimit", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
            Assert(Limit == TimeSpan.FromSeconds(
#if FARM32_CAPTURE
                600
#else
                90
#endif
            ), "hard duration bound changed");
#if FARM32_CAPTURE
            Assert(CaptureProfile.PipeName == "GantriaAgentCoordinatorCaptureFarm32-v2" &&
                CaptureProfile.ServiceName == "GantriaAgentCoordinatorCaptureFarm32" &&
                CaptureProfile.DataDirectory == "AgentCoordinatorCaptureFarm32" &&
                CaptureProfile.HookLimit == TimeSpan.FromSeconds(60) &&
                CaptureProfile.ResponseTimeoutMilliseconds == 135000, "farm profile isolation changed");
#else
            Assert(CaptureProfile.PipeName == "GantriaAgentCoordinatorCapture-v1" &&
                CaptureProfile.ServiceName == "GantriaAgentCoordinatorCapture" &&
                CaptureProfile.DataDirectory == "AgentCoordinatorCapture" &&
                CaptureProfile.HookLimit == TimeSpan.FromSeconds(30) &&
                CaptureProfile.ResponseTimeoutMilliseconds == 75000, "baseline profile changed");
#endif
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
            var ClientPipeName = "agent-coordinator-client-" + Guid.NewGuid();
            var ClientSecurity = new PipeSecurity();
            ClientSecurity.SetAccessRuleProtection(true, false);
            ClientSecurity.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User!,
                PipeAccessRights.FullControl, AccessControlType.Allow));
            using var ClientServer = NamedPipeServerStreamAcl.Create(ClientPipeName, PipeDirection.InOut, 1,
                PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 1024, 1024, ClientSecurity);
            var ClientRunId = Guid.NewGuid().ToString("D");
            var ServeClient = Task.Run(async () =>
            {
                await ClientServer.WaitForConnectionAsync();
                using var Reader = new StreamReader(ClientServer, System.Text.Encoding.UTF8, false, 1024, true);
                using var Writer = new StreamWriter(ClientServer, new System.Text.UTF8Encoding(false), 1024, true) { AutoFlush = true };
                var RequestText = await Reader.ReadLineAsync();
                var Received = JsonSerializer.Deserialize<CaptureRequest>(RequestText!);
                Assert(Received?.Version == CaptureProfile.ProtocolVersion && Received.Operation == "status" &&
                    Received.RunId == ClientRunId, "pipe client sent the wrong fixed request");
                await Writer.WriteLineAsync(JsonSerializer.Serialize(
                    new CaptureResponse(true, "status", ClientRunId, "idle", "mock")));
            });
            var OriginalOutput = Console.Out;
            using var ClientOutput = new StringWriter();
            int ClientExit;
            try
            {
                Console.SetOut(ClientOutput);
                ClientExit = CaptureClient.SendToPipe(ClientPipeName, "status", Evidence, ClientRunId, Environment.ProcessId);
            }
            finally { Console.SetOut(OriginalOutput); }
            await ServeClient;
            var ClientReply = JsonSerializer.Deserialize<CaptureResponse>(ClientOutput.ToString());
            Assert(ClientExit == 0 && ClientReply?.Success == true && ClientReply.State == "idle",
                "bounded named-pipe client could not read the mock response");
            Console.WriteLine("[Coordinator:HelperTests] Fixed operations, confinement, hook hash, lease cleanup, duration, service cleanup, SID pipe denial and bounded pipe client passed");
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
