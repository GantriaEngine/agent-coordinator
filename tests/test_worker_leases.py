import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from agent_coordinator.worker_leases import Ledger


def Claim(Database):
    return Ledger(Database).Apply("acquire", ChatId=str(uuid.uuid4()), Owner="test", Mode="build")


class WorkerLeaseTests(unittest.TestCase):
    def test_independent_process_race_has_one_winner(self):
        with tempfile.TemporaryDirectory() as Root:
            Database = str(Path(Root) / "State.sqlite3")
            with concurrent.futures.ProcessPoolExecutor(max_workers=4) as Pool:
                Results = list(Pool.map(Claim, [Database] * 12))
            self.assertEqual(sum(R["Outcome"] == "Acquired" for R in Results), 1)
            self.assertEqual(sum(R["Outcome"] == "Busy" for R in Results), 11)
            self.assertTrue(all("Token" not in R for R in Results if R["Outcome"] == "Busy"))

    def test_lifecycle_stale_ownership_and_expiry(self):
        with tempfile.TemporaryDirectory() as Root:
            Database = Path(Root) / "State.sqlite3"
            Store = Ledger(Database)
            First = Claim(Database)
            self.assertEqual(str(uuid.UUID(First["ChatId"])), First["ChatId"])
            self.assertNotIn("Token", Store.Apply("status"))
            with closing(sqlite3.connect(Database, isolation_level=None)) as Connection:
                Connection.execute("UPDATE Worker SET Expires=0")
            self.assertEqual(Store.Apply("status")["State"], "Overdue")
            self.assertEqual(Claim(Database)["Outcome"], "Busy")
            self.assertEqual(Store.Apply("release", Token=str(uuid.uuid4()), AcknowledgeIdle=True)["Outcome"], "Denied")
            with self.assertRaises(ValueError):
                Store.Apply("release", Token=First["Token"])
            self.assertEqual(Store.Apply("renew", Token=First["Token"])["State"], "Held")
            Store.Apply("release", Token=First["Token"], AcknowledgeIdle=True)
            Second = Claim(Database)
            self.assertGreater(Second["Generation"], First["Generation"])
            self.assertEqual(Store.Apply("renew", Token=First["Token"])["Outcome"], "Denied")

    def test_reconciliation_is_expired_generation_and_chat_checked(self):
        with tempfile.TemporaryDirectory() as Root:
            Database = Path(Root) / "State.sqlite3"
            Store = Ledger(Database)
            First = Claim(Database)
            Args = dict(ChatId=First["ChatId"], Generation=First["Generation"], AcknowledgeIdle=True)
            self.assertEqual(Store.Apply("reconcile", **Args)["Outcome"], "Denied")
            with sqlite3.connect(Database, isolation_level=None) as Connection:
                Connection.execute("UPDATE Worker SET Expires=0")
            Connection.close()
            self.assertEqual(Store.Apply("reconcile", **(Args | {"Generation": 999}))["Outcome"], "Denied")
            self.assertEqual(Store.Apply("reconcile", **(Args | {"ChatId": str(uuid.uuid4())}))["Outcome"], "Denied")
            self.assertEqual(Store.Apply("reconcile", **Args)["State"], "Free")
            Second = Claim(Database)
            self.assertEqual(Store.Apply("reconcile", **Args)["Outcome"], "Denied")

    def test_failure_rolls_back_and_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as Root:
            Database = Path(Root) / "State.sqlite3"
            with patch("agent_coordinator.worker_leases.time.time", side_effect=RuntimeError("crash")):
                with self.assertRaises(RuntimeError):
                    Claim(Database)
            self.assertEqual(Claim(Database)["Outcome"], "Acquired")
            with closing(sqlite3.connect(Database, isolation_level=None)) as Connection:
                Connection.execute("PRAGMA user_version=99")
            with self.assertRaises(ValueError):
                Ledger(Database).Apply("status")


if __name__ == "__main__":
    unittest.main()
