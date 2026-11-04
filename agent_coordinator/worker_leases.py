"""Host-local, advisory worker admission. Never executes or terminates workloads."""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3
import time
import uuid

DefaultDatabase = Path(r"C:\Sandbox\Codex\Coordinator\WorkerLeases\State.sqlite3")


def Timestamp(Value):
    return datetime.datetime.fromtimestamp(Value, datetime.timezone.utc).isoformat()


class Ledger:
    def __init__(self, Database=DefaultDatabase):
        self.Database = Path(Database)

    def Apply(self, Operation, *, ChatId=None, Owner=None, Mode=None, Token=None,
              Ttl=900, AcknowledgeIdle=False, Generation=None):
        if Operation not in ("status", "acquire", "renew", "release", "reconcile"):
            raise ValueError("Unknown operation")
        if type(Ttl) is not int or not 30 <= Ttl <= 86400:
            raise ValueError("TTL must be 30..86400 seconds")
        if Operation == "acquire":
            if str(uuid.UUID(ChatId)) != ChatId:
                raise ValueError("ChatId must be a canonical UUID")
            if not isinstance(Owner, str) or not 1 <= len(Owner) <= 128 or any(ord(C) < 32 for C in Owner):
                raise ValueError("Owner must be 1..128 printable characters")
            if Mode not in ("build", "test", "benchmark", "migration"):
                raise ValueError("Unknown workload mode")
        if Operation in ("renew", "release"):
            if not Token or str(uuid.UUID(Token)) != Token:
                raise ValueError("Owner token required")
        self.Database.parent.mkdir(parents=True, exist_ok=True)
        Connection = sqlite3.connect(self.Database, timeout=2, isolation_level=None)
        Connection.row_factory = sqlite3.Row
        try:
            Connection.execute("PRAGMA synchronous=FULL")
            Connection.execute("BEGIN IMMEDIATE")
            Version = Connection.execute("PRAGMA user_version").fetchone()[0]
            if Version not in (0, 1):
                raise ValueError("Unknown ledger version")
            Connection.execute("""CREATE TABLE IF NOT EXISTS Worker (
                Id INTEGER PRIMARY KEY CHECK(Id=1), Generation INTEGER NOT NULL,
                ChatId TEXT, Owner TEXT, Mode TEXT, Token TEXT,
                Acquired REAL, Heartbeat REAL, Expires REAL)""")
            Connection.execute("INSERT OR IGNORE INTO Worker(Id, Generation) VALUES(1, 0)")
            Connection.execute("PRAGMA user_version=1")
            Row = dict(Connection.execute("SELECT * FROM Worker WHERE Id=1").fetchone())
            Now = time.time()  # Read after the write lock, not before waiting for it.
            Outcome = "Status"
            GrantedToken = None
            if Operation == "acquire":
                if Row["Token"]:
                    Outcome = "Busy"  # Expiry is diagnostic, never permission to overlap.
                else:
                    GrantedToken = str(uuid.uuid4())
                    Connection.execute("""UPDATE Worker SET Generation=Generation+1,
                        ChatId=?, Owner=?, Mode=?, Token=?, Acquired=?, Heartbeat=?, Expires=? WHERE Id=1""",
                        (ChatId, Owner, Mode, GrantedToken, Now, Now, Now + Ttl))
                    Outcome = "Acquired"
            elif Operation in ("renew", "release"):
                if Row["Token"] != Token:
                    Outcome = "Denied"
                elif Operation == "renew":
                    Connection.execute("UPDATE Worker SET Heartbeat=?, Expires=? WHERE Id=1", (Now, Now + Ttl))
                    Outcome = "Renewed"
                elif not AcknowledgeIdle:
                    raise ValueError("Release requires --acknowledge-idle after owned jobs have stopped")
                else:
                    Connection.execute("""UPDATE Worker SET ChatId=NULL, Owner=NULL, Mode=NULL,
                        Token=NULL, Acquired=NULL, Heartbeat=NULL, Expires=NULL WHERE Id=1""")
                    Outcome = "Released"
            elif Operation == "reconcile":
                if not AcknowledgeIdle:
                    raise ValueError("Reconcile requires host-local verification that recorded jobs are idle")
                if (not Row["Token"] or Row["Expires"] > Now or Row["Generation"] != Generation
                        or Row["ChatId"] != ChatId):
                    Outcome = "Denied"
                else:
                    Connection.execute("""UPDATE Worker SET ChatId=NULL, Owner=NULL, Mode=NULL,
                        Token=NULL, Acquired=NULL, Heartbeat=NULL, Expires=NULL WHERE Id=1""")
                    Outcome = "Reconciled"
            Row = dict(Connection.execute("SELECT * FROM Worker WHERE Id=1").fetchone())
            Connection.execute("COMMIT")
            State = "Free" if Row["Token"] is None else ("Overdue" if Row["Expires"] <= Now else "Held")
            Result = {"Version": 1, "Outcome": Outcome, "Resource": "worker-heavy",
                      "State": State, "Generation": Row["Generation"], "ObservedUtc": Timestamp(Now),
                      "ChatId": Row["ChatId"], "Owner": Row["Owner"], "Mode": Row["Mode"]}
            for Field in ("Acquired", "Heartbeat", "Expires"):
                Result[Field + "Utc"] = Timestamp(Row[Field]) if Row[Field] is not None else None
            if GrantedToken:
                Result["Token"] = GrantedToken  # Returned once, never exposed by status/busy.
            return Result
        finally:
            Connection.close()  # Uncommitted changes roll back on any exception/crash.


def Main():
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("Operation", choices=("status", "acquire", "renew", "release", "reconcile"))
    Parser.add_argument("--database", type=Path, default=DefaultDatabase)
    Parser.add_argument("--chat-id")
    Parser.add_argument("--owner")
    Parser.add_argument("--mode", choices=("build", "test", "benchmark", "migration"))
    Parser.add_argument("--token")
    Parser.add_argument("--ttl", type=int, default=900)
    Parser.add_argument("--acknowledge-idle", action="store_true")
    Parser.add_argument("--generation", type=int)
    Args = Parser.parse_args()
    try:
        Result = Ledger(Args.database).Apply(Args.Operation, ChatId=Args.chat_id, Owner=Args.owner,
            Mode=Args.mode, Token=Args.token, Ttl=Args.ttl, AcknowledgeIdle=Args.acknowledge_idle,
            Generation=Args.generation)
    except (ValueError, TypeError, AttributeError, sqlite3.Error, OSError) as Error:
        print(json.dumps({"Outcome": "Error", "Available": False, "Detail": str(Error)}))
        return 2
    print(json.dumps(Result))
    return 3 if Result["Outcome"] == "Busy" else (4 if Result["Outcome"] == "Denied" else 0)


if __name__ == "__main__":
    raise SystemExit(Main())
