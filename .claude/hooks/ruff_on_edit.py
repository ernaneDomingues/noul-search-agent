"""PostToolUse hook: format and auto-fix a Python file right after Claude edits it (no-op if ruff is missing)."""

import json
import os
import shutil
import subprocess
import sys

data = json.load(sys.stdin)
path = (data.get("tool_input") or {}).get("file_path") or ""
if not path.endswith(".py") or not os.path.exists(path):
    sys.exit(0)
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
local = os.path.join(root, ".venv", "Scripts" if os.name == "nt" else "bin", "ruff")
ruff = local if os.path.exists(local) else shutil.which("ruff")
if not ruff:
    sys.exit(0)
subprocess.run([ruff, "format", "--quiet", path], cwd=root, check=False)
result = subprocess.run([ruff, "check", "--fix", "--quiet", path], cwd=root, capture_output=True, text=True)
if result.returncode != 0:
    # Exit code 2 feeds the remaining lint errors back to Claude so it fixes them.
    print(result.stdout or result.stderr, file=sys.stderr)
    sys.exit(2)
