"""Local installed-adapter ownership; no wire path, argv or process selector."""
import os
import time


class OwnedProcess:
    def __init__(self, Process, Handle):
        self.Process, self.Handle = Process, Handle
        self.Closed = self.Reaped = False

    @classmethod
    def Start(cls, Args, Directory):
        if os.name != "nt":
            raise OSError("checked owned Job lifetime requires Windows")
        from .windows_owned_process import Start
        Process, Handle = Start(Args, Directory)
        return Process, cls(Process, Handle)

    def Close(self):
        if self.Closed:
            if not self.Reaped:
                raise OSError("owned Job cleanup was not proven")
            return True
        self.Closed = True
        from .windows_owned_process import Close, StopJob, RaiseCleanupError
        Deadline = time.monotonic() + 10
        def Remaining():
            return max(0, Deadline - time.monotonic())
        Failures = []
        try:
            StopJob(self.Handle, Deadline)
            self.Process.wait(timeout=Remaining())
        except BaseException as Error:
            Failures.append(("Job member/root exit proof", Error))
        try:
            Close(self.Handle)
            self.Handle = None
        except BaseException as Error:
            Failures.append(("Job handle release", Error))
        try:
            # A checked Job close also enforces kill-on-close if proof failed.
            # Cache the root exit before releasing its owned handle.
            self.Process.wait(timeout=Remaining())
        except BaseException as Error:
            Failures.append(("fallback root exit proof", Error))
        try:
            self.Process.Close()
        except BaseException as Error:
            Failures.append(("root process handle release", Error))
        if Failures:
            RaiseCleanupError(Failures, {Name: Handle for Name, Handle in
                (("Job", self.Handle), ("Process", getattr(self.Process, "_handle", None))) if Handle})
        self.Reaped = True
        return True
