"""Local admission and supervision. Wake cannot provision or edit a ticket."""
import json
from pathlib import Path
import threading
import time

from ..workflow import Exact, Workflow
from .presence import Presence, ValidateNotice


def Atomic(File, Value):
    Temporary = File.with_suffix(".tmp")
    Temporary.write_text(json.dumps(Value) + "\n", encoding="utf-8")
    Temporary.replace(File)


class Endpoint:
    """One endpoint, one running agent, at most 64 locally admitted starts per ledger.

    Tickets are provisioned locally, not accepted through the lifecycle channel.
    The directory must be writable only by the endpoint administrator/agent identity.
    """
    def __init__(self, EndpointId, Directory, Adapter, StartupSeconds=30, Clock=time.monotonic, Policy=None):
        if type(StartupSeconds) is not int or not 1 <= StartupSeconds <= 120:
            raise ValueError("invalid startup budget")
        self.Directory = Path(Directory).resolve()
        self.Directory.mkdir(parents=True, exist_ok=True)
        self.Adapter, self.StartupSeconds, self.Clock = Adapter, StartupSeconds, Clock
        self.Policy = Policy
        self.Presence = Presence(EndpointId, Clock=Clock)
        self.Lock = threading.RLock()
        self.LedgerFile = self.Directory / "used.json"
        self.Used = json.loads(self.LedgerFile.read_text()) if self.LedgerFile.exists() else []
        if not isinstance(self.Used, list) or len(self.Used) > 64 or any(not isinstance(Item, str) for Item in self.Used):
            raise ValueError("invalid replay ledger")
        self.Tickets = {}
        self.Active = None
        self.LastPoll = float("-inf")
        self.Registered = False
        self.BridgeSequence = 0
        self.BridgeState = None

    def InstallTicket(self, Ticket):
        """Endpoint-local provisioning only; there is deliberately no wire counterpart."""
        Exact(Ticket, ("Notice", "Config", "WorkflowFile", "CatalogModule", "Role"))
        ValidateNotice(Ticket["Notice"], self.Presence.EndpointId, time.time_ns() // 1000000)
        Config = Ticket["Config"]
        Exact(Config, ("EndpointId", "PeerIp", "CoordinatorHost", "Port", "RunId", "Token"))
        if Config["EndpointId"] != self.Presence.EndpointId:
            raise ValueError("ticket endpoint mismatch")
        from ..control import Address, Identity
        Identity(Config)
        Address(Config["PeerIp"])
        Address(Config["CoordinatorHost"])
        if type(Config["Port"]) is not int or not 1 <= Config["Port"] <= 65535:
            raise ValueError("invalid local coordinator port")
        # A fresh per-generation bootstrap identity, not a persistent v1 bootstrap.
        if Config["RunId"] != Ticket["Notice"]["Generation"]:
            raise ValueError("bootstrap identity must equal ticket generation")
        WorkflowItem = Workflow(json.loads(Path(Ticket["WorkflowFile"]).read_text()))
        if Ticket["Role"] not in WorkflowItem.Value["Roles"]:
            raise ValueError("unsupported local role")
        from .bootstrap import LoadCatalog
        LoadCatalog(Ticket["CatalogModule"]).Advertise(WorkflowItem, Ticket["Role"])
        Key = Ticket["Notice"]["Generation"]
        with self.Lock:
            if Key in self.Used or Key in self.Tickets or len(self.Tickets) >= 64:
                raise ValueError("duplicate or excessive local ticket")
            self.Tickets[Key] = json.loads(json.dumps(Ticket))

    def StartAgent(self, Notice):
        with self.Lock:
            ValidateNotice(Notice, self.Presence.EndpointId, time.time_ns() // 1000000)
            Generation = Notice["Generation"]
            Ticket = self.Tickets.get(Generation)
            if Ticket is None and self.Policy is not None:
                self.InstallTicket(self.Policy.Ticket(Notice))
                Ticket = self.Tickets[Generation]
            if Ticket is None or Ticket["Notice"] != Notice:
                raise ValueError("wake has no identical locally admitted ticket")
            if self.Active == Generation:
                return self.GetPresence()  # duplicate wake never starts or renews a failed agent
            if Generation in self.Used or len(self.Used) >= 64:
                raise ValueError("replayed wake or start budget exhausted")
            if self.Active is not None:
                raise ValueError("stop current generation before replacement")
            self.Used.append(Generation)
            Atomic(self.LedgerFile, self.Used)  # consume before spawn, including spawn failures
            Atomic(self.Directory / "current.json", Ticket)
            (self.Directory / "status.json").unlink(missing_ok=True)
            self.Active = Generation
            self.Registered = False
            self.BridgeSequence = 0
            self.BridgeState = None
            self.Presence.Begin(Notice["RunId"], Generation)
            self.Started = self.Clock()
            try:
                self.Adapter.StartAgent()
            except Exception:
                self.Set("FAILED", "AGENT_FAILED")
                raise
            return self.Presence.Get()

    def Set(self, Status, Reason="NONE", Detail=""):
        self.Presence.Update(self.Presence.Generation, self.Presence.RunId,
                             self.Presence.Sequence + 1, Status, Reason, Detail)

    def Poll(self):
        if self.Active is None or self.Clock() - self.LastPoll < 0.25:
            return
        self.LastPoll = self.Clock()
        Agent = self.Adapter.GetAgentStatus()
        if Agent["AgentId"] is not None:
            self.Presence.AgentId = Agent["AgentId"]
        File = self.Directory / "status.json"
        if File.exists():
            if File.stat().st_size > 2048:
                self.Adapter.StopAgent()
                self.Set("FAILED", "AGENT_FAILED")
                return
            Row = json.loads(File.read_text())
            Exact(Row, ("Generation", "RunId", "Sequence", "Status", "Reason", "Detail"))
            if Row["Generation"] != self.Active or Row["RunId"] != self.Presence.RunId:
                raise ValueError("stale local status")
            if type(Row["Sequence"]) is not int or Row["Sequence"] < self.BridgeSequence:
                raise ValueError("replayed local status")
            if Row["Sequence"] > self.BridgeSequence:
                if Row["Sequence"] > 256:
                    raise ValueError("status budget exhausted")
                self.BridgeState = Row["Status"]
                self.Set("RUNNING" if Row["Status"] == "IDLE" and Agent["Active"] else Row["Status"],
                         Row["Reason"], Row["Detail"])
                self.BridgeSequence = Row["Sequence"]
                # Local bootstrap writes this only after typed REGISTERED acknowledgement.
                if Row["Status"] in ("WAITING_FOR_PEER", "RUNNING", "IDLE"):
                    self.Registered = True
        if not self.Registered and Agent["Active"] and self.Clock() - self.Started >= self.StartupSeconds:
            self.Adapter.StopAgent()
            self.Set("FAILED", "STARTUP_TIMEOUT")
        elif not Agent["Active"]:
            if self.Presence.Status == "NEEDS_USER":
                pass  # informative escalation never approves/retries
            elif Agent["Success"] and self.Registered and self.BridgeState == "IDLE":
                if self.Presence.Status != "IDLE":
                    self.Set("IDLE")
            else:
                if self.Presence.Status != "FAILED":
                    self.Set("FAILED", "AGENT_FAILED")
        elif self.Presence.Status not in ("FAILED", "NEEDS_USER"):
            # A local owned-process observation renews presence, not workflow authority.
            self.Presence.Expires = self.Clock() + self.Presence.Lease

    def GetPresence(self):
        with self.Lock:
            try:
                self.Poll()
            except (ValueError, KeyError, OSError):
                self.Adapter.StopAgent()
                if self.Presence.Status != "FAILED":
                    self.Set("FAILED", "AGENT_FAILED")
            return self.Presence.Get()

    def GetAgentStatus(self, Generation):
        if Generation != self.Active:
            raise ValueError("unknown/stale session")
        return self.GetPresence()

    def StopAgent(self, Generation):
        with self.Lock:
            if Generation != self.Active:
                raise ValueError("unknown/stale session")
            self.Adapter.StopAgent()
            self.Set("OFFLINE", "STOPPED")
            self.Active = None
            self.Tickets.pop(Generation, None)
            (self.Directory / "current.json").unlink(missing_ok=True)
            (self.Directory / "status.json").unlink(missing_ok=True)

    # Resume intentionally not exposed by endpoint: cross-run context reuse is unqualified.
