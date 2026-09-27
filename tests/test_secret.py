import json
import os
from pathlib import Path
import secrets
import unittest
from unittest.mock import patch

from agent_coordinator.lifecycle.secret import Dpapi, LoadKey, Restrict, RevokeKey, StoreKey


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

    def test_installer_owned_file_copies_inherited_ace_before_grant(self):
        File = Path("config.json")
        with patch("agent_coordinator.lifecycle.secret.os.name", "nt"), \
                patch("agent_coordinator.lifecycle.secret.subprocess.check_output",
                      return_value='"HOSTPC\\host","S-1-5-21-1-1001"\n'), \
                patch("agent_coordinator.lifecycle.secret.subprocess.run") as Run:
            Restrict(File)
        self.assertIn("/inheritance:d", Run.call_args_list[0].args[0])
        self.assertIn("/grant:r", Run.call_args_list[1].args[0])
