#!/usr/bin/env bash
# post-write-validate.sh - PostToolUse gate on Write|Edit. The generic GOVERN gate.
#
# LAW 2, gate half: FAILS CLOSED. A structured file under .claude/ that no longer parses is
# blocked on the spot (exit 2, message to the model) so the agent fixes its own bad write while
# it still has the context - rather than the corruption surfacing three sessions later when the
# console silently renders nothing.
#
# Deliberately narrow: it parse-checks JSON/JSONL and the handshake envelope's required keys.
# Deep semantic validation belongs to project-registered CHECK steps, not to a per-write hook
# that runs under a timeout on every edit.

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
    sys.exit(0)  # cannot tell what was written: nothing to validate
if not isinstance(payload, dict):
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
if not isinstance(tool_input, dict):
    sys.exit(0)
target = tool_input.get("file_path") or tool_input.get("path")
if not target:
    sys.exit(0)

root = Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve()
cwd = str(payload.get("cwd") or root)
path = Path(str(target)).expanduser()
if not path.is_absolute():
    path = Path(cwd) / path
try:
    path = path.resolve()
except OSError:
    sys.exit(0)

try:
    rel = path.relative_to(root).as_posix()
except ValueError:
    sys.exit(0)
if not rel.startswith(".claude/"):
    sys.exit(0)
if path.suffix not in (".json", ".jsonl"):
    sys.exit(0)
if not path.exists():
    sys.exit(0)


def block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


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
        required = ("agent_id", "task_id", "status")
        missing = [k for k in required if k not in (obj if isinstance(obj, dict) else {})]
        if missing:
            block(
                f"VALIDATE_FAIL {rel}: handshake envelope is missing {', '.join(missing)}. "
                f"The envelope contract is {{agent_id, task_id, status, artifacts[], notes}} - "
                f"status is one of done|partial|blocked."
            )
        status = (obj or {}).get("status")
        if status not in ("done", "partial", "blocked"):
            block(f"VALIDATE_FAIL {rel}: status must be done|partial|blocked, got {status!r}.")
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

sys.exit(0)
PY

status=$?
exit $status
