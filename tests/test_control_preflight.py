"""Control-only legacy registration proof with no physical side effects."""

import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from agent_coordinator import legacy, transport


class CheckedLocalRun:
    Calls = []

    def __init__(self, Config, Log):
        self.Role = Config["Role"]

    def Check(self):
        self.Calls.append((self.Role, "Check"))

    def Cleanup(self):
        self.Calls.append((self.Role, "Cleanup"))
        return []

    def ArmCapture(self):
        raise AssertionError("control preflight armed capture")

    def Start(self):
        raise AssertionError("control preflight started a probe")

    def Status(self):
        raise AssertionError("control preflight inspected a probe")


class ControlPreflightTests(unittest.TestCase):
    def setUp(self):
        self.Temp = tempfile.TemporaryDirectory()
        self.Root = Path(self.Temp.name)
        CheckedLocalRun.Calls = []
        with socket.socket() as Sock:
            Sock.bind(("127.0.0.1", 0))
            Port = Sock.getsockname()[1]
        self.Config = {"RunId": "870c114b-44e5-4e43-a677-59b010f388d9",
                       "Token": "a" * 64, "CoordinatorHost": "127.0.0.1",
                       "PeerIps": {"CLIENT": "127.0.0.1", "SERVER": "127.0.0.1"},
                       "Port": Port, "Endpoint": "127.0.0.1:12345",
                       "ArtifactSHA256": "A" * 64, "StageTimeout": 1,
                       "RunTimeout": 1,
                       "EvidenceDir": str(self.Root / "coordinator")}

    def tearDown(self):
        self.Temp.cleanup()

    def StartCoordinator(self):
        Result = {}

        def Run():
            Result["Code"] = legacy.ControlPreflightCoordinator(self.Config, lambda Value: None)

        Thread = threading.Thread(target=Run)
        Thread.start()
        Deadline = time.monotonic() + 2
        Ready = self.Root / "coordinator" / "control.jsonl"
        while time.monotonic() < Deadline:
            if Ready.exists() and '"Event": "LISTENER_READY"' in Ready.read_text():
                return Thread, Result
            time.sleep(0.005)
        self.fail("control listener did not become ready")

    def StartEndpoint(self, Role):
        Config = {**self.Config, "Role": Role,
                  "EvidenceDir": str(self.Root / Role.lower())}
        Result = {}

        def Run():
            Result["Code"] = legacy.ControlPreflightEndpoint(
                Config, lambda Value: None, CheckedLocalRun)

        Thread = threading.Thread(target=Run)
        Thread.start()
        return Thread, Result

    def RawStage(self, Role, *, Token=None, Source="127.0.0.1"):
        Sock = socket.socket()
        Sock.bind((Source, 0))
        Sock.connect((self.Config["CoordinatorHost"], self.Config["Port"]))
        Log = transport.Journal(self.Root / ("raw-" + Role))
        Link = transport.Channel(Sock, {**self.Config, "Token": Token or self.Config["Token"]}, Log)
        Link.Send("STAGE_READY", Role=Role,
                  ArtifactSHA256=self.Config["ArtifactSHA256"],
                  Endpoint=self.Config["Endpoint"])
        return Link

    def test_both_actual_endpoint_paths_exchange_authenticated_stage_and_cleanup(self):
        Coordinator, CoordinatorResult = self.StartCoordinator()
        Client, ClientResult = self.StartEndpoint("CLIENT")
        Server, ServerResult = self.StartEndpoint("SERVER")
        for Thread in (Client, Server, Coordinator):
            Thread.join(3)
            self.assertFalse(Thread.is_alive())
        self.assertEqual([ClientResult, ServerResult, CoordinatorResult],
                         [{"Code": 0}, {"Code": 0}, {"Code": 0}],
                         (self.Root / "coordinator" / "result.json").read_text())
        self.assertCountEqual(CheckedLocalRun.Calls,
                              [("CLIENT", "Check"), ("CLIENT", "Cleanup"),
                               ("SERVER", "Check"), ("SERVER", "Cleanup")])
        for Name in ("coordinator", "client", "server"):
            Directory = self.Root / Name
            Result = json.loads((Directory / "result.json").read_text())
            self.assertTrue(Result["Success"])
            self.assertEqual(Result["Classification"], "CONTROL_PREFLIGHT_ONLY")
            Control = (Directory / "control.jsonl").read_text()
            self.assertNotIn(self.Config["Token"], Control)
            self.assertNotIn("ARM_CAPTURE", Control)
            self.assertNotIn("START_SERVER", Control)
        CoordinatorControl = (self.Root / "coordinator" / "control.jsonl").read_text()
        self.assertIn('"Event": "LISTENER_READY"', CoordinatorControl)
        self.assertIn('"Type": "STAGE_READY"', CoordinatorControl)
        for Role in ("client", "server"):
            Control = (self.Root / Role / "control.jsonl").read_text()
            self.assertIn('"Event": "CONNECT_START"', Control)
            self.assertIn('"Event": "CONNECT_OK"', Control)

    def test_wrong_token_role_and_source_fail_closed(self):
        for Kind in ("token", "role", "source"):
            with self.subTest(Kind=Kind):
                self.Config["EvidenceDir"] = str(self.Root / ("coordinator-" + Kind))
                if Kind == "source":
                    self.Config["PeerIps"]["SERVER"] = "127.0.0.2"
                Result = {}

                def Run():
                    Result["Code"] = legacy.ControlPreflightCoordinator(
                        self.Config, lambda Value: None)

                Thread = threading.Thread(target=Run)
                Thread.start()
                Ready = Path(self.Config["EvidenceDir"]) / "control.jsonl"
                Deadline = time.monotonic() + 2
                while time.monotonic() < Deadline and (
                        not Ready.exists() or '"Event": "LISTENER_READY"' not in Ready.read_text()):
                    time.sleep(0.005)
                self.assertTrue(Ready.exists())
                Role = "SERVER" if Kind == "source" else "BAD" if Kind == "role" else "CLIENT"
                Link = self.RawStage(Role, Token="b" * 64 if Kind == "token" else None)
                Thread.join(3)
                self.assertFalse(Thread.is_alive())
                self.assertEqual(Result, {"Code": 1})
                self.assertFalse(json.loads((Path(self.Config["EvidenceDir"]) / "result.json").read_text())["Success"])
                Link.Socket.close()
                Link.Journal.Stream.close()
                self.Config["PeerIps"]["SERVER"] = "127.0.0.1"

    def test_missing_second_role_times_out_and_releases_listener(self):
        self.Config["StageTimeout"] = 0.2
        Coordinator, Result = self.StartCoordinator()
        Client = self.RawStage("CLIENT")
        Coordinator.join(2)
        self.assertFalse(Coordinator.is_alive())
        self.assertEqual(Result, {"Code": 1})
        self.assertFalse(json.loads((self.Root / "coordinator" / "result.json").read_text())["Success"])
        Client.Socket.close()
        Client.Journal.Stream.close()
        with socket.socket() as Sock:
            Sock.bind((self.Config["CoordinatorHost"], self.Config["Port"]))

    def test_production_endpoint_uses_same_connect_helper_and_cleans_on_failure(self):
        Config = {**self.Config, "Role": "SERVER",
                  "EvidenceDir": str(self.Root / "production")}
        with mock.patch.object(legacy, "ConnectEndpoint", side_effect=OSError("blocked")) as Connect:
            Code = legacy.Endpoint(Config, lambda Value: None, CheckedLocalRun)
        self.assertEqual(Code, 1)
        Connect.assert_called_once()
        self.assertEqual(CheckedLocalRun.Calls,
                         [("SERVER", "Check"), ("SERVER", "Cleanup")])

    def test_unreachable_listener_records_connect_failure_and_cleans(self):
        Config = {**self.Config, "Role": "SERVER",
                  "EvidenceDir": str(self.Root / "unreachable")}
        Code = legacy.ControlPreflightEndpoint(Config, lambda Value: None, CheckedLocalRun)
        self.assertEqual(Code, 1)
        self.assertEqual(CheckedLocalRun.Calls,
                         [("SERVER", "Check"), ("SERVER", "Cleanup")])
        Control = (Path(Config["EvidenceDir"]) / "control.jsonl").read_text()
        self.assertIn('"Event": "CONNECT_START"', Control)
        self.assertIn('"Event": "CONNECT_FAILED"', Control)
        self.assertNotIn('"Type": "STAGE_READY"', Control)

    def test_physical_command_is_rejected_without_invoking_local_work(self):
        Config = {**self.Config, "Role": "SERVER",
                  "EvidenceDir": str(self.Root / "wrong-command")}
        with socket.socket() as Listener:
            Listener.bind(("127.0.0.1", Config["Port"]))
            Listener.listen(1)
            Listener.settimeout(2)
            Result = {}

            def Run():
                Result["Code"] = legacy.ControlPreflightEndpoint(
                    Config, lambda Value: None, CheckedLocalRun)

            Thread = threading.Thread(target=Run)
            Thread.start()
            Sock, _ = Listener.accept()
            Log = transport.Journal(self.Root / "wrong-command-coordinator")
            Link = transport.Channel(Sock, Config, Log)
            Deadline = time.monotonic() + 2
            while time.monotonic() < Deadline:
                Row = Link.Read()
                if Row is not None:
                    break
            self.assertEqual(Row["Type"], "STAGE_READY")
            Link.Send("ARM_CAPTURE")
            while time.monotonic() < Deadline:
                Row = Link.Read()
                if Row is not None:
                    break
            self.assertEqual(Row["Type"], "FAILED")
            Thread.join(2)
            self.assertFalse(Thread.is_alive())
            Link.Socket.close()
            Log.Stream.close()
        self.assertEqual(Result, {"Code": 1})
        self.assertEqual(CheckedLocalRun.Calls,
                         [("SERVER", "Check"), ("SERVER", "Cleanup")])


if __name__ == "__main__":
    unittest.main()
