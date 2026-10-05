"""Start one owned Windows child inside a kill-on-close Job at CreateProcess time.

Only the three standard-stream handles are inherited. There is no post-launch
AssignProcessToJobObject interval and no unconfined executable instruction.
"""
import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import subprocess
import time


Kernel = ctypes.WinDLL("kernel32", use_last_error=True)
HANDLE = wintypes.HANDLE
SIZE_T = ctypes.c_size_t


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("Length", wintypes.DWORD), ("Descriptor", ctypes.c_void_p),
                ("Inherit", wintypes.BOOL)]


class StartupInfo(ctypes.Structure):
    _fields_ = [("Size", wintypes.DWORD), ("Reserved", wintypes.LPWSTR),
                ("Desktop", wintypes.LPWSTR), ("Title", wintypes.LPWSTR),
                ("X", wintypes.DWORD), ("Y", wintypes.DWORD),
                ("Width", wintypes.DWORD), ("Height", wintypes.DWORD),
                ("XChars", wintypes.DWORD), ("YChars", wintypes.DWORD),
                ("Fill", wintypes.DWORD), ("Flags", wintypes.DWORD),
                ("ShowWindow", wintypes.WORD), ("ReservedBytes", wintypes.WORD),
                ("ReservedBuffer", ctypes.c_void_p), ("Input", HANDLE),
                ("Output", HANDLE), ("Error", HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("Startup", StartupInfo), ("Attributes", ctypes.c_void_p)]


class ProcessInformation(ctypes.Structure):
    _fields_ = [("Process", HANDLE), ("Thread", HANDLE),
                ("ProcessId", wintypes.DWORD), ("ThreadId", wintypes.DWORD)]


class BasicLimit(ctypes.Structure):
    _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("Flags", wintypes.DWORD), ("MinWorkingSet", SIZE_T),
                ("MaxWorkingSet", SIZE_T), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", SIZE_T), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(Name, ctypes.c_uint64) for Name in
                ("ReadOperations", "WriteOperations", "OtherOperations",
                 "ReadBytes", "WriteBytes", "OtherBytes")]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [("Basic", BasicLimit), ("Io", IoCounters),
                ("ProcessMemory", SIZE_T), ("JobMemory", SIZE_T),
                ("PeakProcessMemory", SIZE_T), ("PeakJobMemory", SIZE_T)]


class BasicAccounting(ctypes.Structure):
    _fields_ = [(Name, ctypes.c_int64) for Name in
                ("UserTime", "KernelTime", "PeriodUserTime", "PeriodKernelTime")] + [
        (Name, wintypes.DWORD) for Name in
        ("PageFaults", "TotalProcesses", "ActiveProcesses", "TerminatedProcesses")]


Kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
Kernel.CreateJobObjectW.restype = HANDLE
Kernel.SetInformationJobObject.argtypes = (HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
Kernel.SetInformationJobObject.restype = wintypes.BOOL
Kernel.TerminateJobObject.argtypes = (HANDLE, wintypes.UINT)
Kernel.TerminateJobObject.restype = wintypes.BOOL
Kernel.QueryInformationJobObject.argtypes = (HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.c_void_p)
Kernel.QueryInformationJobObject.restype = wintypes.BOOL
Kernel.CloseHandle.argtypes = (HANDLE,)
Kernel.CloseHandle.restype = wintypes.BOOL
Kernel.CreatePipe.argtypes = (ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE),
                              ctypes.POINTER(SecurityAttributes), wintypes.DWORD)
Kernel.CreatePipe.restype = wintypes.BOOL
Kernel.SetHandleInformation.argtypes = (HANDLE, wintypes.DWORD, wintypes.DWORD)
Kernel.SetHandleInformation.restype = wintypes.BOOL
Kernel.InitializeProcThreadAttributeList.argtypes = (ctypes.c_void_p, wintypes.DWORD,
                                                      wintypes.DWORD, ctypes.POINTER(SIZE_T))
Kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
Kernel.UpdateProcThreadAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD, SIZE_T,
                                             ctypes.c_void_p, SIZE_T, ctypes.c_void_p,
                                             ctypes.c_void_p)
Kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
Kernel.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
Kernel.CreateProcessW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                  ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.LPCWSTR,
                                  ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInformation))
Kernel.CreateProcessW.restype = wintypes.BOOL
Kernel.ResumeThread.argtypes = (HANDLE,)
Kernel.ResumeThread.restype = wintypes.DWORD
Kernel.WaitForSingleObject.argtypes = (HANDLE, wintypes.DWORD)
Kernel.WaitForSingleObject.restype = wintypes.DWORD
Kernel.GetExitCodeProcess.argtypes = (HANDLE, ctypes.POINTER(wintypes.DWORD))
Kernel.GetExitCodeProcess.restype = wintypes.BOOL
Kernel.TerminateProcess.argtypes = (HANDLE, wintypes.UINT)
Kernel.TerminateProcess.restype = wintypes.BOOL


def Check(Result, Operation):
    if not Result:
        raise ctypes.WinError(ctypes.get_last_error(), f"{Operation} failed")


def Close(Handle):
    if Handle:
        Check(Kernel.CloseHandle(Handle), "CloseHandle")


class CleanupError(OSError):
    """Failed releases retain their identities; callers cannot claim cleanup."""
    def __init__(self, Failures, RetainedHandles, FailedStreams=()):
        self.Failures = tuple(Failures)
        self.RetainedHandles = dict(RetainedHandles)
        self.FailedStreams = tuple(FailedStreams)
        super().__init__("owned cleanup unproven: " + "; ".join(
            Name + ": " + str(Error) for Name, Error in self.Failures))


def RaiseCleanupError(Failures, RetainedHandles, FailedStreams=()):
    Error = CleanupError(Failures, RetainedHandles, FailedStreams)
    for _Name, Failure in Failures:
        if not isinstance(Failure, Exception):
            Failure.CleanupError = Error
            raise Failure  # Preserve an interruption after all release attempts.
    raise Error


def StopJob(Handle, Deadline=None):
    """Checked termination plus zero active members, even after root exit."""
    Check(Handle, "owned Job handle")
    Check(Kernel.TerminateJobObject(Handle, 1), "TerminateJobObject")
    if Deadline is None:
        Deadline = time.monotonic() + 10
    while True:
        Accounting = BasicAccounting()
        Check(Kernel.QueryInformationJobObject(Handle, 1, ctypes.byref(Accounting),
                                               ctypes.sizeof(Accounting), None),
              "QueryInformationJobObject")
        if Accounting.ActiveProcesses == 0:
            return
        if time.monotonic() >= Deadline:
            raise TimeoutError("owned Job still contains active processes")
        time.sleep(0.01)


class WindowsProcess:
    """The small Popen surface the lifecycle adapter needs."""
    def __init__(self, Information, Input, Output, Error):
        self._handle = Information.Process
        self.pid = Information.ProcessId
        self.stdin, self.stdout, self.stderr = Input, Output, Error
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        State = Kernel.WaitForSingleObject(self._handle, 0)
        if State == 0x102:  # WAIT_TIMEOUT
            return None
        if State != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        Code = wintypes.DWORD()
        Check(Kernel.GetExitCodeProcess(self._handle, ctypes.byref(Code)), "GetExitCodeProcess")
        self.returncode = ctypes.c_int32(Code.value).value
        return self.returncode

    def wait(self, timeout=None):
        Millis = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        State = Kernel.WaitForSingleObject(self._handle, Millis)
        if State == 0x102:
            raise subprocess.TimeoutExpired(self.pid, timeout)
        if State != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def kill(self):
        if self.poll() is None:
            Check(Kernel.TerminateProcess(self._handle, 1), "TerminateProcess")

    def Close(self):
        if self._handle:
            Close(self._handle)
            self._handle = None


def Pipe():
    Attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
    Read, Write = HANDLE(), HANDLE()
    Check(Kernel.CreatePipe(ctypes.byref(Read), ctypes.byref(Write),
                            ctypes.byref(Attributes), 0), "CreatePipe")
    return Read, Write


def Start(Args, Directory):
    """Return owned process/job, or report rollback failures without claiming cleanup."""
    Executable = Path(Args[0]).resolve(strict=True)
    if not Executable.is_file() or not Path(Directory).is_dir():
        raise ValueError("approved executable and directory required")
    Job = HANDLE()
    Information = ProcessInformation()
    Pipes = []
    Parent = []
    AttributeList = None
    Initialized = False
    Streams = []
    Descriptors = []
    Ready = False
    def OpenStream(Handle, Flags, Mode):
        Descriptor = msvcrt.open_osfhandle(Handle.value, Flags)
        Pipes.remove(Handle)  # CRT descriptor now owns this exact native handle.
        Descriptors.append(Descriptor)
        Stream = os.fdopen(Descriptor, Mode, buffering=0)
        Descriptors.remove(Descriptor)  # The stream now owns the descriptor.
        Streams.append(Stream)
        return Stream
    try:
        Job = Kernel.CreateJobObjectW(None, None)
        Check(Job, "CreateJobObject")
        Limits = ExtendedLimit()
        Limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        Check(Kernel.SetInformationJobObject(Job, 9, ctypes.byref(Limits),
                                             ctypes.sizeof(Limits)), "SetInformationJobObject")
        InputRead, InputWrite = Pipe()
        Pipes.extend((InputRead, InputWrite))
        OutputRead, OutputWrite = Pipe()
        Pipes.extend((OutputRead, OutputWrite))
        ErrorRead, ErrorWrite = Pipe()
        Pipes.extend((ErrorRead, ErrorWrite))
        Parent = [InputWrite, OutputRead, ErrorRead]
        for Handle in Parent:
            Check(Kernel.SetHandleInformation(Handle, 1, 0), "SetHandleInformation")
        Size = SIZE_T()
        Kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(Size))
        Check(Size.value, "InitializeProcThreadAttributeList size")
        Buffer = ctypes.create_string_buffer(Size.value)
        AttributeList = ctypes.cast(Buffer, ctypes.c_void_p)
        Check(Kernel.InitializeProcThreadAttributeList(AttributeList, 2, 0,
                                                        ctypes.byref(Size)), "InitializeProcThreadAttributeList")
        Initialized = True
        JobList = (HANDLE * 1)(Job)
        HandleList = (HANDLE * 3)(InputRead, OutputWrite, ErrorWrite)
        Check(Kernel.UpdateProcThreadAttribute(AttributeList, 0, 0x2000D, JobList,
                                               ctypes.sizeof(JobList), None, None), "job-list attribute")
        Check(Kernel.UpdateProcThreadAttribute(AttributeList, 0, 0x20002, HandleList,
                                               ctypes.sizeof(HandleList), None, None), "handle-list attribute")
        Startup = StartupInfoEx()
        Startup.Startup.Size = ctypes.sizeof(StartupInfoEx)
        Startup.Startup.Flags = 0x100 | 0x1  # USESTDHANDLES | USESHOWWINDOW
        Startup.Startup.ShowWindow = 0  # SW_HIDE
        Startup.Startup.Input, Startup.Startup.Output, Startup.Startup.Error = HandleList
        Startup.Attributes = AttributeList
        Command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(Item) for Item in Args]))
        Check(Kernel.CreateProcessW(str(Executable), Command, None, None, True,
                                    0x00080000 | 0x08000000 | 0x00000004,
                                    None, str(Directory), ctypes.byref(Startup),
                                    ctypes.byref(Information)), "CreateProcessW")
        for Handle in (InputRead, OutputWrite, ErrorWrite):
            Close(Handle)
            Pipes.remove(Handle)
        Input = OpenStream(InputWrite, os.O_BINARY | os.O_WRONLY, "wb")
        Output = OpenStream(OutputRead, os.O_BINARY | os.O_RDONLY, "rb")
        Error = OpenStream(ErrorRead, os.O_BINARY | os.O_RDONLY, "rb")
        Check(Kernel.ResumeThread(Information.Thread) == 1, "ResumeThread")
        Process = WindowsProcess(Information, Input, Output, Error)
        Close(Information.Thread)
        Information.Thread = None
        Ready = True
    finally:
        Failures = []
        FailedStreams = []
        if Initialized:
            try:
                Kernel.DeleteProcThreadAttributeList(AttributeList)
            except BaseException as Error:
                Failures.append(("attribute list release", Error))
        if not Ready or Failures:
            if Job and Information.Process:
                try:
                    StopJob(Job, time.monotonic() + 10)
                except BaseException as Error:
                    Failures.append(("rollback Job member proof", Error))
            for Stream in Streams:
                try:
                    Stream.close()
                except BaseException as Error:
                    Failures.append(("rollback stream release", Error))
                    FailedStreams.append(Stream)
            for Descriptor in list(Descriptors):
                try:
                    os.close(Descriptor)
                    Descriptors.remove(Descriptor)
                except BaseException as Error:
                    Failures.append(("rollback descriptor release", Error))
            for Handle in list(Pipes):
                try:
                    Close(Handle)
                    Pipes.remove(Handle)
                except BaseException as Error:
                    Failures.append(("rollback pipe release", Error))
            if Job:
                try:
                    Close(Job)  # kill-on-close remains a fallback, never an unchecked claim
                    Job = None
                except BaseException as Error:
                    Failures.append(("rollback Job release", Error))
            if Information.Thread:
                try:
                    Close(Information.Thread)
                    Information.Thread = None
                except BaseException as Error:
                    Failures.append(("rollback thread release", Error))
            if Information.Process:
                try:
                    Close(Information.Process)
                    Information.Process = None
                except BaseException as Error:
                    Failures.append(("rollback process release", Error))
            if Failures:
                Retained = {"Pipe" + str(Index): Handle.value for Index, Handle in enumerate(Pipes)}
                Retained.update({"Descriptor" + str(Index): Descriptor for Index, Descriptor in enumerate(Descriptors)})
                Retained.update({Name: Handle for Name, Handle in
                                (("Job", Job), ("Thread", Information.Thread), ("Process", Information.Process)) if Handle})
                RaiseCleanupError(Failures, Retained, FailedStreams)
    return Process, Job
