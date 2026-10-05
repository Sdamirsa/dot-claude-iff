#!/usr/bin/env bash
# session-start.sh - the orientation hook. Its stdout is injected into the fresh session's
# context, which makes it the one place where "where were we" arrives without anyone asking.
#
# Three jobs, in order of importance:
#   1. Print the resume block (pointer, open loops, unfinished intents, SEV0/SEV1 counts),
#      then one MODE/PHASE line; in the guided-solo and fableous-orchestrated modes also the
#      current phase's contract (at most five lines, from config/phases.json).
#   2. Nudge the agent to ASK THE USER for the ritual when it has not run in a while (only the
#      user can open it). The nudge lives HERE rather than on SessionEnd because SessionStart's
#      stdout->context path is the one we can prove works. Sessions are counted from the
#      capture lane's SessionStart events (spool + sealed segments), plus this one; with that
#      capture off the count is unknown and the nudge says so and goes by age alone.
#   3. Optionally start the console server, guarded by a pidfile so N sessions start one server.
#
# Read-only with respect to project state: it never writes to the journal.

set -u
# The payload is small here (ids and paths, no prompt), so an env var carries it, as in
# obs-capture.sh. Only its session id is read: the nudge counts this session too.
SS_INPUT="$(cat 2>/dev/null || true)"
export SS_INPUT
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

# 1b. the dials: mode and phase, through _lib's one reader. Display only, so it fails open: a
# broken phases.json (or an older _lib without the reader) loses these lines, never the session.
try:
    lines.append("\n".join(_lib.lifecycle_banner()))
except Exception:
    pass

# 2. the ritual nudge. Only the user can open the ritual (checkctl refuses without the ticket
# their own /project-memory mints), so the nudge tells the agent to ASK, never to run it.
ASK = ("Ask the user to run /project-memory at the next natural boundary (only the user can "
       "open it): every derived surface in this system is rebuilt there, and only there.")


def sessions_since(last: str, exclude, current):
    """Distinct sessions started after `last`: SessionStart events from the capture lane
    (obs-capture.sh spools them; seal moves them into dated segments), plus this session.
    The ritual's own session is excluded. None when SessionStart capture is off: the count is
    then unknown, not zero. (No hook writes a `session_start` journal event, so the journal
    cannot answer this.)"""
    cfg = _lib.load_config("observe")
    captured = cfg.get("capture_all_events") or "SessionStart" in (cfg.get("capture_events") or ())
    if not cfg.get("enabled", True) or not captured:
        return None
    stamp = _lib.parse_ts(last)
    if stamp is None:
        return None
    floor = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    paths = _lib.record_paths()
    files = []
    if paths["spool"].is_dir():  # ingest.jsonl holds token metadata only, never SessionStart
        files += [f for f in sorted(paths["spool"].glob("*.jsonl")) if f.name != "ingest.jsonl"]
    if paths["segments"].is_dir():
        files += [f for f in sorted(paths["segments"].glob("*.jsonl")) if f.stem >= floor[:10]]
    seen = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"SessionStart"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict) or ev.get("hook_event_name") != "SessionStart":
                continue
            if str(ev.get("_obs_ts") or "") > floor and ev.get("session_id"):
                seen.add(str(ev["session_id"]))
    if current:
        seen.add(current)
    seen.discard(str(exclude or ""))
    seen.discard("")
    return len(seen)


try:
    run = _lib.read_json(_lib.state_dir() / "memory-run.json", {}) or {}
    last = run.get("last_completed")
    if last:
        age_days = (_lib.age_seconds(last) or 0) / 86400.0
        try:
            current = str((json.loads(os.environ.get("SS_INPUT") or "{}") or {}).get("session_id") or "")
        except Exception:
            current = ""
        since = sessions_since(str(last), run.get("ticket_session"), current)
        if since is None:
            if age_days >= 3:
                lines.append(
                    f"RITUAL: last /project-memory was {age_days:.1f} days ago (sessions not "
                    f"counted: SessionStart capture is off in observe.json). {ASK}")
        elif age_days >= 3 or since >= 3:
            lines.append(f"RITUAL: last /project-memory was {age_days:.1f} days and {since} "
                         f"session(s) ago, this one included. {ASK}")
    elif journal.exists():
        lines.append(f"RITUAL: /project-memory has not run yet in this project. {ASK}")
except Exception:
    pass

# 2b. machine identity: loud when the repo wakes on a different (or unnamed) machine.
# Read-only, like everything else in this hook: `statectl device` is the writer.
try:
    line = _lib.machine_check()
    if line:
        lines.append(line)
except Exception:
    pass

# 3. console autostart (pidfile-guarded, never blocking)
try:
    import socket

    cfg = _lib.load_config("console")
    console_py = root / ".claude" / "console" / "console.py"
    if cfg.get("autostart", True) and console_py.exists():
        host = cfg.get("host", "127.0.0.1")
        port = _lib.console_port(cfg)
        record = _lib.record_paths()["root"]
        pidfile = record / "console.pid"
        alive = False
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            alive = True
        except Exception:
            alive = False
        busy = False
        if not alive:
            # Probe BEFORE spawning: a dead pidfile plus a listening port means some OTHER
            # process holds it - usually another project's console still on the shipped
            # default. Spawning would die into console.log and the printed URL would point
            # at the other project's console; a named collision beats a silent no-op.
            try:
                probe = socket.create_connection((host, port), timeout=0.25)
                probe.close()
                busy = True
            except OSError:
                busy = False
        if not alive and not busy:
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
        if busy:
            lines.append(
                f"CONSOLE: port {port} is already in use by another process (likely another "
                f"project's console on the same default). Set a unique 'port' in "
                f".claude/config/console.json - decided once per project - and start a new "
                f"session. Details, if any: {_lib.tilde(record / 'console.log')}"
            )
        elif alive:
            lines.append(f"CONSOLE: http://{_lib.console_hostname()}:{port}/console.html "
                         f"(open it beside this terminal; http://{host}:{port}/console.html works too)")
except Exception:
    pass

if lines:
    print("\n\n".join(lines))
PY

exit 0
