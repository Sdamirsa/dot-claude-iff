#!/usr/bin/env bash
# post-write-validate.sh - PostToolUse gate on Write|Edit. The generic GOVERN gate.
#
# LAW 2, gate half: FAILS CLOSED. A structured file under .claude/ that no longer parses is
# blocked on the spot (exit 2, message to the model) so the agent fixes its own bad write while
# it still has the context - rather than the corruption surfacing three sessions later when the
# console silently renders nothing.
#
# Deliberately narrow: it parse-checks JSON/JSONL and checks a handshake envelope against the
# ONE envelope contract (_lib.validate_envelope, .claude/protocols/handshake.md; `checkctl
# handoff` and the statectl done guard call the same function). Deep semantic validation
# belongs to project-registered CHECK steps, not to a per-write hook that runs under a timeout
# on every edit.
#
# Second, separate job: the advisory channel. Only AFTER the validation has passed, it asks
# _lib.lead_advisories() for any note due for the lead (the delegation nudge in
# fableous-orchestrated; the periodic progress report in guided-solo and fableous-orchestrated)
# and prints it as additionalContext on exit 0. That half fails OPEN: it can add a note, never
# block a write and never unblock one.

set -u
VALIDATE_INPUT="$(cat 2>/dev/null || true)"
export VALIDATE_INPUT
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

raw = os.environ.get("VALIDATE_INPUT", "")
try:
    payload = json.loads(raw) if raw.strip() else {}
except Exception:
    payload = {}  # cannot tell what was written: nothing to validate
if not isinstance(payload, dict):
    payload = {}

root = Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve()
TOOLS = str(root / ".claude" / "tools")


def block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def validate() -> None:
    """Returns when the write is fine (or not ours to judge); block()s otherwise."""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    target = tool_input.get("file_path") or tool_input.get("path")
    if not target:
        return

    cwd = str(payload.get("cwd") or root)
    path = Path(str(target)).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path = path.resolve()
    except OSError:
        return

    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return
    if not rel.startswith(".claude/"):
        return
    if path.suffix not in (".json", ".jsonl"):
        return
    if not path.exists():
        return

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        block(f"VALIDATE_FAIL {rel}: cannot read back the file just written ({exc}).")

    if path.suffix == ".json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            block(
                f"VALIDATE_FAIL {rel}: not valid JSON - {exc.msg} at line {exc.lineno} column {exc.colno}. "
                f"Fix the file now; a config or store that does not parse is invisible to every tool "
                f"that reads it."
            )
        if "/state/handshakes/" in rel.replace("\\", "/") and not rel.endswith(".stub.json"):
            try:
                if TOOLS not in sys.path:
                    sys.path.insert(0, TOOLS)
                import _lib
                errors = _lib.validate_envelope(obj)
            except Exception as exc:  # the validator itself is broken: fail closed
                block(
                    f"VALIDATE_FAIL {rel}: the envelope validator could not run "
                    f"({type(exc).__name__}: {exc}). Failing closed; fix .claude/tools/_lib.py "
                    f"and write the envelope again."
                )
            if errors:
                block(
                    f"VALIDATE_FAIL {rel}: handshake envelope breaks the contract in "
                    f".claude/protocols/handshake.md - " + "; ".join(errors) + ". Required: "
                    f"agent_id, task_id, status (done|partial|blocked); a builder adds agent, "
                    f"model, files_changed[], tests[] {{command, exit_code, summary}}, needs_main[]."
                )
    else:
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                block(
                    f"VALIDATE_FAIL {rel}: line {number} is not valid JSON - {exc.msg}. Append-only "
                    f"stores are written one complete JSON object per line; use "
                    f"`python3 .claude/tools/statectl.py` rather than editing them by hand."
                )


validate()

# The decision is made (a block exited above). Advisory notes only from here on, fail open.
try:
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import _lib

    note = _lib.advisory_output("PostToolUse", _lib.lead_advisories(payload))
    if note:
        print(note)
except Exception:
    pass
sys.exit(0)
PY

status=$?
exit $status
