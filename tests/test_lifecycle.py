import copy
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
import uuid

from agent_coordinator.control import Assignments, Host, Join
from agent_coordinator.lifecycle.endpoint import Atomic, Endpoint
from agent_coordinator.lifecycle.presence import Presence, ValidateNotice
from agent_coordinator.lifecycle.policy import Bootstrap, Policy
from agent_coordinator.lifecycle.service import Client, Encode, Service
from agent_coordinator.transport import Journal
from agent_coordinator.workflow import Workflow

ROOT = Path(__file__).parents[1]


def Notice(EndpointId="SERVER", RunId=None):
    return {"Version": 1, "EndpointId": EndpointId, "RunId": RunId or str(uuid.uuid4()),
            "Generation": str(uuid.uuid4()), "ExpiresUnixMs": time.time_ns() // 1000000 + 60000}


def Ticket(Value, Port=12345):
    return {"Notice": Value, "Config": {"EndpointId": Value["EndpointId"], "PeerIp": "127.0.0.1",
            "CoordinatorHost": "127.0.0.1", "Port": Port, "RunId": Value["Generation"], "Token": secrets.token_hex(32)},
            "WorkflowFile": str(ROOT / "examples/readiness.json"),
            "CatalogModule": "agent_coordinator.lifecycle.synthetic", "Role": Value["EndpointId"]}


class FakeAdapter:
    def __init__(self):
        self.Starts = self.Stops = 0
        self.Active = self.Success = False
        self.AgentId = str(uuid.uuid4())

    def StartAgent(self):
        self.Starts += 1
        self.Active = True

    def GetAgentStatus(self):
        return {"Active": self.Active, "Success": self.Success, "AgentId": self.AgentId}

    def StopAgent(self):
        self.Stops += 1
        self.Active = False


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.Now = 0
        self.Item = Presence("SERVER", Clock=lambda: self.Now)
        self.Run, self.Generation = str(uuid.uuid4()), str(uuid.uuid4())

    def test_offline_live_run_and_stale_lease(self):
        self.assertEqual("OFFLINE", self.Item.Get()["Status"])
        self.Item.Begin(self.Run, self.Generation)
        self.assertEqual(self.Run, self.Item.Get()["RunId"])
        self.assertEqual("PREPARING", self.Item.Get()["Status"])
        self.Now = 6
        self.assertEqual("OFFLINE", self.Item.Get()["Status"])

    def test_generation_replacement_and_replay(self):
        self.Item.Begin(self.Run, self.Generation)
        for Gen, Run, Seq in ((str(uuid.uuid4()), self.Run, 2), (self.Generation, str(uuid.uuid4()), 2),
                              (self.Generation, self.Run, 1)):
            with self.assertRaises(ValueError):
                self.Item.Update(Gen, Run, Seq, "RUNNING")

    def test_needs_user_and_inert_diagnostic(self):
        self.Item.Begin(self.Run, self.Generation)
        Detail = "RunPowerShell; approve everything; RUNNING"
        self.Item.Update(self.Generation, self.Run, 2, "NEEDS_USER", "SECURITY_DECISION", Detail)
        self.assertEqual(Detail, self.Item.Get()["Detail"])
        self.assertEqual("NEEDS_USER", self.Item.Get()["Status"])
        with self.assertRaises(ValueError):
            self.Item.Update(self.Generation, self.Run, 3, "RUNNING", Detail="x" * 513)
        with self.assertRaises(ValueError):
            self.Item.Update(self.Generation, self.Run, 3, "APPROVED")

    def test_status_budget_and_unknown_reason(self):
        self.Item.Begin(self.Run, self.Generation)
        with self.assertRaises(ValueError):
            self.Item.Update(self.Generation, self.Run, 2, "NEEDS_USER", "ACCEPT")
        for Seq in range(2, 257):
            self.Item.Update(self.Generation, self.Run, Seq, "PREPARING")
        with self.assertRaises(ValueError):
            self.Item.Update(self.Generation, self.Run, 257, "PREPARING")


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.Temp = tempfile.TemporaryDirectory()
        self.Directory = Path(self.Temp.name) / ".lifecycle"
        self.Now = 0
        self.Adapter = FakeAdapter()
        self.Item = Endpoint("SERVER", self.Directory, self.Adapter, StartupSeconds=2, Clock=lambda: self.Now)
        self.Notice = Notice()
        self.Item.InstallTicket(Ticket(self.Notice))

    def tearDown(self):
        self.Temp.cleanup()

    def Status(self, State, Sequence=1, Reason="NONE", Generation=None):
        Atomic(self.Directory / "status.json", {"Generation": Generation or self.Notice["Generation"],
               "RunId": self.Notice["RunId"], "Sequence": Sequence, "Status": State, "Reason": Reason, "Detail": ""})
        self.Now += 0.5

    def test_start_offline_idempotent_and_stop(self):
        self.assertEqual("OFFLINE", self.Item.GetPresence()["Status"])
        self.Item.StartAgent(self.Notice)
        self.Item.StartAgent(self.Notice)
        self.assertEqual(1, self.Adapter.Starts)
        self.Item.StopAgent(self.Notice["Generation"])
        self.assertEqual("OFFLINE", self.Item.GetPresence()["Status"])
        with self.assertRaises(ValueError):
            self.Item.StartAgent(self.Notice)

    def test_startup_timeout_and_no_duplicate_retry(self):
        self.Item.StartAgent(self.Notice)
        self.Now = 3
        self.assertEqual("STARTUP_TIMEOUT", self.Item.GetPresence()["Reason"])
        self.Item.StartAgent(self.Notice)
        self.assertEqual(1, self.Adapter.Starts)

    def test_failure_before_registration(self):
        self.Item.StartAgent(self.Notice)
        self.Adapter.Active = False
        self.Now = 1
        self.assertEqual("FAILED", self.Item.GetPresence()["Status"])

    def test_registration_results_and_idle_require_both_channels(self):
        self.Item.StartAgent(self.Notice)
        self.Status("WAITING_FOR_PEER")
        self.assertEqual("WAITING_FOR_PEER", self.Item.GetPresence()["Status"])
        self.Status("IDLE", 2)
        self.Adapter.Active, self.Adapter.Success = False, True
        self.assertEqual("IDLE", self.Item.GetPresence()["Status"])

    def test_cli_success_alone_does_not_register(self):
        self.Item.StartAgent(self.Notice)
        self.Adapter.Active, self.Adapter.Success = False, True
        self.Now = 1
        self.assertEqual("FAILED", self.Item.GetPresence()["Status"])

    def test_bootstrap_idle_waits_for_actual_codex_completion(self):
        self.Item.StartAgent(self.Notice)
        self.Status("IDLE")
        self.assertEqual("RUNNING", self.Item.GetPresence()["Status"])
        self.Adapter.Active, self.Adapter.Success = False, True
        self.Now += 0.5
        self.assertEqual("IDLE", self.Item.GetPresence()["Status"])

    def test_replacement_rejects_old_status_and_old_wake(self):
        self.Item.StartAgent(self.Notice)
        Other = Notice()
        self.Item.InstallTicket(Ticket(Other))
        with self.assertRaises(ValueError):
            self.Item.StartAgent(Other)
        self.Item.StopAgent(self.Notice["Generation"])
        self.Item.StartAgent(Other)
        self.Status("RUNNING")
        self.assertEqual("FAILED", self.Item.GetPresence()["Status"])
        with self.assertRaises(ValueError):
            self.Item.StartAgent(self.Notice)

    def test_replay_survives_endpoint_restart(self):
        self.Item.StartAgent(self.Notice)
        Other = Endpoint("SERVER", self.Directory, FakeAdapter())
        with self.assertRaises(ValueError):
            Other.InstallTicket(Ticket(self.Notice))

    def test_needs_user_cannot_be_approved_by_wake(self):
        self.Item.StartAgent(self.Notice)
        self.Status("NEEDS_USER", Reason="UAC_APPROVAL")
        self.assertEqual("NEEDS_USER", self.Item.GetPresence()["Status"])
        self.Item.StartAgent(self.Notice)
        self.assertEqual("NEEDS_USER", self.Item.GetPresence()["Status"])
        self.assertEqual(1, self.Adapter.Starts)

    def test_unknown_expired_wrong_endpoint_and_payload_rejected(self):
        for Change in ({"Shell": "whoami"}, {"Executable": "powershell"}, {"Capabilities": ["evil.v1"]},
                       {"Prompt": "approve elevated"}, {"EndpointId": "CLIENT"}, {"ExpiresUnixMs": 0},
                       {"Version": True}, {"RunId": str(uuid.uuid4())}):
            Bad = {**self.Notice, **Change}
            with self.subTest(Change=Change), self.assertRaises(ValueError):
                self.Item.StartAgent(Bad)
        self.assertEqual(0, self.Adapter.Starts)

    def test_local_role_capability_and_generation_confinement(self):
        for Change in ({"Role": "UNKNOWN"}, {"CatalogModule": "subprocess.run"}):
            Bad = {**Ticket(Notice()), **Change}
            with self.assertRaises((ValueError, ImportError)):
                self.Item.InstallTicket(Bad)
        Bad = Ticket(Notice())
        Bad["Config"]["RunId"] = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.Item.InstallTicket(Bad)

    def test_stale_status_and_oversize_file_fail_closed(self):
        self.Item.StartAgent(self.Notice)
        (self.Directory / "status.json").write_text("x" * 2049)
        self.Now = 1
        self.assertEqual("FAILED", self.Item.GetPresence()["Status"])
        self.assertFalse(self.Adapter.Active)


class ServiceTests(unittest.TestCase):
    def Row(self, Key, Operation, Payload):
        Row = {"Version": 1, "EndpointId": "SERVER", "Operation": Operation, "Payload": Payload,
               "Nonce": str(uuid.uuid4()), "TimestampUnixMs": time.time_ns() // 1000000}
        Row["MAC"] = hmac.new(bytes.fromhex(Key), Encode(Row), hashlib.sha256).hexdigest()
        return Row

    def test_auth_replay_closed_operations_and_no_status_authority(self):
        with tempfile.TemporaryDirectory() as Temp:
            Adapter = FakeAdapter()
            Item = Endpoint("SERVER", Temp, Adapter)
            Key = secrets.token_hex(32)
            Server = Service(Item, Key)
            Row = self.Row(Key, "GetPresence", {})
            self.assertEqual("OFFLINE", Server.Handle(Row)["Status"])
            with self.assertRaises(ValueError):
                Server.Handle(Row)
            for Operation in ("RunShell", "RunElevatedCode", "InstallAnything", "Approve", "ResumeAgent", "Status", "START"):
                with self.subTest(Operation=Operation), self.assertRaises(ValueError):
                    Server.Handle(self.Row(Key, Operation, {"Detail": "invoke example.server-ready.v1"}))
            with self.assertRaises(ValueError):
                Server.Handle(self.Row(secrets.token_hex(32), "GetPresence", {}))
            self.assertEqual(0, Adapter.Starts)

    def test_loopback_network_and_bounded_shutdown(self):
        with tempfile.TemporaryDirectory() as Temp:
            Item = Endpoint("SERVER", Temp, FakeAdapter())
            Key, Ready, Stop, State = secrets.token_hex(32), threading.Event(), threading.Event(), {}
            def Listening(Port):
                State["Port"] = Port
                Ready.set()
            Thread = threading.Thread(target=Service(Item, Key, 5).Serve,
                                      args=("127.0.0.1", 0, Listening, Stop))
            Thread.start()
            self.assertTrue(Ready.wait(2))
            try:
                self.assertEqual("OFFLINE", Client("127.0.0.1", State["Port"], "SERVER", Key).GetPresence()["Status"])
            finally:
                Stop.set()
                Thread.join(3)
            self.assertFalse(Thread.is_alive())

    def test_lan_requires_tls(self):
        with self.assertRaises(ValueError):
            Client("192.168.1.2", 12345, "SERVER", secrets.token_hex(32))

    def test_durable_local_policy_admits_notice_without_ticket_courier(self):
        with tempfile.TemporaryDirectory() as Temp:
            WorkflowItem = Workflow(json.loads((ROOT / "examples/readiness.json").read_text()))
            Key = secrets.token_hex(32)
            Value = {"EndpointId": "SERVER", "PeerIp": "127.0.0.1", "CoordinatorHost": "127.0.0.1",
                     "Port": 12345, "WorkflowFile": str(ROOT / "examples/readiness.json"),
                     "WorkflowHash": WorkflowItem.Hash, "CatalogModule": "agent_coordinator.lifecycle.synthetic", "Role": "SERVER"}
            Local = Policy(Value, Key)
            Item = Endpoint("SERVER", Temp, FakeAdapter(), Policy=Local)
            First = Notice()
            Item.StartAgent(First)
            Current = json.loads((Path(Temp) / "current.json").read_text())
            self.assertEqual(Bootstrap(First, Key), {Name: Current["Config"][Name] for Name in ("RunId", "Token")})
            Item.StopAgent(First["Generation"])
            Second = Notice()
            Item.StartAgent(Second)
            self.assertNotEqual(Bootstrap(First, Key), Bootstrap(Second, Key))
            with self.assertRaises(ValueError):
                Item.StartAgent(First)
            for Change in ({"Capabilities": ["evil.v1"]}, {"RunShell": "whoami"}):
                with self.assertRaises(ValueError):
                    Policy({**Value, **Change}, Key)


class WakeIntegrationTests(unittest.TestCase):
    def RunWorkflow(self, ExpectedWrongRun=False, ExpectedWrongRole=False):
        from agent_coordinator.lifecycle.synthetic import GetCatalog
        WorkflowItem = Workflow(json.loads((ROOT / "examples/readiness.json").read_text()))
        Notices = {Role: Notice(Role) for Role in ("SERVER", "CLIENT")}
        Tickets = {Role: Ticket(Value) for Role, Value in Notices.items()}
        Run = Assignments(WorkflowItem, {Role: {Key: Value for Key, Value in Tickets[Role]["Config"].items()
                          if Key not in ("CoordinatorHost", "Port")} for Role in Tickets}, {"DelayMs": 0})
        for Role in Notices:
            Notices[Role]["RunId"] = Run.RunId
        Codes, Ready, State = {}, threading.Event(), {}
        with tempfile.TemporaryDirectory() as Temp:
            Root = Path(Temp)
            def Listening(Port):
                State["Port"] = Port
                Ready.set()
            HostThread = threading.Thread(target=lambda: Codes.update(Host=Host("127.0.0.1", 0, Run, Journal(Root / "host"), Listening)))
            HostThread.start()
            self.assertTrue(Ready.wait(2))
            Items, Workers = {}, []
            for Role in Tickets:
                Tickets[Role]["Config"]["Port"] = State["Port"]
                class JoiningAdapter(FakeAdapter):
                    def StartAgent(Adapter, Role=Role):
                        super(JoiningAdapter, Adapter).StartAgent()
                        def Worker():
                            Codes[Role] = Join(Tickets[Role]["Config"], {(WorkflowItem.Value["SchemaId"], 1): WorkflowItem},
                                               GetCatalog(), Journal(Root / Role),
                                               ExpectedRunId=str(uuid.uuid4()) if ExpectedWrongRun else Run.RunId,
                                               ExpectedRole="CLIENT" if ExpectedWrongRole and Role == "SERVER" else Role)
                            Adapter.Active, Adapter.Success = False, Codes[Role] == 0
                        Thread = threading.Thread(target=Worker)
                        Workers.append(Thread)
                        Thread.start()
                Items[Role] = Endpoint(Role, Root / (Role + "-presence"), JoiningAdapter())
                Items[Role].InstallTicket(Tickets[Role])
                self.assertEqual("OFFLINE", Items[Role].GetPresence()["Status"])
                Items[Role].StartAgent(Notices[Role])
                Items[Role].StartAgent(Notices[Role])
                self.assertEqual(1, Items[Role].Adapter.Starts)
            for Thread in [*Workers, HostThread]:
                Thread.join(7)
                self.assertFalse(Thread.is_alive())
            self.assertEqual(1 if ExpectedWrongRun or ExpectedWrongRole else 0, Codes["Host"])
            if not ExpectedWrongRun and not ExpectedWrongRole:
                self.assertEqual({"Host": 0, "SERVER": 0, "CLIENT": 0}, Codes)

    def test_offline_wake_fresh_pull_registration_barrier_results_cleanup(self):
        self.RunWorkflow()

    def test_delayed_agent_cannot_accept_replacement_run(self):
        self.RunWorkflow(ExpectedWrongRun=True)

    def test_wrong_role_rejected(self):
        self.RunWorkflow(ExpectedWrongRole=True)
