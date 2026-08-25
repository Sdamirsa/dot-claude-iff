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

set -u
cat >/dev/null 2>&1 || true   # drain stdin; the payload is not needed
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

python3 - <<'PY' 2>/dev/null || true
import os
import sys
from pathlib import Path

try:
    root = Path(os.environ["CLAUDE_PROJECT_DIR"])
    sys.path.insert(0, str(root / ".claude" / "tools"))
    import _lib

    state = _lib.state_dir()
    if not state.exists():
        raise SystemExit(0)
    _lib.atomic_write_json(state / "heartbeat.json", {"ts": _lib.utc_now(), "note": "turn ended"})
except Exception:
    pass
PY

exit 0
