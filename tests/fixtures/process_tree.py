"""Harmless process tree fixture; writes a clock until terminated."""
from pathlib import Path
import subprocess
import sys
import time

if sys.argv[1] == "parent":
    if "immediate" not in sys.argv[3:]:
        sys.stdin.readline()
    Child = subprocess.Popen([sys.executable, __file__, "child", sys.argv[2]])
    print(Child.pid, flush=True)
    if len(sys.argv) > 3:
        raise SystemExit(0)
    time.sleep(30)
else:
    File = Path(sys.argv[2])
    while True:
        File.write_text(str(time.monotonic()))
        time.sleep(0.05)
