"""Bounded synthetic harness after endpoint-local installation and SSH forwarding.

Local setup JSON is protected/ignored; it contains only independent lifecycle keys,
not OpenAI credentials. This harness never launches shell commands on a peer.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_coordinator.control import Assignments, Host
from agent_coordinator.lifecycle.policy import Bootstrap
from agent_coordinator.lifecycle.service import Client
from agent_coordinator.transport import Journal
from agent_coordinator.workflow import Exact, Workflow


def Main():
    Parser = argparse.ArgumentParser()
    Parser.add_argument("--setup", required=True)
    Args = Parser.parse_args()
    Setup = json.loads(Path(Args.setup).read_text())
    Exact(Setup, ("WorkflowFile", "ControlPort", "Endpoints", "Evidence"))
    WorkflowItem = Workflow(json.loads(Path(Setup["WorkflowFile"]).read_text()))
    NowMs = time.time_ns() // 1000000
    Notices = {Role: {"Version": 1, "EndpointId": Item["EndpointId"], "RunId": str(uuid.uuid4()),
                     "Generation": str(uuid.uuid4()), "ExpiresUnixMs": NowMs + WorkflowItem.Value["RegistrationTimeout"] * 1000}
               for Role, Item in Setup["Endpoints"].items()}
    Local = {Role: {"EndpointId": Item["EndpointId"], "PeerIp": "127.0.0.1",
                    **Bootstrap(Notices[Role], Item["Token"])} for Role, Item in Setup["Endpoints"].items()}
    Run = Assignments(WorkflowItem, Local, {"DelayMs": 0})
    # Bootstrap derivation includes RunId: establish identity first, then replace local credentials.
    for Role, Item in Setup["Endpoints"].items():
        Notices[Role]["RunId"] = Run.RunId
        Run.Entries[Item["EndpointId"]]["Local"].update(Bootstrap(Notices[Role], Item["Token"]))
    Clients = {Role: Client("127.0.0.1", Item["LifecyclePort"], Item["EndpointId"], Item["Token"])
               for Role, Item in Setup["Endpoints"].items()}
    Root = Path(Setup["Evidence"]) / Run.RunId
    Root.mkdir(parents=True, exist_ok=False)
    Ready, Codes, Samples = threading.Event(), {}, []
    def Coordinator():
        Codes["Host"] = Host("127.0.0.1", Setup["ControlPort"], Run, Journal(Root / "host"), lambda Port: Ready.set())
    Thread = threading.Thread(target=Coordinator)
    Thread.start()
    try:
        if not Ready.wait(3):
            raise RuntimeError("coordinator failed to listen")
        for Role, Item in Clients.items():
            Before = Item.GetPresence()
            if Before["Status"] != "OFFLINE":
                raise ValueError("qualification requires absent endpoint agents")
            Samples.append({"Role": Role, "Stage": "before", "Presence": Before})
            Item.StartAgent(Notices[Role])
            Item.StartAgent(Notices[Role])  # verifies idempotent duplicate without another agent
        Deadline = time.monotonic() + min(180, WorkflowItem.Value["RegistrationTimeout"] + 30)
        Finished = set()
        while time.monotonic() < Deadline and len(Finished) < len(Clients):
            for Role, Item in Clients.items():
                Presence = Item.GetAgentStatus(Notices[Role]["Generation"])
                Samples.append({"Role": Role, "Stage": "observed", "Presence": Presence})
                if Presence["Status"] in ("IDLE", "FAILED", "NEEDS_USER"):
                    Finished.add(Role)
            time.sleep(0.5)
        Thread.join(5)
        Success = (not Thread.is_alive() and Codes.get("Host") == 0 and
                   all(Clients[Role].GetAgentStatus(Notices[Role]["Generation"])["Status"] == "IDLE" for Role in Clients))
        Report = {"Success": Success, "RunId": Run.RunId, "Codes": Codes, "Samples": Samples}
        (Root / "qualification.json").write_text(json.dumps(Report, indent=2) + "\n")
        print(f"[Coordinator:Lifecycle] Synthetic qualification {'passed' if Success else 'failed'}; evidence {Root}")
        return 0 if Success else 1
    finally:
        for Role, Item in Clients.items():
            try:
                Item.StopAgent(Notices[Role]["Generation"])
            except (OSError, ValueError):
                pass
        Thread.join(WorkflowItem.Value["RegistrationTimeout"] + 2)


if __name__ == "__main__":
    raise SystemExit(Main())
