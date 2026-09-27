"""Extracted bounded LAN control primitives; Python 3.12+."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import selectors
import shutil
import socket
import subprocess
import sys
import time
import uuid

MAX_FRAME = 16384
MAX_LOG = 16 * 1024 * 1024
def Digest(File):
    with open(File, "rb") as Stream:
        return hashlib.file_digest(Stream, "sha256").hexdigest().upper()


def Save(File, Value):
    Path(File).write_text(json.dumps(Value, indent=2) + "\n", encoding="utf-8")


def Hidden():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class Journal:
    def __init__(self, Directory):
        self.Directory = Path(Directory)
        self.Directory.mkdir(parents=True, exist_ok=False)
        self.Stream = (self.Directory / "control.jsonl").open("w", encoding="utf-8")

    def Write(self, Event, **Fields):
        Fields = Redact(Fields)
        Row = {"TimestampUnixMs": time.time_ns() // 1000000, "Event": Event, **Fields}
        self.Stream.write(json.dumps(Row) + "\n")
        self.Stream.flush()
        if self.Stream.tell() > MAX_LOG:
            raise ValueError("control log bound exceeded")

    def Close(self, Result):
        Result = Redact(Result)
        self.Write("RESULT", **Result)
        self.Stream.close()
        Save(self.Directory / "result.json", Result)
        Entries = [{"Path": str(File.relative_to(self.Directory)), "SHA256": Digest(File),
                    "Bytes": File.stat().st_size} for File in sorted(self.Directory.rglob("*"))
                   if File.is_file() and File.name != "evidence-manifest.json"]
        Save(self.Directory / "evidence-manifest.json", {"Files": Entries})


def Redact(Value):
    if isinstance(Value, dict):
        return {Key: Redact(Item) for Key, Item in Value.items() if Key != "Token"}
    if isinstance(Value, list):
        return [Redact(Item) for Item in Value]
    return Value


class Channel:
    def __init__(self, Sock, Config, Journal):
        self.Socket, self.Config, self.Journal = Sock, Config, Journal
        self.Socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.Socket.settimeout(0.1)
        self.Buffer = bytearray()
        self.Tx = self.Rx = 0

    def Send(self, Type, **Fields):
        self.Tx += 1
        Row = {"Version": 1, "RunId": self.Config["RunId"], "Sequence": self.Tx,
               "TimestampUnixMs": time.time_ns() // 1000000, "Token": self.Config["Token"],
               "Type": Type, **Fields}
        Frame = json.dumps(Row, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(Frame) > MAX_FRAME:
            raise ValueError("outbound frame too large")
        self.Socket.settimeout(1)
        try:
            self.Socket.sendall(Frame)
        finally:
            self.Socket.settimeout(0.1)
        self.Journal.Write("SEND", **Row)

    def Read(self):
        if b"\n" not in self.Buffer:
            try:
                Chunk = self.Socket.recv(MAX_FRAME)
            except socket.timeout:
                return None
            if not Chunk:
                raise EOFError("control peer disconnected")
            self.Buffer.extend(Chunk)
        if b"\n" not in self.Buffer:
            if len(self.Buffer) >= MAX_FRAME:
                raise ValueError("frame bound exceeded")
            return None
        Frame, _, Tail = self.Buffer.partition(b"\n")
        if len(Frame) + 1 > MAX_FRAME:
            raise ValueError("frame bound exceeded")
        self.Buffer = bytearray(Tail)
        Row = json.loads(Frame)
        if not isinstance(Row, dict):
            raise ValueError("envelope must be an object")
        if (Row.get("Version") != 1 or Row.get("RunId") != self.Config["RunId"] or
                not isinstance(Row.get("Token"), str) or
                not secrets.compare_digest(Row["Token"], self.Config["Token"])):
            raise ValueError("wrong version, run, or token")
        if type(Row.get("Sequence")) is not int or Row["Sequence"] != self.Rx + 1:
            raise ValueError("duplicate, stale, or skipped sequence")
        if type(Row.get("TimestampUnixMs")) is not int or not isinstance(Row.get("Type"), str):
            raise ValueError("invalid envelope fields")
        self.Rx = Row["Sequence"]
        self.Journal.Write("RECEIVE", **Row)
        return Row


