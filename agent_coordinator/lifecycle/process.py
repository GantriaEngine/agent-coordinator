"""Owned local process-tree lifetime; no external process selector API."""
import os
import signal
import subprocess

from ..transport import Hidden


class ProcessTree:
    def __init__(self, Process, Handle=None):
        if os.name == "nt" and Handle is None:
            raise ValueError("Windows process must enter Job at creation")
        self.Process = Process
        self.Handle = Handle
        self.Closed = False

    @classmethod
    def Start(cls, Args, Directory):
        if os.name == "nt":
            from .windows_process import Start
            Process, Handle = Start(Args, Directory)
            return Process, cls(Process, Handle)
        Process = subprocess.Popen(Args, cwd=Directory, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, **Hidden())
        return Process, cls(Process)

    def Close(self):
        if self.Closed:
            return
        self.Closed = True
        if os.name == "nt":
            from .windows_process import Close, Kernel
            try:
                if self.Handle:
                    Kernel.TerminateJobObject(self.Handle, 1)
            finally:
                Close(self.Handle)
                self.Handle = None
        else:
            try:
                os.killpg(self.Process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.Process.wait(timeout=10)
        finally:
            if os.name == "nt":
                self.Process.Close()
