"""Compatibility v1 barrier; local validation and execution belong to adapters."""
from .transport import *


CONTROL_PREFLIGHT_CLASSIFICATION = "CONTROL_PREFLIGHT_ONLY"


def Listen(Config, Log, Selector):
    """Open the real legacy control listener before publishing readiness."""
    Listener = socket.socket()
    try:
        Listener.bind((Config["CoordinatorHost"], Config["Port"]))
        Listener.listen(2)
        Listener.setblocking(False)
        Selector.register(Listener, selectors.EVENT_READ)
        BoundHost, BoundPort = Listener.getsockname()[:2]
        Log.Write("LISTENING", Host=BoundHost, Port=BoundPort,
                  RunId=Config["RunId"], Pid=os.getpid(), ParentPid=os.getppid())
        Log.Write("LISTENER_READY", Host=BoundHost, Port=BoundPort,
                  RunId=Config["RunId"], Pid=os.getpid(), ParentPid=os.getppid())
        return Listener
    except BaseException:
        Listener.close()
        raise


def ConnectEndpoint(Config, Log):
    """The one source-bind, connect and authenticated STAGE_READY path."""
    Role = Config["Role"]
    Sock = socket.socket()
    Log.Write("ENDPOINT_CONTEXT", Role=Role, Pid=os.getpid(), ParentPid=os.getppid(),
              Executable=sys.executable, Cwd=os.getcwd())
    try:
        try:
            Sock.bind((Config["PeerIps"][Role], 0))
        except OSError as Error:
            Log.Write("BIND_FAILED", Role=Role, Source=Config["PeerIps"][Role],
                      Errno=Error.errno, WinError=getattr(Error, "winerror", None),
                      Detail=str(Error)[:512])
            raise
        Source = Sock.getsockname()[:2]
        Target = (Config["CoordinatorHost"], Config["Port"])
        Sock.settimeout(3)
        Started = time.monotonic_ns()
        Log.Write("CONNECT_START", Role=Role, Source=Source, Target=Target)
        try:
            # Coordinator starts first. A reconnect could replay registration.
            Sock.connect(Target)
        except OSError as Error:
            Log.Write("CONNECT_FAILED", Role=Role, Source=Source, Target=Target,
                      ElapsedUs=(time.monotonic_ns() - Started) // 1000,
                      Errno=Error.errno, WinError=getattr(Error, "winerror", None),
                      Detail=str(Error)[:512])
            raise
        Log.Write("CONNECT_OK", Role=Role, Source=Sock.getsockname()[:2],
                  Target=Sock.getpeername()[:2],
                  ElapsedUs=(time.monotonic_ns() - Started) // 1000)
        Link = Channel(Sock, Config, Log)
        Link.Send("STAGE_READY", Role=Role, ArtifactSHA256=Config["ArtifactSHA256"],
                  Endpoint=Config["Endpoint"])
        return Link
    except BaseException:
        Sock.close()
        raise


def RegisterStage(Item, Row, Config, Peers):
    Role = Row.get("Role")
    if (Row.get("Type") != "STAGE_READY" or Role not in ("CLIENT", "SERVER") or
            Role in Peers or Item.PeerIp != Config["PeerIps"][Role] or
            Row.get("ArtifactSHA256") != Config["ArtifactSHA256"] or
            Row.get("Endpoint") != Config["Endpoint"]):
        raise ValueError("invalid role, artifact, endpoint, or registration")
    Item.Role = Role
    Peers[Role] = Item


def Coordinator(Config, ValidateConfig):
    ValidateConfig(Config)
    ExpectedClassification = Config.get("ResultClassification", "ONE_CLIENT_READINESS_ONLY")
    if (not isinstance(ExpectedClassification, str) or
            not 1 <= len(ExpectedClassification) <= 64 or
            any(Letter not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for Letter in ExpectedClassification)):
        raise ValueError("invalid local result classification")
    Log = Journal(Config["EvidenceDir"])
    Listener = None
    Selector = selectors.DefaultSelector()
    Peers = {}
    Accepted = []
    State = "STAGING"
    Done = {}
    Result = {"Success": False, "Classification": "ABORT"}
    try:
        Listener = Listen(Config, Log, Selector)
        Deadline = time.monotonic() + Config["StageTimeout"]
        print("[Qualification:Coordinator] Waiting for staged local helpers", flush=True)
        while State != "COMPLETE":
            if time.monotonic() >= Deadline:
                raise TimeoutError("barrier/run deadline expired")
            # Drain buffered frames as well as socket readiness (TCP may coalesce envelopes).
            Ready = {Key.fileobj for Key, _ in Selector.select(0.05)}
            for ChannelItem in Accepted:
                if b"\n" in ChannelItem.Buffer:
                    Ready.add(ChannelItem.Socket)
            for Sock in Ready:
                if Sock is Listener:
                    NewSock, Address = Listener.accept()
                    if Address[0] not in Config["PeerIps"].values() or len(Accepted) >= 2:
                        NewSock.close()
                        raise ValueError("unexpected control peer IP or excess connection")
                    Item = Channel(NewSock, Config, Log)
                    Item.PeerIp, Item.Role = Address[0], None
                    Accepted.append(Item)
                    Selector.register(NewSock, selectors.EVENT_READ, Item)
                    continue
                Item = Selector.get_key(Sock).data
                Row = Item.Read()
                if Row is None:
                    continue
                Type = Row["Type"]
                if Type in ("ABORT", "FAILED"):
                    raise RuntimeError(Row.get("Detail", "endpoint failed"))
                if Item.Role is None:
                    RegisterStage(Item, Row, Config, Peers)
                    if len(Peers) == 2:
                        State = "ARMING"
                        Deadline = time.monotonic() + Config["RunTimeout"]
                        Peers["CLIENT"].Send("ARM_CAPTURE")
                    continue
                Role = Item.Role
                if State == "ARMING" and Role == "CLIENT" and Type == "CAPTURE_LIVE":
                    State = "STARTING_SERVER"
                    Peers["SERVER"].Send("START_SERVER")
                elif State == "STARTING_SERVER" and Role == "SERVER" and Type == "SERVER_LIVE":
                    if (Row.get("Endpoint") != Config["Endpoint"] or
                            type(Row.get("Pid")) is not int or Row["Pid"] <= 0):
                        raise ValueError("invalid server live report")
                    State = "RUNNING"
                    Peers["CLIENT"].Send("START_CLIENT", ServerPid=Row["Pid"])
                elif State == "RUNNING" and Role == "CLIENT" and Type == "CLIENT_RUNNING":
                    pass
                elif State == "RUNNING" and Type in ("CLIENT_DONE", "SERVER_DONE") and Type == Role + "_DONE":
                    if Role in Done:
                        raise ValueError("duplicate result")
                    Done[Role] = Row
                    # Finished endpoints remain connected until RUN_DONE; no reconnect/replay.
                    if Role == "CLIENT" and "SERVER" not in Done:
                        Peers["SERVER"].Send("FINALIZE")
                    if len(Done) == 2:
                        ClassesMatch = ("ResultClassification" not in Config or
                                        all(Value.get("Classification") == ExpectedClassification
                                            for Value in Done.values()))
                        Result = {"Success": (ClassesMatch and
                                              all(Value.get("Success") is True for Value in Done.values())),
                                  "Classification": ExpectedClassification, "Endpoints": Done}
                        if not ClassesMatch:
                            Result["Detail"] = "endpoint result classification mismatch"
                        for Peer in Peers.values():
                            Peer.Send("RUN_DONE", Success=Result["Success"])
                        State = "COMPLETE"
                else:
                    raise ValueError(f"out-of-state message {Role}:{Type} in {State}")
    except (Exception, KeyboardInterrupt) as Error:
        Result["Detail"] = str(Error) or type(Error).__name__
        for Item in Accepted:
            try:
                Item.Send("ABORT", Detail=Result["Detail"][:2048])
            except Exception:
                pass
    finally:
        for Item in Accepted:
            Item.Socket.close()
        Selector.close()
        if Listener:
            Listener.close()
        # Tokens are not retained in result evidence.
        for Value in Result.get("Endpoints", {}).values():
            Value.pop("Token", None)
        Log.Close(Result)
    return 0 if Result["Success"] else 1


def Endpoint(Config, ValidateConfig, LocalRun):
    ValidateConfig(Config)
    Role = Config["Role"]
    if Role not in ("CLIENT", "SERVER"):
        raise ValueError("invalid local role")
    Log = Journal(Config["EvidenceDir"])
    Run = LocalRun(Config, Log)
    Link = None
    Result = {"Success": False, "Classification": "ABORT"}
    State = "STAGED"
    Live = Done = False
    try:
        Run.Check()
        Link = ConnectEndpoint(Config, Log)
        print(f"[Qualification:{Role}] Staged; waiting on the LAN barrier", flush=True)
        Deadline = time.monotonic() + Config["StageTimeout"] + Config["RunTimeout"]
        while State != "COMPLETE":
            if time.monotonic() >= Deadline:
                raise TimeoutError("local coordination deadline expired")
            Row = Link.Read()
            if Row:
                Type = Row["Type"]
                if Type == "ABORT":
                    raise RuntimeError(Row.get("Detail", "coordinator aborted"))
                if Role == "CLIENT" and State == "STAGED" and Type == "ARM_CAPTURE":
                    Run.ArmCapture()
                    State = "ARMED"
                    Link.Send("CAPTURE_LIVE")
                elif Role == "SERVER" and State == "STAGED" and Type == "START_SERVER":
                    Run.ArmCapture()
                    Run.Start()
                    State = "RUNNING"
                elif Role == "CLIENT" and State == "ARMED" and Type == "START_CLIENT":
                    Run.Start()
                    State = "RUNNING"
                    Link.Send("CLIENT_RUNNING", Pid=Run.Probe.pid)
                elif Role == "SERVER" and State == "RUNNING" and Live and Type == "FINALIZE" and not Done:
                    Run.FinalizeAt = time.monotonic() + 5
                    State = "FINALIZING"
                    Log.Write("STATE", Value=State)
                elif Role == "SERVER" and State == "RESULT_READY" and Done and Type == "FINALIZE":
                    # The coordinator can send FINALIZE just before it receives an
                    # already-sent SERVER_DONE. Accept this one delayed signal only
                    # after the server result and cleanup are complete.
                    State = "FINALIZING"
                    Log.Write("STATE", Value=State, Detail="late FINALIZE after SERVER_DONE")
                elif Type == "RUN_DONE" and Done and State in ("RESULT_READY", "FINALIZING"):
                    State = "COMPLETE"
                    Log.Write("STATE", Value=State)
                else:
                    raise ValueError("unexpected command " + Type + " in " + State)
            if Done:
                continue
            Run.Status()
            if Run.Probe is None:
                continue
            if Role == "SERVER" and not Live:
                if Run.Probe.poll() is not None:
                    raise RuntimeError("server exited before a verified live listener")
                if Run.ServerLive():
                    Live = True
                    Link.Send("SERVER_LIVE", Pid=Run.Probe.pid, Endpoint=Config["Endpoint"])
                elif time.monotonic() - Run.Started > 6:
                    raise TimeoutError("server listener did not become live")
            if Run.Probe.poll() is not None:
                Result = {"Classification": "ONE_CLIENT_READINESS_ONLY", **Run.Result()}
                Errors = Run.Cleanup()
                if Errors:
                    Result["Success"] = False
                    Result["CleanupErrors"] = Errors
                Done = True
                State = "RESULT_READY"
                Log.Write("STATE", Value=State)
                Link.Send(Role + "_DONE", **Result)
    except (Exception, KeyboardInterrupt) as Error:
        Result = {"Success": False, "Classification": "ABORT", "Detail": str(Error) or type(Error).__name__}
        if Link:
            try:
                Link.Send("FAILED", Detail=Result["Detail"][:2048])
            except Exception:
                pass
    finally:
        if not Done:
            Errors = Run.Cleanup()
            if Errors:
                Result["CleanupErrors"] = Errors
        if Link:
            Link.Socket.close()
        Log.Close(Result)
    return 0 if Result["Success"] else 1


def ControlPreflightCoordinator(Config, ValidateConfig):
    """Locally selected two-role registration proof; no probe/capture states."""
    ValidateConfig(Config)
    Log = Journal(Config["EvidenceDir"])
    Listener = None
    Selector = selectors.DefaultSelector()
    Accepted = []
    Peers = {}
    Done = set()
    State = "STAGING"
    Result = {"Success": False, "Classification": "ABORT"}
    try:
        Listener = Listen(Config, Log, Selector)
        Deadline = time.monotonic() + Config["StageTimeout"]
        while len(Done) != 2:
            if time.monotonic() >= Deadline:
                raise TimeoutError("control preflight deadline expired")
            Ready = {Key.fileobj for Key, _ in Selector.select(0.05)}
            for Item in Accepted:
                if b"\n" in Item.Buffer:
                    Ready.add(Item.Socket)
            for Sock in Ready:
                if Sock is Listener:
                    NewSock, Address = Listener.accept()
                    if Address[0] not in Config["PeerIps"].values() or len(Accepted) >= 2:
                        NewSock.close()
                        raise ValueError("unexpected control peer IP or excess connection")
                    Item = Channel(NewSock, Config, Log)
                    Item.PeerIp, Item.Role = Address[0], None
                    Accepted.append(Item)
                    Selector.register(NewSock, selectors.EVENT_READ, Item)
                    Log.Write("ACCEPT", Source=Address[:2], Target=NewSock.getsockname()[:2],
                              Pid=os.getpid())
                    continue
                Item = Selector.get_key(Sock).data
                Row = Item.Read()
                if Row is None:
                    continue
                if Row["Type"] in ("ABORT", "FAILED"):
                    raise RuntimeError(Row.get("Detail", "endpoint failed"))
                if Item.Role is None:
                    RegisterStage(Item, Row, Config, Peers)
                    if len(Peers) == 2:
                        State = "AWAITING_DONE"
                        Deadline = time.monotonic() + Config["RunTimeout"]
                        for Peer in Peers.values():
                            Peer.Send("RUN_DONE", Success=True,
                                      Classification=CONTROL_PREFLIGHT_CLASSIFICATION)
                    continue
                Role = Item.Role
                if (State != "AWAITING_DONE" or Row["Type"] != Role + "_DONE" or
                        Role in Done or Row.get("Success") is not True or
                        Row.get("Classification") != CONTROL_PREFLIGHT_CLASSIFICATION):
                    raise ValueError("invalid control preflight acknowledgement")
                Done.add(Role)
                if len(Done) == 2:
                    for Peer in Peers.values():
                        Peer.Send("RUN_DONE", Success=True,
                                  Classification=CONTROL_PREFLIGHT_CLASSIFICATION)
                    break
        Result = {"Success": True,
                  "Classification": CONTROL_PREFLIGHT_CLASSIFICATION,
                  "Roles": sorted(Done)}
    except (Exception, KeyboardInterrupt) as Error:
        Result["Detail"] = str(Error) or type(Error).__name__
        for Item in Accepted:
            try:
                Item.Send("ABORT", Detail=Result["Detail"][:2048])
            except Exception:
                pass
    finally:
        for Item in Accepted:
            Item.Socket.close()
        Selector.close()
        if Listener:
            Listener.close()
        Log.Close(Result)
    return 0 if Result["Success"] else 1


def ControlPreflightEndpoint(Config, ValidateConfig, LocalRun):
    """Prove the production child connection path and exit before physical work."""
    ValidateConfig(Config)
    Role = Config["Role"]
    if Role not in ("CLIENT", "SERVER"):
        raise ValueError("invalid local role")
    Log = Journal(Config["EvidenceDir"])
    Run = LocalRun(Config, Log)
    Link = None
    Result = {"Success": False, "Classification": "ABORT"}
    Cleaned = False
    try:
        Run.Check()
        Link = ConnectEndpoint(Config, Log)
        Deadline = time.monotonic() + Config["StageTimeout"] + Config["RunTimeout"]
        while True:
            if time.monotonic() >= Deadline:
                raise TimeoutError("control preflight response deadline expired")
            Row = Link.Read()
            if Row is None:
                continue
            if Row["Type"] == "ABORT":
                raise RuntimeError(Row.get("Detail", "coordinator aborted"))
            if (Row["Type"] != "RUN_DONE" or Row.get("Success") is not True or
                    Row.get("Classification") != CONTROL_PREFLIGHT_CLASSIFICATION):
                raise ValueError("unexpected control preflight response")
            Errors = Run.Cleanup()
            Cleaned = True
            if Errors:
                raise RuntimeError("control preflight cleanup failed: " + str(Errors))
            Link.Send(Role + "_DONE", Success=True,
                      Classification=CONTROL_PREFLIGHT_CLASSIFICATION)
            while True:
                if time.monotonic() >= Deadline:
                    raise TimeoutError("control preflight completion deadline expired")
                Final = Link.Read()
                if Final is None:
                    continue
                if (Final["Type"] != "RUN_DONE" or Final.get("Success") is not True or
                        Final.get("Classification") != CONTROL_PREFLIGHT_CLASSIFICATION):
                    raise ValueError("unexpected control preflight completion")
                break
            Result = {"Success": True,
                      "Classification": CONTROL_PREFLIGHT_CLASSIFICATION,
                      "Role": Role}
            break
    except (Exception, KeyboardInterrupt) as Error:
        Result = {"Success": False, "Classification": "ABORT",
                  "Detail": str(Error) or type(Error).__name__}
        if Link:
            try:
                Link.Send("FAILED", Detail=Result["Detail"][:2048])
            except Exception:
                pass
    finally:
        if not Cleaned:
            Errors = Run.Cleanup()
            if Errors:
                Result["Success"] = False
                Result["CleanupErrors"] = Errors
        if Link:
            Link.Socket.close()
        Log.Close(Result)
    return 0 if Result["Success"] else 1

