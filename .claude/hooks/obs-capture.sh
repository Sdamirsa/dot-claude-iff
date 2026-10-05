#!/usr/bin/env bash
# obs-capture.sh - the capture lane. Appends the raw hook payload to the out-of-repo spool.
#
# LAW 2, telemetry half: THIS HOOK FAILS OPEN, ALWAYS. Every path through it exits 0, every
# exception is swallowed. A broken observatory must never break the agent loop - an unwritable
# record root, a full disk, or a malformed payload costs us an event, never the session.
#
# LAW 3: what lands here is VERBATIM (prompts, file contents, tool output). That is why the
# spool lives in RECORD_ROOT, outside the repo and outside git, and why sealing redacts
# through an allowlist before anything reaches a committed surface.
#
# Wired to the lean event set by default (see .claude/config/observe.json); the payload is
# passed through an env var because the python heredoc below claims stdin.

set -u
OBS_INPUT="$(cat 2>/dev/null || true)"
export OBS_INPUT
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

python3 - <<'PY' 2>/dev/null || true
import json
import os
import sys
import time
from pathlib import Path

try:
    tools = Path(os.environ["CLAUDE_PROJECT_DIR"]) / ".claude" / "tools"
    sys.path.insert(0, str(tools))
    import _lib

    raw = os.environ.get("OBS_INPUT", "")
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {"_obs_raw": str(payload)[:2000]}
    except Exception:
        payload = {"_obs_raw": raw[:2000]}

    cfg = _lib.load_config("observe")
    if not cfg.get("enabled", True):
        raise SystemExit(0)

    event = payload.get("hook_event_name") or "unknown"
    if not cfg.get("capture_all_events", False):
        allowed = set(cfg.get("capture_events") or ())
        if event not in allowed:
            raise SystemExit(0)

    payload["_obs_ts"] = _lib.utc_now()
    payload.setdefault("_obs_source", "hook")
    # A uniquifier, stamped at capture. Sealing dedupes by identity, and a second-precision
    # timestamp is not an identity: parallel sub-agents finishing in the same second used to
    # collapse into a single sealed row, silently discarding the rest from the tier that is
    # supposed to keep everything forever.
    payload["_obs_uid"] = f"{time.time_ns()}-{os.getpid()}"

    session = str(payload.get("session_id") or "unknown")
    spool = _lib.record_paths()["spool"] / f"{_lib.slugify(session)}.jsonl"
    _lib.append_jsonl(spool, payload)
except SystemExit:
    pass
except Exception:
    pass
PY

exit 0
