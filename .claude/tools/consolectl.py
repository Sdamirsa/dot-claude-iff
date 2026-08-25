#!/usr/bin/env python3
"""consolectl.py - build the console payload, render it into console.html, or delegate to
the live server.

`payload()` is the ONE function that builds the console's data. The static `build` command
injects its output into console.template.html; the live server (console.py) calls the SAME
function per request (with live=True). Live and committed views can therefore never drift
in SHAPE - only in freshness, which the payload itself labels via `freshness` and per-source
timestamps.

Every reader here tolerates a missing or malformed source file and degrades to nulls/zeros:
the console must render on a brand-new install that has nothing but shipped configs.

Stdlib only, forever - see _lib.py's module docstring for why.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _lib

SCHEMA_VERSION = 1
TEMPLATE_TOKEN = "__CONSOLE_DATA__"
TOKEN_CLASSES = ("input", "output", "cache_read", "cache_creation")

# --------------------------------------------------------------------------- now

def _read_heartbeat() -> tuple[dict, float | None]:
    hb = _lib.read_json(_lib.state_dir() / "heartbeat.json", None) or {}
    ts = hb.get("ts") if isinstance(hb.get("ts"), str) else None
    note = hb.get("note") if isinstance(hb.get("note"), str) else None
    age = _lib.age_seconds(ts) if ts else None
    return {"ts": ts, "note": note}, age


def _read_session() -> tuple[str | None, str | None, str | None, list]:
    # session.json is statectl.py's projection: {..., "session": {"id","phase","started"},
    # "resume_pointer", "open_loops": [{"id","text","ts"}], ...}. Read through THAT shape,
    # not a guessed flat one - see obsctl.py's ROLLUP_CONTRACT comment for why a console
    # reader that guesses at a producer's key names is exactly the bug class this system
    # exists to prevent.
    sess = _lib.read_json(_lib.state_dir() / "session.json", None) or {}
    session_obj = sess.get("session") if isinstance(sess.get("session"), dict) else {}
    resume_pointer = sess.get("resume_pointer")
    phase = session_obj.get("phase")
    session_id = session_obj.get("id")
    loops = []
    for item in sess.get("open_loops") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "closed":
            continue
        loops.append({"id": str(item.get("id", "")), "text": str(item.get("text", ""))})
    return resume_pointer, phase, session_id, loops


def _read_needs_human() -> dict:
    # needs-human.json is statectl.py's projection: {"counts": {"open", "by_band"},
    # "tasks": [{"id","title","category","band",...,"status":"open"}], "resolved_recent"}.
    # `tasks` is already band-first-then-oldest sorted by the projector, so it doubles
    # directly as the payload's "top" list - no re-deriving that ordering here.
    raw = _lib.read_json(_lib.state_dir() / "needs-human.json", None) or {}
    counts = raw.get("counts") if isinstance(raw.get("counts"), dict) else {}
    by_band_raw = counts.get("by_band") if isinstance(counts.get("by_band"), dict) else {}
    by_band = {band: int(by_band_raw.get(band, 0) or 0) for band in _lib.SEV_BANDS}
    open_count = int(counts.get("open", 0) or 0)

    top = []
    for it in (raw.get("tasks") or [])[:8]:
        if not isinstance(it, dict):
            continue
        item = {
            "id": str(it.get("id", "")),
            "title": str(it.get("title", "")),
            "band": str(it.get("band", "")),
            "category": str(it.get("category", "")),
            "context": str(it.get("context", "")),
            "action": str(it.get("action", "")),
            "note": str(it.get("note", "")),
        }
        # The copy-context bridge, human -> agent: one click copies this, the human appends
        # their answer and pastes it into the Claude Code terminal, and the agent receives the
        # full situation with the reply instead of a bare "yes" it has to re-anchor. Built
        # server-side so the paste text and the panel can never disagree.
        item["reply_template"] = (
            f"Answering {item['id']} ({item['category']}, {item['band']}): {item['title']}\n"
            + (f"Context: {item['context']}\n" if item["context"] else "")
            + (f"You asked me to: {item['action']}\n" if item["action"] else "")
            + (f"Note: {item['note']}\n" if item["note"] else "")
            + "My answer: "
        )
        top.append(item)
    return {"open": open_count, "by_band": by_band, "top": top}


def _journal_summary(ev: dict) -> str:
    action = ev.get("action")
    if action == "pointer":
        return str(ev.get("text", ""))
    if action == "task":
        return f"{ev.get('id', '')} · {ev.get('title', '')} · {ev.get('status', '')}"
    if action == "milestone":
        return str(ev.get("title", ""))
    if action == "decision":
        return str(ev.get("text", ""))
    if action == "note":
        return str(ev.get("text", ""))
    if action == "tooling":
        return str(ev.get("what", ""))
    if action == "gate":
        return str(ev.get("question", ""))
    for key in ("text", "title", "note", "what", "question"):
        if ev.get(key):
            return str(ev.get(key))
    return str(action or "")


def _read_journal_tail() -> list:
    out = []
    for ev in _lib.tail_jsonl(_lib.journal_path(), 8):
        out.append({
            "ts": str(ev.get("ts", "")),
            "action": str(ev.get("action", "")),
            "summary": _journal_summary(ev),
        })
    return out


def _read_in_flight() -> list:
    hs_dir = _lib.state_dir() / "handshakes"
    out = []
    if not hs_dir.is_dir():
        return out
    for stub in sorted(hs_dir.glob("*.stub.json")):
        task_id = stub.name[: -len(".stub.json")]
        envelope = hs_dir / f"{task_id}.json"
        if envelope.exists():
            continue
        data = _lib.read_json(stub, {}) or {}
        # The envelope contract (post-write-validate.sh, handshake.md) is
        # {agent_id, task_id, status, artifacts[], notes}; a stub is exempt from that
        # contract's required-keys check but conventionally carries the same field names.
        out.append({
            "agent": str(data.get("agent_id") or data.get("agent") or ""),
            "task_id": str(data.get("task_id") or task_id),
            "since": str(data.get("since") or data.get("ts") or ""),
        })
    return out


def _read_now() -> dict:
    heartbeat, heartbeat_age = _read_heartbeat()
    resume_pointer, phase, session_id, open_loops = _read_session()
    return {
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": heartbeat_age,
        "resume_pointer": resume_pointer,
        "phase": phase,
        "session_id": session_id,
        "open_loops": open_loops,
        "in_flight": _read_in_flight(),
        "needs_human": _read_needs_human(),
        "journal_tail": _read_journal_tail(),
    }


# --------------------------------------------------------------------------- tokens

def _read_tokens() -> dict:
    rollup_dir = _lib.iff_dir() / "obs" / "rollups"
    rollups = []
    if rollup_dir.is_dir():
        for f in sorted(rollup_dir.glob("*.json")):
            d = _lib.read_json(f, None)
            if isinstance(d, dict):
                rollups.append(d)

    zero = {k: 0 for k in TOKEN_CLASSES}
    total = dict(zero)
    today_totals = dict(zero)
    today_str = _lib.today()
    as_of = None
    unknown_models: set = set()
    any_unknown = False
    cost_sum = 0.0
    have_cost = False

    # Rollup shape is obsctl's ROLLUP_CONTRACT: {"date", "generated_at", "events", "sessions",
    # "tokens": {input, output, cache_read, cache_creation, by_model},
    #  "cost": {usd, known, unknown_models, unpriced_classes}}.
    # Read it through that contract and nothing else: a reader that guesses at the producer's
    # key names is precisely the drift this system exists to prevent.
    for r in rollups:
        totals = r.get("tokens") or {}
        for k in TOKEN_CLASSES:
            v = totals.get(k)
            if isinstance(v, (int, float)):
                total[k] += v
                if r.get("date") == today_str:
                    today_totals[k] += v
        gen = r.get("generated_at")
        if isinstance(gen, str) and (as_of is None or gen > as_of):
            as_of = gen
        cost = r.get("cost") or {}
        models = cost.get("unknown_models") or []
        if models:
            unknown_models.update(models)
            any_unknown = True
        if cost.get("known") is False:
            any_unknown = True
        cu = cost.get("usd")
        if isinstance(cu, (int, float)):
            cost_sum += cu
            have_cost = True

    cost_known = have_cost and not any_unknown
    # billing "subscription": dollar cost is not applicable (usage is in a plan), a different
    # truth than "unknown". Read live from config so flipping the knob updates the console on
    # the next poll, not the next ritual.
    billing = str(_lib.load_config("model-prices").get("billing", "api"))
    return {
        "as_of": as_of,
        "today": today_totals,
        "total": total,
        "cost_usd": cost_sum if cost_known else None,
        "cost_known": cost_known,
        "billing": billing,
        "unknown_models": sorted(unknown_models),
    }


# --------------------------------------------------------------------------- work

_TASK_TITLE_RE = re.compile(r"^#\s*Task:\s*(.+?)\s*$", re.MULTILINE)
_TASK_STATUS_RE = re.compile(r"^_Created.*?·\s*Status:\s*([A-Za-z0-9_-]+)_?\s*$", re.MULTILINE)
_SECTION_RE = "##\\s*{name}(.*?)(?:\\n##\\s|\\Z)"
_CHECKPOINT_RE = re.compile(_SECTION_RE.format(name="Checkpoint"), re.DOTALL)
_NEEDS_HUMAN_RE = re.compile(_SECTION_RE.format(name="NEEDS-HUMAN"), re.DOTALL)
_NEXT_ACTION_RE = re.compile(r"Next action:\s*(.+)", re.IGNORECASE)


def _count_table_rows(block: str) -> int:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return 0
    # first line is the header, second the --- separator; only data rows count
    if re.match(r"^\|?[\s:|-]+\|?$", lines[1]):
        return len(lines[2:])
    return len(lines[1:])


def _parse_task_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    title_m = _TASK_TITLE_RE.search(text)
    status_m = _TASK_STATUS_RE.search(text)
    next_action = ""
    cp_m = _CHECKPOINT_RE.search(text)
    if cp_m:
        na_m = _NEXT_ACTION_RE.search(cp_m.group(1))
        if na_m:
            next_action = na_m.group(1).strip()
    needs_human = 0
    nh_m = _NEEDS_HUMAN_RE.search(text)
    if nh_m:
        needs_human = _count_table_rows(nh_m.group(1))
    return {
        "file": _lib.rel(path),
        "title": title_m.group(1).strip() if title_m else path.stem,
        "status": status_m.group(1).strip() if status_m else "",
        "next_action": next_action,
        "needs_human": needs_human,
    }


def _read_tasks() -> list:
    tdir = _lib.claude_dir() / "tasks"
    if not tdir.is_dir():
        return []
    return [_parse_task_file(f) for f in sorted(tdir.glob("*.md"))]


def _read_log_tail() -> list:
    path = _lib.claude_dir() / "Project-log.jsonl"
    out = []
    for e in _lib.tail_jsonl(path, 8):
        out.append({
            "date": str(e.get("date", "")),
            "type": str(e.get("type", "")),
            "title": str(e.get("title", "")),
            "summary": str(e.get("summary", "")),
        })
    return out


def _read_watch_outs() -> list:
    path = _lib.claude_dir() / "LESSONS.jsonl"
    out = []
    for e in _lib.read_jsonl(path):
        if e.get("active"):
            out.append(str(e.get("prevention", "")))
    return out


_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _read_research() -> list:
    rdir = _lib.claude_dir() / "research"
    if not rdir.is_dir():
        return []
    out = []
    for f in sorted(rdir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = _H1_RE.search(text)
        out.append({"file": _lib.rel(f), "title": m.group(1).strip() if m else f.stem})
    return out


def _read_work() -> dict:
    return {
        "tasks": _read_tasks(),
        "log_tail": _read_log_tail(),
        "watch_outs": _read_watch_outs(),
        "research": _read_research(),
    }


# --------------------------------------------------------------------------- map / story

def _read_map():
    return _lib.read_json(_lib.map_dir() / "map.json", None)


def _read_story():
    return _lib.read_json(_lib.state_dir() / "story-feed.json", None)


# --------------------------------------------------------------------------- payload

def payload(live: bool = False) -> dict:
    """Build the ONE console payload. Called by `build` (live=False) and by console.py's
    /live/console.json handler (live=True). Never raises on a missing/malformed source
    file - every reader above degrades to nulls, zeros or empty collections instead."""
    now = _read_now()
    tokens = _read_tokens()
    work = _read_work()
    map_data = _read_map()
    story_data = _read_story()

    # The analysis engine's status comes from obsctl.analyze_status() - one function, imported,
    # so the console and the CLI can never disagree about "configured". It reports whether an
    # env key is PRESENT as a boolean; the key itself never enters this payload.
    try:
        import obsctl
        analysis = obsctl.analyze_status()
    except Exception:  # noqa: BLE001 - the console renders with or without the engine
        analysis = None

    warnings = []
    if not now["heartbeat"]["ts"]:
        warnings.append("no heartbeat yet")
    prices = _lib.load_config("model-prices")
    if (not (prices.get("per_million_tokens") or {})
            and str(prices.get("billing", "api")) != "subscription"):
        warnings.append("price table empty - costs read unknown")
    if map_data is None:
        warnings.append("map not built")
    if story_data is None:
        warnings.append("story not built")

    return {
        "v": SCHEMA_VERSION,
        "generated_at": _lib.utc_now(),
        "server_ts": None,
        "mode": "live" if live else "static",
        "project": {
            "name": _lib.project_name(),
            # Never the absolute path: this payload is baked into a COMMITTED file, and a
            # machine's home directory does not belong in a repo. The file:// links that used
            # this derive the root client-side from the page's own location instead, which is
            # the only situation where those links work anyway.
            "root": None,
            "system_version": _lib.system_version(),
        },
        "now": now,
        "tokens": tokens,
        "work": work,
        "map": map_data,
        "story": story_data,
        "analysis": analysis,
        "freshness": {"live": ["now", "analysis"], "ritual": ["tokens", "work.log_tail", "map", "story"]},
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- render / build

def render(data: dict, template_path: Path | None = None) -> str:
    template_path = template_path or (_lib.console_dir() / "console.template.html")
    text = template_path.read_text(encoding="utf-8")
    count = text.count(TEMPLATE_TOKEN)
    if count != 1:
        raise _lib.LibError(
            f"expected exactly one {TEMPLATE_TOKEN} in {template_path}, found {count}"
        )
    json_text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    json_text = json_text.replace("</", "<\\/")
    return text.replace(TEMPLATE_TOKEN, json_text, 1)


def _extract_embedded_data(html: str) -> dict | None:
    """Pull the payload dict back out of a previously-built console.html, the same way
    test_console.py verifies render() round-trips. Used only by build()'s write gate."""
    marker = "const DATA = "
    idx = html.find(marker)
    if idx == -1:
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(html[idx + len(marker):])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build(demo: bool = False, out: str | None = None) -> dict:
    """Compute the payload and write it into console.html - but ONLY when it actually
    changed. console.html is a committed derived file (policy.json's derived_files); a
    rebuild that touches it on every ritual even when nothing happened is exactly the kind
    of noise that makes "did anything actually change" unanswerable from git status - the
    same reasoning behind statectl.py's `_write_gated`, mirrored here for the same reason.
    Comparison strips only the top-level `generated_at` (a nested one, e.g. a story-feed
    rebuild's own `generated_at`, is a genuine content change and is deliberately NOT
    masked). Deterministic serialization (sorted keys, sorted globs) is what makes two
    payloads from the same source state compare equal in the first place.
    """
    data = payload(live=False)
    if demo:
        # A published snapshot with no server behind it: the flag disables client polling and
        # names itself honestly in the badge. Used by the demo_build generator to keep a real,
        # current console on the docs site.
        data["demo"] = True
    out_path = (_lib.project_root() / out) if out else (_lib.console_dir() / "console.html")

    old_data = None
    try:
        old_data = _extract_embedded_data(out_path.read_text(encoding="utf-8"))
    except OSError:
        pass

    stripped_new = {k: v for k, v in data.items() if k != "generated_at"}
    stripped_old = {k: v for k, v in old_data.items() if k != "generated_at"} if old_data else None

    wrote = stripped_old != stripped_new
    if wrote:
        _lib.atomic_write_text(out_path, render(data), durable=False)
    return {"path": out_path, "data": data, "wrote": wrote}


# --------------------------------------------------------------------------- CLI

def _cmd_build(args: argparse.Namespace) -> int:
    result = build(demo=getattr(args, "demo", False), out=getattr(args, "out", None))
    warn = bool(result["data"]["warnings"])
    _lib.print_verdict("CONSOLE", True, warn)
    if result["wrote"]:
        print(f"wrote {_lib.rel(result['path'])}")
    else:
        print(f"up to date: {_lib.rel(result['path'])} (payload unchanged, not rewritten)")
    for w in result["data"]["warnings"]:
        print(f"  warning: {w}")
    return 0


def _cmd_payload(args: argparse.Namespace) -> int:
    data = payload(live=False)
    indent = 2 if args.pretty else None
    print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=indent))
    # stdout is machine-readable JSON for piping into `json.load` etc.; the verdict token
    # still has to appear exactly once (checkctl's verdict_of scans stdout+stderr combined),
    # so it goes to stderr instead of contaminating the JSON stream.
    print("CONSOLE_OK", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    console_py = _lib.console_dir() / "console.py"
    if not console_py.exists():
        print(f"console.py not found at {console_py}", file=sys.stderr)
        _lib.print_verdict("CONSOLE", False)
        return 1
    cmd = [sys.executable, str(console_py), *args.serve_args]
    code = subprocess.call(cmd)
    _lib.print_verdict("CONSOLE", code == 0)
    return code


def _cmd_open(args: argparse.Namespace) -> int:
    cfg = _lib.load_config("console")
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 7717)
    html_path = _lib.console_dir() / "console.html"
    print(f"file://{html_path}")
    print(f"python3 {_lib.rel(_lib.console_dir() / 'console.py')} --host {host} --port {port}")
    _lib.print_verdict("CONSOLE", True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="consolectl.py", description="Console build / payload / serve control.")
    sub = p.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Build console.html from the current payload.")
    p_build.add_argument("--demo", action="store_true",
                         help="mark the page as a serverless demo snapshot (disables polling)")
    p_build.add_argument("--out", default=None,
                         help="alternate output path, relative to the project root")
    p_build.set_defaults(func=_cmd_build)

    p_payload = sub.add_parser("payload", help="Print the console payload as JSON.")
    p_payload.add_argument("--pretty", action="store_true", help="pretty-print with indent=2")
    p_payload.set_defaults(func=_cmd_payload)

    p_serve = sub.add_parser("serve", help="Delegate to console.py, the live server.")
    p_serve.add_argument("serve_args", nargs=argparse.REMAINDER)
    p_serve.set_defaults(func=_cmd_serve)

    p_open = sub.add_parser("open", help="Print the file:// path and the serve one-liner.")
    p_open.set_defaults(func=_cmd_open)

    return p


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except _lib.LibError as exc:
        print(f"CONSOLE_FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
