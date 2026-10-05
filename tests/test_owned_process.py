"""Owned temporary children only; no service, endpoint or physical workload."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import ExitStack

from agent_coordinator.owned_process import OwnedProcess


@unittest.skipUnless(os.name == "nt", "Windows Job ownership")
class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        from agent_coordinator import windows_owned_process as Windows
        self.Windows = Windows
        self.Temporary = tempfile.TemporaryDirectory()
        def CleanupTemporary():
            Deadline = time.monotonic() + 2
            while True:
                try:
                    self.Temporary.cleanup()
                    return
                except PermissionError as Error:
                    if Error.winerror != 32 or time.monotonic() >= Deadline:
                        raise
                    time.sleep(0.01)
        self.addCleanup(CleanupTemporary)
        self.Root = Path(self.Temporary.name)

    def Start(self, Code):
        File = self.Root / "owned.py"
        File.write_text(Code, encoding="utf-8")
        Process, Tree = OwnedProcess.Start([sys.executable, "-I", "-B", str(File)], self.Root)
        Process.stdin.close()
        def Cleanup():
            if not Tree.Closed:
                Tree.Close()
            for Stream in (Process.stdout, Process.stderr):
                Stream.close()
        self.addCleanup(Cleanup)
        return Process, Tree

    def test_nonzero_natural_exit_is_reaped_independently(self):
        Process, Tree = self.Start("import sys\nprint('failure')\nsys.exit(7)\n")
        self.assertEqual(Process.wait(timeout=5), 7)
        self.assertTrue(Tree.Close())
        self.assertTrue(Tree.Reaped)
        self.assertEqual(Process.poll(), 7)
        self.assertEqual(Process.stdout.read(), b"failure\r\n")
        self.assertTrue(Tree.Close())

    def test_exited_root_leaves_descendant_owned_until_close(self):
        Process, Tree = self.Start("import subprocess,sys\n"
            "subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(120)'])\n"
            "sys.exit(7)\n")
        self.assertEqual(Process.wait(timeout=5), 7)
        Accounting = self.Windows.BasicAccounting()
        self.Windows.Check(self.Windows.Kernel.QueryInformationJobObject(Tree.Handle, 1,
            self.Windows.ctypes.byref(Accounting), self.Windows.ctypes.sizeof(Accounting), None), "query test Job")
        self.assertGreater(Accounting.ActiveProcesses, 0)
        self.assertTrue(Tree.Close())
        self.assertEqual(Process.stdout.read(), b"")  # inherited pipe EOF proves descendant exit too
        self.assertTrue(Tree.Reaped)

    def test_running_root_and_descendant_are_stopped(self):
        Process, Tree = self.Start("import subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(120)'])\n"
            "print('started',flush=True)\ntime.sleep(120)\n")
        self.assertEqual(Process.stdout.readline(), b"started\r\n")
        self.assertTrue(Tree.Close())
        self.assertIsNotNone(Process.poll())
        self.assertEqual(Process.stdout.read(), b"")

    def test_nested_powershell_and_direct_helper_inherit_owned_job(self):
        PowerShell = Path(shutil.which("pwsh") or "missing-powershell-7")
        self.assertTrue(PowerShell.is_file(), "qualified Windows test runtime requires PowerShell 7")
        Child = self.Root / "helper.py"
        Child.write_text("from pathlib import Path\nimport time\nPath('helper-ready.txt').write_text('ready')\ntime.sleep(120)\n")
        Script = self.Root / "supervisor.ps1"
        Executable = str(Path(sys.executable)).replace("'", "''")
        Script.write_text("$ErrorActionPreference='Stop'\n"
            "$Info=[Diagnostics.ProcessStartInfo]::new()\n$Info.UseShellExecute=$false\n"
            "$Info.CreateNoWindow=$true\n$Info.FileName='" + Executable + "'\n"
            "$Info.ArgumentList.Add('-I');$Info.ArgumentList.Add('-B');$Info.ArgumentList.Add('helper.py')\n"
            "$Child=[Diagnostics.Process]::Start($Info)\nStart-Sleep -Seconds 120\n")
        Process, Tree = self.Start("import subprocess,sys,time\nfrom pathlib import Path\n"
            "subprocess.Popen(" + repr([str(PowerShell), "-NoProfile", "-NonInteractive", "-File", str(Script)]) +
            ",creationflags=subprocess.CREATE_NO_WINDOW)\n"
            "deadline=time.monotonic()+10\n"
            "while not Path('helper-ready.txt').exists():\n"
            "    if time.monotonic()>deadline: raise RuntimeError('helper readiness timeout')\n"
            "    time.sleep(.01)\nraise SystemExit(7)\n")
        self.assertEqual(Process.wait(timeout=12), 7)
        Accounting = self.Windows.BasicAccounting()
        self.Windows.Check(self.Windows.Kernel.QueryInformationJobObject(Tree.Handle, 1,
            self.Windows.ctypes.byref(Accounting), self.Windows.ctypes.sizeof(Accounting), None), "nested test Job")
        self.assertGreaterEqual(Accounting.ActiveProcesses, 2)
        self.assertTrue(Tree.Close())
        self.assertTrue(Tree.Reaped)
        self.assertEqual(Process.stdout.read(), b"")

    def test_failed_proof_closes_job_and_caches_exit_without_claim(self):
        Process, Tree = self.Start("import time\nprint('running',flush=True)\ntime.sleep(120)\n")
        self.assertEqual(Process.stdout.readline(), b"running\r\n")
        with mock.patch.object(self.Windows, "StopJob", side_effect=OSError("query denied")):
            with self.assertRaises(OSError):
                Tree.Close()
        self.assertFalse(Tree.Reaped)
        self.assertIsNotNone(Process.returncode)
        self.assertEqual(Process.stdout.read(), b"")

    def test_failed_termination_does_not_claim_reaped(self):
        with mock.patch.object(self.Windows.Kernel, "TerminateJobObject", return_value=0):
            with self.assertRaises(OSError):
                self.Windows.StopJob(123)

    def test_failed_member_query_does_not_claim_reaped(self):
        with mock.patch.object(self.Windows.Kernel, "TerminateJobObject", return_value=1), \
                mock.patch.object(self.Windows.Kernel, "QueryInformationJobObject", return_value=0):
            with self.assertRaises(OSError):
                self.Windows.StopJob(123)

    def test_active_members_at_deadline_deny_reaping(self):
        def Query(_Handle, _Kind, Pointer, _Size, _Length):
            Pointer._obj.ActiveProcesses = 1
            return 1
        with mock.patch.object(self.Windows.Kernel, "TerminateJobObject", return_value=1), \
                mock.patch.object(self.Windows.Kernel, "QueryInformationJobObject", side_effect=Query), \
                mock.patch.object(self.Windows.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaises(TimeoutError):
                self.Windows.StopJob(123)

    def test_cleanup_failure_keeps_false_and_closes_owned_handles(self):
        Process = mock.Mock()
        Tree = OwnedProcess(Process, 123)
        with mock.patch.object(self.Windows, "StopJob", side_effect=OSError("query denied")), \
                mock.patch.object(self.Windows, "Close") as Close:
            with self.assertRaises(OSError):
                Tree.Close()
            Close.assert_called_once_with(123)
            Process.Close.assert_called_once()
        self.assertFalse(Tree.Reaped)
        with self.assertRaises(OSError):
            Tree.Close()

    def test_cleanup_waits_share_one_deadline_without_reset(self):
        Process = mock.Mock()
        Tree = OwnedProcess(Process, 123)
        with mock.patch.object(self.Windows, "StopJob") as Stop, \
                mock.patch.object(self.Windows, "Close"), \
                mock.patch("agent_coordinator.owned_process.time.monotonic", side_effect=[100, 106, 111]):
            self.assertTrue(Tree.Close())
        Stop.assert_called_once_with(123, 110)
        self.assertEqual(Process.wait.call_args_list,
                         [mock.call(timeout=4), mock.call(timeout=0)])
        Process.Close.assert_called_once()
        self.assertTrue(Tree.Reaped)

    def test_close_handle_false_is_not_a_successful_release(self):
        with mock.patch.object(self.Windows.Kernel, "CloseHandle", return_value=0):
            with self.assertRaises(OSError):
                self.Windows.Close(123)

    def test_zero_members_does_not_hide_failed_job_handle_release(self):
        Process, Tree = self.Start("raise SystemExit(7)\n")
        self.assertEqual(Process.wait(timeout=5), 7)
        Job = Tree.Handle
        ActualClose = self.Windows.Kernel.CloseHandle
        with mock.patch.object(self.Windows.Kernel, "CloseHandle", side_effect=lambda Handle:
                0 if Handle == Job else ActualClose(Handle)):
            with self.assertRaises(self.Windows.CleanupError) as Failure:
                Tree.Close()
        self.assertFalse(Tree.Reaped)
        self.assertEqual(Tree.Handle, Job)
        self.assertEqual(Failure.exception.RetainedHandles, {"Job": Job})
        self.assertIn("Job handle release", str(Failure.exception))
        self.Windows.Close(Tree.Handle)
        Tree.Handle = None

    def test_failed_process_handle_release_keeps_handle_identity(self):
        Information = self.Windows.ProcessInformation()
        Information.Process = 123
        Process = self.Windows.WindowsProcess(Information, None, None, None)
        with mock.patch.object(self.Windows.Kernel, "CloseHandle", return_value=0):
            with self.assertRaises(OSError):
                Process.Close()
        self.assertEqual(Process._handle, 123)

    def test_failed_job_and_root_releases_are_aggregated_without_false_reap(self):
        Process, Tree = self.Start("raise SystemExit(7)\n")
        self.assertEqual(Process.wait(timeout=5), 7)
        Job, Root = Tree.Handle, Process._handle
        with mock.patch.object(self.Windows.Kernel, "CloseHandle", return_value=0) as Close:
            with self.assertRaises(self.Windows.CleanupError) as Failure:
                Tree.Close()
        self.assertFalse(Tree.Reaped)
        self.assertEqual(Close.call_args_list, [mock.call(Job), mock.call(Root)])
        self.assertEqual(Failure.exception.RetainedHandles, {"Job": Job, "Process": Root})
        self.assertEqual(Tree.Handle, Job)
        self.assertEqual(Process._handle, Root)
        self.Windows.Close(Job)
        Tree.Handle = None
        Process.Close()

    def MockStartup(self, CloseAction, Resume=1, Streams=None):
        Stack = ExitStack()
        self.addCleanup(Stack.close)
        Kernel = self.Windows.Kernel
        def Initialize(_List, _Count, _Flags, Size):
            Size._obj.value = 256
            return 1
        def Create(*Args):
            Information = Args[-1]._obj
            Information.Process, Information.Thread = 200, 201
            Information.ProcessId = 202
            return 1
        for Name, Value in (("CreateJobObjectW", 100), ("SetInformationJobObject", 1),
                            ("SetHandleInformation", 1), ("UpdateProcThreadAttribute", 1),
                            ("ResumeThread", Resume), ("DeleteProcThreadAttributeList", None)):
            Stack.enter_context(mock.patch.object(Kernel, Name, return_value=Value))
        Stack.enter_context(mock.patch.object(Kernel, "InitializeProcThreadAttributeList", side_effect=Initialize))
        Stack.enter_context(mock.patch.object(Kernel, "CreateProcessW", side_effect=Create))
        Close = Stack.enter_context(mock.patch.object(Kernel, "CloseHandle", side_effect=CloseAction))
        Stack.enter_context(mock.patch.object(self.Windows, "Pipe", side_effect=[
            (self.Windows.HANDLE(1), self.Windows.HANDLE(2)),
            (self.Windows.HANDLE(3), self.Windows.HANDLE(4)),
            (self.Windows.HANDLE(5), self.Windows.HANDLE(6))]))
        Stop = Stack.enter_context(mock.patch.object(self.Windows, "StopJob"))
        if Streams is not None:
            Stack.enter_context(mock.patch.object(self.Windows.msvcrt, "open_osfhandle", side_effect=[2, 3, 5]))
            Stack.enter_context(mock.patch.object(self.Windows.os, "fdopen", side_effect=Streams))
        return Close, Stop

    def test_start_rollback_attempts_all_handles_after_one_close_denial(self):
        def Value(Handle):
            return getattr(Handle, "value", Handle)
        Close, Stop = self.MockStartup(lambda Handle: 0 if Value(Handle) == 1 else 1)
        with self.assertRaises(self.Windows.CleanupError) as Failure:
            self.Windows.Start([sys.executable], self.Root)
        self.assertEqual([Value(Call.args[0]) for Call in Close.call_args_list],
                         [1, 1, 2, 3, 4, 5, 6, 100, 201, 200])
        self.assertEqual(Failure.exception.RetainedHandles, {"Pipe0": 1})
        Stop.assert_called_once()
        self.assertEqual(Stop.call_args.args[0], 100)

    def test_start_rollback_zero_member_proof_does_not_hide_job_release_denial(self):
        Close, Stop = self.MockStartup(lambda Handle: 0 if getattr(Handle, "value", Handle) in (1, 100) else 1)
        with self.assertRaises(self.Windows.CleanupError) as Failure:
            self.Windows.Start([sys.executable], self.Root)
        self.assertEqual(Failure.exception.RetainedHandles, {"Pipe0": 1, "Job": 100})
        self.assertEqual([getattr(Call.args[0], "value", Call.args[0]) for Call in Close.call_args_list][-2:], [201, 200])
        Stop.assert_called_once()

    def test_start_rollback_keeps_failed_stream_and_attempts_other_releases(self):
        Streams = [mock.Mock(), mock.Mock(), mock.Mock()]
        Streams[0].close.side_effect = OSError("supplied stream release denied")
        Close, Stop = self.MockStartup(lambda _Handle: 1, Resume=0xFFFFFFFF, Streams=Streams)
        with self.assertRaises(self.Windows.CleanupError) as Failure:
            self.Windows.Start([sys.executable], self.Root)
        for Stream in Streams:
            Stream.close.assert_called_once()
        self.assertEqual(Failure.exception.FailedStreams, (Streams[0],))
        self.assertEqual(Failure.exception.RetainedHandles, {})
        self.assertEqual([getattr(Call.args[0], "value", Call.args[0]) for Call in Close.call_args_list],
                         [1, 4, 6, 100, 201, 200])
        Stop.assert_called_once()

    def test_start_descriptor_transfer_failure_retains_single_owner_without_double_close(self):
        Close, Stop = self.MockStartup(lambda _Handle: 1)
        with mock.patch.object(self.Windows.msvcrt, "open_osfhandle", return_value=222), \
                mock.patch.object(self.Windows.os, "fdopen", side_effect=OSError("supplied fdopen failure")), \
                mock.patch.object(self.Windows.os, "close", side_effect=OSError("supplied descriptor release denied")) as DescriptorClose:
            with self.assertRaises(self.Windows.CleanupError) as Failure:
                self.Windows.Start([sys.executable], self.Root)
        DescriptorClose.assert_called_once_with(222)
        self.assertEqual(Failure.exception.RetainedHandles, {"Descriptor0": 222})
        self.assertEqual([getattr(Call.args[0], "value", Call.args[0]) for Call in Close.call_args_list],
                         [1, 4, 6, 3, 5, 100, 201, 200])
        Stop.assert_called_once()

    def test_cleanup_interruption_still_attempts_all_releases_then_reraises(self):
        Process = mock.Mock()
        Process._handle = None
        Tree = OwnedProcess(Process, 123)
        Interrupted = KeyboardInterrupt("supplied interruption")
        with mock.patch.object(self.Windows, "StopJob", side_effect=Interrupted), \
                mock.patch.object(self.Windows, "Close") as Close:
            with self.assertRaises(KeyboardInterrupt) as Failure:
                Tree.Close()
        self.assertIs(Failure.exception, Interrupted)
        self.assertFalse(Tree.Reaped)
        self.assertIsNone(Tree.Handle)
        Close.assert_called_once_with(123)
        Process.wait.assert_called_once()
        Process.Close.assert_called_once()
        self.assertEqual(Interrupted.CleanupError.RetainedHandles, {})
        self.assertIs(Interrupted.CleanupError.Failures[0][1], Interrupted)

    def test_start_rollback_release_interruption_continues_then_reraises(self):
        Interrupted = SystemExit("supplied release interruption")
        def CloseAction(Handle):
            if getattr(Handle, "value", Handle) == 1:
                raise Interrupted
            return 1
        Close, Stop = self.MockStartup(CloseAction)
        with self.assertRaises(SystemExit) as Failure:
            self.Windows.Start([sys.executable], self.Root)
        self.assertIs(Failure.exception, Interrupted)
        self.assertEqual([getattr(Call.args[0], "value", Call.args[0]) for Call in Close.call_args_list],
                         [1, 1, 2, 3, 4, 5, 6, 100, 201, 200])
        self.assertEqual(Interrupted.CleanupError.RetainedHandles, {"Pipe0": 1})
        Stop.assert_called_once()

    def test_start_defers_transfer_until_attribute_cleanup_completes(self):
        Streams = [mock.Mock(), mock.Mock(), mock.Mock()]
        Close, Stop = self.MockStartup(lambda _Handle: 1, Streams=Streams)
        Interrupted = KeyboardInterrupt("supplied attribute cleanup interruption")
        with mock.patch.object(self.Windows.Kernel, "DeleteProcThreadAttributeList", side_effect=Interrupted):
            with self.assertRaises(KeyboardInterrupt) as Failure:
                self.Windows.Start([sys.executable], self.Root)
        self.assertIs(Failure.exception, Interrupted)
        for Stream in Streams:
            Stream.close.assert_called_once()
        self.assertEqual([getattr(Call.args[0], "value", Call.args[0]) for Call in Close.call_args_list],
                         [1, 4, 6, 201, 100, 200])
        Stop.assert_called_once()
        self.assertEqual(Interrupted.CleanupError.RetainedHandles, {})

    def test_missing_executable_cannot_create_orphan(self):
        with self.assertRaises(FileNotFoundError):
            OwnedProcess.Start([str(self.Root / "missing.exe")], self.Root)

    def test_resume_failure_rolls_back_job_at_creation(self):
        with mock.patch.object(self.Windows.Kernel, "ResumeThread", return_value=0xFFFFFFFF):
            with self.assertRaises(OSError):
                OwnedProcess.Start([sys.executable, "-I", "-c", "raise SystemExit(0)"], self.Root)


class BoundaryTests(unittest.TestCase):
    def test_platform_without_checked_job_fails_closed(self):
        with mock.patch("agent_coordinator.owned_process.os.name", "posix"):
            with self.assertRaises(OSError):
                OwnedProcess.Start(["unused"], ".")


if __name__ == "__main__":
    unittest.main()
