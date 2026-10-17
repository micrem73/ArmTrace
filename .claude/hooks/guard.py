#!/usr/bin/env python3
"""Claude Code hooks for the rules that must hold without exception.

    guard.py pre    PreToolUse on Edit/Write: the sources in In/ are never edited,
                    and Out/ is never hand-edited (only Out/.work/, where checkers
                    write their verdicts).
    guard.py post   PostToolUse on Edit/Write: lint the edited .py file (ruff.toml).

Exit 2 blocks the tool call (pre) or hands the message back to Claude (post).
Writes through Bash are not seen here; the scripts that own In/ and Out/ use Bash.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def target(event):
    tool_input = event.get("tool_input", {})
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return Path(path).resolve() if path else None


def pre(path):
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return 0
    parts = rel.parts
    if parts[:1] == ("In",) and len(parts) > 2:
        reason = ("In/<year>/ holds the official reports, never edited. "
                  "Re-download them instead: skill download-sources.")
    elif parts[:1] == ("Out",) and parts[1:2] != (".work",):
        reason = ("Out/ is generated and never hand-edited. Change the code "
                  "and re-run (run_year.py, tools/build_index.py).")
    else:
        return 0
    print(f"blocked: {rel}: {reason}", file=sys.stderr)
    return 2


def post(path):
    if path.suffix != ".py" or not path.exists():
        return 0
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--quiet", str(path)],
            cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return 0
    if "No module named ruff" in result.stderr:
        return 0
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    path = target(json.load(sys.stdin))
    if path is None:
        sys.exit(0)
    sys.exit({"pre": pre, "post": post}[sys.argv[1]](path))
