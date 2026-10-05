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
# The file is gitignored (it changes every turn) and the kits ship no state/ at all, so this
# hook is what makes it exist: on a fresh install it creates state/ the first time a turn ends.
# UTF-8 for every python child, whatever the machine's locale: on a cp1252 Windows box the
# hook's own output (it contains non-ASCII characters) was otherwise mis-encoded.
export PYTHONUTF8=1

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

    if not _lib.claude_dir().is_dir():
        raise SystemExit(0)
    # atomic_write_json creates state/ when it is missing (a fresh install ships none).
    _lib.atomic_write_json(_lib.state_dir() / "heartbeat.json",
                           {"ts": _lib.utc_now(), "note": "turn ended"})
except Exception:
    pass
PY

exit 0
