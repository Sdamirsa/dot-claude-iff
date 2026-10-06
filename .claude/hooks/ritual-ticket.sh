#!/usr/bin/env bash
# ritual-ticket.sh - UserPromptSubmit + UserPromptExpansion hook. Mints the ritual ticket.
#
# The ritual (/project-memory) and the installer (/adopt) are the USER's to open. checkctl
# refuses to open, continue or complete a ritual run without a fresh ticket in
# .claude/state/ritual-ticket.json, and the policy gate denies that file to every agent
# identity on every write lane. This hook is its one writer: when the user's own prompt starts
# with /project-memory or /adopt (UserPromptSubmit `user_input`, `prompt` in older releases),
# or Claude Code expands one of those two commands (UserPromptExpansion `command_name`), it
# writes {skill, ts, session_id, event}. A prompt that merely mentions the command
# mid-sentence writes nothing, and neither does a turn Claude Code starts on its own.
#
# A tripwire, not cryptography: an agent-opened ritual fails loudly, and every issue lands in
# the record. It does not make forging impossible.
#
# NEVER blocks or alters the prompt: every path exits 0 and prints nothing (stdout on these two
# events would be injected into the context). If it breaks, no ticket is written and checkctl
# refuses the ritual: fail closed overall, through checkctl, never by blocking the user here.
#
# The payload goes to a temp FILE, as in policy-gate.sh: a prompt can be far larger than an
# environment variable may hold.
# UTF-8 for every python child, whatever the machine's locale: on a cp1252 Windows box the
# hook's own output (it contains non-ASCII characters) was otherwise mis-encoded.
export PYTHONUTF8=1

set -u
export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

payload_file="$(mktemp "${TMPDIR:-/tmp}/claude-iff-ticket.XXXXXX" 2>/dev/null)" || payload_file=""
if [ -z "$payload_file" ]; then
  cat >/dev/null 2>&1 || true
  exit 0
fi
trap 'rm -f "$payload_file"' EXIT
cat > "$payload_file" 2>/dev/null || true

# Most prompts name neither command: skip the python start-up for them.
grep -qE 'project-memory|adopt' "$payload_file" 2>/dev/null || exit 0

python3 - "$payload_file" <<'PY' >/dev/null 2>&1 || true
import json
import os
import re
import sys
from pathlib import Path

SKILLS = ("project-memory", "adopt")
EVENTS = {"userpromptsubmit": "UserPromptSubmit", "userpromptexpansion": "UserPromptExpansion"}
# The command first (after leading whitespace), then the end, or whitespace and an argument.
PROMPT_RE = re.compile(r"\A\s*/(project-memory|adopt)(?=\s|\Z)")
# What the user typed: `user_input` in the current docs (both events), `prompt` in older
# releases. Never `expanded_prompt`: that is the command's template, not the user's words.
PROMPT_KEYS = ("user_input", "prompt", "userInput", "user_prompt", "userPrompt")
# UserPromptExpansion names the command it expanded (`command_name` in the docs); payload
# shapes move between releases, so the plausible spellings are accepted too.
COMMAND_KEYS = ("command_name", "commandName", "command", "slash_command", "slashCommand",
                "skill_name", "skillName", "skill")
NESTED_KEYS = ("expansion", "command", "slash_command", "skill")


def command_skill(value):
    """'project-memory', '/adopt', '/adopt --upgrade' -> the skill; anything else -> None."""
    if not isinstance(value, str):
        return None
    words = value.strip().split()
    name = words[0] if words else ""
    name = name[1:] if name.startswith("/") else name
    return name if name in SKILLS else None


def skill_of(payload):
    """(skill, event) for a payload that invokes the ritual, else (None, event)."""
    if not isinstance(payload, dict):
        return None, None
    raw_event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    event = EVENTS.get(str(raw_event).strip().lower())
    if raw_event and event is None:
        return None, None  # wired to another event by mistake: never mint there
    origin = payload.get("user_input_type", payload.get("userInputType"))
    if origin is not None and str(origin).strip().lower() != "user":
        return None, event  # a turn Claude Code started on its own (task notice, schedule)
    for key in PROMPT_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            m = PROMPT_RE.match(value)
            if m:
                return m.group(1), event
    if event == "UserPromptSubmit":
        return None, event  # a submitted prompt is judged by its text alone
    sources = [payload] + [payload[k] for k in NESTED_KEYS if isinstance(payload.get(k), dict)]
    for source in sources:
        for key in COMMAND_KEYS:
            skill = command_skill(source.get(key))
            if skill:
                return skill, event
    return None, event


try:
    sys.path.insert(0, str(Path(os.environ["CLAUDE_PROJECT_DIR"]) / ".claude" / "tools"))
    import _lib

    with open(sys.argv[1], "rb") as fh:
        raw = fh.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if raw.strip() else None
    except ValueError:
        payload = None
    skill, event = skill_of(payload)
    if skill and _lib.claude_dir().is_dir():
        session = payload.get("session_id")
        ticket = {"skill": skill, "ts": _lib.utc_now(),
                  "session_id": str(session) if session else None,
                  "event": event or "unknown"}
        _lib.atomic_write_json(_lib.state_dir() / "ritual-ticket.json", ticket, durable=True)
        # Telemetry (fails open): the record keeps when, and in which session, each ticket
        # was minted, so a ticket can be traced back to the prompt that minted it.
        _lib.obslog("ritual.ticket", skill=skill, trigger=ticket["event"],
                    session_id=ticket["session_id"] or "unknown")
except Exception:
    pass
PY

exit 0
