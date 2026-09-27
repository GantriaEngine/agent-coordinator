"""Local document links, checked-in schemas and text whitespace verification."""
import json
from pathlib import Path
import re
import subprocess

Root = Path(__file__).resolve().parents[1]
Files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=Root, text=True).splitlines()
for Name in Files:
    File = Root / Name
    if File.suffix == ".json":
        json.loads(File.read_text(encoding="utf-8"))
    if File.suffix in (".md", ".py", ".json", ".yml", ".cs", ".ps1"):
        Text = File.read_text(encoding="utf-8")
        for Index, Line in enumerate(Text.splitlines(), 1):
            if Line.rstrip() != Line:
                raise ValueError(f"{Name}:{Index}: trailing whitespace")
        if File.suffix == ".md":
            for Target in re.findall(r"\]\(([^)]+)\)", Text):
                if ":" not in Target and not (File.parent / Target.split("#")[0]).exists():
                    raise ValueError(f"{Name}: missing link {Target}")
print("[Coordinator:Check] JSON, local document links and whitespace passed")
