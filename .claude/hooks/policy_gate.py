#!/usr/bin/env python3
"""policy_gate.py - the PreToolUse gate's logic. Invoked by policy-gate.sh with ONE argument:
the path to a file containing the hook payload.

The payload arrives as a FILE, not an environment variable. That is not a style choice: a single
env string is capped at MAX_ARG_STRLEN (128 KB on Linux), and a Write payload carries the file's
whole content, so passing it through the environment meant that writing a large enough file to a
protected path made execve fail with E2BIG, python never ran, and the gate emitted no decision,
which the harness reads as ALLOW. The gate was disabled by nothing more exotic than a big file.

Exit codes are the contract with the wrapper:
    0  decision emitted (or explicitly allowed)
    3  this script could not do its job; the wrapper must fail closed on protected paths

Two rings:
  1. The record (RECORD_ROOT and .claude-iff/) is write-denied to EVERY identity, main session
     included. It is written only by the capture hook's append and by obsctl.
  2. The protected tree is main-session only. It includes every directory whose contents this
     gate itself executes or trusts: hooks, tools, config, agents, protocols, skills, console,
     settings. A sub-agent that can write .claude/tools/_lib.py owns the gate on the next tool
     call, because that is the module this file imports.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

FALLBACK_PROTECTED = (
    ".claude/hooks/",
    ".claude/tools/",
    ".claude/config/",
    ".claude/agents/",
    ".claude/protocols/",
    ".claude/skills/",
    ".claude/console/",
    ".claude-iff/",
)

# Shell verbs that can mutate or destroy. Reading the record stays allowed on purpose: the
# maintainer inspects it and obsctl analyze reads it. The second line is PowerShell's
# mutating cmdlets: on Windows the PowerShell tool is a shell lane too, and a gate that only
# reads bash verbs waves `Remove-Item` straight through.
MUTATORS = (
    ">", ">>", "rm ", "rmdir ", "mv ", "cp ", "chmod ", "chown ", "truncate ", "tee ", "dd ",
    "sed -i", "shred ", "unlink", "rmtree", "-delete", "-exec rm", "mkdir ", "touch ",
    "remove-item", "move-item", "copy-item", "rename-item", "new-item", "set-content",
    "add-content", "clear-content", "out-file",
)

# A single, simple, read-only git invocation. Anchored and flag-tolerant, but it refuses
# anything with a shell operator in it, so `git log && git push` can never match.
# symbolic-ref is deliberately absent (here AND in policy.json's read_only_subcommands,
# which must stay in step): its two-argument form writes the ref.
READONLY_GIT = re.compile(
    r"^\s*git\s+(?:-[-\w]+(?:[= ]\S+)?\s+)*"
    r"(?:diff|log|show|status|ls-files|rev-parse|blame|describe|shortlog|cat-file|grep|"
    r"show-ref|for-each-ref|count-objects|var)\b[^;&|`$]*$"
)


def emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow() -> None:
    sys.exit(0)


def load_payload(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        sys.exit(3)
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def norm(path_text: str, root: Path, cwd: str) -> Path:
    p = Path(str(path_text)).expanduser()
    if not p.is_absolute():
        p = Path(cwd or str(root)) / p
    try:
        return p.resolve()
    except OSError:
        return p


def under(path: Path, prefix: Path) -> bool:
    """Containment on resolved paths. String-compared through os.path.normcase rather than
    pathlib.relative_to: Windows filesystems are case-insensitive and accept both slash
    forms, so a lowercased or mixed-slash spelling of the record path must still count as
    inside it. On POSIX normcase is the identity and this is plain prefix matching."""
    a = os.path.normcase(str(path))
    b = os.path.normcase(str(prefix)).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def segments_of(command: str) -> list:
    out, buf = [], ""
    for ch in command:
        if ch in ";&|\n":
            out.append(buf)
            buf = ""
        else:
            buf += ch
    out.append(buf)
    return out


def main(argv: list) -> int:
    if len(argv) < 1:
        return 3
    payload = load_payload(argv[0])

    root_env = payload.get("_project_root") or ""
    root = Path(root_env).resolve() if root_env else Path.cwd().resolve()
    tool = str(payload.get("tool_name") or "")
    raw_input = payload.get("tool_input")
    cwd = str(payload.get("cwd") or root)

    # Identity: ONLY an absent agent_type/agent_name means the main session. A sub-agent that
    # happens to be named "orchestrator" must not inherit main-session privileges by its name.
    agent_type = str(payload.get("agent_type") or "").strip()
    agent_name = str(payload.get("agent_name") or "").strip()
    identity = agent_type or agent_name or "orchestrator"
    is_main = not (agent_type or agent_name)

    # PowerShell is the Windows shell lane: same judgment as Bash, over the same command
    # string. Leaving it unmatched left every ring open to one tool on one platform.
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"):
        allow()

    if not isinstance(raw_input, dict):
        # A shape we cannot parse is a shape we cannot judge. Deny rather than wave it through.
        emit_deny(
            f"the gate could not read this {tool} call's tool_input (expected an object, got "
            f"{type(raw_input).__name__}). Refusing rather than guessing."
        )
    tool_input = raw_input

    policy = None
    record_root = None
    try:
        sys.path.insert(0, str(root / ".claude" / "tools"))
        import _lib  # noqa: E402

        policy = _lib.load_config("policy")
        record_root = _lib.record_root()
    except Exception:
        policy, record_root = None, None

    degraded = not isinstance(policy, dict) or not policy
    if degraded:
        protected = FALLBACK_PROTECTED
        grants: dict = {}
        deny_bash = () if is_main else ("git",)
        read_only: dict = {}
    else:
        protected = tuple(policy.get("protected") or FALLBACK_PROTECTED)
        agents = policy.get("agents") or {}
        entry = agents.get(identity) if isinstance(agents, dict) else None
        default = policy.get("default") or {}
        grants = entry if isinstance(entry, dict) else {}
        if is_main:
            deny_bash = tuple(grants.get("deny_bash") or ())
            read_only = {}
        else:
            source = grants if "deny_bash" in grants else default
            deny_bash = tuple(source.get("deny_bash") or ())
            ro = source.get("read_only_subcommands")
            if not isinstance(ro, dict):
                ro = default.get("read_only_subcommands") or {}
            read_only = ro if isinstance(ro, dict) else {}

    if record_root is None:
        record_root = root.parent / f"{root.name}_claude_iff"
    iff_dir = root / ".claude-iff"

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = (tool_input.get("file_path") or tool_input.get("path")
                  or tool_input.get("notebook_path"))
        if not target:
            allow()
        path = norm(target, root, cwd)

        if under(path, record_root) or under(path, iff_dir):
            emit_deny(
                f"{path} is inside the append-only record. Nothing writes there by hand: the "
                f"capture hook appends and obsctl seals. Read it freely; to analyse it run "
                f"`python3 .claude/tools/obsctl.py analyze`."
            )

        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            allow()
        # Case-fold the comparison on Windows: the filesystem is case-insensitive there, so
        # a Write to ".ClAuDe/config/x" lands in the real .claude/config and a case-exact
        # prefix match is a spelling away from open. POSIX stays case-exact.
        fold = str.lower if os.name == "nt" else str
        rel_slash = rel + ("/" if path.is_dir() else "")
        hit = next((p for p in protected
                    if fold(rel_slash).startswith(fold(p)) or fold(rel) == fold(p).rstrip("/")),
                   None)
        if hit:
            if is_main:
                allow()
            if any(fold(rel_slash).startswith(fold(g))
                   for g in tuple(grants.get("write_paths") or ())):
                allow()
            reason = (
                f"{rel} is in the protected tree ({hit}) and sub-agent '{identity}' has no write "
                f"grant for it. The main session owns the gate, the tools it executes, the "
                f"configs, the agents and the protocols: a sub-agent must not be able to edit "
                f"what governs it. Return the change as a proposal in your Structured Return."
            )
            if degraded:
                reason += " (policy.json unreadable: failing closed on the fallback tree.)"
            emit_deny(reason)
        allow()

    # ---- Bash -------------------------------------------------------------------------
    command = str(tool_input.get("command") or "")
    if not command.strip():
        allow()
    lowered = command.lower()

    for denied in deny_bash:
        # Word-boundary match anywhere in the command, not just the leading token. Lexing shell
        # in python is not sound (bash -c, eval, quoting, $(), backticks, variable indirection
        # all defeat a token walk), so this errs toward false positives and says so when it
        # fires. The single exception is one simple read-only invocation of the denied command.
        #
        # `/` and `.` join the excluded neighbors: a PATH SEGMENT spelled like the command is a
        # mention, not an invocation. Without this, any repo living under a folder named `git`
        # or `GIT` (a common convention, including this machine's ~/Documents/GIT/) made every
        # `ls`, `cp` and `python3` that named a path in it read as running git - the adoption
        # dry-run hit that constantly. The shipped tests covered the adjacent class (gitleaks,
        # mygit) and missed this one. Executing `./git` slips the net as a consequence; the
        # docstring already says this arm is advisory against a determined adversary, and the
        # record ring below is what actually guards the data.
        if not re.search(rf"(?<![\w./-]){re.escape(denied)}(?![\w./-])", lowered):
            continue
        if denied == "git" and READONLY_GIT.match(command.strip()):
            sub = next((t for t in command.split()[1:] if not t.startswith("-")), "")
            allowed_subs = read_only.get("git") or ()
            if sub in allowed_subs:
                continue
        emit_deny(
            f"sub-agent '{identity}' may not run `{denied}`. Only a single, simple, read-only "
            f"invocation is allowed ({', '.join(sorted(read_only.get(denied) or ())[:5])}); "
            f"anything that mutates, and anything wrapped in a shell, an eval or a substitution, "
            f"is the main session's job. This check matches the word anywhere in the command, so "
            f"it can fire on a harmless mention: if that happened, ask the main session to run it."
        )

    # Ring 1 for the shell: deny mutation of THIS project's record, and only this project's.
    # Every path-like mention of ".claude-iff" or the record folder's name is extracted and
    # RESOLVED (relative mentions against the command's cwd); only a mention that lands inside
    # this project's record or committed surface arms the mutator check. A bare substring match
    # used to deny `mkdir -p <other-project>/.claude-iff/obs` - the exact step the adopt skill
    # instructs when installing into a target repo. Another project's record is that project's
    # own gate's business.
    # The mention classes include ':' and '\\' so a Windows absolute path (C:\Users\...) is
    # captured whole; without them the drive prefix was cut off, the tail resolved against
    # cwd to a path that exists nowhere, and the record ring never armed on Windows.
    mention_re = re.compile(
        r"[\w~.:/\\-]*(?:\.claude-iff|" + re.escape(record_root.name.lower()) + r")[\w.:/\\-]*"
    )

    def targets_this_record(seg: str) -> bool:
        for mention in mention_re.findall(seg):
            p = Path(mention).expanduser()
            if not p.is_absolute():
                p = Path(cwd or str(root)) / p
            try:
                rp = p.resolve()
            except OSError:
                return True  # an unresolvable mention of the record: keep the stricter reading
            if under(rp, record_root) or under(rp, iff_dir):
                return True
        return False

    for seg in segments_of(lowered):
        if not targets_this_record(seg):
            continue
        # A redirect to /dev/null mutates nothing; without stripping it, every read-only
        # inspection of the record that silences stderr (`grep ... 2>/dev/null`) tripped the
        # `>` mutator and the gate denied its own maintainer's audits.
        seg_clean = re.sub(r"\d?>>?\s*/dev/null", " ", seg)
        if any(m in seg_clean for m in MUTATORS):
            emit_deny(
                "that command would modify the append-only record. Inspect it read-only (ls, "
                "cat, du, grep) or use `python3 .claude/tools/obsctl.py`, the only writer."
            )
    allow()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        # Any unhandled failure means this gate did not judge the call. Tell the wrapper.
        sys.exit(3)
