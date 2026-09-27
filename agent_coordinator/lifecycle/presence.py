"""Lease-bound informational presence, independent of workflow authority."""
import time
import uuid

from ..workflow import Exact, Symbol

STATES = frozenset(("OFFLINE", "IDLE", "PREPARING", "WAITING_FOR_ASSIGNMENT",
                    "WAITING_FOR_PEER", "RUNNING", "NEEDS_USER", "FAILED"))
REASONS = frozenset(("UAC_APPROVAL", "PHYSICAL_INTERVENTION", "SECURITY_DECISION",
                     "MISSING_CAPABILITY", "AUTH_REQUIRED", "STARTUP_TIMEOUT",
                     "AGENT_FAILED", "RUN_TIMEOUT", "STOPPED", "NONE"))


def Uuid(Value):
    if not isinstance(Value, str) or str(uuid.UUID(Value)) != Value:
        raise ValueError("canonical UUID required")


class Presence:
    def __init__(self, EndpointId, LeaseSeconds=5, Clock=time.monotonic):
        Symbol(EndpointId)
        if type(LeaseSeconds) is not int or not 1 <= LeaseSeconds <= 30:
            raise ValueError("invalid presence lease")
        self.EndpointId, self.Lease, self.Clock = EndpointId, LeaseSeconds, Clock
        self.Generation = self.RunId = self.AgentId = None
        self.Status, self.Reason, self.Detail = "OFFLINE", "NONE", ""
        self.Expires = 0
        self.Sequence = 0
        self.Count = 0

    def Begin(self, RunId, Generation):
        Uuid(RunId)
        Uuid(Generation)
        self.RunId, self.Generation, self.AgentId = RunId, Generation, None
        self.Sequence = self.Count = 0
        self.Update(Generation, RunId, 1, "PREPARING")

    def Update(self, Generation, RunId, Sequence, Status, Reason="NONE", Detail=""):
        if Generation != self.Generation or RunId != self.RunId:
            raise ValueError("stale endpoint generation/run")
        if type(Sequence) is not int or Sequence != self.Sequence + 1:
            raise ValueError("replayed or skipped status")
        if self.Count >= 256:
            raise ValueError("status budget exhausted")
        if Status not in STATES or Reason not in REASONS:
            raise ValueError("unknown status/reason")
        if Status == "NEEDS_USER" and Reason not in REASONS - {"NONE", "STOPPED"}:
            raise ValueError("human escalation needs typed reason")
        if not isinstance(Detail, str) or len(Detail.encode("utf-8")) > 512:
            raise ValueError("diagnostic bound exceeded")
        self.Sequence, self.Count = Sequence, self.Count + 1
        self.Status, self.Reason, self.Detail = Status, Reason, Detail
        self.Expires = self.Clock() + self.Lease

    def Get(self):
        return {"EndpointId": self.EndpointId, "AgentId": self.AgentId,
                "Generation": self.Generation, "RunId": self.RunId,
                "TimestampUnixMs": time.time_ns() // 1000000,
                "LeaseRemainingMs": max(0, int((self.Expires - self.Clock()) * 1000)),
                "Status": self.Status if self.Clock() < self.Expires else "OFFLINE",
                "Reason": self.Reason, "Detail": self.Detail}


def ValidateNotice(Value, EndpointId, NowMs):
    Exact(Value, ("Version", "EndpointId", "RunId", "Generation", "ExpiresUnixMs"))
    if type(Value["Version"]) is not int or Value["Version"] != 1 or Value["EndpointId"] != EndpointId:
        raise ValueError("unsupported lifecycle notice")
    Uuid(Value["RunId"])
    Uuid(Value["Generation"])
    if type(Value["ExpiresUnixMs"]) is not int or not NowMs < Value["ExpiresUnixMs"] <= NowMs + 600000:
        raise ValueError("expired or unbounded wake notice")
