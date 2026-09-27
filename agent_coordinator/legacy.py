"""Compatibility v1 barrier; local validation and execution belong to adapters."""
from .transport import *

def Coordinator(Config, ValidateConfig):
    ValidateConfig(Config)
    Log = Journal(Config["EvidenceDir"])
    Listener = socket.socket()
    Selector = selectors.DefaultSelector()
    Peers = {}
    Accepted = []
    State = "STAGING"
    Done = {}
    Result = {"Success": False, "Classification": "ABORT"}
    try:
        Listener.bind((Config["CoordinatorHost"], Config["Port"]))
        Listener.listen(2)
        Listener.setblocking(False)
        Selector.register(Listener, selectors.EVENT_READ)
        Deadline = time.monotonic() + Config["StageTimeout"]
        Log.Write("LISTENING", Host=Config["CoordinatorHost"], Port=Config["Port"], RunId=Config["RunId"])
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
                    Role = Row.get("Role")
                    if (Type != "STAGE_READY" or Role not in ("CLIENT", "SERVER") or
                            Role in Peers or Item.PeerIp != Config["PeerIps"][Role] or
                            Row.get("ArtifactSHA256") != Config["ArtifactSHA256"] or
                            Row.get("Endpoint") != Config["Endpoint"]):
                        raise ValueError("invalid role, artifact, endpoint, or registration")
                    Item.Role = Role
                    Peers[Role] = Item
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
                        Result = {"Success": all(Value.get("Success") is True for Value in Done.values()),
                                  "Classification": "ONE_CLIENT_READINESS_ONLY", "Endpoints": Done}
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
    Sock = None
    Result = {"Success": False, "Classification": "ABORT"}
    State = "STAGED"
    Live = Done = False
    try:
        Run.Check()
        Sock = socket.socket()
        Sock.bind((Config["PeerIps"][Role], 0))
        Sock.settimeout(3)
        # Coordinator starts first. No reconnect can replay a run after registration.
        Sock.connect((Config["CoordinatorHost"], Config["Port"]))
        Link = Channel(Sock, Config, Log)
        Link.Send("STAGE_READY", Role=Role, ArtifactSHA256=Config["ArtifactSHA256"], Endpoint=Config["Endpoint"])
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
                elif Role == "SERVER" and Live and Type == "FINALIZE" and not Done:
                    Run.FinalizeAt = time.monotonic() + 5
                elif Type == "RUN_DONE" and Done:
                    State = "COMPLETE"
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
        elif Sock:
            Sock.close()
        Log.Close(Result)
    return 0 if Result["Success"] else 1


