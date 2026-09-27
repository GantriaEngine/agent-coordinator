import importlib.util
from pathlib import Path
import unittest
import json
import secrets
import tempfile
import threading
import time
import uuid

from agent_coordinator.control import Assignments, Host, Join
from agent_coordinator.transport import Journal
from agent_coordinator.workflow import Catalog, Workflow


class IntegrationTests(unittest.TestCase):
    def test_two_endpoints_pull_register_barrier_live_result_cleanup(self):
        Spec = importlib.util.spec_from_file_location("simulation", Path(__file__).parents[1] / "examples/simulation.py")
        Example = importlib.util.module_from_spec(Spec)
        Spec.loader.exec_module(Example)
        Codes = Example.Main()
        self.assertTrue(Codes["SERVERCleanup"])
        self.assertTrue(Codes["CLIENTCleanup"])

    def Attempt(self, Mode):
        Value = json.loads((Path(__file__).parents[1] / "examples/readiness.json").read_text())
        Value["RegistrationTimeout"] = 3
        Value["ExecutionTimeout"] = 1
        for Step in Value["Transitions"]:
            Step["Timeout"] = 1
        WorkflowItem = Workflow(Value)
        Local = {Role: {"EndpointId": Role, "PeerIp": "127.0.0.1", "RunId": str(uuid.uuid4()), "Token": secrets.token_hex(32)} for Role in Value["Roles"]}
        AssignmentSet = Assignments(WorkflowItem, Local, {"DelayMs": 0})
        if Mode == "expired":
            AssignmentSet.ExpiresUnixMs = 0
        Codes, Ready, State = {}, threading.Event(), {}
        with tempfile.TemporaryDirectory() as Temp:
            Root = Path(Temp)
            def Listening(Port):
                State["Port"] = Port
                Ready.set()
            def Coordinator():
                Codes["Host"] = Host("127.0.0.1", 0, AssignmentSet, Journal(Root / "host"), Listening)
            def Worker(Role):
                def Handler(Parameters, Context):
                    if Mode == "timeout" and Role == "SERVER":
                        time.sleep(1.1)
                        Context.Check()
                    return {"Success": Mode != "failure", "Evidence": []}
                Handlers = {Name: Handler for Name in Value["Roles"][Role]}
                if Mode == "missing":
                    Handlers = {}
                Approved = {} if Mode == "unknown-schema" else {(Value["SchemaId"], 1): WorkflowItem}
                Config = {**Local[Role], "CoordinatorHost": "127.0.0.1", "Port": State["Port"]}
                if Mode == "stale":
                    Config["RunId"] = str(uuid.uuid4())
                Codes[Role] = Join(Config, Approved, Catalog(Handlers, lambda: Codes.update({Role + "Cleanup": True})), Journal(Root / Role))
            HostThread = threading.Thread(target=Coordinator)
            HostThread.start()
            self.assertTrue(Ready.wait(2))
            First = threading.Thread(target=Worker, args=("SERVER",))
            First.start()
            if Mode == "registration-budget":
                time.sleep(1.1)  # exceeds execution budget before client registers
            Second = threading.Thread(target=Worker, args=("CLIENT",))
            Second.start()
            for Thread in (First, Second, HostThread):
                Thread.join(5)
                self.assertFalse(Thread.is_alive())
            self.assertTrue(Codes["SERVERCleanup"])
            self.assertTrue(Codes["CLIENTCleanup"])
            for File in Root.rglob("*.jsonl"):
                self.assertNotIn('"Token"', File.read_text())
            self.assertEqual(0 if Mode == "registration-budget" else 1, Codes["Host"])

    def test_registration_wait_does_not_consume_execution_budget(self):
        self.Attempt("registration-budget")

    def test_unknown_schema_rejects_and_cleans_up(self):
        self.Attempt("unknown-schema")

    def test_missing_capability_rejects_and_cleans_up(self):
        self.Attempt("missing")

    def test_expired_assignment_rejects_and_cleans_up(self):
        self.Attempt("expired")

    def test_stale_config_rejects_and_cleans_up(self):
        self.Attempt("stale")

    def test_failure_and_operation_deadline_abort_cleanup(self):
        for Mode in ("failure", "timeout"):
            with self.subTest(Mode=Mode):
                self.Attempt(Mode)
