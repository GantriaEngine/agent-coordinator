import json
import socket
import threading
import time
import unittest

from agent_coordinator.control import Link


class Log:
    def Write(self, *Args, **Fields):
        pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.Listener = socket.socket()
        self.Listener.bind(("127.0.0.1", 0))
        self.Listener.listen()
        self.Client = socket.create_connection(self.Listener.getsockname())
        self.Server, _ = self.Listener.accept()
        self.Config = {"RunId": "870c114b-44e5-4e43-a677-59b010f388d9", "Token": "a" * 64}
        self.Link = Link(self.Server, self.Config, Log())
        self.Row = {"Version": 1, **self.Config, "Sequence": 1, "TimestampUnixMs": 0, "Type": "STAGED", "Role": "SERVER"}

    def tearDown(self):
        for Sock in (self.Client, self.Server, self.Listener):
            Sock.close()

    def Send(self, Row):
        self.Client.sendall(json.dumps(Row).encode() + b"\n")
        return self.Link.Read()

    def test_valid_envelope(self):
        self.assertEqual("STAGED", self.Send(self.Row)["Type"])

    def test_wrong_token_run_version_sequence(self):
        for Field, Value in (("Token", "b" * 64), ("RunId", "stale"), ("Version", 2), ("Version", True), ("Sequence", 0), ("Sequence", True)):
            with self.subTest(Field=Field), self.assertRaises(ValueError):
                self.Send({**self.Row, Field: Value})

    def test_replay_rejected(self):
        self.Send(self.Row)
        with self.assertRaises(ValueError):
            self.Send(self.Row)

    def test_natural_language_and_arbitrary_execution_rejected(self):
        for Type in ("RunShell", "ExecuteProcess", "RunElevatedCode", "powershell", "DIAGNOSTIC", "please start capture"):
            self.Link.Rx = 0
            with self.subTest(Type=Type), self.assertRaises(ValueError):
                self.Send({**self.Row, "Type": Type})

    def test_unknown_fields_and_oversized_frame(self):
        with self.assertRaises(ValueError):
            self.Send({**self.Row, "Command": "evil"})
        self.Client.sendall(b"x" * 16384)
        with self.assertRaises(ValueError):
            self.Link.Read()
