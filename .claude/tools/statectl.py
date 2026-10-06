#!/usr/bin/env python3
"""statectl.py - the continuity engine: journal in, projections out.

`state/journal.jsonl` is the append-only source of truth for a working session -
every other continuity artifact (`session.json`, `HANDOFF.md`, `needs-human.json`)
is a PROJECTION rebuilt from it and must never be hand-edited. This module is the
only writer of the journal's derived state and the only reader that is allowed to
assume the projection schema; everything else (hooks, the console) reads the JSON
files this module produces.

Why a projector at all, instead of updating the derived files in place: an
in-place update can silently drift from the append-only log the moment one writer
forgets a field, and a crash mid-update leaves a torn projection with no way back.
Rebuilding the projection from the journal on every mutation makes "what does the
session look like right now" a pure function of "what happened", which is the
whole point of a continuity engine surviving a disconnect.

`needs-human.jsonl` is a second, independent append-only log (open/amend/resolve),
projected the same way into `needs-human.json`. It is not part of JOURNAL_ACTIONS -
`need` events are their own vocabulary, deliberately decoupled from the session
journal so an unresolved human question survives even a session nobody ever closes.
`proposals.jsonl` (the proposal box: add/resolve) is a third log of the same kind, for
ideas that are out of scope right now; its open count lands in session.json.

Mode and phase are journal actions (`mode`, `phase`), folded by `_lib.fold_lifecycle` - the
one reader the projector, the SessionStart hook, checkctl and the console share. Leaving a
phase in an organised mode runs `checkctl phase-exit --from <current>` first. In
fableous-orchestrated, `task <id> --status done` runs `checkctl handoff <id>` first and refuses
on FAIL unless `--no-envelope "<why>"` (logged). `dispatch` cuts a builder's worktree, writes the
stub and prints its brief; `accept` validates, commits and merges the builder's work (lead only).
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import _lib

import argparse
import re
import uuid
from pathlib import Path

RECENT_N = 5


# --------------------------------------------------------------------------- paths

def _needs_jsonl_path() -> Path:
    return _lib.state_dir() / "needs-human.jsonl"


def _needs_json_path() -> Path:
    return _lib.state_dir() / "needs-human.json"


def _session_json_path() -> Path:
    return _lib.state_dir() / "session.json"


def _handoff_path() -> Path:
    return _lib.state_dir() / "HANDOFF.md"


def _heartbeat_path() -> Path:
    return _lib.state_dir() / "heartbeat.json"


# --------------------------------------------------------------------------- write gating

_GEN_AT_LINE_RE = re.compile(r"^<!-- generated_at: .* -->\n", re.MULTILINE)


def _write_gated(path: Path, content, durable: bool = False) -> None:
    """Write only when content differs from what's on disk, ignoring generated_at.

    A rebuild that touches mtime/git on every ritual even when nothing happened is
    exactly the kind of noise that makes "did anything actually change" unanswerable
    from git status; comparing content with the timestamp masked out keeps rebuilds
    silent when they should be silent.
    """
    if isinstance(content, dict):
        new_stripped = {k: v for k, v in content.items() if k != "generated_at"}
        old = _lib.read_json(path, default=None)
        if isinstance(old, dict):
            old_stripped = {k: v for k, v in old.items() if k != "generated_at"}
            if old_stripped == new_stripped:
                return
        _lib.atomic_write_json(path, content, durable=durable)
        return
    try:
        old_text = path.read_text(encoding="utf-8")
    except OSError:
        old_text = None
    if old_text is not None and _GEN_AT_LINE_RE.sub("", old_text) == _GEN_AT_LINE_RE.sub("", content):
        return
    _lib.atomic_write_text(path, content, durable=durable)


# --------------------------------------------------------------------------- time text

def _human_age(ts: str) -> str:
    secs = _lib.age_seconds(ts)
    if secs is None:
        return "unknown"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# --------------------------------------------------------------------------- session projection

def _build_session_projection() -> dict:
    """Fold the journal into the current-state snapshot. One pass, latest-wins per
    entity id, with a field only overwriting the prior value when the event actually
    carries that field - journal_append drops None fields, so "field present" is
    exactly "field was given on the command line" (task's partial-update contract).
    """
    events = _lib.journal_read(tolerant=True)

    session = {"id": None, "started": None}
    resume_pointer = None
    tasks: dict = {}
    loops: dict = {}
    intents: dict = {}
    gates: dict = {}
    milestones: list = []
    decisions: list = []
    tooling: list = []

    for ev in events:
        action = ev.get("action")
        ts = ev.get("ts", "")

        if action == "session_start":
            # Its free-text `phase` is legacy: the lifecycle phase comes from `phase` events
            # (folded below), and a non-lifecycle value like "implementation" reads as unset.
            session = {"id": ev.get("session"), "started": ts}

        elif action == "pointer":
            resume_pointer = ev.get("text")

        elif action == "task":
            tid = ev.get("id")
            if not tid:
                continue
            rec = tasks.setdefault(tid, {"id": tid, "title": "", "status": "todo", "deps": [],
                                         "milestone": None, "ts": ts})
            for key in ("title", "status", "deps", "milestone"):
                if key in ev:
                    rec[key] = ev[key]
            rec["ts"] = ts

        elif action == "milestone":
            milestones.append({"id": ev.get("id", ""), "title": ev.get("title", ""), "ts": ts})

        elif action == "decision":
            decisions.append({"text": ev.get("text", ""), "why": ev.get("why", ""), "ts": ts})

        elif action == "loop":
            lid = ev.get("id")
            if not lid:
                continue
            rec = loops.setdefault(lid, {"id": lid, "text": "", "status": "open", "ts": ts})
            if "text" in ev:
                rec["text"] = ev["text"]
            if "status" in ev:
                rec["status"] = ev["status"]
            rec["ts"] = ts

        elif action == "note":
            pass  # narration only - surfaces via the raw journal tail (resume/status), no session.json slot

        elif action == "intent":
            iid = ev.get("intent_id")
            if not iid:
                continue
            rec = intents.setdefault(iid, {"intent_id": iid, "op": "", "files": [], "state": "begin", "ts": ts})
            if "op" in ev:
                rec["op"] = ev["op"]
            if "files" in ev:
                rec["files"] = ev["files"]
            if "state" in ev:
                rec["state"] = ev["state"]
            rec["ts"] = ts

        elif action == "gate":
            q = ev.get("question")
            if not q:
                continue
            rec = gates.setdefault(q, {"question": q, "kind": "", "answered": False, "ts": ts})
            if "kind" in ev:
                rec["kind"] = ev["kind"]
            rec["answered"] = "answer" in ev
            rec["ts"] = ts

        elif action == "tooling":
            tooling.append({"change_type": ev.get("change_type", ""), "what": ev.get("what", ""), "ts": ts})

        elif action in ("mode", "phase"):
            pass  # folded after the loop by _lib.fold_lifecycle, the reader the hook and console share

        # Every action in _lib.JOURNAL_ACTIONS has a branch above (even if the branch is a
        # deliberate no-op, per the comments). An action reaching none of them is a projector
        # bug, not a new feature - see test_all_actions_projected.

    life = _lib.fold_lifecycle(events)
    session = {"id": session["id"], "mode": life["mode"], "phase": life["phase"],
               "phase_since": life["phase_since"], "started": session["started"]}
    proposals_open = sum(1 for p in _lib.proposal_records() if p["status"] == "open")

    tasks_list = sorted(tasks.values(), key=lambda t: t["id"])
    tasks_done = sum(1 for t in tasks_list if t["status"] == "done")

    open_loops = sorted((l for l in loops.values() if l["status"] == "open"), key=lambda l: l["ts"])
    open_loops = [{"id": l["id"], "text": l["text"], "ts": l["ts"]} for l in open_loops]

    open_intents = sorted((i for i in intents.values() if i["state"] == "begin"), key=lambda i: i["ts"])
    open_intents = [{"intent_id": i["intent_id"], "op": i["op"], "ts": i["ts"], "files": i["files"]} for i in open_intents]

    open_gates = sorted((g for g in gates.values() if not g["answered"]), key=lambda g: g["ts"])
    open_gates = [{"question": g["question"], "kind": g["kind"], "ts": g["ts"]} for g in open_gates]

    heartbeat = _lib.read_json(_heartbeat_path(), default=None)
    if isinstance(heartbeat, dict):
        heartbeat = {"ts": heartbeat.get("ts", ""), "note": heartbeat.get("note", "")}
    else:
        heartbeat = None

    return {
        "generated_at": _lib.utc_now(),
        "last_event_ts": events[-1].get("ts", "") if events else "",
        "session": session,
        "resume_pointer": resume_pointer,
        "open_loops": open_loops,
        "open_intents": open_intents,
        "tasks": tasks_list,
        "counts": {
            "events": len(events),
            "tasks_total": len(tasks_list),
            "tasks_done": tasks_done,
            "tasks_open": len(tasks_list) - tasks_done,
            "open_loops": len(open_loops),
            "milestones": len(milestones),
            "decisions": len(decisions),
            "tooling": len(tooling),
            "proposals_open": proposals_open,
        },
        "heartbeat": heartbeat,
        "recent_milestones": milestones[-RECENT_N:],
        "recent_decisions": decisions[-RECENT_N:],
        "recent_tooling": tooling[-RECENT_N:],
        "open_gates": open_gates,
    }


# --------------------------------------------------------------------------- needs-human projection

def _load_need_records() -> dict:
    """Merge needs-human.jsonl into current per-id state. Same latest-wins-per-field
    rule as the session projector; `amend` only touches band/note, `resolve` only
    flips status/answer, so an unresolved need's original title/category survive.
    """
    records: dict = {}
    for ev in _lib.read_jsonl(_needs_jsonl_path(), tolerant=True):
        nid = ev.get("id")
        if not nid:
            continue
        op = ev.get("op")
        ts = ev.get("ts", "")
        if op == "open":
            records[nid] = {
                "id": nid,
                "title": ev.get("title", ""),
                "category": ev.get("category", ""),
                "band": ev.get("band", ""),
                "blocks": ev.get("blocks") or 0,
                "note": ev.get("note", ""),
                "context": ev.get("context", ""),
                "action": ev.get("action", ""),
                "opened": ts,
                "status": "open",
                "answer": "",
                "resolved": "",
            }
        elif op == "amend":
            rec = records.get(nid)
            if rec is None:
                continue
            if "band" in ev:
                rec["band"] = ev["band"]
            if "note" in ev:
                rec["note"] = ev["note"]
            if "context" in ev:
                rec["context"] = ev["context"]
            if "action" in ev:
                rec["action"] = ev["action"]
        elif op == "resolve":
            rec = records.get(nid)
            if rec is None:
                continue
            rec["status"] = "resolved"
            rec["resolved"] = ts
            if "answer" in ev:
                rec["answer"] = ev["answer"]
    return records


def _build_needs_projection() -> dict:
    records = _load_need_records()
    band_index = {b: i for i, b in enumerate(_lib.SEV_BANDS)}

    open_recs = [r for r in records.values() if r["status"] == "open"]
    open_recs.sort(key=lambda r: (band_index.get(r["band"], len(_lib.SEV_BANDS)), r["opened"]))

    by_band = {b: 0 for b in _lib.SEV_BANDS}
    tasks = []
    for r in open_recs:
        if r["band"] in by_band:
            by_band[r["band"]] += 1
        age = _lib.age_seconds(r["opened"])
        tasks.append({
            "id": r["id"], "title": r["title"], "category": r["category"], "band": r["band"],
            "blocks": r["blocks"], "note": r["note"],
            "context": r.get("context", ""), "action": r.get("action", ""),
            "opened": r["opened"],
            "age_days": int(age // 86400) if age is not None else 0,
            "status": "open",
        })

    resolved_recs = sorted((r for r in records.values() if r["status"] == "resolved"), key=lambda r: r["resolved"])
    resolved_recent = [
        {"id": r["id"], "title": r["title"], "answer": r["answer"], "resolved": r["resolved"]}
        for r in resolved_recs[-RECENT_N:]
    ]

    return {
        "generated_at": _lib.utc_now(),
        "counts": {"open": len(open_recs), "resolved": len(resolved_recs), "by_band": by_band},
        "tasks": tasks,
        "resolved_recent": resolved_recent,
    }


def _next_need_id() -> str:
    """A collision-free id, derived rather than counted.

    Read-max-then-write is a race, and this is the one queue whose whole purpose is that a
    human question survives. Under concurrent `need open` calls the counter handed out the same
    id twice, the projector's `records[nid] = ...` overwrote one with the other, and a question
    (possibly a SEV0 blocker) vanished with no error at all. The design explicitly allows any
    session to append, so the id cannot depend on having read the file first.

    Sortable-by-time prefix so the queue still reads chronologically, plus enough entropy that
    two sessions in the same second cannot collide.
    """
    stamp = _lib.utc_now().replace("-", "").replace(":", "").replace("Z", "").replace("T", "")
    return f"NH-{stamp}-{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------- HANDOFF.md

def _render_handoff(proj: dict, needs_proj: dict, proposals=None) -> str:
    sess = proj["session"]
    phase = sess.get("phase")
    since = f" (since {sess['phase_since']})" if phase and sess.get("phase_since") else ""
    lines = [
        "<!-- AUTO-GENERATED by `statectl.py refresh` - a projection of state/journal.jsonl, "
        "state/needs-human.jsonl and state/proposals.jsonl. Do not hand-edit; edits are "
        "overwritten on the next refresh. -->",
        f"<!-- generated_at: {proj['generated_at']} -->",
        "",
        "# Handoff",
        "",
        "▶ Resume here",
        "",
        f"> {proj['resume_pointer'] or '(no pointer set)'}",
        "",
        f"**Mode:** {_lib.mode_label(sess.get('mode'))} · **Phase:** {phase or 'unset'}{since}",
        "",
        "## Open loops",
        "",
    ]
    if proj["open_loops"]:
        lines += [f"- [ ] **{l['id']}** {l['text']}" for l in proj["open_loops"]]
    else:
        lines.append("_none_")

    lines += ["", "## Where we are", ""]
    if proj["tasks"]:
        lines += ["| id | title | status | milestone |", "|---|---|---|---|"]
        lines += [f"| {t['id']} | {t['title']} | {t['status']} | {t.get('milestone') or ''} |"
                  for t in proj["tasks"]]
    else:
        lines.append("_no tasks tracked_")

    open_props = [p for p in (proposals or []) if p["status"] == "open"]
    lines += ["", "## Open proposals", ""]
    if open_props:
        lines += [f"- **{p['id']}** [{p['kind']} · {p['source']}] {p['text']}" for p in open_props]
    else:
        lines.append("_none open_")

    lines += ["", "## Needs human", ""]
    if needs_proj["tasks"]:
        lines += [
            f"- **{n['id']}** [{n['band']}] {n['title']} - {n['note']} ({n['age_days']}d open)"
            for n in needs_proj["tasks"]
        ]
    else:
        lines.append("_none open_")

    lines += ["", "## Recent milestones", ""]
    if proj["recent_milestones"]:
        lines += [f"- **{m['id']}** {m['title']} ({m['ts']})" for m in proj["recent_milestones"]]
    else:
        lines.append("_none yet_")

    lines += ["", "## Recent decisions", ""]
    if proj["recent_decisions"]:
        lines += [f"- {d['text']} - _{d['why']}_ ({d['ts']})" for d in proj["recent_decisions"]]
    else:
        lines.append("_none yet_")

    hb = proj["heartbeat"]
    lines += ["", f"_Last heartbeat: {hb['ts']} - {hb['note']}_" if hb else "_Last heartbeat: never_", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- refresh

def refresh_all() -> dict:
    proj = _build_session_projection()
    _write_gated(_session_json_path(), proj, durable=False)

    needs_proj = _build_needs_projection()
    _write_gated(_needs_json_path(), needs_proj, durable=False)

    _write_gated(_handoff_path(), _render_handoff(proj, needs_proj, _lib.proposal_records()),
                 durable=False)
    return proj


# --------------------------------------------------------------------------- commands

def cmd_start(args) -> int:
    _lib.journal_append("session_start", session=args.session, phase=args.phase, note=args.note)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_pointer(args) -> int:
    _lib.journal_append("pointer", text=args.text)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def _handoff_problems(task_id: str) -> list:
    """Why `task_id` may not be marked done in fableous-orchestrated: the FAIL rows of
    `checkctl handoff <task_id>` (no rerun), so the guard and the lead's review agree on what a
    valid handoff is. Fails closed: a check that cannot run is a problem, not a pass."""
    try:
        import checkctl
        results = checkctl.handoff_check(task_id)
        fail = checkctl.FAIL
    except Exception as exc:  # noqa: BLE001
        return [f"the handoff check could not run: {type(exc).__name__}: {exc}"]
    return [f"{r.name}: {r.message}" + (f" ({'; '.join(str(d) for d in r.details[:3])})"
                                        if r.details else "")
            for r in results if r.status == fail]


def cmd_task(args) -> int:
    deps = [d.strip() for d in args.deps.split(",") if d.strip()] if args.deps is not None else None
    # An explicit empty --milestone "" unlinks the task (same partial-update rule as --deps).
    milestone = args.milestone.strip() if args.milestone is not None else None
    # fableous-orchestrated: done means a builder handed back a valid envelope whose tests
    # passed, or the lead says on the record why there is none (it did a small task itself).
    no_envelope = (args.no_envelope or "").strip() or None
    guarded = args.status == "done" and _lib.current_mode() == _lib.ORCHESTRATED_MODE
    if guarded and not no_envelope:
        problems = _handoff_problems(args.id)
        if problems:
            print(f"refused: in {_lib.mode_label(_lib.ORCHESTRATED_MODE)} mode, {args.id} is done "
                  f"only with a valid handoff envelope whose tests passed "
                  f"(.claude/state/handshakes/{args.id}.json):")
            for problem in problems:
                print(f"  - {problem}")
            print(f"Fix the envelope (`checkctl handoff {args.id}` shows it), or, for a task the "
                  f"lead did itself: statectl task {args.id} --status done --no-envelope \"<why>\"")
            _lib.print_verdict("STATE", False)
            return 1
        print(f"handoff envelope for {args.id} is valid and its tests passed")
    elif guarded:
        print(f"recorded without a handoff envelope: {no_envelope}")
    elif no_envelope:
        print("--no-envelope is not recorded: the handoff guard applies only to --status done "
              "in fableous-orchestrated mode")
        no_envelope = None
    _lib.journal_append("task", id=args.id, title=args.title, status=args.status, deps=deps,
                        milestone=milestone, note=args.note, no_envelope=no_envelope)
    refresh_all()
    _lib.print_verdict("STATE", True, warn=no_envelope is not None)
    return 0


# --------------------------------------------------------------------------- dispatch / accept
#
# The lead's two worktree commands (.claude/protocols/orchestration.md). `dispatch` cuts a
# builder's worktree from HEAD, writes the stub and prints the filled builder brief; `accept`
# validates the builder's handoff inside that worktree, commits it there, merges it into the
# current branch and removes the worktree. LEAD ONLY: both run git, which the policy gate denies
# to sub-agents. Every git call is an argv list (never a shell); nothing is pushed; `--force`
# appears once, removing a worktree whose branch was just merged.

import os  # noqa: E402 - only the worktree commands below need it

WORKTREES_DIR = ".claude/worktrees"
WORKTREE_BRANCH_PREFIX = "wt/"
BRIEF_TEMPLATE = ".claude/tasks/_builder-brief.md"
# Uncommitted tracked changes that do not block a dispatch: the lead's bookkeeping (statectl
# rewrites the journal and its projections on every command, dispatch included), the zips, and
# _lib.derived_files(). Anything else uncommitted is invisible to a worktree cut from HEAD.
DISPATCH_DIRTY_EXEMPT = (".claude/state/", ".claude/dist/")
# Restored to HEAD in the worktree before accept commits, with _lib.derived_files(): the zips
# are rebuilt and committed by the release step, never by a builder (release-flow.md).
ACCEPT_RESTORE = (".claude/dist",)
GIT_TIMEOUT = 600
_WT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTRACTS_RE = re.compile(r"Shared contracts:\s*`([^`\n]+)`")
_BRIEF_SCAFFOLD_RE = re.compile(r"^Scaffold, not a task:.*?\n[ \t]*\n", re.MULTILINE | re.DOTALL)
# A placeholder stands alone (`<wt>`), unlike a name pattern glued to a word (`test_<name>.py`).
_BRIEF_PLACEHOLDER_RE = re.compile(r"(?<!\w)<[A-Za-z_][A-Za-z0-9_ -]*>")


class _Stop(Exception):
    """Why dispatch or accept will not go on, in one line."""


def _git(args: list, cwd):
    import subprocess  # local: only the two worktree commands run git
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_MERGE_AUTOEDIT="no")
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                              check=False, env=env)
    except FileNotFoundError:
        raise _Stop("git is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise _Stop(f"`git {' '.join(args)}` timed out after {GIT_TIMEOUT}s") from None


def _git_said(res) -> str:
    lines = [ln.strip() for ln in ((res.stderr or "") + (res.stdout or "")).splitlines() if ln.strip()]
    return " / ".join(lines[-3:]) or f"exit {res.returncode}"


def _git_ok(args: list, cwd) -> str:
    """Raw stdout of a git command that must succeed; a failure stops with git's own words."""
    res = _git(args, cwd)
    if res.returncode != 0:
        raise _Stop(f"`git {' '.join(args)}` failed: {_git_said(res)}")
    return res.stdout


def _same_dir(a, b) -> bool:
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def _covers(prefix: str, path: str) -> bool:
    """`path` is `prefix` or lies under it (repo-relative; case-folded where the OS folds, L-9)."""
    a = os.path.normcase(prefix.replace("\\", "/").rstrip("/"))
    b = os.path.normcase(path.replace("\\", "/"))
    return b == a or b.startswith(a + os.path.normcase("/"))


def _porcelain_paths(out: str) -> list:
    """Paths named by `git status --porcelain=v1 -z` (both sides of a rename)."""
    tokens, paths, i = out.split("\0"), [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue
        paths.append(tok[3:])
        if "R" in tok[:2] or "C" in tok[:2]:
            if i < len(tokens) and tokens[i]:
                paths.append(tokens[i])
            i += 1
    return paths


def _repo_branch(root: Path) -> str:
    """The checkout's current branch; stops when `root` is not the top of a git repository with
    a commit, or HEAD is detached."""
    res = _git(["rev-parse", "--show-toplevel"], root)
    if res.returncode != 0:
        raise _Stop(f"{_lib.tilde(root)} is not a git repository")
    top = res.stdout.strip()
    if not _same_dir(top, root):
        raise _Stop(f"the project root is not the git top level ({_lib.tilde(top)}): make the "
                    f"worktree by hand and record it with --worktree <path> --no-worktree")
    if _git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], root).returncode != 0:
        raise _Stop("the repository has no commit yet: a worktree is cut from HEAD")
    res = _git(["symbolic-ref", "-q", "--short", "HEAD"], root)
    if res.returncode != 0 or not res.stdout.strip():
        raise _Stop("HEAD is detached: check out the working branch first")
    return res.stdout.strip()


def _task_file_for(task_id: str):
    """The task file the id maps to (checkctl's rule: the file stem or the id leading its
    `# Task:` title, case-insensitive), or None."""
    import checkctl
    want = task_id.casefold()
    return next((tf for tf in checkctl.task_files() if want in [i.casefold() for i in tf["ids"]]),
                None)


def _worktree_target(task_id: str, given) -> tuple:
    """(repo-relative path, name) of a builder's worktree: .claude/worktrees/<name>, the one
    place the stop check and the delegation nudge look."""
    raw = Path(given) if given else Path(WORKTREES_DIR) / task_id.lower()
    rel_path = _lib.rel(raw) if raw.is_absolute() else raw.as_posix()
    parent, _, name = rel_path.rpartition("/")
    if parent != WORKTREES_DIR or not _WT_NAME_RE.match(name) or ".." in name:
        raise _Stop(f"a builder's worktree is {WORKTREES_DIR}/<name> with a plain name, not "
                    f"{rel_path!r}")
    return rel_path, name


def _dispatch_preflight(root: Path, task_id: str, given) -> tuple:
    """Every dispatch refusal, checked before anything is written: (task file, worktree path,
    worktree name, current branch)."""
    tf = _task_file_for(task_id)
    if tf is None:
        raise _Stop(f"no task file maps to {task_id}: none in .claude/tasks/ is named for it or "
                    f"titled `# Task: {task_id} - ...` (/plan-task writes one)")
    wt_rel, name = _worktree_target(task_id, given)
    branch = _repo_branch(root)
    task_rel = _lib.rel(tf["path"])
    if not _git_ok(["ls-tree", "--name-only", "HEAD", "--", task_rel], root).strip():
        raise _Stop(f"{task_rel} is not committed: the worktree is cut from HEAD and would not "
                    f"see it. Commit it first")
    exempt = list(DISPATCH_DIRTY_EXEMPT)
    try:
        exempt += _lib.derived_files(root)
    except _lib.LibError:
        pass  # fewer exemptions only make the refusal below stricter
    dirty = sorted({p for p in _porcelain_paths(_git_ok(
        ["status", "--porcelain=v1", "-z", "--untracked-files=no"], root))
        if not any(_covers(e, p) for e in exempt)})
    if dirty:
        shown = ", ".join(dirty[:5]) + (f" and {len(dirty) - 5} more" if len(dirty) > 5 else "")
        raise _Stop(f"uncommitted changes to tracked files ({shown}): the worktree is cut from "
                    f"HEAD and the builder would not see them. Commit them first")
    if (root / wt_rel).exists():
        raise _Stop(f"{wt_rel} already exists: accept or remove that worktree first, or name "
                    f"another with --worktree")
    wt_branch = WORKTREE_BRANCH_PREFIX + name
    if _git(["check-ref-format", "--branch", wt_branch], root).returncode != 0:
        raise _Stop(f"{wt_branch} is not a valid branch name")
    if _git(["rev-parse", "-q", "--verify", f"refs/heads/{wt_branch}"], root).returncode == 0:
        raise _Stop(f"branch {wt_branch} already exists: merge or delete it first "
                    f"(`git branch -d {wt_branch}`)")
    return tf, wt_rel, name, branch


def _agent_model(root: Path, agent: str) -> str:
    try:
        text = (root / ".claude" / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"(?m)^model:\s*([^\s#]+)", text)
    return m.group(1) if m else ""


def _ci_platforms(root: Path) -> str:
    found = []
    wf = root / ".github" / "workflows"
    for path in sorted(wf.glob("*.y*ml")) if wf.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r"(?m)^\s*(?:-\s*)?(?:os|runs-on):\s*([A-Za-z0-9._-]+)\s*(?:#.*)?$",
                             text):
            if m.group(1) not in found:
                found.append(m.group(1))
    return ", ".join(found) if found else "no CI workflow in .github/workflows/; the platforms " \
                                          "the project supports"


def _platform_text() -> str:
    import platform
    if os.name == "nt":
        return "Windows with Git Bash; `python3` works"
    system = platform.system() or "POSIX"
    return ("macOS" if system == "Darwin" else system) + " with bash"


def fill_brief(template: str, values: dict) -> str:
    """The builder brief with every `<placeholder>` in `values` filled and the scaffold note
    dropped. An empty `<contracts>` drops its line and renumbers the read-first list."""
    text = _BRIEF_SCAFFOLD_RE.sub("", template, count=1)
    if not values.get("<contracts>"):
        text = "".join(ln for ln in text.splitlines(keepends=True) if "<contracts>" not in ln)
    head, sep, tail = text.partition("## Read first")
    if sep:
        section, nxt, rest = tail.partition("\n## ")
        counter = iter(range(1, 100))
        section = re.sub(r"(?m)^\d+\. ", lambda _m: f"{next(counter)}. ", section)
        text = head + sep + section + nxt + rest
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def _builder_prompt(root: Path, task_id: str, tf, wt_rel: str, name: str, branch: str,
                    agent: str) -> tuple:
    """(prompt text, placeholders left unfilled); (None, why) when the template is missing."""
    template = root / BRIEF_TEMPLATE
    try:
        text = template.read_text(encoding="utf-8")
    except OSError:
        return None, f"no brief template at {BRIEF_TEMPLATE}: write the builder's prompt by hand"
    task_rel = _lib.rel(tf["path"])
    contracts = ""
    m = _CONTRACTS_RE.search(tf["path"].read_text(encoding="utf-8", errors="replace"))
    if m:
        named = m.group(1).strip()
        for cand in (root / named, tf["path"].parent / named):
            if cand.is_file():
                contracts = _lib.rel(cand)
                break
        contracts = contracts or named
    brief = fill_brief(text, {
        "<TASK_ID>": task_id, "<task file>": task_rel, "<contracts>": contracts,
        "<branch>": branch or "current", "<wt>": name, "<platform>": _platform_text(),
        "<ci>": _ci_platforms(root), "<model>": _agent_model(root, agent) or agent,
    })
    header = (f"You are the {agent} for task {task_id}. Task file: `{task_rel}`. Worktree: "
              f"`{name}` (`{wt_rel}/`, branch `{WORKTREE_BRANCH_PREFIX}{name}`). Brief: "
              f"`{BRIEF_TEMPLATE}`, filled below; follow it exactly.\n\n")
    return header + brief, sorted(set(_BRIEF_PLACEHOLDER_RE.findall(brief)))


def cmd_dispatch(args) -> int:
    """Dispatch an agent for a task. For a builder (the default agent): cut its worktree from
    HEAD (`git worktree add .claude/worktrees/<id> -b wt/<id> HEAD`), write the stub and print the
    filled builder brief, ready to paste as its prompt; refuse with one line and change nothing
    when no task file maps to the id, the worktree or branch exists, tracked files outside the
    lead's bookkeeping are uncommitted, or this is not a git repo. --no-worktree (or any other
    agent) writes the stub alone, recording --worktree when given, as before. The stub shows the
    agent in flight on the console and dates the dispatch for the builder stop check; the
    journal note lets a resumed session see it."""
    root = _lib.project_root()
    builder = args.agent == "builder"
    wt_rel = Path(args.worktree).as_posix() if args.worktree else None
    tf, name, branch, warn = None, None, "", False
    if builder and not args.no_worktree:
        try:
            tf, wt_rel, name, branch = _dispatch_preflight(root, args.id, args.worktree)
        except _Stop as exc:
            print(f"refused: {exc}. Nothing was created.")
            _lib.print_verdict("STATE", False)
            return 1
        res = _git(["worktree", "add", wt_rel, "-b", WORKTREE_BRANCH_PREFIX + name, "HEAD"], root)
        if res.returncode != 0:
            print(f"refused: git could not create the worktree: {_git_said(res)}. Check "
                  f"`git worktree list` and `git branch --list {WORKTREE_BRANCH_PREFIX}{name}`.")
            _lib.print_verdict("STATE", False)
            return 1
        base = _git(["rev-parse", "--short", "HEAD"], root).stdout.strip()
        print(f"worktree: {wt_rel} on branch {WORKTREE_BRANCH_PREFIX}{name}, cut from {branch} "
              f"at {base}")
        if _git(["check-ignore", "-q", wt_rel], root).returncode != 0:
            warn = True
            print(f"WARNING: {WORKTREES_DIR}/ is not gitignored here; the main checkout lists the "
                  f"worktree as untracked (distctl.py gitignore --apply adds the rule)")
    elif builder:
        try:
            tf = _task_file_for(args.id)
        except Exception:  # noqa: BLE001 - the brief is a courtesy here; the stub is the job
            tf = None
        name = Path(wt_rel).name if wt_rel else args.id.lower()
        branch = _lib.git_output(["rev-parse", "--abbrev-ref", "HEAD"], root)
    path = _lib.write_stub(args.id, args.agent, wt_rel)
    where = f" in {wt_rel}" if wt_rel else ""
    _lib.journal_append("note", text=f"dispatched {args.agent} for {args.id}{where}",
                        tags=["dispatch"])
    refresh_all()
    print(f"stub: {_lib.rel(path)}")
    if tf is not None:
        prompt, left = _builder_prompt(root, args.id, tf, wt_rel or f"{WORKTREES_DIR}/{name}",
                                       name, branch, args.agent)
        if prompt is None:
            warn = True
            print(left)
        else:
            if left:
                warn = True
                print(f"WARNING: the brief still holds {', '.join(left)}: fill it before pasting")
            print("----- builder prompt: paste everything between these markers -----")
            print(prompt.rstrip("\n"))
            print("----- end of builder prompt -----")
    _lib.print_verdict("STATE", True, warn=warn)
    return 0


def _accept_worktree(root: Path, task_id: str, given) -> Path:
    """--worktree, else the worktree the stub recorded, else .claude/worktrees/<id lowercased>."""
    if not given:
        stub = _lib.stub_path(task_id)
        given = _lib.read_stub(stub)["worktree"] if stub.is_file() else ""
    raw = Path(given) if given else Path(WORKTREES_DIR) / task_id.lower()
    return raw if raw.is_absolute() else root / raw


def _task_topic(task_id: str) -> str:
    """What the task is about, for the commit subject: its title without the leading id."""
    title = ""
    try:
        tf = _task_file_for(task_id)
        title = tf["title"] if tf else ""
    except Exception:  # noqa: BLE001 - a subject line is not worth failing an accept over
        pass
    if not title:
        title = next((t.get("title") or "" for t in _build_session_projection()["tasks"]
                      if t["id"].casefold() == task_id.casefold()), "")
    m = re.match(rf"^\s*{re.escape(task_id)}\s*[-–—:]\s*(.+)$", title, re.IGNORECASE)
    return (m.group(1) if m else title).strip() or "builder handoff"


def _restore_derived(wt: Path, paths: list) -> list:
    """Restore `paths` to HEAD inside the worktree, so a builder's rebuild of the zips or a
    projection never merges. Returns what the builder had changed there (discarded: a new
    untracked file is left out of the commit and goes with the worktree)."""
    changed = _porcelain_paths(_git_ok(["status", "--porcelain=v1", "-z", "--untracked-files=all",
                                        "--", *paths], wt))
    tracked = [p for p in paths if _git_ok(["ls-tree", "-r", "--name-only", "HEAD", "--", p], wt).strip()]
    if tracked:
        _git_ok(["checkout", "HEAD", "--", *tracked], wt)
    return sorted(set(changed))


def _hook_exec_bits(wt: Path) -> list:
    """Mark every .sh under .claude/hooks/ executable in the index (git on Windows never sees the
    bit, so a new hook would merge as 100644 and fail to run on Linux)."""
    fixed = []
    for entry in _git_ok(["ls-files", "-s", "-z", "--", ".claude/hooks"], wt).split("\0"):
        meta, _, path = entry.partition("\t")
        if path.endswith(".sh") and meta.split(" ")[0] != "100755":
            _git_ok(["update-index", "--chmod=+x", "--", path], wt)
            fixed.append(path)
    return fixed


def cmd_accept(args) -> int:
    """Accept a builder's work: (1) `checkctl handoff <id> --run --root <worktree>` must pass
    (--no-run validates without rerunning); (2) restore .claude/dist/ and the derived files to
    HEAD in the worktree; (3) stage the rest, mark hook scripts executable in the index, commit
    there; (4) merge its branch into the current branch with --no-ff, stopping on a conflict with
    the merge left in progress; (5) remove the worktree and its branch. Never pushes; never marks
    the task done (the lead does, after the suite)."""
    root = _lib.project_root()
    task_id = args.id
    try:
        branch = _repo_branch(root)
        if _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root).returncode == 0:
            raise _Stop("a merge is already in progress here: finish it (`git commit`) or abort "
                        "it (`git merge --abort`) first")
        wt = _accept_worktree(root, task_id, args.worktree)
        wt_rel = _lib.rel(wt)
        listed = [ln[len("worktree "):] for ln in _git_ok(["worktree", "list", "--porcelain"], root)
                  .splitlines() if ln.startswith("worktree ")]
        if not wt.is_dir() or not any(_same_dir(p, wt) for p in listed if Path(p).is_dir()):
            raise _Stop(f"no worktree at {wt_rel}. Never resume a builder whose worktree is gone: "
                        f"re-dispatch (`statectl.py dispatch {task_id}`)")
        res = _git(["symbolic-ref", "-q", "--short", "HEAD"], wt)
        wt_branch = res.stdout.strip() if res.returncode == 0 else ""
        if not wt_branch or wt_branch == branch:
            raise _Stop(f"{wt_rel} is not on a branch of its own: commit and merge it by hand")
        restore = list(ACCEPT_RESTORE) + _lib.derived_files(root)
    except (_Stop, _lib.LibError) as exc:
        print(f"refused: {exc}. Nothing was committed or merged.")
        _lib.print_verdict("STATE", False)
        return 1

    import checkctl
    results = checkctl.handoff_check(task_id, root=wt, run=not args.no_run)
    checkctl.render(results, f"handoff {task_id}")
    if any(r.status == checkctl.FAIL for r in results):
        print(f"\nrefused: {task_id}'s handoff in {wt_rel} does not pass; nothing was committed or "
              f"merged. Send the builder back, or fix the envelope, then accept again.")
        _lib.print_verdict("STATE", False)
        return 1

    subject = (args.message or "").strip() or f"{task_id}: {_task_topic(task_id)}"
    topic = re.sub(rf"^\s*{re.escape(task_id)}\s*:\s*", "", subject, flags=re.IGNORECASE) or subject
    try:
        discarded = _restore_derived(wt, restore)
        _git_ok(["add", "-A", "--", ".", *[f":(exclude){p}" for p in restore]], wt)
        made_exec = _hook_exec_bits(wt)
        leaked = _git_ok(["diff", "--cached", "--name-only", "--", *restore], wt).split()
        if leaked:
            raise _Stop(f"derived files are still staged ({', '.join(leaked)})")
        if _git(["diff", "--cached", "--quiet"], wt).returncode != 0:
            _git_ok(["commit", "-q", "-m", subject], wt)
            print(f"\ncommitted in {wt_rel} on {wt_branch}: {subject}")
        else:
            print(f"\nnothing new to commit in {wt_rel}; merging {wt_branch} as it stands")
    except _Stop as exc:
        print(f"stopped before the merge: {exc}. Nothing was merged; {wt_rel} is kept as accept "
              f"left it. Fix the cause and accept again.")
        _lib.print_verdict("STATE", False)
        return 1
    for path in discarded:
        print(f"discarded the builder's change to derived {path}")
    for path in made_exec:
        print(f"exec bit set in the index: {path}")
    try:
        return _accept_merge(root, task_id, topic, branch, wt, wt_rel, wt_branch)
    except _Stop as exc:
        print(f"stopped during the merge: {exc}. `git status` shows where it stands; {wt_rel} and "
              f"{wt_branch} are kept.")
        _lib.print_verdict("STATE", False)
        return 1


def _accept_merge(root: Path, task_id: str, topic: str, branch: str, wt: Path, wt_rel: str,
                  wt_branch: str) -> int:
    """accept's steps 4 and 5: merge --no-ff (stop on a conflict, merge left in progress), then
    remove the worktree and its branch."""
    before = _git(["rev-parse", "HEAD"], root).stdout.strip()
    res = _git(["merge", "--no-ff", "--no-edit", "-m", f"merge {task_id}: {topic}", wt_branch], root)
    if res.returncode != 0:
        if _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root).returncode == 0:
            conflicted = [p for p in _git(["diff", "--name-only", "--diff-filter=U"], root)
                          .stdout.splitlines() if p.strip()]
            print(f"\nCONFLICT: merging {wt_branch} into {branch} stopped on "
                  f"{len(conflicted)} path(s):")
            for path in conflicted:
                print(f"  {path}")
            print(f"The merge is left in progress; {wt_rel} and {wt_branch} are kept.")
            print("  finish: resolve each path, `git add <path>`, `git commit --no-edit`, then "
                  f"`statectl.py accept {task_id} --no-run` removes the worktree and branch")
            print(f"  abort:  `git merge --abort` ({wt_branch} keeps the builder's commit)")
        else:
            print(f"\nstopped: git refused the merge: {_git_said(res)}. Nothing was merged; "
                  f"{wt_branch} keeps the builder's commit and {wt_rel} is kept. Fix the cause, "
                  f"then `statectl.py accept {task_id}` again.")
        _lib.print_verdict("STATE", False)
        return 1
    head = _git(["rev-parse", "HEAD"], root).stdout.strip()
    after = head[:12]
    merged = "already merged, nothing new" if head == before else f"merge commit {after}"
    print(f"merged {wt_branch} into {branch} ({merged})")

    problems = []
    for step in (["worktree", "remove", "--force", str(wt)], ["branch", "-d", wt_branch]):
        res = _git(step, root)
        if res.returncode != 0:
            problems.append(f"`git {' '.join(step)}` failed: {_git_said(res)}")
    _lib.journal_append("note", text=f"accepted {task_id}: merged {wt_branch} into {branch} "
                                     f"({after})", tags=["accept"])
    refresh_all()
    if problems:
        for problem in problems:
            print(f"WARNING: cleanup: {problem}; finish it by hand")
    else:
        print(f"removed {wt_rel} and branch {wt_branch}")
    print(f"next: `python3 .claude/tools/tests/run_tests.py -q` on {branch}, then "
          f"`statectl.py task {task_id} --status done`. accept never pushes and never marks a "
          f"task done.")
    _lib.print_verdict("STATE", True, warn=bool(problems))
    return 0


# --------------------------------------------------------------------------- mode / phase

def cmd_mode(args) -> int:
    before = _lib.current_mode()
    _lib.journal_append("mode", value=args.value)
    refresh_all()
    print(f"mode: {_lib.mode_label(args.value)} (was {_lib.mode_label(before)})")
    if args.value in _lib.ORGANISED_MODES:
        print("leaving a phase now runs its exit check: statectl phase <next>")
    _lib.print_verdict("STATE", True)
    return 0


def _phase_exit_results(current: str, signoff):
    """Run checkctl's exit check for `current`. Returns (results, failed: bool). The gate fails
    closed: a check that cannot run at all counts as failed, so only --override gets past it."""
    try:
        import checkctl
    except Exception as exc:  # noqa: BLE001
        print(f"exit check could not load checkctl: {type(exc).__name__}: {exc}")
        return [], True
    try:
        results = checkctl.phase_exit(current, signoff=signoff)
    except Exception as exc:  # noqa: BLE001
        results = [checkctl.Result(f"{current}_exit", checkctl.FAIL,
                                   f"exit check raised {type(exc).__name__}: {exc}")]
    checkctl.render(results, f"exit check: {current}")
    return results, any(r.status == checkctl.FAIL for r in results)


def cmd_phase(args) -> int:
    """Move the lifecycle phase. Organised modes run the CURRENT phase's exit check first and
    refuse on FAIL unless --override names why; freestyle and a first phase just record."""
    state = _lib.lifecycle_state()
    current, mode, target = state["phase"], state["mode"], args.value
    if current == target:
        print(f"already in phase {target}; nothing recorded")
        _lib.print_verdict("STATE", True)
        return 0
    override = (args.override or "").strip() or None
    signoff = (args.signoff or "").strip() or None
    used_override = None
    checked = mode in _lib.ORGANISED_MODES and bool(current)
    if checked:
        _results, failed = _phase_exit_results(current, signoff)
        if failed and not override:
            print(f"\nrefused: the {current} exit check failed in {_lib.mode_label(mode)} mode. "
                  f"Fix what it names, or record why you leave anyway: "
                  f'statectl phase {target} --override "<why>"')
            _lib.print_verdict("STATE", False)
            return 1
        if failed:
            used_override = override
            print(f"\nOVERRIDE: leaving {current} over a failed exit check - {override}")
        elif override:
            print("exit check passed; --override was not needed and is not recorded")
    elif current:
        print(f"{_lib.mode_label(mode)}: no exit check (statectl mode guided-solo turns them on)")
    else:
        print("first phase: no exit check to run")
    if override and not checked:
        print("--override is not recorded: no exit check ran")
    _lib.journal_append("phase", value=target, override=used_override, signoff=signoff,
                        **{"from": current})
    refresh_all()
    print(f"phase: {current or 'unset'} -> {target}")
    for line in _lib.lifecycle_banner():
        print(line)
    _lib.print_verdict("STATE", True, warn=used_override is not None)
    return 0


# --------------------------------------------------------------------------- proposal box

def _next_proposal_id() -> str:
    """PR-<n>, counted from the store: one past the highest id ever added."""
    top = 0
    for ev in _lib.read_jsonl(_lib.proposals_path(), tolerant=True):
        m = re.fullmatch(r"PR-(\d+)", str(ev.get("id", "")))
        if m:
            top = max(top, int(m.group(1)))
    return f"PR-{top + 1}"


def cmd_proposal_add(args) -> int:
    text = (args.text or "").strip()
    if not text:
        print("a proposal needs text", file=sys.stderr)
        _lib.print_verdict("STATE", False)
        return 1
    pid = _next_proposal_id()
    _lib.append_jsonl(_lib.proposals_path(), {
        "ts": _lib.utc_now(), "op": "add", "id": pid, "text": text,
        "source": args.source, "kind": args.kind,
    }, durable=True)
    refresh_all()
    print(pid)
    _lib.print_verdict("STATE", True)
    return 0


def cmd_proposal_list(args) -> int:
    records = _lib.proposal_records()
    shown = records if args.all else [r for r in records if r["status"] == "open"]
    if not shown:
        print("no proposals" if args.all else "no open proposals")
    for r in shown:
        tail = f" - {r['note']}" if r["note"] else ""
        print(f"{r['id']} [{r['status']}] {r['kind']} · {r['source']}: {r['text']}{tail}")
    _lib.print_verdict("STATE", True)
    return 0


def cmd_proposal_resolve(args) -> int:
    records = {r["id"]: r for r in _lib.proposal_records()}
    rec = records.get(args.id)
    if rec is None:
        print(f"no such proposal: {args.id}", file=sys.stderr)
        _lib.print_verdict("STATE", False)
        return 1
    warn = rec["status"] != "open"
    if warn:
        print(f"WARNING: {args.id} was already {rec['status']}; recording {args.resolution} over it",
              file=sys.stderr)
    _lib.append_jsonl(_lib.proposals_path(), {
        "ts": _lib.utc_now(), "op": "resolve", "id": args.id,
        "resolution": args.resolution, "note": args.note,
    }, durable=True)
    refresh_all()
    _lib.print_verdict("STATE", True, warn)
    return 0


def cmd_milestone(args) -> int:
    _lib.journal_append("milestone", id=args.id, title=args.title, note=args.note)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_decision(args) -> int:
    _lib.journal_append("decision", text=args.text, why=args.why)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_loop(args) -> int:
    _lib.journal_append("loop", id=args.id, text=args.text, status=args.status)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_note(args) -> int:
    _lib.journal_append("note", text=args.text)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_device(args) -> int:
    """Name the running machine. The one writer of state/machine.json: the session-start
    hook only compares and nags, so a device change stays loud until this runs."""
    snap = _lib.machine_snapshot(args.alias)
    _lib.atomic_write_json(_lib.machine_state_path(), snap)
    _lib.journal_append("note", text=f"device named '{args.alias}' "
                                     f"(fp {snap['fingerprint']}, {snap['os']}/{snap['arch']})",
                        tags=["device-change"])
    refresh_all()
    print(f"device '{args.alias}' recorded (fp {snap['fingerprint']})")
    _lib.print_verdict("STATE", True)
    return 0


def cmd_intent(args) -> int:
    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files is not None else None
    warn = False
    if args.state == "done":
        # A `done` with no matching open `begin` is drift, not an error - the intent
        # bracket exists precisely to catch this, so surface it rather than swallow it.
        before = _build_session_projection()
        if not any(i["intent_id"] == args.id for i in before["open_intents"]):
            warn = True
            print(f"WARNING: intent {args.id!r} marked done with no matching open begin", file=sys.stderr)
    _lib.journal_append("intent", state=args.state, intent_id=args.id, op=args.op, files=files)
    refresh_all()
    _lib.print_verdict("STATE", True, warn)
    return 0


def cmd_gate(args) -> int:
    _lib.journal_append("gate", question=args.question, answer=args.answer, kind=args.kind)
    refresh_all()
    warn = args.answer is None  # a freshly-opened gate demands attention; that's the point of asking
    _lib.print_verdict("STATE", True, warn)
    return 0


def cmd_tooling(args) -> int:
    _lib.journal_append("tooling", change_type=args.change_type, what=args.what, evidence=args.evidence)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_need_open(args) -> int:
    """Open a queue item a human can actually act on cold.

    --context and --action are REQUIRED, mechanically: a queue whose rows read "pick a color"
    with no situation and no next step just teaches the human to ignore the queue. Context says
    what is going on and why it matters; action says the one concrete thing to do. The console
    renders both, plus a copy button that turns them into a paste-ready reply, so the human can
    answer in the terminal without reconstructing the situation first.
    """
    nid = _next_need_id()
    band = args.band or _lib.DEFAULT_BAND_BY_CATEGORY.get(args.category, "SEV2")
    warn = band in ("SEV0", "SEV1")
    context = (args.context or "").strip()
    action = (args.action or "").strip()
    if len(context) < 60:
        print(
            f"note: context is {len(context)} chars. A human reading this cold needs the "
            f"situation, why it matters, and what it blocks - one truncated line makes the "
            f"queue unusable. Consider `need amend {nid} --context ...` with the full picture.",
            file=sys.stderr,
        )
        warn = True
    _lib.append_jsonl(_needs_jsonl_path(), {
        "ts": _lib.utc_now(), "op": "open", "id": nid, "title": args.title, "category": args.category,
        "band": band, "blocks": args.blocks or 0, "note": args.note or "",
        "context": context, "action": action,
    }, durable=True)
    refresh_all()
    print(nid)
    _lib.print_verdict("STATE", True, warn)
    return 0


def cmd_need_amend(args) -> int:
    records = _load_need_records()
    rec = records.get(args.id)
    if rec is None:
        print(f"no such need: {args.id}", file=sys.stderr)
        _lib.print_verdict("STATE", False)
        return 1

    band_index = {b: i for i, b in enumerate(_lib.SEV_BANDS)}
    event = {"ts": _lib.utc_now(), "op": "amend", "id": args.id}
    warn = False
    if args.band is not None:
        current_i = band_index.get(rec["band"], len(_lib.SEV_BANDS))
        requested_i = band_index.get(args.band, len(_lib.SEV_BANDS))
        if requested_i > current_i:
            print(
                f"WARNING: refusing to de-escalate {args.id} from {rec['band']} to {args.band}; "
                f"keeping {rec['band']}",
                file=sys.stderr,
            )
            warn = True
        else:
            event["band"] = args.band
    if args.note is not None:
        event["note"] = args.note
    if args.context is not None:
        event["context"] = args.context
    if args.action is not None:
        event["action"] = args.action

    if len(event) > 3:  # more than just {ts, op, id}: the refusal left nothing to record
        _lib.append_jsonl(_needs_jsonl_path(), event, durable=True)
        refresh_all()
    _lib.print_verdict("STATE", True, warn)
    return 0


def cmd_need_resolve(args) -> int:
    records = _load_need_records()
    if args.id not in records:
        print(f"no such need: {args.id}", file=sys.stderr)
        _lib.print_verdict("STATE", False)
        return 1
    _lib.append_jsonl(_needs_jsonl_path(), {
        "ts": _lib.utc_now(), "op": "resolve", "id": args.id, "answer": args.answer or "",
    }, durable=True)
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_need_list(args) -> int:
    proj = _build_needs_projection()
    if not proj["tasks"]:
        print("no open needs-human tasks")
    for t in proj["tasks"]:
        print(f"{t['id']} [{t['band']}] {t['title']} ({t['age_days']}d) - {t['note']}")
    sev0 = proj["counts"]["by_band"].get("SEV0", 0)
    _lib.print_verdict("STATE", True, sev0 > 0)
    return 0


def cmd_refresh(args) -> int:
    refresh_all()
    _lib.print_verdict("STATE", True)
    return 0


def cmd_resume(args) -> int:
    proj = refresh_all()
    needs_proj = _build_needs_projection()
    events = _lib.journal_read(tolerant=True)

    lines = ["=== SESSION CONTINUITY ===", f"Resume: {proj['resume_pointer'] or '(no pointer set)'}"]

    loops = proj["open_loops"]
    lines.append(f"Open loops: {len(loops)}")
    lines += [f"  - {l['id']}: {l['text']}" for l in loops[:3]]

    intents = proj["open_intents"]
    if intents:
        lines.append(f"[!] {len(intents)} open intent(s) - unfinished, possible crash:")
        lines += [f"  - {i['intent_id']} op={i['op'] or '?'} started {_human_age(i['ts'])}" for i in intents[:3]]

    sev0 = needs_proj["counts"]["by_band"].get("SEV0", 0)
    sev1 = needs_proj["counts"]["by_band"].get("SEV1", 0)
    if sev0 or sev1:
        lines.append(f"Needs-human: SEV0={sev0} SEV1={sev1}")

    lines.append("Last events:")
    lines += [f"  {ev.get('ts', '')} {ev.get('action', '')}" for ev in events[-3:]]

    hb = proj["heartbeat"]
    if hb:
        suffix = f" - {hb['note']}" if hb.get("note") else ""
        lines.append(f"Heartbeat: {_human_age(hb['ts'])}{suffix}")
    else:
        lines.append("Heartbeat: never")

    print("\n".join(lines))
    _lib.print_verdict("STATE", True, bool(intents) or sev0 > 0)
    return 0


def cmd_status(args) -> int:
    proj = refresh_all()
    needs_proj = _build_needs_projection()

    lines = ["=== STATE STATUS ==="]
    sess = proj["session"]
    lines.append(f"Session: {sess['id']} started={sess['started']}")
    lines.append(f"Mode: {_lib.mode_label(sess['mode'])}  Phase: {sess['phase'] or 'unset'}"
                 + (f" (since {sess['phase_since']})" if sess["phase"] and sess["phase_since"] else "")
                 + f"  Open proposals: {proj['counts']['proposals_open']}")
    lines.append(f"Resume pointer: {proj['resume_pointer'] or '(none)'}")
    c = proj["counts"]
    lines.append(
        f"Events: {c['events']}  Tasks: {c['tasks_done']}/{c['tasks_total']} done  "
        f"Open loops: {c['open_loops']}  Milestones: {c['milestones']}  Decisions: {c['decisions']}"
    )
    if proj["open_loops"]:
        lines.append("Open loops:")
        lines += [f"  - {l['id']}: {l['text']}" for l in proj["open_loops"]]
    if proj["open_intents"]:
        lines.append("Open intents (unfinished):")
        lines += [f"  - {i['intent_id']} op={i['op'] or '?'} started {_human_age(i['ts'])}" for i in proj["open_intents"]]
    if proj["open_gates"]:
        lines.append("Open gates:")
        lines += [f"  - [{g['kind'] or '?'}] {g['question']}" for g in proj["open_gates"]]
    by_band = needs_proj["counts"]["by_band"]
    lines.append(
        f"Needs-human: open={needs_proj['counts']['open']} resolved={needs_proj['counts']['resolved']} "
        f"(SEV0={by_band.get('SEV0', 0)} SEV1={by_band.get('SEV1', 0)} "
        f"SEV2={by_band.get('SEV2', 0)} SEV3={by_band.get('SEV3', 0)})"
    )
    hb = proj["heartbeat"]
    lines.append(f"Heartbeat: {_human_age(hb['ts']) if hb else 'never'}")

    print("\n".join(lines))
    sev0 = by_band.get("SEV0", 0)
    _lib.print_verdict("STATE", True, bool(proj["open_intents"]) or sev0 > 0)
    return 0


def cmd_progress(args) -> int:
    """The progress model (tools/progress.py, the one computation the console and the periodic
    report share): a compact text block, or --json with the verdict on stderr so stdout stays
    pure JSON. READ-ONLY: no journal event, no projection rebuilt. Never crashes on a console
    that cannot encode the block bars or a title: ASCII bars, unencodable characters as '?'."""
    import json
    import progress
    model = progress.compute()
    if args.json:
        print(json.dumps(model, indent=2))
        print("STATE_OK", file=sys.stderr)
        return 0
    text = progress.render_text(model, unicode=progress.stream_supports_unicode(sys.stdout))
    print(progress.safe_text(text, sys.stdout))
    _lib.print_verdict("STATE", True)
    return 0


# --------------------------------------------------------------------------- argparse

def _mode_arg(text: str) -> str:
    value = _lib.normalize_mode(text)
    if value is None:
        raise argparse.ArgumentTypeError(
            f"unknown mode {text!r}: one of {', '.join(_lib.MODES)} "
            f"(aliases: {', '.join(sorted(_lib.MODE_ALIASES))})")
    return value


def _source_arg(text: str) -> str:
    value = str(text or "").strip()
    if not _lib.PROPOSAL_SOURCE_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"bad source {text!r}: issue#<N>, human, or agent:<name>")
    return value


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="statectl", description="continuity engine: journal in, projections out")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="open a working session")
    sp.add_argument("--session", required=True)
    sp.add_argument("--phase", help="legacy free-text label; the lifecycle phase is `statectl phase`")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("pointer", help="set the resume pointer")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_pointer)

    sp = sub.add_parser("task", help="record task state (partial update)")
    sp.add_argument("id")
    sp.add_argument("--title")
    sp.add_argument("--status", choices=_lib.TASK_STATUSES)
    sp.add_argument("--deps", help="comma-separated task ids")
    sp.add_argument("--milestone", help="the milestone this task belongs to (\"\" unlinks it)")
    sp.add_argument("--note")
    sp.add_argument("--no-envelope", dest="no_envelope",
                    help="fableous-orchestrated: why this task is done without a builder's "
                         "handoff envelope (logged); without it, --status done needs a valid one")
    sp.set_defaults(func=cmd_task)

    lead_only = ("LEAD ONLY (main session): it runs git, which the policy gate denies to "
                 "sub-agents. ")
    sp = sub.add_parser("dispatch", help="for a builder: cut its worktree from HEAD, write the "
                                         "stub, print the filled brief; other agents: the stub "
                                         "only (state/handshakes/<id>.stub.json)",
                        description=lead_only + cmd_dispatch.__doc__.split("\n\n")[0])
    sp.add_argument("id", help="the task id; the agent's envelope will be <id>.json")
    sp.add_argument("--agent", default="builder", help="the agent type dispatched (default builder)")
    sp.add_argument("--worktree", help="the worktree the agent works in (a builder's default: "
                                       ".claude/worktrees/<id lowercased>, branch wt/<same>)")
    sp.add_argument("--no-worktree", dest="no_worktree", action="store_true",
                    help="create nothing: record --worktree (one made by hand) in the stub")
    sp.set_defaults(func=cmd_dispatch)

    sp = sub.add_parser("accept", help="validate a builder's handoff in its worktree, commit it "
                                       "there, merge it --no-ff, remove the worktree",
                        description=lead_only + cmd_accept.__doc__.split("\n\n")[0])
    sp.add_argument("id", help="the task id the builder was dispatched for")
    sp.add_argument("--no-run", dest="no_run", action="store_true",
                    help="validate the envelope without rerunning its tests")
    sp.add_argument("--message", help="the worktree commit's subject (default: <id>: <task title>)")
    sp.add_argument("--worktree", help="the worktree to accept (default: the stub's, else "
                                       ".claude/worktrees/<id lowercased>)")
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("mode", help="how organised the work is: "
                                     + " | ".join(_lib.MODES) + " (aliases: "
                                     + ", ".join(sorted(_lib.MODE_ALIASES)) + ")")
    sp.add_argument("value", type=_mode_arg)
    sp.set_defaults(func=cmd_mode)

    sp = sub.add_parser("phase", help="move the lifecycle phase; organised modes run the current "
                                      "phase's exit check first")
    sp.add_argument("value", choices=_lib.LIFECYCLE_PHASES)
    sp.add_argument("--override", help="why you leave over a failed exit check (logged)")
    sp.add_argument("--signoff", help="the human's sign-off text (the review exit needs one)")
    sp.set_defaults(func=cmd_phase)

    proposal = sub.add_parser("proposal", help="the proposal box: ideas out of scope right now")
    proposal_sub = proposal.add_subparsers(dest="proposal_cmd", required=True)

    sp = proposal_sub.add_parser("add")
    sp.add_argument("text")
    sp.add_argument("--source", required=True, type=_source_arg,
                    help="issue#<N> | human | agent:<name>")
    sp.add_argument("--kind", choices=_lib.PROPOSAL_KINDS, default="feature")
    sp.set_defaults(func=cmd_proposal_add)

    sp = proposal_sub.add_parser("list")
    sp.add_argument("--all", action="store_true", help="include resolved proposals")
    sp.set_defaults(func=cmd_proposal_list)

    sp = proposal_sub.add_parser("resolve")
    sp.add_argument("id")
    sp.add_argument("--as", dest="resolution", required=True, choices=_lib.PROPOSAL_RESOLUTIONS)
    sp.add_argument("--note", required=True, help="why: where it was planned, or why not")
    sp.set_defaults(func=cmd_proposal_resolve)

    sp = sub.add_parser("milestone", help="record something shipped")
    sp.add_argument("id")
    sp.add_argument("--title", required=True)
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_milestone)

    sp = sub.add_parser("decision", help="record a choice and its reason")
    sp.add_argument("text")
    sp.add_argument("--why", required=True)
    sp.set_defaults(func=cmd_decision)

    sp = sub.add_parser("loop", help="open/close/update a thread (partial update)")
    sp.add_argument("id")
    sp.add_argument("--text")
    sp.add_argument("--status", choices=_lib.LOOP_STATUSES)
    sp.set_defaults(func=cmd_loop)

    sp = sub.add_parser("note", help="free narration")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_note)

    sp = sub.add_parser("intent", help="write-ahead bracket for a composite op")
    sp.add_argument("state", choices=("begin", "done"))
    sp.add_argument("--id", required=True)
    sp.add_argument("--op")
    sp.add_argument("--files", help="comma-separated file list")
    sp.set_defaults(func=cmd_intent)

    sp = sub.add_parser("gate", help="ask or answer a human gate")
    sp.add_argument("--question", required=True)
    sp.add_argument("--answer")
    sp.add_argument("--kind", choices=("blocking", "checkpoint", "fyi"))
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("tooling", help="record a .claude system change")
    sp.add_argument("--change-type", required=True)
    sp.add_argument("--what", required=True)
    sp.add_argument("--evidence")
    sp.set_defaults(func=cmd_tooling)

    need = sub.add_parser("need", help="the needs-human queue")
    need_sub = need.add_subparsers(dest="need_cmd", required=True)

    sp = need_sub.add_parser("open")
    sp.add_argument("--title", required=True)
    sp.add_argument("--category", required=True, choices=_lib.NEEDS_HUMAN_CATEGORIES)
    sp.add_argument("--context", required=True,
                    help="the situation in plain language: what is going on, why it matters, "
                         "what it blocks. Written for a human reading it cold.")
    sp.add_argument("--action", required=True,
                    help="the one concrete thing the human should do")
    sp.add_argument("--band", choices=_lib.SEV_BANDS)
    sp.add_argument("--blocks", type=int)
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_need_open)

    sp = need_sub.add_parser("amend")
    sp.add_argument("id")
    sp.add_argument("--band", choices=_lib.SEV_BANDS)
    sp.add_argument("--note")
    sp.add_argument("--context")
    sp.add_argument("--action")
    sp.set_defaults(func=cmd_need_amend)

    sp = need_sub.add_parser("resolve")
    sp.add_argument("id")
    sp.add_argument("--answer")
    sp.set_defaults(func=cmd_need_resolve)

    sp = need_sub.add_parser("list")
    sp.set_defaults(func=cmd_need_list)

    sp = sub.add_parser("device", help="name this machine in state/machine.json")
    sp.add_argument("alias")
    sp.set_defaults(func=cmd_device)

    sp = sub.add_parser("refresh", help="rebuild all three projections")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("resume", help="print the orientation block")
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("status", help="one-screen summary")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("progress", help="the current milestone at a glance: tasks, checklists, "
                                         "agents in flight, needs-human (read-only)")
    sp.add_argument("--json", action="store_true", help="the progress model as JSON")
    sp.set_defaults(func=cmd_progress)

    return p


def main(argv: list) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) == "need" and getattr(args, "need_cmd", None) == "amend":
        if args.band is None and args.note is None and args.context is None and args.action is None:
            parser.error("need amend requires at least one of --band, --note, --context, --action")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
