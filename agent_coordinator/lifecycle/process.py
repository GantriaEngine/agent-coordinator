"""Owned local process-tree lifetime; no external process selector API."""
import os
import signal


class ProcessTree:
    def __init__(self, Process):
        self.Process = Process
        self.Closed = False
        self.Handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            Kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            Kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            Kernel.CreateJobObjectW.restype = wintypes.HANDLE
            Kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            Kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            Kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            Kernel.TerminateJobObject.restype = wintypes.BOOL
            Kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            Kernel.CloseHandle.restype = wintypes.BOOL
            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]
            class Counters(ctypes.Structure):
                _fields_ = [(Name, ctypes.c_uint64) for Name in ("ReadOperationCount", "WriteOperationCount",
                             "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
            Kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
            Kernel.SetInformationJobObject.restype = wintypes.BOOL
            self.Kernel = Kernel
            self.Handle = Kernel.CreateJobObjectW(None, None)
            Limits = Extended()
            Limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if (not self.Handle or not Kernel.SetInformationJobObject(self.Handle, 9, ctypes.byref(Limits), ctypes.sizeof(Limits))
                    or not Kernel.AssignProcessToJobObject(self.Handle, wintypes.HANDLE(Process._handle))):
                if self.Handle:
                    Kernel.CloseHandle(self.Handle)
                    self.Handle = None
                Process.kill()
                Process.wait(timeout=10)
                raise OSError("cannot confine owned agent process tree")

    def Close(self):
        if self.Closed:
            return
        self.Closed = True
        if os.name == "nt":
            if self.Handle:
                self.Kernel.TerminateJobObject(self.Handle, 1)
                self.Kernel.CloseHandle(self.Handle)
                self.Handle = None
        else:
            try:
                os.killpg(self.Process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.Process.wait(timeout=10)
