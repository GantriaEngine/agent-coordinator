"""Durable endpoint-local admission; wake supplies identity, never authority."""
import hashlib
import hmac
import json
from pathlib import Path

from ..control import Address
from ..workflow import Exact, Workflow
from .bootstrap import LoadCatalog


def Bootstrap(Notice, Key):
    """Independent per-run/generation v1 bootstrap credentials from a local wake key."""
    if not isinstance(Key, str) or len(Key) != 64:
        raise ValueError("256-bit admission key required")
    Context = json.dumps({"Scope": "agent-coordinator-lifecycle-v1-bootstrap", **Notice},
                         sort_keys=True, separators=(",", ":")).encode()
    return {"RunId": Notice["Generation"],
            "Token": hmac.new(bytes.fromhex(Key), Context, hashlib.sha256).hexdigest()}


class Policy:
    def __init__(self, Value, Key):
        Exact(Value, ("EndpointId", "PeerIp", "CoordinatorHost", "Port", "WorkflowFile",
                      "WorkflowHash", "CatalogModule", "Role"))
        Address(Value["PeerIp"])
        Address(Value["CoordinatorHost"])
        if type(Value["Port"]) is not int or not 1 <= Value["Port"] <= 65535:
            raise ValueError("invalid locally approved coordinator port")
        WorkflowItem = Workflow(json.loads(Path(Value["WorkflowFile"]).read_text()))
        if Value["WorkflowHash"] != WorkflowItem.Hash:
            raise ValueError("local admission schema hash mismatch")
        LoadCatalog(Value["CatalogModule"]).Advertise(WorkflowItem, Value["Role"])
        Bootstrap({"Generation": "00000000-0000-0000-0000-000000000000"}, Key)
        self.Value, self.Key = dict(Value), Key

    def Ticket(self, Notice):
        if Notice["EndpointId"] != self.Value["EndpointId"]:
            raise ValueError("local admission endpoint mismatch")
        WorkflowItem = Workflow(json.loads(Path(self.Value["WorkflowFile"]).read_text()))
        if WorkflowItem.Hash != self.Value["WorkflowHash"]:
            raise ValueError("approved local workflow changed")
        Config = {Key: self.Value[Key] for Key in ("EndpointId", "PeerIp", "CoordinatorHost", "Port")}
        return {"Notice": Notice, "Config": {**Config, **Bootstrap(Notice, self.Key)},
                "WorkflowFile": self.Value["WorkflowFile"], "CatalogModule": self.Value["CatalogModule"],
                "Role": self.Value["Role"]}
