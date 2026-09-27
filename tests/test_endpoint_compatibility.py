import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from types import SimpleNamespace
from agent_coordinator import legacy, transport
import test_legacy

class LocalRun:
    def __init__(self, Config, Log):
        self.Config, self.Log = Config, Log
        self.Probe = None
        self.Files = []
        self.FinalizeAt = None
    def Check(self): pass
    def ArmCapture(self): pass
    def Start(self): pass
    def ServerLive(self): return True
    def Status(self): pass
    def Result(self): return {"Success": self.Probe.returncode == 0, "Pid": self.Probe.pid}
    def Cleanup(self):
        if self.Probe is not None and self.Probe.poll() is None:
            self.Probe.terminate()
            self.Probe.wait(timeout=3)
        for File in self.Files: File.close()
        return []

Q = SimpleNamespace(**{Name: getattr(transport, Name) for Name in ("Channel", "Journal", "Hidden")})
Q.Endpoint = lambda Config: legacy.Endpoint(Config, lambda Value: None, LocalRun)

class EndpointCompatibilityTests(unittest.TestCase):
    setUp = test_legacy.ExtractedProtocolTests.setUp
    tearDown = test_legacy.ExtractedProtocolTests.tearDown
    Read = test_legacy.ExtractedProtocolTests.Read

    def test_worker_accepts_delayed_finalize_after_reporting_success(self):
        Config = {**self.Config, "Role": "SERVER", "EvidenceDir": str(self.Root / "late-finalize-worker"),
                  "CaptureCommand": ["test-only"]}
        Listener = socket.socket()
        Listener.bind(("127.0.0.1", 0))
        Config["Port"] = Listener.getsockname()[1]
        Listener.listen(1)
        Listener.settimeout(3)
        EndpointResult = {}

        def Start(Run):
            Output = (Run.Log.Directory / "probe.stdout.log").open("wb")
            Error = (Run.Log.Directory / "probe.stderr.log").open("wb")
            Run.Files.extend((Output, Error))
            Run.Probe = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(.15); print('[Probe:Readiness] result=pass'); print('[Probe:Cleanup] good=1')"],
                stdout=Output, stderr=Error, **Q.Hidden())
            Run.Started = time.monotonic()

        def Helper():
            EndpointResult["Code"] = Q.Endpoint(Config)

        Worker = threading.Thread(target=Helper)
        with mock.patch.object(LocalRun, "Check"), mock.patch.object(LocalRun, "ArmCapture"), \
             mock.patch.object(LocalRun, "Start", Start), mock.patch.object(LocalRun, "ServerLive", return_value=True):
            Worker.start()
            Sock, _ = Listener.accept()
            Link = Q.Channel(Sock, Config, Q.Journal(self.Root / "late-finalize-coordinator"))
            self.Links.append(Link)
            self.Read(Link, "STAGE_READY")
            Link.Send("START_SERVER")
            self.Read(Link, "SERVER_LIVE")
            ServerResult = self.Read(Link, "SERVER_DONE")
            self.assertTrue(ServerResult["Success"])
            # FINALIZE was in flight while the coordinator had not yet received
            # SERVER_DONE; deliver it after the result to force the observed race.
            Link.Send("FINALIZE")
            Link.Send("RUN_DONE", Success=True)
            Worker.join(4)
        Listener.close()
        self.assertFalse(Worker.is_alive())
        self.assertEqual(0, EndpointResult["Code"])
        Result = json.loads((Path(Config["EvidenceDir"]) / "result.json").read_text())
        self.assertTrue(Result["Success"])
        Control = (Path(Config["EvidenceDir"]) / "control.jsonl").read_text()
        self.assertIn('"Detail": "late FINALIZE after SERVER_DONE"', Control)
        self.assertNotIn('"Type": "FAILED"', Control)

    def test_worker_abort_during_live_probe_still_fails_closed(self):
        Config = {**self.Config, "Role": "SERVER", "EvidenceDir": str(self.Root / "abort-live-worker"),
                  "CaptureCommand": ["test-only"]}
        Listener = socket.socket()
        Listener.bind(("127.0.0.1", 0))
        Config["Port"] = Listener.getsockname()[1]
        Listener.listen(1)
        Listener.settimeout(3)
        EndpointResult = {}
        ProbeProcesses = []

        def Start(Run):
            Output = (Run.Log.Directory / "probe.stdout.log").open("wb")
            Error = (Run.Log.Directory / "probe.stderr.log").open("wb")
            Run.Files.extend((Output, Error))
            Run.Probe = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                         stdout=Output, stderr=Error, **Q.Hidden())
            ProbeProcesses.append(Run.Probe)
            Run.Started = time.monotonic()

        def Helper():
            EndpointResult["Code"] = Q.Endpoint(Config)

        Worker = threading.Thread(target=Helper)
        with mock.patch.object(LocalRun, "Check"), mock.patch.object(LocalRun, "ArmCapture"), \
             mock.patch.object(LocalRun, "Start", Start), mock.patch.object(LocalRun, "ServerLive", return_value=True):
            Worker.start()
            Sock, _ = Listener.accept()
            Link = Q.Channel(Sock, Config, Q.Journal(self.Root / "abort-live-coordinator"))
            self.Links.append(Link)
            self.Read(Link, "STAGE_READY")
            Link.Send("START_SERVER")
            self.Read(Link, "SERVER_LIVE")
            Link.Send("ABORT", Detail="test abort")
            Worker.join(4)
        Listener.close()
        self.assertFalse(Worker.is_alive())
        self.assertEqual(1, EndpointResult["Code"])
        self.assertIsNotNone(ProbeProcesses[0].poll())
        Result = json.loads((Path(Config["EvidenceDir"]) / "result.json").read_text())
        self.assertFalse(Result["Success"])
        self.assertEqual("ABORT", Result["Classification"])
