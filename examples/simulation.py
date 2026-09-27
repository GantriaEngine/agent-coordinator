"""Two local agents, authenticated assignment pull, barrier, LIVE, RESULT, cleanup."""
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_coordinator.control import Assignments, Host, Join
from agent_coordinator.transport import Journal
from agent_coordinator.workflow import Catalog, Workflow


def Main():
    WorkflowItem = Workflow(json.loads(Path(__file__).with_name("readiness.json").read_text()))
    Endpoints = {Role: {"EndpointId": Role, "PeerIp": "127.0.0.1", "RunId": str(uuid.uuid4()),
                        "Token": secrets.token_hex(32)} for Role in WorkflowItem.Value["Roles"]}
    Run = Assignments(WorkflowItem, Endpoints, {"DelayMs": 10})
    Ready = threading.Event()
    State, Codes = {}, {}
    with tempfile.TemporaryDirectory() as Temp:
        Root = Path(Temp)
        def Listening(Port):
            State["Port"] = Port
            Ready.set()
        def Coordinator():
            Codes["Host"] = Host("127.0.0.1", 0, Run, Journal(Root / "host"), Listening)
        def Worker(Role):
            def Invoke(Parameters, Context):
                time.sleep(Parameters.get("DelayMs", 0) / 1000)
                Context.Check()
                return {"Success": True, "Evidence": []}
            CatalogItem = Catalog({Name: Invoke for Name in WorkflowItem.Value["Roles"][Role]},
                                  lambda: Codes.update({Role + "Cleanup": True}))
            Config = {**Endpoints[Role], "CoordinatorHost": "127.0.0.1", "Port": State["Port"]}
            Codes[Role] = Join(Config, {(WorkflowItem.Value["SchemaId"], 1): WorkflowItem}, CatalogItem, Journal(Root / Role))
        HostThread = threading.Thread(target=Coordinator)
        HostThread.start()
        if not Ready.wait(3):
            raise RuntimeError("coordinator did not listen")
        Workers = [threading.Thread(target=Worker, args=(Role,)) for Role in Endpoints]
        for WorkerThread in Workers:
            WorkerThread.start()
        for WorkerThread in [*Workers, HostThread]:
            WorkerThread.join(10)
            if WorkerThread.is_alive():
                raise RuntimeError("simulation did not terminate")
        if any(Codes.get(Role) != 0 for Role in ("Host", "SERVER", "CLIENT")):
            for File in Root.rglob("result.json"):
                print(File.read_text())
            raise RuntimeError("simulation failed")
        print("[Coordinator:Simulation] Assignment, registration, barrier, LIVE, RESULT and cleanup passed")
    return Codes


if __name__ == "__main__":
    Main()
