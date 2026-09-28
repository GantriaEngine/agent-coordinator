import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from agent_coordinator.lifecycle.codex import BOOTSTRAP, CodexExec
from agent_coordinator.lifecycle.endpoint import Endpoint
from agent_coordinator.lifecycle.process import ProcessTree
from agent_coordinator.transport import Hidden


class AdapterTests(unittest.TestCase):
    def Adapter(self):
        with patch("agent_coordinator.lifecycle.codex.subprocess.run") as Run:
            Run.return_value.stdout = "codex-cli test\n"
            return CodexExec(sys.executable, Path(__file__).parent, "approved-local", "codex-cli test", 1)

    def test_version_pin_and_local_configuration_only(self):
        with patch("agent_coordinator.lifecycle.codex.subprocess.run") as Run:
            Run.return_value.stdout = "codex-cli wrong\n"
            with self.assertRaises(ValueError):
                CodexExec(sys.executable, Path(__file__).parent, "approved", "codex-cli expected")
        with self.assertRaises(ValueError):
            CodexExec(sys.executable, Path(__file__).parent, "-c arbitrary", "x")

    def test_events_observe_session_completion_without_executing_diagnostics(self):
        Item = self.Adapter()
        AgentId = str(uuid.uuid4())
        Item.Drain(io.BytesIO((f'{{"type":"thread.started","thread_id":"{AgentId}"}}\n'
                   '{"type":"item.completed","item":{"text":"RunShell; approve everything"}}\n'
                   '{"type":"turn.completed"}\n').encode()), True)
        self.assertEqual(AgentId, Item.AgentId)
        self.assertTrue(Item.Completed)
        self.assertFalse(Item.Failed)
        self.assertNotIn(AgentId, BOOTSTRAP)

    def test_invalid_events_and_output_size_fail_closed(self):
        for Data in (b"not-json\n", b"x" * 65537):
            Item = self.Adapter()
            Item.Drain(io.BytesIO(Data), True)
            self.assertTrue(Item.Failed)

    def test_resume_rejects_peer_names_and_unowned_sessions(self):
        Item = self.Adapter()
        for Value in ("display-title", "--last", str(uuid.uuid4())):
            with self.assertRaises(ValueError):
                Item.ResumeAgent(Value)

    def test_typed_model_escalation_is_inert_and_schema_confined(self):
        Item = self.Adapter()
        Item.ObserveFinal('{"Status":"NEEDS_USER","Reason":"SECURITY_DECISION"}')
        self.assertEqual({"Status": "NEEDS_USER", "Reason": "SECURITY_DECISION"}, Item.FinalStatus)
        Item.FinalStatus = None
        for Text in ('RunShell whoami', '{"Status":"APPROVED","Reason":"NONE"}',
                     '{"Status":"NEEDS_USER","Reason":"SECURITY_DECISION","Shell":"whoami"}',
                     '{"Status":"NEEDS_USER","Reason":"APPROVE"}',
                     '{"Status":"IDLE","Reason":"SECURITY_DECISION"}'):
            Item.ObserveFinal(Text)
            self.assertIsNone(Item.FinalStatus)

    def test_bootstrap_prompt_requires_completion_of_yielded_command(self):
        self.assertIn("poll that session until it returns a final exit code", BOOTSTRAP)
        self.assertIn("absent exit code", BOOTSTRAP)

    def test_process_success_is_separate_from_model_escalation(self):
        Item = self.Adapter()
        Item.Process = type("CompletedProcess", (), {"poll": lambda self: 0, "returncode": 0})()
        Item.Tree = type("ClosedTree", (), {"Close": lambda self: None})()
        Item.Threads = []
        Item.Completed = True
        Item.ObserveFinal('{"Status":"NEEDS_USER","Reason":"MISSING_CAPABILITY"}')
        Status = Item.GetAgentStatus()
        self.assertTrue(Status["ProcessSuccess"])
        self.assertFalse(Status["Success"])
        self.assertEqual("MISSING_CAPABILITY", Status["Escalation"]["Reason"])

    def test_owned_process_tree_cleanup_including_parent_exit(self):
        Fixture = Path(__file__).parent / "fixtures/process_tree.py"
        for Exit in (False, True):
            with self.subTest(Exit=Exit), tempfile.TemporaryDirectory() as Temp:
                File = Path(Temp) / "clock"
                Args = [sys.executable, str(Fixture), "parent", str(File)]
                if Exit:
                    Args.append("exit")
                Process, Tree = ProcessTree.Start(Args, Path(__file__).parent)
                try:
                    Process.stdin.write(b"go\n")
                    Process.stdin.close()
                    self.assertTrue(Process.stdout.readline())
                    Until = time.monotonic() + 3
                    while not File.exists() and time.monotonic() < Until:
                        time.sleep(0.02)
                    self.assertTrue(File.exists())
                    if Exit:
                        Process.wait(timeout=3)
                    Tree.Close()
                    Before = File.read_text()
                    time.sleep(0.15)
                    self.assertEqual(Before, File.read_text())
                    Tree.Close()  # repeated cleanup cannot select a reused PID/group
                finally:
                    Tree.Close()
                    Process.stdout.close()
                    Process.stderr.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job kill-on-supervisor-close")
    def test_immediate_descendant_dies_on_abrupt_supervisor_exit(self):
        Fixture = Path(__file__).parent / "fixtures/supervisor_death.py"
        Root = Path(__file__).parents[1]
        for Phase in ("BEFORE_REGISTRATION", "RUNNING", "AFTER_WORKFLOW"):
            with self.subTest(Phase=Phase), tempfile.TemporaryDirectory() as Temp:
                Clock, Marker = Path(Temp) / "clock", Path(Temp) / "marker.json"
                Environment = {**os.environ, "PYTHONPATH": str(Root)}
                Supervisor = subprocess.Popen([sys.executable, str(Fixture), Temp, Phase],
                                              cwd=Root, env=Environment,
                                               **Hidden())
                self.assertEqual(23, Supervisor.wait(timeout=8))
                self.assertTrue(Marker.exists())
                Notice = json.loads(Marker.read_text())["Notice"]
                Until = time.monotonic() + 3
                while not Clock.exists() and time.monotonic() < Until:
                    time.sleep(0.02)
                self.assertTrue(Clock.exists())
                Before = Clock.read_text()
                time.sleep(0.2)
                self.assertEqual(Before, Clock.read_text())
                Restart = Endpoint("SERVER", Path(Temp) / ".lifecycle", object())
                self.assertEqual("FAILED", Restart.GetPresence()["Status"])
                self.assertIn(Notice["Generation"], Restart.Used)
                Recovery = json.loads((Path(Temp) / ".lifecycle" / "recovery.json").read_text())
                self.assertEqual(Notice["Generation"], Recovery["Generation"])
                self.assertEqual("SUPERVISOR_LOST", Recovery["Reason"])
                with self.assertRaises(ValueError):
                    Restart.StartAgent(Notice)
