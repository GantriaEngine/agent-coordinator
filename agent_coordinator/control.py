"""Schema v1 coordination over the extracted newline JSON transport v1."""
import ipaddress
import secrets
import selectors
import socket
import time
import uuid

from .transport import Channel
from .workflow import Exact, ValidateResult


FIELDS = {
    "PULL_ASSIGNMENT": ("EndpointId",),
    "ASSIGNMENT": ("Assignment",),
    "REGISTER": ("Role", "SchemaId", "SchemaVersion", "SchemaHash", "Capabilities"),
    "REGISTERED": ("Role",),
    "STAGED": ("Role",),
    "ARMED": ("ExecutionTimeout",),
    "CLEANED": ("Role", "Success"),
    "START": ("Index", "Role", "Capability", "From", "To", "Parameters", "Timeout", "ExecutionTimeout"),
    "LIVE": ("Index", "Role", "Success", "Evidence"),
    "RESULT": ("Index", "Role", "Success", "Evidence"),
    "COMPLETE": ("Success",),
    "ABORT": ("Detail",),
}
ENVELOPE = ("Version", "RunId", "Sequence", "TimestampUnixMs", "Token", "Type")


class Link(Channel):
    def Read(self):
        Row = super().Read()
        if Row is not None:
            if Row["Type"] not in FIELDS:
                raise ValueError("unknown message category")
            Exact(Row, (*ENVELOPE, *FIELDS[Row["Type"]]))
            if type(Row["Version"]) is not int:
                raise ValueError("invalid protocol version type")
            for Field in ("Index", "Timeout", "ExecutionTimeout", "SchemaVersion"):
                if Field in Row and type(Row[Field]) is not int:
                    raise ValueError("invalid integer metadata")
            for Field in ("Role", "SchemaId", "SchemaHash", "Capability", "From", "To", "EndpointId", "Detail"):
                if Field in Row and (not isinstance(Row[Field], str) or len(Row[Field]) > 2048):
                    raise ValueError("invalid symbolic or diagnostic metadata")
        return Row


def Address(Host):
    Parsed = ipaddress.IPv4Address(Host)
    if Parsed.is_unspecified or Parsed.is_multicast or not (Parsed.is_private or Parsed.is_loopback):
        raise ValueError("explicit trusted-LAN or loopback IPv4 required")


def Identity(Value):
    if str(uuid.UUID(Value["RunId"])) != Value["RunId"]:
        raise ValueError("canonical run UUID required")
    if not isinstance(Value["Token"], str) or len(Value["Token"]) != 64:
        raise ValueError("256-bit token required")
    bytes.fromhex(Value["Token"])


class Assignments:
    """Endpoint IDs/roles/bootstrap secrets are approved out of band locally."""
    def __init__(self, Workflow, Endpoints, Parameters):
        if set(Endpoints) != set(Workflow.Value["Roles"]):
            raise ValueError("exactly one preconfigured endpoint per role required")
        self.Workflow = Workflow
        self.Parameters = Workflow.Parameters(Parameters)
        self.RunId = str(uuid.uuid4())
        self.ExpiresUnixMs = time.time_ns() // 1000000 + Workflow.Value["RegistrationTimeout"] * 1000
        self.Entries = {}
        for Role, Local in Endpoints.items():
            Exact(Local, ("EndpointId", "PeerIp", "RunId", "Token"))
            Address(Local["PeerIp"])
            Identity(Local)
            if Local["EndpointId"] in self.Entries:
                raise ValueError("duplicate endpoint identity")
            self.Entries[Local["EndpointId"]] = {"Role": Role, "Local": dict(Local), "Claimed": False}

    def Pull(self, EndpointId, PeerIp):
        if time.time_ns() // 1000000 >= self.ExpiresUnixMs:
            raise TimeoutError("assignment expired")
        Entry = self.Entries[EndpointId]
        if Entry["Claimed"] or PeerIp != Entry["Local"]["PeerIp"]:
            raise ValueError("assignment replay or peer mismatch")
        Entry["Claimed"] = True
        return {"RunId": self.RunId, "Token": secrets.token_hex(32), "Role": Entry["Role"],
                "SchemaId": self.Workflow.Value["SchemaId"], "SchemaVersion": self.Workflow.Value["SchemaVersion"],
                "SchemaHash": self.Workflow.Hash, "Parameters": self.Parameters,
                "ExpiresUnixMs": self.ExpiresUnixMs}


def Await(LinkItem, Deadline):
    while time.monotonic() < Deadline:
        Row = LinkItem.Read()
        if Row is not None:
            if Row["Type"] == "ABORT":
                raise RuntimeError("peer aborted")
            return Row
    raise TimeoutError("control deadline expired")


def Host(HostIp, Port, AssignmentsItem, Journal, Listening=None):
    Address(HostIp)
    Workflow = AssignmentsItem.Workflow
    Listener = socket.socket()
    Peers, Accepted, Results, Sockets = {}, [], [], []
    Selector = selectors.DefaultSelector()
    Success = False
    try:
        Listener.bind((HostIp, Port))
        Listener.listen(len(AssignmentsItem.Entries))
        Listener.setblocking(False)
        if Listening:
            Listening(Listener.getsockname()[1])
        RegistrationDeadline = time.monotonic() + Workflow.Value["RegistrationTimeout"]
        # Bound slow clients by the same registration budget; no execution starts here.
        while len(Peers) < len(AssignmentsItem.Entries):
            if time.monotonic() >= RegistrationDeadline:
                raise TimeoutError("registration deadline expired")
            try:
                Sock, Peer = Listener.accept()
                Sockets.append(Sock)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            Candidates = [Entry for Entry in AssignmentsItem.Entries.values()
                          if Entry["Local"]["PeerIp"] == Peer[0] and not Entry["Claimed"]]
            if not Candidates:
                Sock.close()
                raise ValueError("unknown peer")
            # First envelope identifies which local bootstrap identity to validate.
            # Peek bounded bytes without consuming or trusting them.
            Sock.settimeout(max(0.01, RegistrationDeadline - time.monotonic()))
            Frame = bytearray()
            while b"\n" not in Frame and len(Frame) < 16384:
                Byte = Sock.recv(1)
                if not Byte:
                    raise EOFError("registration disconnected")
                Frame.extend(Byte)
            import json
            First = json.loads(Frame)
            if not isinstance(First, dict):
                Sock.close()
                raise ValueError("invalid bootstrap envelope")
            Entry = AssignmentsItem.Entries.get(First.get("EndpointId"))
            if Entry not in Candidates:
                Sock.close()
                raise ValueError("unknown endpoint")
            Item = Link(Sock, Entry["Local"], Journal)
            Accepted.append(Item)
            Item.Buffer = Frame
            Row = Await(Item, RegistrationDeadline)
            if Row["Type"] != "PULL_ASSIGNMENT":
                raise ValueError("expected assignment pull")
            Assignment = AssignmentsItem.Pull(Row["EndpointId"], Peer[0])
            Item.Send("ASSIGNMENT", Assignment=Assignment)
            Item.Config = Assignment
            Register = Await(Item, RegistrationDeadline)
            Role = Assignment["Role"]
            if (Register["Type"] != "REGISTER" or Register["Role"] != Role or
                    Register["SchemaId"] != Assignment["SchemaId"] or Register["SchemaVersion"] != Assignment["SchemaVersion"] or
                    Register["SchemaHash"] != Workflow.Hash or Role in Peers):
                raise ValueError("schema/run/role registration mismatch")
            Workflow.Registration(Role, Register["Capabilities"])
            Item.Advertised = Register["Capabilities"]
            Item.Send("REGISTERED", Role=Role)
            Staged = Await(Item, RegistrationDeadline)
            if Staged["Type"] != "STAGED" or Staged["Role"] != Role:
                raise ValueError("endpoint not staged")
            Peers[Role] = Item
        Deadline = time.monotonic() + Workflow.Value["ExecutionTimeout"]
        for Item in Peers.values():
            Item.Send("ARMED", ExecutionTimeout=Workflow.Value["ExecutionTimeout"])
            Selector.register(Item.Socket, selectors.EVENT_READ, Item)
        for Index, Step in enumerate(Workflow.Value["Transitions"]):
            Item = Peers[Step["Role"]]
            if Step["Capability"] not in Item.Advertised:
                raise ValueError("coordinator cannot invoke unadvertised capability")
            Item.Send("START", Index=Index, Role=Step["Role"], Capability=Step["Capability"],
                      From=Step["From"], To=Step["To"],
                      Parameters={Name: AssignmentsItem.Parameters[Name] for Name in Step["Parameters"]},
                      Timeout=Step["Timeout"], ExecutionTimeout=Workflow.Value["ExecutionTimeout"])
            StepDeadline = min(Deadline, time.monotonic() + Step["Timeout"])
            Received = False
            while not Received:
                if time.monotonic() >= StepDeadline:
                    raise TimeoutError("execution/operation deadline expired")
                Ready = [Key.data for Key, _ in Selector.select(0.02)]
                Ready.extend(Peer for Peer in Peers.values() if b"\n" in Peer.Buffer and Peer not in Ready)
                for Peer in Ready:
                    Row = Peer.Read()
                    if Row is None:
                        continue
                    if (Received or Peer is not Item or Row["Type"] != Step["Response"] or
                            Row["Role"] != Step["Role"] or Row["Index"] != Index or Row["Success"] is not True):
                        raise ValueError("unauthorized transition or failed result")
                    # Metadata only, never artifact content or executable payloads.
                    ValidateResult({"Success": Row["Success"], "Evidence": Row["Evidence"]})
                    Results.append({Key: Value for Key, Value in Row.items() if Key != "Token"})
                    Received = True
        for Item in Peers.values():
            Item.Send("COMPLETE", Success=True)
        for Role, Item in Peers.items():
            Row = Await(Item, Deadline)
            if Row["Type"] != "CLEANED" or Row["Role"] != Role or Row["Success"] is not True:
                raise ValueError("endpoint cleanup failed")
        Success = True
    except (Exception, KeyboardInterrupt) as Error:
        Journal.Write("ABORT", Detail=str(Error)[:512])
        for Item in Accepted:
            try:
                Item.Send("ABORT", Detail="run aborted")
            except Exception:
                pass
    finally:
        for Item in Accepted:
            Item.Socket.close()
        for Sock in Sockets:
            Sock.close()
        Selector.close()
        Listener.close()
        Journal.Close({"Success": Success, "RunId": AssignmentsItem.RunId, "Results": Results})
    return 0 if Success else 1


def Join(Config, ApprovedWorkflows, CatalogItem, Journal, ExpectedRunId=None, Observe=None, ExpectedRole=None):
    Exact(Config, ("EndpointId", "PeerIp", "CoordinatorHost", "Port", "RunId", "Token"))
    Address(Config["PeerIp"])
    Address(Config["CoordinatorHost"])
    Identity(Config)
    Sock, Item, Success = socket.socket(), None, False
    Cleaned = False
    try:
        Sock.bind((Config["PeerIp"], 0))
        Sock.settimeout(3)
        Sock.connect((Config["CoordinatorHost"], Config["Port"]))
        Item = Link(Sock, Config, Journal)
        Item.Send("PULL_ASSIGNMENT", EndpointId=Config["EndpointId"])
        Response = Await(Item, time.monotonic() + 10)
        if Response["Type"] != "ASSIGNMENT":
            raise ValueError("expected assignment")
        Assignment = Response["Assignment"]
        Exact(Assignment, ("RunId", "Token", "Role", "SchemaId", "SchemaVersion", "SchemaHash", "Parameters", "ExpiresUnixMs"))
        Identity(Assignment)
        if ExpectedRunId is not None and Assignment["RunId"] != ExpectedRunId:
            raise ValueError("lifecycle run mismatch")
        if type(Assignment["SchemaVersion"]) is not int:
            raise ValueError("unsupported schema version")
        if type(Assignment["ExpiresUnixMs"]) is not int or time.time_ns() // 1000000 >= Assignment["ExpiresUnixMs"]:
            raise TimeoutError("expired assignment")
        Workflow = ApprovedWorkflows[(Assignment["SchemaId"], Assignment["SchemaVersion"])]
        if Workflow.Hash != Assignment["SchemaHash"]:
            raise ValueError("local workflow hash mismatch")
        Parameters = Workflow.Parameters(Assignment["Parameters"])
        Role = Assignment["Role"]
        if ExpectedRole is not None and Role != ExpectedRole:
            raise ValueError("lifecycle role mismatch")
        Advertised = CatalogItem.Advertise(Workflow, Role)
        Item.Config = Assignment
        Item.Send("REGISTER", Role=Role, SchemaId=Assignment["SchemaId"], SchemaVersion=Assignment["SchemaVersion"],
                  SchemaHash=Workflow.Hash, Capabilities=Advertised)
        RegistrationDeadline = time.monotonic() + min(Workflow.Value["RegistrationTimeout"],
            max(0, (Assignment["ExpiresUnixMs"] - time.time_ns() // 1000000) / 1000))
        Response = Await(Item, RegistrationDeadline)
        if Response["Type"] != "REGISTERED" or Response["Role"] != Role:
            raise ValueError("invalid registration acknowledgement")
        if Observe:
            Observe("WAITING_FOR_PEER")
        Item.Send("STAGED", Role=Role)
        Armed = Await(Item, RegistrationDeadline)
        if Armed["Type"] != "ARMED" or Armed["ExecutionTimeout"] != Workflow.Value["ExecutionTimeout"]:
            raise ValueError("invalid barrier")
        ExecutionDeadline = time.monotonic() + Workflow.Value["ExecutionTimeout"]
        LastIndex = -1
        while True:
            # Before first command, waiting for registration must not spend execution budget.
            Row = Await(Item, ExecutionDeadline)
            if Row["Type"] == "COMPLETE":
                Required = [Index for Index, Step in enumerate(Workflow.Value["Transitions"]) if Step["Role"] == Role]
                if Row["Success"] is not True or LastIndex != max(Required, default=-1):
                    raise ValueError("premature completion")
                CatalogItem.Cleanup()
                Cleaned = True
                Item.Send("CLEANED", Role=Role, Success=True)
                Success = True
                break
            if Row["Type"] != "START" or Row["Index"] <= LastIndex:
                raise ValueError("unexpected control message")
            Step = Workflow.Transition(Row["Index"], Role, Row["Capability"], Row["From"], Row["To"])
            ExpectedIndex = next((Index for Index, Candidate in enumerate(Workflow.Value["Transitions"])
                                  if Index > LastIndex and Candidate["Role"] == Role), None)
            if Row["Index"] != ExpectedIndex or Row["Timeout"] != Step["Timeout"] or Row["ExecutionTimeout"] != Workflow.Value["ExecutionTimeout"]:
                raise ValueError("skipped transition or altered deadline")
            if Row["Parameters"] != {Name: Parameters[Name] for Name in Step["Parameters"]}:
                raise ValueError("unapproved invocation parameters")
            if ExecutionDeadline is None:
                ExecutionDeadline = time.monotonic() + Workflow.Value["ExecutionTimeout"]
            Deadline = min(ExecutionDeadline, time.monotonic() + Step["Timeout"])
            if Observe:
                Observe("RUNNING")
            Result = CatalogItem.Invoke(Row["Capability"], Advertised, Row["Parameters"], Deadline)
            Item.Send(Step["Response"], Index=Row["Index"], Role=Role, **Result)
            LastIndex = Row["Index"]
    except (Exception, KeyboardInterrupt) as Error:
        Journal.Write("ABORT", Detail=str(Error)[:512])
        if Item:
            try:
                Item.Send("ABORT", Detail="endpoint aborted")
            except Exception:
                pass
    finally:
        try:
            if not Cleaned:
                CatalogItem.Cleanup()
        except Exception as Error:
            Success = False
            Journal.Write("CLEANUP_FAILED", Detail=str(Error)[:512])
        Sock.close()
        Journal.Close({"Success": Success})
    return 0 if Success else 1
