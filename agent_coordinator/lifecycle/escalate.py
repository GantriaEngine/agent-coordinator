"""Local inert NEEDS_USER report; cannot answer or grant an approval."""
import json
from pathlib import Path
import sys

from .endpoint import Atomic
from .presence import REASONS


def Main():
    if len(sys.argv) != 2 or sys.argv[1] not in REASONS - {"NONE", "STOPPED"}:
        raise ValueError("one typed human-required reason is required")
    Directory = Path(".lifecycle").resolve()
    Ticket = json.loads((Directory / "current.json").read_text())
    File = Directory / "status.json"
    Existing = json.loads(File.read_text()) if File.exists() else {"Sequence": 0}
    Atomic(File, {"Generation": Ticket["Notice"]["Generation"], "RunId": Ticket["Notice"]["RunId"],
                 "Sequence": Existing["Sequence"] + 1, "Status": "NEEDS_USER", "Reason": sys.argv[1], "Detail": ""})
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
