#!/usr/bin/env bash
# session-start.sh - the orientation hook. Its stdout is injected into the fresh session's
# context, which makes it the one place where "where were we" arrives without anyone asking.
#
# Three jobs, in order of importance:
#   1. Print the resume block (pointer, open loops, unfinished intents, SEV0/SEV1 counts).
#   2. Nudge if the ritual has not run in a while. The nudge lives HERE rather than on
#      SessionEnd because SessionStart's stdout->context path is the one we can prove works.
#   3. Optionally start the console server, guarded by a pidfile so N sessions start one server.
#
# Read-only with respect to project state: it never writes to the journal.

set -u
cat >/dev/null 2>&1 || true
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

python3 - <<'PY' 2>/dev/null || true
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve()
tools = root / ".claude" / "tools"
sys.path.insert(0, str(tools))

try:
    import _lib
except Exception:
    raise SystemExit(0)

lines = []

# 1. the resume block
statectl = tools / "statectl.py"
journal = _lib.journal_path()
if statectl.exists() and journal.exists():
    try:
        res = subprocess.run(
            [sys.executable, str(statectl), "resume"],
            capture_output=True, text=True, timeout=15, cwd=str(root), check=False,
        )
        if res.stdout.strip():
            lines.append(res.stdout.rstrip())
    except Exception:
        pass
elif not journal.exists():
    lines.append(
        "SESSION CONTINUITY - no journal yet. Start one with:\n"
        "  python3 .claude/tools/statectl.py start --session <name>\n"
        "and set a pointer before any long or risky operation."
    )

# 2. the ritual nudge
try:
    run = _lib.read_json(_lib.state_dir() / "memory-run.json", {}) or {}
    last = run.get("last_completed")
    if last:
        age_days = (_lib.age_seconds(last) or 0) / 86400.0
        since = sum(
            1 for e in _lib.journal_read()
            if e.get("action") == "session_start" and str(e.get("ts", "")) > str(last)
        )
        if age_days >= 3 or since >= 3:
            lines.append(
                f"RITUAL: last /project-memory was {age_days:.1f} days and {since} session(s) ago. "
                f"Run it at the next natural boundary - every derived surface in this system is "
                f"rebuilt there, and only there."
            )
    elif journal.exists():
        lines.append("RITUAL: /project-memory has not run yet in this project.")
except Exception:
    pass

# 3. console autostart (pidfile-guarded, never blocking)
try:
    cfg = _lib.load_config("console")
    console_py = root / ".claude" / "console" / "console.py"
    if cfg.get("autostart", True) and console_py.exists():
        record = _lib.record_paths()["root"]
        pidfile = record / "console.pid"
        alive = False
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            alive = True
        except Exception:
            alive = False
        if not alive:
            _lib.ensure_dir(record)
            # `with` so this hook does not leak a descriptor on every session start. The child
            # keeps its own duplicated handle after we close ours.
            with open(record / "console.log", "ab", buffering=0) as log:
                proc = subprocess.Popen(
                    [sys.executable, str(console_py), "--pidfile", str(pidfile)],
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                    start_new_session=True, cwd=str(root),
                )
            pidfile.write_text(str(proc.pid), encoding="utf-8")
            alive = True
        if alive:
            host = cfg.get("host", "127.0.0.1")
            port = cfg.get("port", 7717)
            lines.append(f"CONSOLE: http://{host}:{port}/console.html (open it beside this terminal)")
except Exception:
    pass

if lines:
    print("\n\n".join(lines))
PY

exit 0
