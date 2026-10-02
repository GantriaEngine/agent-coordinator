using System.Diagnostics;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AgentCoordinator.CaptureService;

// Selected at build time. The farm service has a distinct pipe, service identity,
// data directory and request version; no IPC field can select a longer lease.
internal static class CaptureProfile
{
#if FARM32_CAPTURE
    internal const string PipeName = "GantriaAgentCoordinatorCaptureFarm32-v2";
    internal const string ServiceName = "GantriaAgentCoordinatorCaptureFarm32";
    internal const string DataDirectory = "AgentCoordinatorCaptureFarm32";
    internal const int ProtocolVersion = 2;
    internal static readonly TimeSpan CaptureLimit = TimeSpan.FromSeconds(600);
    internal static readonly TimeSpan HookLimit = TimeSpan.FromSeconds(60);
    internal const int ResponseTimeoutMilliseconds = 135000;
#else
    internal const string PipeName = "GantriaAgentCoordinatorCapture-v1";
    internal const string ServiceName = "GantriaAgentCoordinatorCapture";
    internal const string DataDirectory = "AgentCoordinatorCapture";
    internal const int ProtocolVersion = 1;
    internal static readonly TimeSpan CaptureLimit = TimeSpan.FromSeconds(90);
    internal static readonly TimeSpan HookLimit = TimeSpan.FromSeconds(30);
    internal const int ResponseTimeoutMilliseconds = 75000;
#endif
}

internal static class Program
{
    public static int Main(string[] Args)
    {
        if (Args.Length == 4 && (Args[0] is "start" or "stop" or "status") && Guid.TryParse(Args[2], out _) &&
            int.TryParse(Args[3], out var LeasePid))
            return CaptureClient.Send(Args[0], Args[1], Args[2], LeasePid);
        if (Args.Length == 1 && Args[0] == "--service")
        {
            ServiceBase.Run(new CaptureService());
            return 0;
        }
        Console.Error.WriteLine("Usage: AgentCoordinator.CaptureService.exe start|stop|status <evidence-directory> <run-uuid> <endpoint-pid> | --service");
        return 2;
    }
}

internal sealed class CaptureClient
{
    public static int Send(string Operation, string EvidenceDirectory, string RunId, int LeasePid)
    {
        try
        {
            using var Pipe = new NamedPipeClientStream(".", CaptureProfile.PipeName, PipeDirection.InOut, PipeOptions.None,
                TokenImpersonationLevel.Impersonation);
            Pipe.Connect(5000);
            Pipe.ReadTimeout = CaptureProfile.ResponseTimeoutMilliseconds;
            Pipe.WriteTimeout = 5000;
            using var Writer = new StreamWriter(Pipe, new UTF8Encoding(false), 1024, true) { AutoFlush = true };
            using var Reader = new StreamReader(Pipe, Encoding.UTF8, false, 1024, true);
            var Request = JsonSerializer.Serialize(new CaptureRequest(CaptureProfile.ProtocolVersion, Operation, EvidenceDirectory, RunId, LeasePid));
            Writer.WriteLine(Request);
            var ResponseText = Reader.ReadLine();
            if (ResponseText is null || ResponseText.Length > 16384)
                throw new InvalidDataException("Capture service returned an invalid response.");
            var Response = JsonSerializer.Deserialize<CaptureResponse>(ResponseText)
                ?? throw new InvalidDataException("Capture service returned an empty response.");
            Console.WriteLine(JsonSerializer.Serialize(Response));
            return Response.Success ? 0 : 1;
        }
        catch (Exception Error)
        {
            Console.Error.WriteLine("[Coordinator:CaptureService] " + Error.Message);
            return 1;
        }
    }
}

internal sealed record CaptureRequest(int Version, string Operation, string EvidenceDirectory, string RunId, int LeaseProcessId);
internal sealed record CaptureResponse(bool Success, string Operation, string RunId, string State, string Detail);
internal sealed record CaptureConfig(string AuthorizedSid, string HookPath, string HookSha256, string EvidenceRoot, string LeaseImagePath);
internal sealed record ActiveCapture(string RunId, string EvidenceDirectory, DateTime StartedUtc, DateTime DeadlineUtc,
    int LeaseProcessId, long LeaseProcessStartedUtcTicks);

internal sealed class CaptureService : ServiceBase
{
    private const int MaximumRequestBytes = 16384;
    private static readonly TimeSpan CaptureLimit = CaptureProfile.CaptureLimit;
    private static readonly TimeSpan HookLimit = CaptureProfile.HookLimit;
    private readonly string BaseDirectory;
    private readonly CancellationTokenSource StopSource = new();
    private readonly SemaphoreSlim StateLock = new(1, 1);
    private CaptureConfig Config = null!;
    private ActiveCapture? Active;
    private bool RecoveryBlocked;

    public CaptureService() : this(null) { }

    // Internal local test seam; never selected by a pipe request or agent message.
    internal CaptureService(string? LocalTestDirectory)
    {
        BaseDirectory = LocalTestDirectory ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "GantriaEngine", CaptureProfile.DataDirectory);
        ServiceName = CaptureProfile.ServiceName;
        CanStop = true;
        AutoLog = true;
    }

    protected override void OnStart(string[] Args)
    {
        Config = ReadConfig();
        _ = RecoverThenServeAsync(StopSource.Token);
    }

    protected override void OnStop()
    {
        StopSource.Cancel();
        // A concurrent fixed hook may hold StateLock for its full bound. Ask
        // SCM for enough stop-pending time to wait for it and then stop once.
        var StopBudget = (int)(2 * HookLimit.TotalMilliseconds + 10000);
        try { RequestAdditionalTime(StopBudget); }
        catch (InvalidOperationException) { } // Direct local test seam.
        catch (System.ComponentModel.Win32Exception) { } // Not SCM-hosted.
        bool Locked;
        try { Locked = StateLock.Wait(HookLimit + TimeSpan.FromSeconds(5)); }
        catch { Audit("SERVICE_STOP_CLEANUP", Active?.RunId ?? "", "state lock wait failed"); return; }
        if (!Locked)
        {
            Audit("SERVICE_STOP_CLEANUP", Active?.RunId ?? "", "state lock remained busy");
            return;
        }
        try
        {
            if (Active is not null)
            {
                InvokeHook("Stop", Active.EvidenceDirectory);
                Audit("SERVICE_STOP_CLEANUP", Active.RunId, "completed");
                DeleteActiveState();
                Active = null;
            }
        }
        catch (Exception Error) { Audit("SERVICE_STOP_CLEANUP", Active?.RunId ?? "", Error.Message); }
        finally { StateLock.Release(); }
    }

    private async Task RecoverThenServeAsync(CancellationToken Token)
    {
        await RecoverPersistedCaptureAsync(Token).ConfigureAwait(false);
        await ServeAsync(Token).ConfigureAwait(false);
    }

    private async Task ServeAsync(CancellationToken Token)
    {
        while (!Token.IsCancellationRequested)
        {
            try
            {
                var Security = new PipeSecurity();
                Security.SetAccessRuleProtection(true, false);
                Security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                    PipeAccessRights.FullControl, AccessControlType.Allow));
                Security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(Config.AuthorizedSid),
                    PipeAccessRights.ReadWrite | PipeAccessRights.CreateNewInstance, AccessControlType.Allow));
                using var Pipe = NamedPipeServerStreamAcl.Create(CaptureProfile.PipeName, PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.WriteThrough,
                    MaximumRequestBytes, MaximumRequestBytes, Security);
                await Pipe.WaitForConnectionAsync(Token).ConfigureAwait(false);
                await HandleClientAsync(Pipe, Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (Token.IsCancellationRequested) { break; }
            catch (Exception Error) { Audit("IPC_ERROR", "", Error.ToString()); await Task.Delay(250, Token).ConfigureAwait(false); }
        }
    }

    private async Task HandleClientAsync(NamedPipeServerStream Pipe, CancellationToken Token)
    {
        using var Writer = new StreamWriter(Pipe, new UTF8Encoding(false), 1024, true) { AutoFlush = true };
        string? Line;
        try { Line = await ReadBoundedLineAsync(Pipe, Token).ConfigureAwait(false); }
        catch (InvalidDataException)
        {
            await Writer.WriteLineAsync(JsonSerializer.Serialize(new CaptureResponse(false, "", "", "denied", "request exceeds bounds"))).ConfigureAwait(false);
            Audit("IPC_DENIED", "", "request exceeds bounds");
            return;
        }
        if (Line is null)
        {
            Audit("IPC_DENIED", "", "empty request");
            return;
        }
        CaptureRequest? Request;
        try { Request = JsonSerializer.Deserialize<CaptureRequest>(Line); }
        catch (JsonException) { Request = null; }
        if (Request is null || Request.Version != CaptureProfile.ProtocolVersion || !Guid.TryParse(Request.RunId, out var RunGuid) ||
            RunGuid.ToString("D") != Request.RunId || Request.Operation is not ("start" or "stop" or "status"))
        {
            await Writer.WriteLineAsync(JsonSerializer.Serialize(new CaptureResponse(false, "", "", "denied", "invalid request shape"))).ConfigureAwait(false);
            Audit("IPC_DENIED", "", "invalid request shape");
            return;
        }
        var Response = await ExecuteAsync(Request, Token).ConfigureAwait(false);
        await Writer.WriteLineAsync(JsonSerializer.Serialize(Response)).ConfigureAwait(false);
    }

    private static async Task<string?> ReadBoundedLineAsync(Stream Pipe, CancellationToken Token)
    {
        using var Buffer = new MemoryStream();
        var Byte = new byte[1];
        while (Buffer.Length <= MaximumRequestBytes)
        {
            var Read = await Pipe.ReadAsync(Byte.AsMemory(0, 1), Token).ConfigureAwait(false);
            if (Read == 0) return Buffer.Length == 0 ? null : throw new InvalidDataException("unterminated request");
            if (Byte[0] == (byte)'\n')
            {
                if (Buffer.Length + 1 > MaximumRequestBytes) throw new InvalidDataException("request exceeds bounds");
                if (Buffer.Length > 0 && Buffer.GetBuffer()[Buffer.Length - 1] == (byte)'\r') Buffer.SetLength(Buffer.Length - 1);
                try { return new UTF8Encoding(false, true).GetString(Buffer.GetBuffer(), 0, (int)Buffer.Length); }
                catch (DecoderFallbackException) { throw new InvalidDataException("request is not valid UTF-8"); }
            }
            Buffer.WriteByte(Byte[0]);
        }
        throw new InvalidDataException("request exceeds bounds");
    }

    private async Task<CaptureResponse> ExecuteAsync(CaptureRequest Request, CancellationToken Token)
    {
        if (Request.Version != CaptureProfile.ProtocolVersion || !Guid.TryParse(Request.RunId, out var RunGuid) ||
            RunGuid.ToString("D") != Request.RunId || Request.Operation is not ("start" or "stop" or "status"))
            return new(false, Request.Operation, Request.RunId, "denied", "invalid request shape");
        await StateLock.WaitAsync(Token).ConfigureAwait(false);
        try
        {
            var EvidenceDirectory = ValidateEvidenceDirectory(Request.EvidenceDirectory);
            if (Request.Operation == "status")
            {
                if (Active is null) return new(!RecoveryBlocked, Request.Operation, Request.RunId,
                    RecoveryBlocked ? "recovery-blocked" : "idle", RecoveryBlocked ? "administrator reconciliation is required" : "no active capture");
                if (Active.RunId != Request.RunId || Active.EvidenceDirectory != EvidenceDirectory)
                    return new(false, Request.Operation, Request.RunId, "not-owner", "run does not own the active capture");
                return new(true, Request.Operation, Request.RunId, "running", Active.DeadlineUtc.ToString("O"));
            }
            if (Request.Operation == "start")
            {
                if (RecoveryBlocked) return new(false, Request.Operation, Request.RunId, "recovery-blocked", "administrator reconciliation is required");
                if (Active is not null)
                    return new(false, Request.Operation, Request.RunId, "busy", "one capture is already active");
                var Started = DateTime.UtcNow;
                var LeaseStarted = VerifyLeaseProcess(Request.LeaseProcessId);
                Directory.CreateDirectory(EvidenceDirectory);
                Active = new(Request.RunId, EvidenceDirectory, Started, Started + CaptureLimit,
                    Request.LeaseProcessId, LeaseStarted.Ticks);
                SaveActiveState(Active);
                try { InvokeHook("Start", EvidenceDirectory); }
                catch
                {
                    try { InvokeHook("Stop", EvidenceDirectory); DeleteActiveState(); Active = null; }
                    catch (Exception CleanupError) { Audit("START_ROLLBACK_FAILED", Request.RunId, CleanupError.Message); }
                    throw;
                }
                Audit("CAPTURE_START", Request.RunId, EvidenceDirectory);
                _ = EnforceDeadlineAsync(Request.RunId, StopSource.Token);
                return new(true, Request.Operation, Request.RunId, "running", "bounded capture started");
            }
            if (Active is null || Active.RunId != Request.RunId || Active.EvidenceDirectory != EvidenceDirectory)
                return new(false, Request.Operation, Request.RunId, "not-owner", "run does not own the active capture");
            InvokeHook("Stop", EvidenceDirectory);
            DeleteActiveState();
            Active = null;
            Audit("CAPTURE_STOP", Request.RunId, "completed");
            return new(true, Request.Operation, Request.RunId, "stopped",
#if FARM32_CAPTURE
                "owned capture stopped; bounded offline export remains required");
#else
                "owned capture stopped and exported");
#endif
        }
        catch (Exception Error)
        {
            Audit("CAPTURE_ERROR", Request.RunId, Error.GetType().Name + ": " + Error.Message);
            return new(false, Request.Operation, Request.RunId, "failed", Error.Message);
        }
        finally { StateLock.Release(); }
    }

    private async Task EnforceDeadlineAsync(string RunId, CancellationToken Token)
    {
        try
        {
            while (true)
            {
                ActiveCapture? Current;
                await StateLock.WaitAsync(Token).ConfigureAwait(false);
                try { Current = Active?.RunId == RunId ? Active : null; }
                finally { StateLock.Release(); }
                if (Current is null) return;
                if (DateTime.UtcNow >= Current.DeadlineUtc) break;
                if (!IsLeaseProcessAlive(Current))
                {
                    await StateLock.WaitAsync(Token).ConfigureAwait(false);
                    try
                    {
                        if (Active?.RunId == RunId)
                        {
                            InvokeHook("Stop", Active.EvidenceDirectory);
                            DeleteActiveState();
                            Active = null;
                            Audit("CAPTURE_LEASE_LOST", RunId, "endpoint process exited; capture stopped");
                        }
                    }
                    finally { StateLock.Release(); }
                    return;
                }
                await Task.Delay(TimeSpan.FromMilliseconds(500), Token).ConfigureAwait(false);
            }
            await StateLock.WaitAsync(Token).ConfigureAwait(false);
            try
            {
                if (Active?.RunId == RunId)
                {
                    InvokeHook("Stop", Active.EvidenceDirectory);
                    DeleteActiveState();
                    Active = null;
                    Audit("CAPTURE_DEADLINE", RunId, $"stopped at hard {CaptureLimit.TotalSeconds:0} second deadline");
                }
            }
            finally { StateLock.Release(); }
        }
        catch (OperationCanceledException) { }
        catch (Exception Error) { Audit("CAPTURE_DEADLINE_ERROR", RunId, Error.Message); }
    }

    private DateTime VerifyLeaseProcess(int ProcessId)
    {
        if (ProcessId <= 0) throw new InvalidDataException("endpoint lease process ID is invalid");
        using var Lease = System.Diagnostics.Process.GetProcessById(ProcessId);
        var Image = Path.GetFullPath(Lease.MainModule?.FileName ?? throw new InvalidDataException("endpoint lease image is unavailable"));
        if (!StringComparer.OrdinalIgnoreCase.Equals(Image, Path.GetFullPath(Config.LeaseImagePath)))
            throw new UnauthorizedAccessException("endpoint lease process image does not match the installed qualifier runtime");
        return Lease.StartTime.ToUniversalTime();
    }

    private bool IsLeaseProcessAlive(ActiveCapture Capture)
    {
        try
        {
            using var Lease = System.Diagnostics.Process.GetProcessById(Capture.LeaseProcessId);
            var Image = Path.GetFullPath(Lease.MainModule?.FileName ?? "");
            return StringComparer.OrdinalIgnoreCase.Equals(Image, Path.GetFullPath(Config.LeaseImagePath)) &&
                Lease.StartTime.ToUniversalTime().Ticks == Capture.LeaseProcessStartedUtcTicks;
        }
        catch { return false; }
    }

    private async Task RecoverPersistedCaptureAsync(CancellationToken Token)
    {
        var StatePath = ActiveStatePath;
        if (!File.Exists(StatePath)) return;
        try
        {
            var State = JsonSerializer.Deserialize<ActiveCapture>(File.ReadAllText(StatePath))
                ?? throw new InvalidDataException("active capture state is invalid");
            if (!Guid.TryParse(State.RunId, out _) || Token.IsCancellationRequested) return;
            var EvidenceDirectory = ValidateEvidenceDirectory(State.EvidenceDirectory);
            Active = State;
            await StateLock.WaitAsync(Token).ConfigureAwait(false);
            try
            {
                InvokeHook("Stop", EvidenceDirectory);
                DeleteActiveState();
                Active = null;
                Audit("STALE_CAPTURE_RECOVERY", State.RunId, "persisted owned capture reconciled");
            }
            finally { StateLock.Release(); }
        }
        catch (Exception Error)
        {
            RecoveryBlocked = true;
            Audit("STALE_CAPTURE_PRESERVED", "", Error.GetType().Name + ": " + Error.Message);
        }
    }

    private string ActiveStatePath => Path.Combine(BaseDirectory, "active-capture.json");
    private void SaveActiveState(ActiveCapture State) => File.WriteAllText(ActiveStatePath, JsonSerializer.Serialize(State), new UTF8Encoding(false));
    private void DeleteActiveState() { if (File.Exists(ActiveStatePath)) File.Delete(ActiveStatePath); }

    private string ValidateEvidenceDirectory(string Value)
    {
        if (String.IsNullOrWhiteSpace(Value) || Value.Length > 512 || Value.Contains('"') || Value.Contains('\'') || Value.Contains('\0'))
            throw new InvalidDataException("evidence path is invalid");
        var Root = Path.GetFullPath(Config.EvidenceRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var Full = Path.GetFullPath(Value).TrimEnd(Path.DirectorySeparatorChar);
        if (!Full.StartsWith(Root, StringComparison.OrdinalIgnoreCase))
            throw new UnauthorizedAccessException("evidence path is outside the configured root");
        var Current = Root.TrimEnd(Path.DirectorySeparatorChar);
        foreach (var Part in Full[Root.Length..].Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
        {
            if (Part is "" or "." or "..") throw new UnauthorizedAccessException("evidence path contains an invalid segment");
            Current = Path.Combine(Current, Part);
            if (Directory.Exists(Current) && (File.GetAttributes(Current) & FileAttributes.ReparsePoint) != 0)
                throw new UnauthorizedAccessException("reparse points are not allowed in evidence paths");
        }
        if (!Directory.Exists(Full)) throw new DirectoryNotFoundException("evidence directory must already exist");
        return Full;
    }

    private void InvokeHook(string Action, string EvidenceDirectory)
    {
        string OutputText = "";
        string ErrorText = "";
        var LogPath = Path.Combine(EvidenceDirectory, "privileged-capture-" + Action.ToLowerInvariant() + ".log");
        try
        {
        if (!StringComparer.OrdinalIgnoreCase.Equals(Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Config.HookPath))),
                Config.HookSha256))
            throw new InvalidDataException("installed capture hook hash does not match service configuration");
        var PowerShell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
#if FARM32_CAPTURE
        // The bounded Farm32 hook is larger than Windows' command-line limit
        // when embedded as UTF-16 Base64. Its installed path is read-only to
        // the pipe caller and hash-pinned immediately before this fixed launch.
        var Start = CreateFarm32HookStartInfo(PowerShell, Config.HookPath, EvidenceDirectory, Action, BaseDirectory);
#else
        var Script = File.ReadAllText(Config.HookPath, Encoding.UTF8);
        var Code = "& {\n" + Script + "\n} '" + EvidenceDirectory.Replace("'", "''") + "' " + Action;
        var Encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(Code));
        var Start = new ProcessStartInfo(PowerShell) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = BaseDirectory };
        Start.ArgumentList.Add("-NoProfile");
        Start.ArgumentList.Add("-NonInteractive");
        Start.ArgumentList.Add("-EncodedCommand");
        Start.ArgumentList.Add(Encoded);
#endif
        using var Child = System.Diagnostics.Process.Start(Start) ?? throw new InvalidOperationException("PowerShell did not start");
        var OutputTask = Child.StandardOutput.ReadToEndAsync();
        var ErrorTask = Child.StandardError.ReadToEndAsync();
        if (!Child.WaitForExit((int)HookLimit.TotalMilliseconds))
        {
            try { Child.Kill(true); } catch { }
            throw new System.TimeoutException($"capture hook exceeded its {HookLimit.TotalSeconds:0} second deadline");
        }
        Task.WaitAll(OutputTask, ErrorTask);
        OutputText = OutputTask.Result;
        ErrorText = ErrorTask.Result;
        if (OutputText.Length > 32768 || ErrorText.Length > 32768)
            throw new InvalidDataException("capture hook output exceeded its bound");
        if (Child.ExitCode != 0) throw new InvalidOperationException("capture hook failed: " + ErrorText.Trim());
        }
        catch (Exception Error)
        {
            WriteHookLog(LogPath, "failed: " + Error.Message + Environment.NewLine + OutputText + ErrorText);
            throw;
        }
        WriteHookLog(LogPath, "completed" + Environment.NewLine + OutputText + ErrorText);
    }

#if FARM32_CAPTURE
    internal static ProcessStartInfo CreateFarm32HookStartInfo(string PowerShell, string HookPath,
        string EvidenceDirectory, string Action, string WorkingDirectory)
    {
        if (Action is not ("Start" or "Stop")) throw new InvalidDataException("invalid fixed hook operation");
        var Start = new ProcessStartInfo(PowerShell) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = WorkingDirectory };
        Start.ArgumentList.Add("-NoProfile");
        Start.ArgumentList.Add("-NonInteractive");
        Start.ArgumentList.Add("-File");
        Start.ArgumentList.Add(HookPath);
        Start.ArgumentList.Add("-EvidenceDir");
        Start.ArgumentList.Add(EvidenceDirectory);
        Start.ArgumentList.Add("-Action");
        Start.ArgumentList.Add(Action);
        return Start;
    }
#endif

    private static void WriteHookLog(string PathName, string Text)
    {
        var Bounded = Text.Length > 65536 ? Text[..65536] : Text;
        File.WriteAllText(PathName, Bounded, new UTF8Encoding(false));
    }

    private CaptureConfig ReadConfig()
    {
        var PathName = Path.Combine(BaseDirectory, "service.json");
        var ConfigValue = JsonSerializer.Deserialize<CaptureConfig>(File.ReadAllText(PathName))
            ?? throw new InvalidDataException("service configuration is invalid");
        if (!Regex.IsMatch(ConfigValue.AuthorizedSid, @"^S-1-(?:[0-9]+-)*[0-9]+$", RegexOptions.CultureInvariant) ||
            !File.Exists(ConfigValue.HookPath) || !Regex.IsMatch(ConfigValue.HookSha256, "^[A-Fa-f0-9]{64}$") ||
            !Path.IsPathFullyQualified(ConfigValue.EvidenceRoot) || !Path.IsPathFullyQualified(ConfigValue.LeaseImagePath) ||
            !File.Exists(ConfigValue.LeaseImagePath))
            throw new InvalidDataException("service configuration failed validation");
        Config = ConfigValue;
        return ConfigValue;
    }

    private void Audit(string Operation, string RunId, string Detail)
    {
        try
        {
            var Line = JsonSerializer.Serialize(new { TimestampUtc = DateTime.UtcNow, Operation, RunId, Detail = Detail.Length > 512 ? Detail[..512] : Detail });
            File.AppendAllText(Path.Combine(BaseDirectory, "audit.jsonl"), Line + Environment.NewLine, new UTF8Encoding(false));
        }
        catch { }
    }

    private static async Task WriteResponseAsync(Stream Pipe, CaptureResponse Response, CancellationToken Token)
    {
        using var Writer = new StreamWriter(Pipe, new UTF8Encoding(false), 1024, true) { AutoFlush = true };
        await Writer.WriteLineAsync(JsonSerializer.Serialize(Response).AsMemory(), Token).ConfigureAwait(false);
    }
}
