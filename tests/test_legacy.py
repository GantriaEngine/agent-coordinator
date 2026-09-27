import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from agent_coordinator import transport, legacy

Q = SimpleNamespace(**{Name: getattr(transport, Name) for Name in ("Journal", "Channel", "Digest", "MAX_FRAME")})
Q.Coordinator = lambda Config: legacy.Coordinator(Config, lambda Value: None)

class ExtractedProtocolTests(unittest.TestCase):
    def setUp(self):
        self.Temp = tempfile.TemporaryDirectory()
        self.Root = Path(self.Temp.name)
        with socket.socket() as Sock:
            Sock.bind(("127.0.0.1", 0))
            Port = Sock.getsockname()[1]
        self.Config = {"RunId": "870c114b-44e5-4e43-a677-59b010f388d9", "Token": "a" * 64,
                       "CoordinatorHost": "127.0.0.1", "PeerIps": {"CLIENT": "127.0.0.1", "SERVER": "127.0.0.1"},
                       "Port": Port, "Endpoint": "127.0.0.1:12345", "ArtifactSHA256": "A" * 64,
                       "StageTimeout": 2, "RunTimeout": 2, "EvidenceDir": str(self.Root / "coordinator")}
        self.Thread = None
        self.Links = []

    def tearDown(self):
        for Link in self.Links:
            Link.Socket.close()
            Link.Journal.Stream.close()
        if self.Thread:
            self.Thread.join(5)
            self.assertFalse(self.Thread.is_alive())
        self.Temp.cleanup()

    def Start(self):
        self.Code = None
        def Run():
            self.Code = Q.Coordinator(self.Config)
        self.Thread = threading.Thread(target=Run)
        self.Thread.start()

    def Peer(self, Role, **Overrides):
        Sock = socket.socket()
        Deadline = time.monotonic() + 2
        while True:
            try:
                Sock.connect(("127.0.0.1", self.Config["Port"]))
                break
            except ConnectionRefusedError:
                if time.monotonic() >= Deadline:
                    raise
                time.sleep(0.01)
        Log = Q.Journal(self.Root / (Role + str(len(self.Links))))
        Link = Q.Channel(Sock, {**self.Config, **Overrides}, Log)
        self.Links.append(Link)
        Link.Send("STAGE_READY", Role=Role, ArtifactSHA256=self.Config["ArtifactSHA256"], Endpoint=self.Config["Endpoint"])
        return Link

    def Read(self, Link, Expected):
        Deadline = time.monotonic() + 3
        while time.monotonic() < Deadline:
            Row = Link.Read()
            if Row:
                self.assertEqual(Expected, Row["Type"])
                return Row
        self.fail("missing " + Expected)

    def Finish(self, ExpectedCode):
        self.Thread.join(4)
        self.assertEqual(self.Code, ExpectedCode)
        Manifest = json.loads((self.Root / "coordinator" / "evidence-manifest.json").read_text())
        for Entry in Manifest["Files"]:
            self.assertEqual(Q.Digest(self.Root / "coordinator" / Entry["Path"]), Entry["SHA256"])
        self.assertNotIn(self.Config["Token"], (self.Root / "coordinator" / "control.jsonl").read_text())

    def test_success_and_signal_latency(self):
        self.Start()
        Client = self.Peer("CLIENT")
        Server = self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Client.Send("CAPTURE_LIVE")
        self.Read(Server, "START_SERVER")
        Sent = time.monotonic()
        Server.Send("SERVER_LIVE", Pid=123, Endpoint=self.Config["Endpoint"])
        self.Read(Client, "START_CLIENT")
        self.assertLess(time.monotonic() - Sent, 0.5)
        Client.Send("CLIENT_RUNNING", Pid=456)
        Client.Send("CLIENT_DONE", Success=True)
        self.Read(Server, "FINALIZE")
        Server.Send("SERVER_DONE", Success=True)
        self.Read(Client, "RUN_DONE")
        self.Read(Server, "RUN_DONE")
        self.Finish(0)

    def test_failed_probe_finalizes_both_endpoints(self):
        self.Start()
        Client, Server = self.Peer("CLIENT"), self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Client.Send("CAPTURE_LIVE")
        self.Read(Server, "START_SERVER")
        Server.Send("SERVER_LIVE", Pid=123, Endpoint=self.Config["Endpoint"])
        self.Read(Client, "START_CLIENT")
        Client.Send("CLIENT_DONE", Success=False)
        self.Read(Server, "FINALIZE")
        Server.Send("SERVER_DONE", Success=False)
        self.Read(Client, "RUN_DONE")
        self.Read(Server, "RUN_DONE")
        self.Finish(1)

    def test_server_finishes_before_client(self):
        self.Start()
        Client, Server = self.Peer("CLIENT"), self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Client.Send("CAPTURE_LIVE")
        self.Read(Server, "START_SERVER")
        Server.Send("SERVER_LIVE", Pid=123, Endpoint=self.Config["Endpoint"])
        self.Read(Client, "START_CLIENT")
        Server.Send("SERVER_DONE", Success=True)
        Client.Send("CLIENT_DONE", Success=True)
        self.Read(Client, "RUN_DONE")
        self.Read(Server, "RUN_DONE")
        self.Finish(0)

    def test_wrong_token_aborts(self):
        self.Start()
        Client = self.Peer("CLIENT", Token="b" * 64)
        with self.assertRaises(ValueError):
            self.Read(Client, "ABORT")  # Coordinator envelope uses the expected token.
        self.Finish(1)

    def test_prior_run_cannot_trigger_start(self):
        self.Start()
        Client = self.Peer("CLIENT", RunId="870c114b-44e5-4e43-a677-59b010f388d8")
        with self.assertRaises(ValueError):
            self.Read(Client, "ABORT")
        self.Finish(1)

    def test_duplicate_sequence_aborts(self):
        self.Start()
        Client, Server = self.Peer("CLIENT"), self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Client.Tx = 0
        Client.Send("CAPTURE_LIVE")
        self.Read(Client, "ABORT")
        self.Read(Server, "ABORT")
        self.Finish(1)

    def test_early_server_live_cannot_launch_client(self):
        self.Start()
        Client, Server = self.Peer("CLIENT"), self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Server.Send("SERVER_LIVE", Pid=123, Endpoint=self.Config["Endpoint"])
        self.Read(Client, "ABORT")
        self.Finish(1)

    def test_disconnect_aborts_surviving_peer(self):
        self.Start()
        Client, Server = self.Peer("CLIENT"), self.Peer("SERVER")
        self.Read(Client, "ARM_CAPTURE")
        Server.Socket.close()
        self.Read(Client, "ABORT")
        self.Finish(1)

    def test_barrier_timeout_does_not_launch(self):
        self.Config["StageTimeout"] = 1
        self.Start()
        Client = self.Peer("CLIENT")
        self.Read(Client, "ABORT")
        self.Finish(1)

    def test_oversized_frame_aborts(self):
        self.Start()
        Client = self.Peer("CLIENT")
        Client.Socket.sendall(b"x" * Q.MAX_FRAME)
        self.Read(Client, "ABORT")
        self.Finish(1)
