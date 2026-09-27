import json
import os
from pathlib import Path
import secrets
import unittest

from agent_coordinator.lifecycle.secret import Dpapi, LoadKey, RevokeKey, StoreKey


class SecretTests(unittest.TestCase):
    def test_user_protected_key_rotation_and_revocation(self):
        if os.name != "nt":
            with self.assertRaises(OSError):
                Dpapi(b"test")
            return
        File = Path.home() / ".codex" / "agent-coordinator" / "secrets" / (secrets.token_hex(10) + ".dpapi")
        First, Second = secrets.token_hex(32), secrets.token_hex(32)
        try:
            StoreKey(File, First)
            self.assertEqual(First, LoadKey(File))
            self.assertNotIn(First, File.read_text())
            with self.assertRaises(FileExistsError):
                StoreKey(File, Second)
            StoreKey(File, Second, Rotate=True)
            self.assertEqual(Second, LoadKey(File))
            Item = json.loads(File.read_text())
            Item["Scope"] = "WindowsLocalMachine"
            File.write_text(json.dumps(Item))
            with self.assertRaises(ValueError):
                LoadKey(File)
        finally:
            RevokeKey(File)
        with self.assertRaises(FileNotFoundError):
            LoadKey(File)

    def test_key_path_cannot_use_shared_checkout(self):
        with self.assertRaises(ValueError):
            StoreKey(Path(__file__).parent / "shared.dpapi", secrets.token_hex(32))
