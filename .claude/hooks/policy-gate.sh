#!/usr/bin/env bash
# policy-gate.sh - PreToolUse gate on Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell.
#
# A thin wrapper. The judgment lives in policy_gate.py; this script's whole job is to hand the
# payload over WITHOUT truncating it and to fail closed when the judge cannot run.
#
# The payload goes to a temp FILE, never an environment variable: a single env string is capped
# at MAX_ARG_STRLEN (128 KB), and a Write payload carries the file's entire content, so the
# env route meant that writing a large enough file to a protected path made execve fail, python
# never ran, no decision was emitted, and the harness read that as ALLOW. A gate that a big
# file switches off is not a gate.
#
# LAW 2, gate half: if the judge exits non-zero, or python3 is missing entirely, we do a
# last-resort grep for the protected prefixes and DENY on a match. That keeps a broken judge
# from opening the protected tree without bricking every unrelated write in the session.

set -u
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

payload_file="$(mktemp "${TMPDIR:-/tmp}/claude-iff-gate.XXXXXX" 2>/dev/null)" || payload_file=""
if [ -z "$payload_file" ]; then
  # Cannot even stage the payload: refuse the call rather than guess at it.
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"the policy gate could not stage the payload (mktemp failed) and will not judge a call it cannot read."}}'
  exit 0
fi
trap 'rm -f "$payload_file"' EXIT

cat > "$payload_file" 2>/dev/null || true

# Record the project root inside the payload so the judge never has to guess it.
python3 - "$payload_file" <<'PY' 2>/dev/null
import json, os, sys
p = sys.argv[1]
try:
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    obj = json.loads(raw) if raw.strip() else {}
    if not isinstance(obj, dict):
        obj = {}
except Exception:
    obj = {}
obj["_project_root"] = os.environ.get("CLAUDE_PROJECT_DIR", "")
with open(p, "w", encoding="utf-8") as fh:
    json.dump(obj, fh)
PY

python3 "$HOOK_DIR/policy_gate.py" "$payload_file"
status=$?

if [ "$status" -ne 0 ]; then
  # The judge did not judge. Fail closed for the protected tree only.
  # [/\\]+ accepts the posix form and the JSON-escaped Windows form (.claude\\config) alike.
  if grep -qE '\.claude[/\\]+(hooks|tools|config|agents|protocols|skills|console)[/\\]|\.claude-iff|settings\.json|_claude_iff' "$payload_file" 2>/dev/null; then
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"the policy gate could not run (python3 missing or the judge crashed) and this call touches the protected tree. Failing closed. Fix the gate before editing it."}}'
  fi
fi

exit 0
