"""Crash after a confined child starts; owned test process and marker only."""
import json
import os
from pathlib import Path
import sys
import time

from agent_coordinator.lifecycle.process import ProcessTree

Clock, Marker, Phase = map(Path, sys.argv[1:4])
Fixture = Path(__file__).with_name("process_tree.py")
Process, Tree = ProcessTree.Start([sys.executable, str(Fixture), "parent", str(Clock),
                                   "immediate"], Fixture.parent)
Child = int(Process.stdout.readline().decode().strip())
Until = time.monotonic() + 3
while not Clock.exists() and time.monotonic() < Until:
    time.sleep(0.02)
if not Clock.exists():
    raise RuntimeError("descendant never started")
Marker.write_text(json.dumps({"Supervisor": os.getpid(), "Parent": Process.pid,
                              "Child": Child, "Phase": str(Phase)}))
os._exit(23)
