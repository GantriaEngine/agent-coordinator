"""Fixed installed entry point invoked by the local Codex agent, never by wire text."""
import importlib
import json
from pathlib import Path
import re
import sys

from ..control import Join
from ..transport import Journal
from ..workflow import Workflow
from .endpoint import Atomic
from .presence import ValidateNotice


def LoadCatalog(Module):
    if not isinstance(Module, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*", Module):
        raise ValueError("installed module name required")
    return importlib.import_module(Module).GetCatalog()


def Main():
    # No command/argument parsing; location is fixed in the endpoint's approved repository.
    if len(sys.argv) != 1:
        raise ValueError("bootstrap accepts no caller instructions")
    Directory = Path(".lifecycle").resolve()
    Ticket = json.loads((Directory / "current.json").read_text())
    Notice = Ticket["Notice"]
    import time
    ValidateNotice(Notice, Ticket["Config"]["EndpointId"], time.time_ns() // 1000000)
    Sequence = 0
    def Status(State, Reason="NONE", Detail=""):
        nonlocal Sequence
        Sequence += 1
        Atomic(Directory / "status.json", {"Generation": Notice["Generation"], "RunId": Notice["RunId"],
                                          "Sequence": Sequence, "Status": State, "Reason": Reason, "Detail": Detail})
    Status("PREPARING")
    try:
        WorkflowItem = Workflow(json.loads(Path(Ticket["WorkflowFile"]).read_text()))
        CatalogItem = LoadCatalog(Ticket["CatalogModule"])
        CatalogItem.Advertise(WorkflowItem, Ticket["Role"])
    except (ValueError, ImportError, AttributeError, OSError):
        Status("NEEDS_USER", "MISSING_CAPABILITY")
        return 1
    Code = Join(Ticket["Config"], {(WorkflowItem.Value["SchemaId"], WorkflowItem.Value["SchemaVersion"]): WorkflowItem},
                CatalogItem, Journal(Directory / Notice["Generation"]),
                ExpectedRunId=Notice["RunId"], ExpectedRole=Ticket["Role"], Observe=Status)
    Status("IDLE" if Code == 0 else "FAILED", "NONE" if Code == 0 else "AGENT_FAILED")
    return Code


if __name__ == "__main__":
    raise SystemExit(Main())
