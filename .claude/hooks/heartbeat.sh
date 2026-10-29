#!/usr/bin/env bash
# heartbeat.sh - Stop hook. One O(1) overwrite of state/heartbeat.json per turn.
#
# This is a LIVENESS signal, not the resume guarantee. A usage-limit cutoff or a crash in the
# middle of a turn never fires Stop, so the thing that actually survives is the pointer written
# to the journal BEFORE the risky operation. Say it here because the file's name invites the
# opposite assumption.
#
# Overwrite, not append: the journal stays small and meaningful, and the console gets a
# freshness number for free.
#
# Mid-turn, the activity pulse (_lib.activity_pulse, from the policy gate and the sub-agent
# capture lane) rewrites the same file as {ts, note: "working", via}. This hook's note stays
# "turn ended" (_lib.TURN_ENDED_NOTE), so a reader can tell a working turn from a finished one.
#
# The file is gitignored (it changes every turn) and the kits ship no state/ at all, so this
# hook is what makes it exist: on a fresh install it creates state/ the first time a turn ends.
#
# Second, separate job, in a linked worktree only (a Cview worker, _lib.worktree_info): when
# TASK.md sits at the worktree root and neither DONE.md nor BLOCKED.md does, one advisory line
# rides `systemMessage` ("the task is open: write DONE.md or BLOCKED.md when you stop for
# good"). ADVISORY, never blocking: this hook never emits {"decision": "block"}; Cview's
# recorder, not this hook, decides what done means. Fails open like the rest of the file.
# UTF-8 for every python child, whatever the machine's locale: on a cp1252 Windows box the
# hook's own output (it contains non-ASCII characters) was otherwise mis-encoded.
export PYTHONUTF8=1

set -u
cat >/dev/null 2>&1 || true   # drain stdin; the payload is not needed
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

python3 - <<'PY' 2>/dev/null || true
import json
import os
import sys
from pathlib import Path

try:
    root = Path(os.environ["CLAUDE_PROJECT_DIR"])
    sys.path.insert(0, str(root / ".claude" / "tools"))
    import _lib

    if not _lib.claude_dir().is_dir():
        raise SystemExit(0)
    # atomic_write_json creates state/ when it is missing (a fresh install ships none).
    _lib.atomic_write_json(_lib.state_dir() / "heartbeat.json",
                           {"ts": _lib.utc_now(), "note": "turn ended"})
except Exception:
    pass

# The worker's open-task reminder. Isolated from the heartbeat above: a broken detector costs
# the line, never the beat, and the other way round.
try:
    if _lib.worktree_info() is not None:
        task = _lib.worker_task(root)
        if task["task"] and not task["report"]:
            print(json.dumps({"systemMessage": (
                "the task is open: write DONE.md (`# DONE`, `summary:`, `tests:`) or "
                "BLOCKED.md (your question) at the worktree root when you stop for good")}))
except Exception:
    pass
PY

exit 0
