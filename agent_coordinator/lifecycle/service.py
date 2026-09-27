"""Bounded lifecycle v1 request/reply. TLS required beyond loopback.

No provisioning, prompts, approval responses, capability calls, or status-to-command
translation exist in this channel. SSH forwarding is an optional deployment tunnel.
"""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import ssl
import threading
import time

from ..control import Address
from ..workflow import Exact
from .presence import Uuid

MAX_FRAME = 4096


def Encode(Value):
    return json.dumps(Value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def Read(Sock):
    Data = bytearray()
    while len(Data) <= MAX_FRAME:
        Byte = Sock.recv(1)
        if not Byte:
            raise EOFError("lifecycle disconnected")
        if Byte == b"\n":
            return json.loads(Data)
        Data.extend(Byte)
    raise ValueError("lifecycle frame bound exceeded")


class Service:
    def __init__(self, Endpoint, Token, MaxSeconds=180):
        if not isinstance(Token, str) or len(Token) != 64:
            raise ValueError("256-bit local lifecycle key required")
        self.Key = bytes.fromhex(Token)
        if type(MaxSeconds) is not int or not 1 <= MaxSeconds <= 600:
            raise ValueError("invalid daemon budget")
        self.Endpoint, self.MaxSeconds = Endpoint, MaxSeconds
        self.Nonces = {}
        self.Requests = 0
        self.Lock = threading.Lock()

    def Handle(self, Row):
        with self.Lock:
            Exact(Row, ("Version", "EndpointId", "Operation", "Nonce", "TimestampUnixMs", "Payload", "MAC"))
            if type(Row["Version"]) is not int or Row["Version"] != 1 or Row["EndpointId"] != self.Endpoint.Presence.EndpointId:
                raise ValueError("unsupported lifecycle identity")
            Now = time.time_ns() // 1000000
            if type(Row["TimestampUnixMs"]) is not int or abs(Now - Row["TimestampUnixMs"]) > 10000:
                raise ValueError("stale lifecycle request")
            Uuid(Row["Nonce"])
            Mac = Row["MAC"]
            Unsigned = {Key: Value for Key, Value in Row.items() if Key != "MAC"}
            Expected = hmac.new(self.Key, Encode(Unsigned), hashlib.sha256).hexdigest()
            if not isinstance(Mac, str) or not hmac.compare_digest(Mac, Expected):
                raise ValueError("lifecycle authentication failed")
            self.Nonces = {Key: Expiry for Key, Expiry in self.Nonces.items() if Expiry > Now}
            if Row["Nonce"] in self.Nonces or self.Requests >= 1024:
                raise ValueError("replayed request or request budget exhausted")
            self.Nonces[Row["Nonce"]] = Now + 20000
            self.Requests += 1
            Operation, Payload = Row["Operation"], Row["Payload"]
            if Operation == "GetPresence":
                Exact(Payload, ())
                return self.Endpoint.GetPresence()
            if Operation == "StartAgent":
                return self.Endpoint.StartAgent(Payload)
            if Operation in ("StopAgent", "GetAgentStatus"):
                Exact(Payload, ("Generation",))
                Uuid(Payload["Generation"])
                if Operation == "StopAgent":
                    self.Endpoint.StopAgent(Payload["Generation"])
                    return self.Endpoint.GetPresence()
                return self.Endpoint.GetAgentStatus(Payload["Generation"])
            raise ValueError("unsupported lifecycle operation")

    def Serve(self, Host, Port, Ready=None, Stop=None, TLS=None):
        Address(Host)
        if not ipaddress.ip_address(Host).is_loopback and TLS is None:
            raise ValueError("TLS required for LAN lifecycle listener")
        End = time.monotonic() + self.MaxSeconds
        with socket.socket() as Listener:
            Listener.bind((Host, Port))
            Listener.listen(4)
            Listener.settimeout(0.25)
            if Ready:
                Ready(Listener.getsockname()[1])
            try:
                while time.monotonic() < End and not (Stop and Stop.is_set()):
                    self.Endpoint.GetPresence()  # supervision continues without coordinator polls
                    try:
                        Sock, _ = Listener.accept()
                    except socket.timeout:
                        continue
                    with Sock:
                        Sock.settimeout(1)
                        try:
                            if TLS:
                                Sock = TLS.wrap_socket(Sock, server_side=True)
                            with Sock:
                                try:
                                    Result = {"Success": True, "Presence": self.Handle(Read(Sock))}
                                except (ValueError, KeyError, OSError, EOFError):
                                    Result = {"Success": False, "Error": "request rejected"}
                                Sock.sendall(Encode(Result) + b"\n")
                        except OSError:
                            pass
            finally:
                if self.Endpoint.Active is not None:
                    self.Endpoint.StopAgent(self.Endpoint.Active)


class Client:
    def __init__(self, Host, Port, EndpointId, Token, TLS=None, ServerName=None):
        Address(Host)
        if not ipaddress.ip_address(Host).is_loopback and TLS is None:
            raise ValueError("verified TLS required beyond loopback")
        if TLS is not None and (TLS.verify_mode != ssl.CERT_REQUIRED or not TLS.check_hostname):
            raise ValueError("lifecycle client must verify TLS certificate and hostname")
        if not isinstance(Token, str) or len(Token) != 64:
            raise ValueError("256-bit lifecycle key required")
        self.Host, self.Port, self.EndpointId = Host, Port, EndpointId
        self.Key, self.TLS, self.ServerName = bytes.fromhex(Token), TLS, ServerName

    def Request(self, Operation, Payload):
        import uuid
        Row = {"Version": 1, "EndpointId": self.EndpointId, "Operation": Operation,
               "Payload": Payload, "Nonce": str(uuid.uuid4()), "TimestampUnixMs": time.time_ns() // 1000000}
        Row["MAC"] = hmac.new(self.Key, Encode(Row), hashlib.sha256).hexdigest()
        Frame = Encode(Row) + b"\n"
        if len(Frame) > MAX_FRAME:
            raise ValueError("lifecycle frame bound exceeded")
        with socket.create_connection((self.Host, self.Port), timeout=2) as Sock:
            if self.TLS:
                Sock = self.TLS.wrap_socket(Sock, server_hostname=self.ServerName or self.Host)
            with Sock:
                Sock.sendall(Frame)
                Result = Read(Sock)
        if Result.get("Success") is not True:
            raise ValueError("endpoint rejected lifecycle request")
        return Result["Presence"]

    def GetPresence(self):
        return self.Request("GetPresence", {})

    def StartAgent(self, Notice):
        return self.Request("StartAgent", Notice)

    def GetAgentStatus(self, Generation):
        return self.Request("GetAgentStatus", {"Generation": Generation})

    def StopAgent(self, Generation):
        return self.Request("StopAgent", {"Generation": Generation})
