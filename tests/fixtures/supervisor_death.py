"""Crash a real endpoint supervisor with a confined harmless child tree."""
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid

from agent_coordinator.lifecycle.endpoint import Atomic, Endpoint
from agent_coordinator.lifecycle.process import ProcessTree

Root = Path(__file__).resolve().parents[2]
Temporary = Path(sys.argv[1]).resolve()
Phase = sys.argv[2]
if Phase not in ("BEFORE_REGISTRATION", "RUNNING", "AFTER_WORKFLOW"):
    raise ValueError("unknown crash phase")
Clock = Temporary / "clock"
Marker = Temporary / "marker.json"
Directory = Temporary / ".lifecycle"
Fixture = Path(__file__).with_name("process_tree.py")


class Adapter:
    def StartAgent(self):
        self.Process, self.Tree = ProcessTree.Start(
            [sys.executable, str(Fixture), "parent", str(Clock), "immediate"], Fixture.parent)
        self.Child = int(self.Process.stdout.readline().decode().strip())

    def StopAgent(self):
        self.Tree.Close()


Notice = {"Version": 1, "EndpointId": "SERVER", "RunId": str(uuid.uuid4()),
          "Generation": str(uuid.uuid4()),
          "ExpiresUnixMs": time.time_ns() // 1000000 + 60000}
Ticket = {"Notice": Notice,
          "Config": {"EndpointId": "SERVER", "PeerIp": "127.0.0.1",
                     "CoordinatorHost": "127.0.0.1", "Port": 12345,
                     "RunId": Notice["Generation"], "Token": secrets.token_hex(32)},
          "WorkflowFile": str(Root / "examples" / "readiness.json"),
          "CatalogModule": "agent_coordinator.lifecycle.synthetic", "Role": "SERVER"}
AdapterItem = Adapter()
Item = Endpoint("SERVER", Directory, AdapterItem)
Item.InstallTicket(Ticket)
Item.StartAgent(Notice)
Until = time.monotonic() + 3
while not Clock.exists() and time.monotonic() < Until:
    time.sleep(0.02)
if not Clock.exists():
    raise RuntimeError("descendant never started")
if Phase != "BEFORE_REGISTRATION":
    Atomic(Directory / "status.json",
           {"Generation": Notice["Generation"], "RunId": Notice["RunId"],
            "Sequence": 1, "Status": "IDLE" if Phase == "AFTER_WORKFLOW" else "RUNNING",
            "Reason": "NONE", "Detail": ""})
Marker.write_text(json.dumps({"Supervisor": os.getpid(), "Parent": AdapterItem.Process.pid,
                              "Child": AdapterItem.Child, "Notice": Notice, "Phase": Phase}))
os._exit(23)
