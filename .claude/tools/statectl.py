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

    session = {"id": None, "phase": None, "started": None}
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
            session = {"id": ev.get("session"), "phase": ev.get("phase"), "started": ts}

        elif action == "pointer":
            resume_pointer = ev.get("text")

        elif action == "task":
            tid = ev.get("id")
            if not tid:
                continue
            rec = tasks.setdefault(tid, {"id": tid, "title": "", "status": "todo", "deps": [], "ts": ts})
            for key in ("title", "status", "deps"):
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

        elif action == "config":
            pass  # config-change events are audit trail only - no dedicated session.json slot

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

        # Every action in _lib.JOURNAL_ACTIONS has a branch above (even if the branch is a
        # deliberate no-op, per the comments). An action reaching none of them is a projector
        # bug, not a new feature - see test_all_actions_projected.

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

def _render_handoff(proj: dict, needs_proj: dict) -> str:
    lines = [
        "<!-- AUTO-GENERATED by `statectl.py refresh` - a projection of state/journal.jsonl "
        "and state/needs-human.jsonl. Do not hand-edit; edits are overwritten on the next refresh. -->",
        f"<!-- generated_at: {proj['generated_at']} -->",
        "",
        "# Handoff",
        "",
        "▶ Resume here",
        "",
        f"> {proj['resume_pointer'] or '(no pointer set)'}",
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
        lines += ["| id | title | status |", "|---|---|---|"]
        lines += [f"| {t['id']} | {t['title']} | {t['status']} |" for t in proj["tasks"]]
    else:
        lines.append("_no tasks tracked_")

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

    _write_gated(_handoff_path(), _render_handoff(proj, needs_proj), durable=False)
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


def cmd_task(args) -> int:
    deps = [d.strip() for d in args.deps.split(",") if d.strip()] if args.deps is not None else None
    _lib.journal_append("task", id=args.id, title=args.title, status=args.status, deps=deps, note=args.note)
    refresh_all()
    _lib.print_verdict("STATE", True)
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
    lines.append(f"Session: {proj['session']['id']} phase={proj['session']['phase']} started={proj['session']['started']}")
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


# --------------------------------------------------------------------------- argparse

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="statectl", description="continuity engine: journal in, projections out")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="open a working session")
    sp.add_argument("--session", required=True)
    sp.add_argument("--phase")
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
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_task)

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

    sp = sub.add_parser("refresh", help="rebuild all three projections")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("resume", help="print the orientation block")
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("status", help="one-screen summary")
    sp.set_defaults(func=cmd_status)

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
