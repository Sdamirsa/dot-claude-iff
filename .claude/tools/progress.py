#!/usr/bin/env python3
"""progress.py - the progress model: one computation, three renderings.

During a run that lasts hours the maintainer must see at a glance how far the work is, what is
in flight and what waits on them, without asking. compute() folds that from the sources of
truth (the journal, the task files, the needs-human and proposal stores, the dispatch stubs and
envelopes, the heartbeat) into ONE dict. `statectl progress` prints it as text or `--json`, the
console renders the same dict as the NOW tab's Progress panel (payload key `progress`), and the
periodic report (_lib.progress_report) hands the text block to the lead. Two computations is how
the chat and the console would come to disagree about the number.

The number: `totals.percent` is checked Plan items over all Plan items across the current
milestone's task files. A task marked done counts all its items as checked (the status breaks
the tie with a checklist nobody ticked), and a task with no checklist, or no task file, counts as
one item, checked when done. Both renderings say what it counts (`totals.basis`).

Reused, never re-derived: the current milestone and the journal-task to task-file match are
checkctl's (the rule the phase exit checks apply), the task and needs-human folds are statectl's
projector, mode and phase are _lib.fold_lifecycle, envelope validity is _lib.validate_envelope.

READ-ONLY by contract and stdlib only: it runs on every live console poll and inside a hook.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

BAR_WIDTH = 20
MINI_BAR_WIDTH = 10
TITLE_MAX = 44
IDS_MAX = 72
NEEDS_SHOWN = 5
AGENTS_SHOWN = 4
LABEL_WIDTH = 14
GROUPS = ("done", "in progress", "waiting")
BASIS = ("plan items checked across the milestone's task files; a done task counts all its "
         "items, a task with no checklist counts as one item")
# Fields that move with the clock alone. The console's build gate compares payloads without
# them, so an unchanged project does not rewrite console.html on every ritual.
CLOCK_KEYS = ("as_of", "age", "elapsed")
# Probed against the output stream: a console that cannot encode these gets the ASCII form.
UNICODE_PROBE = "█░·"

_PLAN_RE = re.compile(r"^##[ \t]+Plan[ \t]*\r?$(.*?)(?=^##[ \t]|\Z)", re.MULTILINE | re.DOTALL)
_BOX_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[([ xX])\]", re.MULTILINE)


def _now() -> float:
    """The model's clock; one call per compute() so every age in a model agrees."""
    return time.time()


# --------------------------------------------------------------------------- small readers

def plan_checklist(text: str) -> tuple:
    """(checked, total) checkbox items under a task file's `## Plan` heading, sub-headings
    included; (0, 0) when the file has no Plan section or no checkbox in it."""
    m = _PLAN_RE.search(text or "")
    if not m:
        return 0, 0
    boxes = _BOX_RE.findall(m.group(1))
    return sum(1 for b in boxes if b in "xX"), len(boxes)


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _age(ts, now: float):
    when = _lib._epoch(ts)
    return None if when is None else int(max(0.0, now - when))


def _natural(task_id: str) -> list:
    """T2 before T10: digits compare as numbers."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.casefold())
            for p in re.split(r"(\d+)", str(task_id)) if p]


def stub_states(statuses: dict, now: float) -> list:
    """Every dispatch stub in state/handshakes/, with whether its agent is still in flight: the
    task is not done and no valid envelope (a builder's for a builder stub) was delivered, in the
    project or any worktree, since the dispatch. `statuses` maps casefolded task id to status."""
    hs = _lib.handshakes_dir()
    out = []
    if not hs.is_dir():
        return out
    for path in sorted(hs.glob("*.stub.json")):
        stub = _lib.read_stub(path)
        dispatched = _lib._epoch(stub["dispatched_at"])
        if dispatched is None:
            try:
                dispatched = path.stat().st_mtime
            except OSError:
                dispatched = None
        delivered = _lib._delivered_at(stub["task_id"], stub["worktree"],
                                       builder=stub["agent"] == "builder")
        answered = delivered is not None and (
            dispatched is None or delivered >= dispatched - _lib.STOP_SLACK_SECONDS)
        out.append({
            "task": stub["task_id"], "agent": stub["agent"] or "agent",
            "worktree": stub["worktree"], "dispatched_at": stub["dispatched_at"],
            "elapsed": int(max(0.0, now - dispatched)) if dispatched is not None else None,
            "in_flight": statuses.get(stub["task_id"].casefold()) != "done" and not answered,
        })
    return out


def _task_file(journal_task: dict, files: list, checkctl):
    """The task file the phase exits would pair with this journal task (checkctl's match: the
    file stem, or the id leading the file's title), else None."""
    one = {str(journal_task["id"]).casefold(): journal_task}
    return next((f for f in files if checkctl._match_journal_task(f, one) is not None), None)


def _last_activity(events: list, now: float) -> dict:
    """The newest sign of life: the heartbeat (a working pulse or the Stop hook's turn ended)
    or the latest journal event, whichever is later; the heartbeat wins a tie."""
    candidates = []
    beat = _lib.read_json(_lib.heartbeat_path(), None)
    if isinstance(beat, dict) and _lib._epoch(beat.get("ts")) is not None:
        note = str(beat.get("note") or "heartbeat")
        if note == _lib.PULSE_NOTE and beat.get("via"):
            note = f"{note} ({beat['via']})"
        candidates.append((str(beat["ts"]), note, "heartbeat"))
    last = next((ev for ev in reversed(events) if _lib._epoch(ev.get("ts")) is not None), None)
    if last is not None:
        what = str(last.get("action") or "event")
        if last.get("id"):
            what += f" {last['id']}"
        candidates.append((str(last["ts"]), f"journal: {what}", "journal"))
    if not candidates:
        return {"ts": None, "age": None, "note": None, "source": None}
    ts, note, source = max(candidates, key=lambda c: _lib._epoch(c[0]))
    return {"ts": ts, "age": _age(ts, now), "note": note, "source": source}


def group_of(task: dict) -> str:
    if task["status"] == "done":
        return "done"
    if task["status"] == "doing" or task.get("in_flight"):
        return "in progress"
    return "waiting"


# --------------------------------------------------------------------------- the model

def empty_totals() -> dict:
    return {"tasks_done": 0, "tasks_total": 0, "checklist_done": 0, "checklist_total": 0,
            "percent": 0, "basis": BASIS}


def compute() -> dict:
    """THE progress model. Never raises on a missing or malformed source: each degrades to an
    empty value, and no milestone or no tasks under it sets `empty` to an honest one-liner."""
    import checkctl  # the milestone rule and the task-file match the phase exits use
    import statectl  # the projector: the task and needs-human folds

    now = _now()
    events = _lib.journal_read(tolerant=True)
    life = _lib.fold_lifecycle(events)
    proj = statectl._build_session_projection()
    statuses = {str(t["id"]).casefold(): t.get("status") or "todo" for t in proj["tasks"]}
    flight = [s for s in stub_states(statuses, now) if s["in_flight"]]
    needs = [{"id": str(n.get("id", "")), "title": str(n.get("title", "")),
              "band": str(n.get("band", "")), "age": _age(n.get("opened"), now)}
             for n in statectl._build_needs_projection()["tasks"]]
    model = {
        "as_of": _iso(now),
        "mode": life["mode"],
        "mode_label": _lib.mode_label(life["mode"]),
        "phase": life["phase"],
        "milestone": None,
        "tasks": [],
        "totals": empty_totals(),
        "needs_human": needs,
        "proposals_open": sum(1 for p in _lib.proposal_records() if p["status"] == "open"),
        "agents_in_flight": [{k: s[k] for k in ("task", "agent", "worktree", "elapsed",
                                                "dispatched_at")} for s in flight],
        "run": {"started": None, "elapsed": None},
        "last_activity": _last_activity(events, now),
        "empty": None,
    }

    mid = checkctl.current_milestone()
    marks = [ev for ev in events if ev.get("action") == "milestone" and str(ev.get("id")) == mid]
    started = life["phase_since"] if life["phase"] else (marks[-1].get("ts") if marks else None)
    model["run"] = {"started": started, "elapsed": _age(started, now)}
    if not mid:
        model["empty"] = ('no milestone yet: python3 .claude/tools/statectl.py milestone <id> '
                          '--title "<what this body of work ships>"')
        return model
    model["milestone"] = {"id": mid, "title": str(marks[-1].get("title") or "") if marks else ""}
    journal_tasks = sorted((t for t in proj["tasks"] if t.get("milestone") == mid),
                           key=lambda t: _natural(t["id"]))
    if not journal_tasks:
        model["empty"] = (f"milestone {mid} has no tasks yet: python3 .claude/tools/statectl.py "
                          f"task <id> --milestone {mid}")
        return model

    files = checkctl.task_files(include_archive=True)
    by_task = {s["task"].casefold(): s for s in flight}
    totals = model["totals"]
    for jt in journal_tasks:
        tid = str(jt["id"])
        status = jt.get("status") or "todo"
        tf = _task_file(jt, files, checkctl)
        title = str(jt.get("title") or "")
        checked = total = 0
        if tf is not None:
            m = checkctl._TITLE_ID_RE.match(tf["title"])
            title = tf["title"][m.end():] if m else tf["title"]
            try:
                checked, total = plan_checklist(tf["path"].read_text(encoding="utf-8", errors="replace"))
            except OSError:
                checked = total = 0
        agent = by_task.get(tid.casefold())
        task = {
            "id": tid, "title": title, "status": status,
            "file": _lib.rel(tf["path"]) if tf is not None else None,
            "checklist_done": checked, "checklist_total": total,
            "in_flight": ({k: agent[k] for k in ("agent", "worktree", "elapsed", "dispatched_at")}
                          if agent else None),
            "has_valid_envelope": _lib._delivered_at(tid) is not None,
        }
        task["group"] = group_of(task)
        model["tasks"].append(task)
        totals["tasks_total"] += 1
        totals["tasks_done"] += int(status == "done")
        units = total or 1
        totals["checklist_total"] += units
        totals["checklist_done"] += units if status == "done" else (checked if total else 0)
    if totals["checklist_total"]:
        totals["percent"] = int(100 * totals["checklist_done"] / totals["checklist_total"])
    return model


def without_clock(node):
    """The model minus every field that moves with the clock alone (CLOCK_KEYS)."""
    if isinstance(node, dict):
        return {k: without_clock(v) for k, v in node.items() if k not in CLOCK_KEYS}
    if isinstance(node, list):
        return [without_clock(v) for v in node]
    return node


# --------------------------------------------------------------------------- the text block

def fmt_duration(seconds) -> str:
    """45s · 12m · 3h05m · 2d03h; ? when unknown."""
    if seconds is None:
        return "?"
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    minutes = s // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h{minutes % 60:02d}m"
    return f"{hours // 24}d{hours % 24:02d}h"


def bar(done: int, total: int, width: int, unicode: bool = False) -> str:
    """[#####-----], or the block form; full only when done reaches total."""
    filled = int(width * done / total) if total else 0
    filled = max(0, min(width, filled))
    on, off = ("█", "░") if unicode else ("#", "-")
    return "[" + on * filled + off * (width - filled) + "]"


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _ids(tasks: list) -> str:
    words = [t["id"] + ("(blocked)" if t["status"] == "blocked" else "") for t in tasks]
    shown, used = [], 0
    for k, word in enumerate(words):
        if used + len(word) + 1 > IDS_MAX and shown:
            shown.append(f"+{len(words) - k} more")
            break
        shown.append(word)
        used += len(word) + 1
    return " ".join(shown)


def render_text(model: dict, unicode: bool = False) -> str:
    """The compact block for a chat message or a terminal: done / in progress / waiting, a
    NEEDS YOU section only when something is open, one line for agents in flight, one for the
    last activity. Plain ASCII unless `unicode` (block bars, middle-dot separators); no colour."""
    sep = " · " if unicode else " | "
    lines = []

    def row(label: str, text: str) -> None:
        lines.append(f"{label:<{LABEL_WIDTH}}{text}".rstrip())

    ms = model.get("milestone")
    dials = [model.get("mode_label") or "Freestyle", f"phase {model.get('phase') or 'unset'}"]
    run = model.get("run") or {}
    if run.get("started"):
        dials.append(f"run {fmt_duration(run.get('elapsed'))}")
    dials.append(f"proposals open {model.get('proposals_open', 0)}")
    if model.get("empty") or not ms:
        lines.append(f"PROGRESS: {model.get('empty') or 'nothing to measure yet'}")
        lines.append(sep.join(dials))
    else:
        lines.append(_clip(f"PROGRESS {ms['id']}" + (f" - {ms['title']}" if ms.get("title") else ""),
                           96))
        t = model["totals"]
        lines.append(f"{bar(t['checklist_done'], t['checklist_total'], BAR_WIDTH, unicode)} "
                     f"{t['percent']}% of plan items checked "
                     f"({t['checklist_done']}/{t['checklist_total']}){sep}"
                     f"tasks {t['tasks_done']}/{t['tasks_total']} done")
        lines.append(sep.join(dials))
        groups = {g: [x for x in model["tasks"] if x.get("group") == g] for g in GROUPS}
        if groups["done"]:
            row(f"done ({len(groups['done'])})", _ids(groups["done"]))
        for k, task in enumerate(groups["in progress"]):
            bits = [task["id"], bar(task["checklist_done"], task["checklist_total"], MINI_BAR_WIDTH,
                                    unicode)
                    + (f" {task['checklist_done']}/{task['checklist_total']}"
                       if task["checklist_total"] else " no checklist")]
            flight = task.get("in_flight")
            if flight:
                bits.append(f"{flight['agent']} {fmt_duration(flight.get('elapsed'))}")
            elif task.get("has_valid_envelope"):
                bits.append("handoff ready")
            bits.append("- " + _clip(task["title"], TITLE_MAX))
            row("in progress" if k == 0 else "", " ".join(bits))
        if groups["waiting"]:
            row(f"waiting ({len(groups['waiting'])})", _ids(groups["waiting"]))
    needs = model.get("needs_human") or []
    for k, item in enumerate(needs[:NEEDS_SHOWN]):
        row(f"NEEDS YOU ({len(needs)})" if k == 0 else "",
            f"[{item['band'] or '?'}] {_clip(item['title'], TITLE_MAX)} - {item['id']}, "
            f"{fmt_duration(item.get('age'))} open")
    if len(needs) > NEEDS_SHOWN:
        row("", f"+{len(needs) - NEEDS_SHOWN} more: python3 .claude/tools/statectl.py need list")
    agents = model.get("agents_in_flight") or []
    if agents:
        shown = [f"{a['agent']} {a['task']} {fmt_duration(a.get('elapsed'))}"
                 for a in agents[:AGENTS_SHOWN]]
        if len(agents) > AGENTS_SHOWN:
            shown.append(f"+{len(agents) - AGENTS_SHOWN} more")
        row("agents", sep.join(shown))
    last = model.get("last_activity") or {}
    row("last activity", f"{fmt_duration(last.get('age'))} ago - {last.get('note')}"
        if last.get("ts") else "none recorded yet")
    return "\n".join(lines)


def stream_supports_unicode(stream) -> bool:
    """Whether `stream` can encode the block bars (a Windows console on cp1252 cannot)."""
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        UNICODE_PROBE.encode(encoding)
        return True
    except (UnicodeError, LookupError):
        return False


def safe_text(text: str, stream) -> str:
    """`text` as `stream` can print it: characters its encoding lacks (a title's em dash on an
    ASCII console) become '?' instead of raising UnicodeEncodeError."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except LookupError:
        return text.encode("ascii", errors="replace").decode("ascii")
