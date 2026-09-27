import copy
import json
from pathlib import Path
import time
import unittest

from agent_coordinator.control import Assignments, Identity
from agent_coordinator.workflow import Catalog, Workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.Value = json.loads((Path(__file__).parents[1] / "examples/readiness.json").read_text())
        self.Workflow = Workflow(self.Value)
        self.Calls = []
        self.Catalog = Catalog({"example.server-ready.v1": lambda Parameters, Context:
                               self.Calls.append(Parameters) or {"Success": True, "Evidence": []}}, lambda: None)

    def test_valid_schema_and_registration(self):
        self.Workflow.Registration("SERVER", self.Catalog.Advertise(self.Workflow, "SERVER"))

    def test_unsupported_schema_version(self):
        for Version in (2, True, "1"):
            with self.subTest(Version=Version), self.assertRaises(ValueError):
                Workflow({**self.Value, "SchemaVersion": Version})

    def test_shell_script_and_process_schema_smuggling(self):
        for Name in ("RunShell", "RunElevatedCode", "ExecuteProcess", "Script", "Command"):
            Value = copy.deepcopy(self.Value)
            Value["Transitions"][0][Name] = "evil.exe"
            with self.subTest(Name=Name), self.assertRaises(ValueError):
                Workflow(Value)

    def test_capability_version_and_missing_local_handler(self):
        Value = copy.deepcopy(self.Value)
        Value["Roles"]["SERVER"] = ["example.server-ready.v2"]
        Value["Transitions"][0]["Capability"] = "example.server-ready.v2"
        with self.assertRaises(ValueError):
            self.Catalog.Advertise(Workflow(Value), "SERVER")
        with self.assertRaises(ValueError):
            self.Catalog.Advertise(self.Workflow, "CLIENT")

    def test_endpoint_cannot_advertise_extra_or_missing_capability(self):
        for Values in ([], ["invented.v1"], ["example.server-ready.v1", "invented.v1"]):
            with self.subTest(Values=Values), self.assertRaises(ValueError):
                self.Workflow.Registration("SERVER", Values)

    def test_unauthorized_transition(self):
        with self.assertRaises(ValueError):
            self.Workflow.Transition(0, "CLIENT", "example.server-ready.v1", "staged", "server-live")

    def test_coordinator_cannot_invoke_unadvertised_capability(self):
        with self.assertRaises(ValueError):
            self.Catalog.Invoke("example.server-ready.v1", [], {}, time.monotonic() + 1)
        self.assertEqual([], self.Calls)

    def test_parameter_bounds_and_executable_payload(self):
        for Values in ({"DelayMs": 101}, {"DelayMs": "powershell"}, {"DelayMs": True}, {"DelayMs": 1, "Command": "evil"}):
            with self.subTest(Values=Values), self.assertRaises(ValueError):
                self.Workflow.Parameters(Values)

    def test_capability_deadline_and_result_metadata(self):
        with self.assertRaises(TimeoutError):
            self.Catalog.Invoke("example.server-ready.v1", ["example.server-ready.v1"], {}, time.monotonic() - 1)
        self.assertEqual([], self.Calls)

    def test_fresh_assignment_replay_expiry_and_separate_execution_budget(self):
        Local = {"EndpointId": "worker", "PeerIp": "127.0.0.1", "RunId": "870c114b-44e5-4e43-a677-59b010f388d9", "Token": "a" * 64}
        Endpoints = {Role: {**Local, "EndpointId": Role} for Role in self.Value["Roles"]}
        Run = Assignments(self.Workflow, Endpoints, {"DelayMs": 1})
        Assignment = Run.Pull("SERVER", "127.0.0.1")
        self.assertNotEqual(Local["RunId"], Assignment["RunId"])
        self.assertNotEqual(Local["Token"], Assignment["Token"])
        self.assertNotIn("ExecutionDeadline", Assignment)
        Identity(Assignment)
        with self.assertRaises(ValueError):
            Run.Pull("SERVER", "127.0.0.1")
        Run.ExpiresUnixMs = 0
        with self.assertRaises(TimeoutError):
            Run.Pull("CLIENT", "127.0.0.1")

    def test_wrong_peer_cannot_pull(self):
        Endpoints = {Role: {"EndpointId": Role, "PeerIp": "127.0.0.1", "RunId": "870c114b-44e5-4e43-a677-59b010f388d9", "Token": "a" * 64} for Role in self.Value["Roles"]}
        with self.assertRaises(ValueError):
            Assignments(self.Workflow, Endpoints, {"DelayMs": 0}).Pull("SERVER", "127.0.0.2")
