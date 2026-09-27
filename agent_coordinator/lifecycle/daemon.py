"""Locally launched bounded daemon. No remote install/configuration operation."""
import argparse
import json
from pathlib import Path

from ..workflow import Exact
from .codex import CodexExec
from .endpoint import Endpoint
from .service import Service
from .policy import Policy


def Main():
    Parser = argparse.ArgumentParser()
    Parser.add_argument("--config", required=True)
    Args = Parser.parse_args()
    Config = json.loads(Path(Args.config).read_text())
    Exact(Config, ("EndpointId", "Repository", "Executable", "Profile", "Version", "Token",
                   "Port", "AgentSeconds", "StartupSeconds", "DaemonSeconds", "Tickets", "Policy"))
    Adapter = CodexExec(Config["Executable"], Config["Repository"], Config["Profile"], Config["Version"], Config["AgentSeconds"])
    Admission = Policy(Config["Policy"], Config["Token"]) if Config["Policy"] is not None else None
    Item = Endpoint(Config["EndpointId"], Path(Config["Repository"]) / ".lifecycle", Adapter,
                    Config["StartupSeconds"], Policy=Admission)
    for Ticket in Config["Tickets"]:
        Item.InstallTicket(Ticket)
    # Shipping daemon is loopback-only. A separately approved SSH forward can reach it.
    Service(Item, Config["Token"], Config["DaemonSeconds"]).Serve("127.0.0.1", Config["Port"])


if __name__ == "__main__":
    Main()
