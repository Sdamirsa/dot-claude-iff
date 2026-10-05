#!/usr/bin/env bash
# handoff-guard.sh - SubagentStart / SubagentStop, fableous-orchestrated mode only.
#
# SubagentStart: records when the agent began (state/orchestration.json) and resets the lead's
# delegation-nudge counter, because a dispatch is exactly what the nudge asks for.
#
# SubagentStop: a `builder` that is stopping without a valid handoff envelope written since it
# started, for an open builder stub, is sent back ONCE to write it ({"decision": "block",
# "reason": ...}, the documented Stop/SubagentStop blocking output). When the harness says
# stop_hook_active (this stop was already sent back once), the builder stops: the hook can
# never loop. Every other agent type stops untouched.
#
# BEST EFFORT, on purpose. The payload fields read here (hook_event_name, agent_type, agent_id,
# agent_transcript_path, stop_hook_active) are what the harness sends today and may change; a
# missing field reads as "cannot tell" and the agent stops normally. The HARD guarantee is not
# this hook: it is `statectl task <id> --status done`, which in fableous-orchestrated refuses
# without a valid envelope whose tests passed.
#
# LAW 2, telemetry half: FAILS OPEN. A malformed payload, a missing python3 or any internal
# error means no output and exit 0. In freestyle and guided-solo it does nothing at all. The
# payload goes through a temp file, not an environment variable: a SubagentStop payload can
# carry the agent's whole last message, and an env string is capped (see policy-gate.sh).
# UTF-8 for every python child, whatever the machine's locale: on a cp1252 Windows box the
# hook's own output (it contains non-ASCII characters) was otherwise mis-encoded.
export PYTHONUTF8=1

set -u
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

payload_file="$(mktemp "${TMPDIR:-/tmp}/claude-iff-guard.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$payload_file"' EXIT
cat > "$payload_file" 2>/dev/null || true

python3 - "$payload_file" <<'PY' 2>/dev/null || true
import json
import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(os.environ["CLAUDE_PROJECT_DIR"]) / ".claude" / "tools"))
    import _lib

    with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    payload = json.loads(raw) if raw.strip() else {}
    if isinstance(payload, dict):
        event = payload.get("hook_event_name")
        if event == "SubagentStart":
            _lib.note_subagent_start(payload)
        elif event == "SubagentStop":
            reason = _lib.builder_stop_reason(payload)
            if reason:
                print(json.dumps({"decision": "block", "reason": reason}))
except Exception:
    pass
PY

exit 0
