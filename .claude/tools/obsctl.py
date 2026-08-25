#!/usr/bin/env python3
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import _lib

__doc__ = """obsctl.py - the OBSERVE spine: capture -> seal -> rollup -> anchor, plus the
transcript token harness, cost/story projections, size report, the one sanctioned agent
pathway over raw (analyze), and a spool->seal->rollup->anchor selftest.

LAW 3 (the whole point of this file): verbatim content never enters git. Hooks and
_lib.obslog() write RAW capture (which contains prompts, file contents, tool output) to
RECORD_ROOT/spool - out of the repo, write-denied to every agent identity. `seal` is the
ONE place raw becomes metadata: it copies each spool event through observe.json's
seal_allowlist (an ALLOWLIST, never a denylist - an unknown field is dropped by default,
so a new secret-bearing field can never leak by omission) into RECORD_ROOT/segments/. Only
rollups (aggregate counts) and the anchor (a hash chain over sealed segments) ever land
in-repo, under .claude-iff/obs/. Raw itself - sealed-raw/ - is kept FOREVER by default;
`analyze` is the only code path in this file allowed to read it and to touch the network,
and only when a provider is configured.

    ingest      ~/.claude/projects/<slug>*/**/*.jsonl (token metadata only) -> spool
    seal        spool -> allowlisted segments (+ sealed-raw backstop), chmod 0444
    rollup      segments -> .claude-iff/obs/rollups/<date>.json (in-repo, metadata only)
    anchor      sealed segments -> .claude-iff/obs/anchor.json (tamper EVIDENCE, not
                prevention: a sha256 chain anyone with disk access could also recompute
                and overwrite - it proves nothing changed silently, it stops nobody)
    report      aggregate segments by model|day|session, print a table
    story       segments+rollups+journal+anatomy+data_series -> state/story-feed.json
    size        record size by subtree (never deletes)
    analyze     the one sanctioned agent pathway over raw (network; degrades to a no-op)
    selftest    spool->seal->rollup->anchor round trip on an isolated scratch record
"""

import argparse
import gzip
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- token classes

# The four token classes carried end to end: transcript usage field -> our flat dotted event
# key (which doubles as the seal_allowlist entry - no nesting, so allowlist filtering is a
# flat dict comprehension) -> the short name used in rollups/reports/cost.
USAGE_FIELDS = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_input_tokens": "gen_ai.usage.cache_read_input_tokens",
    "cache_creation_input_tokens": "gen_ai.usage.cache_creation_input_tokens",
}
CLASS_ATTR = {
    "input": "gen_ai.usage.input_tokens",
    "output": "gen_ai.usage.output_tokens",
    "cache_read": "gen_ai.usage.cache_read_input_tokens",
    "cache_creation": "gen_ai.usage.cache_creation_input_tokens",
}
COST_CLASSES = ("input", "output", "cache_read", "cache_creation")

FAILURE_HOOK_EVENTS = {"PostToolUseFailure", "StopFailure", "PermissionDenied"}


# --------------------------------------------------------------------------- ingest

def _project_slug(root: Path) -> str:
    """Claude Code's own ~/.claude/projects directory naming: '/' AND '.' both become '-'.
    Verified empirically against a live projects dir: a git-worktree path
    .../my-repo/.claude/worktrees/x sits as '...my-repo--claude-worktrees-x' - the '.' in
    '.claude' converts too, not only path separators. Guessing '/'-only would silently
    produce a slug that matches nothing for every worktree."""
    return re.sub(r"[/.]", "-", str(root.resolve()))


def _usage_event_from_record(rec) -> dict | None:
    """One llm.usage spool event from a transcript JSONL record, or None when the line
    carries no usage (most lines - tool_use, tool_result, user turns - don't)."""
    if not isinstance(rec, dict):
        return None
    message = rec.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    fields = {attr: usage.get(src) for src, attr in USAGE_FIELDS.items()}
    fields = {k: v for k, v in fields.items() if isinstance(v, int)}
    if not fields:
        return None
    ev = {
        "_obs_ts": rec.get("timestamp") or _lib.utc_now(),
        "_obs_source": "transcript",
        "hook_event_name": "llm.usage",
        "session_id": rec.get("sessionId"),
        "is_subagent": bool(rec.get("isSidechain")),
        "message_id": message.get("id") or rec.get("uuid"),
        **fields,
    }
    if message.get("model"):
        ev["gen_ai.request.model"] = message["model"]
    return ev


def _ingest_one_file(f: Path, root: Path, tcursors: dict, spool_path: Path, seen_ids: set,
                      tokens: dict, copy_verbatim: bool, transcripts_dir: Path) -> int:
    """Read the bytes of `f` past its recorded cursor, spool one llm.usage event per
    usage-bearing line, and update the cursor. Returns the count of NEW events spooled."""
    key = str(f.resolve())
    cur = tcursors.get(key, {})
    offset = int(cur.get("offset", 0))
    recorded_size = int(cur.get("size", 0))
    try:
        cur_size = f.stat().st_size
    except OSError:
        return 0
    if cur_size < recorded_size:
        offset = 0  # rotated/replaced with something smaller: re-read from the start

    if copy_verbatim:  # the completeness backstop, kept OUT of the repo (RECORD_ROOT)
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = Path(f.name)
        dst = transcripts_dir / rel
        if not dst.exists() or dst.stat().st_size < cur_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)

    if cur_size <= offset:
        tcursors[key] = {"offset": offset, "size": cur_size}
        return 0

    new_events = 0
    consumed = offset
    with f.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    for line in chunk.split(b"\n"):
        ln = len(line) + 1
        if not line.strip():
            consumed += ln
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if consumed + ln >= cur_size:
                break  # torn tail (file mid-write): retried next run, offset stays behind it
            consumed += ln
            continue
        ev = _usage_event_from_record(rec)
        if ev is not None:
            dedupe_key = ev.get("message_id") or f"{key}:{consumed}"
            if dedupe_key not in seen_ids:
                seen_ids.add(dedupe_key)
                _lib.append_jsonl(spool_path, ev)
                new_events += 1
                for cls, attr in CLASS_ATTR.items():
                    v = ev.get(attr)
                    if isinstance(v, int):
                        tokens[cls] += v
        consumed += ln
    tcursors[key] = {"offset": min(consumed, cur_size), "size": cur_size}
    return new_events


def cmd_ingest(args) -> int:
    cfg = _lib.load_config("observe", {})
    icfg = cfg.get("ingest", {}) or {}
    roots_cfg = icfg.get("roots") or ["~/.claude/projects"]
    copy_verbatim = bool(icfg.get("copy_verbatim", True))
    max_files = int(icfg.get("max_files_per_run", 0) or 0)

    paths = _lib.record_paths()
    cursors = _lib.read_json(paths["cursors"], {}) or {}
    tcursors = cursors.setdefault("transcripts", {})

    slug = _project_slug(_lib.project_root())
    spool_path = paths["spool"] / "ingest.jsonl"
    seen_ids: set = set()  # dedupe by message_id across the WHOLE run, not just per file
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    files_scanned = 0
    new_events = 0

    for root_str in roots_cfg:
        root = Path(root_str).expanduser()
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not d.name.startswith(slug):
                continue
            # rglob, NOT a top-level glob: subagent transcripts live nested under
            # <session-id>/subagents/agent-*.jsonl - in the source system 502 of 515
            # transcript files were invisible to a top-level glob.
            for f in sorted(d.rglob("*.jsonl")):
                if max_files and files_scanned >= max_files:
                    break
                files_scanned += 1
                new_events += _ingest_one_file(
                    f, root, tcursors, spool_path, seen_ids, tokens, copy_verbatim,
                    paths["transcripts"])

    _lib.atomic_write_json(paths["cursors"], cursors)
    print(f"files_scanned={files_scanned} new_events={new_events} "
          f"tokens_input={tokens['input']} tokens_output={tokens['output']} "
          f"tokens_cache_read={tokens['cache_read']} tokens_cache_creation={tokens['cache_creation']}")
    _lib.print_verdict("OBS", True)
    return 0


# --------------------------------------------------------------------------- seal

def _compute_error_class(ev: dict) -> str | None:
    """A failure verdict, or None. hook_event_name failures come straight from
    observe.json's capture_events; a nonzero exit_code is the generic fallback for
    tool-run events that don't carry one of those names."""
    hook = ev.get("hook_event_name")
    if hook in FAILURE_HOOK_EVENTS:
        return hook
    exit_code = ev.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return "exit_nonzero"
    return None


def _hashable(value):
    """Any JSON value, rendered into something a set can hold.

    Identity fields come from raw hook payloads, so a field that is normally a string can
    arrive as a list or a dict. Without this, `ident in known` raised TypeError, seal died with
    an unhandled traceback, the spool was never drained, and every later seal hit the same line:
    one malformed event wedged PUBLISH permanently.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _event_identity(ev: dict) -> tuple:
    """Per-event identity for idempotent sealing, computed from the RAW event.

    Prefers `_obs_uid`, the uniquifier the capture lane stamps at write time. It has to exist:
    the previous identity was (second-precision timestamp, session, event name, tool, message id),
    and hook events carry neither tool_name nor message_id, so every SubagentStop in the same
    session in the same second collapsed to one row. Parallel sub-agents finishing together is
    routine, and the events it dropped were dropped from the tier documented as kept forever.

    Events captured before `_obs_uid` existed fall back to the old tuple, which is the best that
    can be reconstructed for them.
    """
    uid = ev.get("_obs_uid")
    if uid:
        return ("uid", _hashable(uid))
    return ("legacy",
            _hashable(ev.get("_obs_ts")), _hashable(ev.get("session_id")),
            _hashable(ev.get("hook_event_name")), _hashable(ev.get("tool_name")),
            _hashable(ev.get("message_id")))


def _seal_value(value):
    """Allowlisted NAMES are not enough: an allowlisted name holding a nested structure used to
    pass through verbatim, so a prompt or a secret nested under `reason` reached the redacted
    tier intact. Scalars survive; anything structured is replaced by a shape marker, and the
    verbatim original stays in sealed-raw where it belongs."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return {"_type": "list", "_len": len(value)}
    if isinstance(value, dict):
        return {"_type": "dict", "_len": len(value)}
    return {"_type": type(value).__name__}


def _read_sealed_raw(path: Path) -> list:
    opener = gzip.open if path.suffix == ".gz" else open
    out = []
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_sealed_raw(path: Path, events: list, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        # gzip.open(..., "ab") appends a new concatenated gzip member; Python's gzip reader
        # transparently decompresses concatenated members back into one stream, so repeated
        # appends across seal runs read back as one continuous JSONL file.
        with gzip.open(path, "ab") as gz:
            for ev in events:
                gz.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
    else:
        for ev in events:
            _lib.append_jsonl(path, ev)


def _seal_date(date: str, events: list, allowlist: set, compact: bool, paths: dict) -> int:
    """Merge `events` (raw, this run's spool lines for `date`) into the sealed segment and
    sealed-raw for `date`, skipping anything already sealed by identity. Returns the count
    of genuinely new events sealed."""
    seg_path = paths["segments"] / f"{date}.jsonl"
    raw_path = (paths["sealed_raw"] / f"{date}.jsonl.gz") if compact else (paths["sealed_raw"] / f"{date}.jsonl")

    known = {_event_identity(e) for e in _read_sealed_raw(raw_path)}
    new_raw, new_segment = [], []
    for ev in events:
        ident = _event_identity(ev)
        if ident in known:
            continue
        known.add(ident)
        new_raw.append(ev)
        sealed = dict(ev)
        sealed["error_class"] = _compute_error_class(ev)
        new_segment.append({
            k: _seal_value(v) for k, v in sealed.items() if k in allowlist and v is not None
        })

    if not new_raw:
        return 0

    # A rename/replace (what atomic_write_text does) only needs write permission on the
    # DIRECTORY, not on the target file - so a prior chmod 0444 on seg_path does not block
    # re-sealing here; no need to unlock it first.
    existing = _lib.read_jsonl(seg_path)
    _lib.atomic_write_text(
        seg_path,
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in existing + new_segment))
    os.chmod(seg_path, 0o444)

    _append_sealed_raw(raw_path, new_raw, compact)
    return len(new_raw)


def _apply_retention(paths: dict, retention_days: int) -> None:
    """Raw is KEPT FOREVER by default (retention_days <= 0): this is a no-op unless a
    maintainer has explicitly opted into expiry."""
    if retention_days <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    sealed_raw = paths["sealed_raw"]
    if not sealed_raw.is_dir():
        return
    for f in sealed_raw.glob("*.jsonl*"):
        if f.name[:10] < cutoff:
            f.unlink(missing_ok=True)


def cmd_seal(args) -> int:
    cfg = _lib.load_config("observe", {})
    allowlist = set(cfg.get("seal_allowlist") or [])
    compact = bool(cfg.get("compact_sealed_raw", True))
    retention_days = int(cfg.get("retention_days", 0) or 0)
    paths = _lib.record_paths()
    spool_dir = paths["spool"]
    today = _lib.today()
    target_date = getattr(args, "date", None)

    spool_files = sorted(spool_dir.glob("*.jsonl")) if spool_dir.is_dir() else []
    by_date: dict[str, list] = {}
    kept_lines: dict[Path, list] = {}
    for sf in spool_files:
        kept = []
        # errors="replace": one bad byte in a spool file must not crash seal and wedge the
        # whole publish path. Losing a character beats losing the pipeline.
        for raw_line in sf.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                ev = json.loads(stripped)
            except json.JSONDecodeError:
                kept.append(raw_line)  # not our call to discard unparsable raw data
                continue
            date = str(ev.get("_obs_ts") or "")[:10]
            if not date:
                kept.append(raw_line)  # can't date it; leave for a human/ops to notice
                continue
            eligible = (date == target_date) if target_date else (date < today)
            if eligible:
                by_date.setdefault(date, []).append(ev)
            else:
                kept.append(raw_line)
        kept_lines[sf] = kept

    events_sealed = 0
    dates_sealed = []
    for date, events in sorted(by_date.items()):
        n = _seal_date(date, events, allowlist, compact, paths)
        events_sealed += n
        dates_sealed.append(date)

    for sf, kept in kept_lines.items():
        if kept:
            _lib.atomic_write_text(sf, "\n".join(kept) + "\n")
        else:
            sf.unlink(missing_ok=True)

    _apply_retention(paths, retention_days)
    print(f"dates_sealed={len(dates_sealed)} events_sealed={events_sealed} "
          f"dates={','.join(dates_sealed) or '-'}")
    _lib.print_verdict("OBS", True)
    return 0


# --------------------------------------------------------------------------- rollup

def _compute_cost(by_model: dict, prices: dict) -> dict:
    """COST FORMULA: per model, usd = sum over class in {input,output,cache_read,
    cache_creation} of tokens[model][class] * price[model][class] / 1e6.

    A model absent from model-prices.json contributes to unknown_models and makes the
    WHOLE rollup's cost known=False, usd=None - one unpriced model must not make an
    otherwise-complete-looking total silently lie by omission. A model that IS priced but
    is missing the price for one class instead adds "model:class" to unpriced_classes and
    that class contributes NOTHING to usd (never a guessed/zeroed price) while the rest of
    that model's classes still price normally and `known` stays True - the caller can tell
    "zero cost" and "we don't know" apart by checking unpriced_classes.
    """
    unknown_models: list[str] = []
    unpriced_classes: list[str] = []
    usd = 0.0
    for model, tokens in sorted(by_model.items()):
        price = prices.get(model)
        if price is None:
            unknown_models.append(model)
            continue
        for cls in COST_CLASSES:
            n = tokens.get(cls, 0)
            if not n:
                continue
            p = price.get(cls)
            if p is None:
                unpriced_classes.append(f"{model}:{cls}")
                continue
            usd += n * p / 1e6
    known = not unknown_models
    return {
        "usd": round(usd, 6) if known else None,
        "known": known,
        "unknown_models": unknown_models,
        "unpriced_classes": unpriced_classes,
    }


def cmd_rollup(args) -> int:
    date = getattr(args, "date", None) or _lib.today()
    paths = _lib.record_paths()
    events = _lib.read_jsonl(paths["segments"] / f"{date}.jsonl")
    prices = (_lib.load_config("model-prices", {}) or {}).get("per_million_tokens", {}) or {}

    by_event: dict[str, int] = {}
    errors = 0
    sessions: set = set()
    tokens_total = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    by_model: dict[str, dict] = {}

    for ev in events:
        hook = ev.get("hook_event_name") or "(unknown)"
        by_event[hook] = by_event.get(hook, 0) + 1
        if ev.get("error_class"):
            errors += 1
        sid = ev.get("session_id")
        if sid:
            sessions.add(sid)
        has_tokens = any(isinstance(ev.get(attr), int) for attr in CLASS_ATTR.values())
        if has_tokens:
            model = ev.get("gen_ai.request.model") or "(unknown)"
            bucket = by_model.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
            for cls, attr in CLASS_ATTR.items():
                v = ev.get(attr)
                if isinstance(v, int):
                    bucket[cls] += v
                    tokens_total[cls] += v

    cost = _compute_cost(by_model, prices)
    # billing "subscription" means dollar cost is NOT APPLICABLE (usage is included in a plan),
    # which is a different truth than "unknown". Token counts stay; consumers render "in plan".
    cost["billing"] = str((_lib.load_config("model-prices", {}) or {}).get("billing", "api"))
    rollup = {
        "date": date,
        "generated_at": _lib.utc_now(),
        "events": {"total": len(events), "by_event": by_event, "errors": errors},
        "sessions": len(sessions),
        "tokens": {**tokens_total, "by_model": by_model},
        "cost": cost,
    }
    out_path = _lib.iff_dir() / "obs" / "rollups" / f"{date}.json"
    _lib.atomic_write_json(out_path, rollup)
    print(f"date={date} events={len(events)} sessions={len(sessions)} "
          f"tokens_output={tokens_total['output']} cost_known={cost['known']}")
    _lib.print_verdict("OBS", True)
    return 0


# --------------------------------------------------------------------------- anchor

def cmd_anchor(args) -> int:
    """Write .claude-iff/obs/anchor.json: a sha256 per sealed segment, chained by date.

    This is tamper EVIDENCE, not prevention - anyone with filesystem access to both the
    segment and this anchor file can recompute and overwrite both. It proves nothing
    changed silently between one anchor run and the next; it stops nobody with write
    access to the repo.
    """
    paths = _lib.record_paths()
    seg_dir = paths["segments"]
    segs = sorted(seg_dir.glob("*.jsonl")) if seg_dir.is_dir() else []
    chain = [
        {"date": seg.stem, "sha256": _lib.sha256_file(seg), "events": len(_lib.read_jsonl(seg))}
        for seg in segs
    ]
    anchor = {
        "generated_at": _lib.utc_now(),
        "last_sealed_date": chain[-1]["date"] if chain else None,
        "segment": segs[-1].name if segs else None,
        "sha256": chain[-1]["sha256"] if chain else None,
        "chain": chain,
    }
    out_path = _lib.iff_dir() / "obs" / "anchor.json"
    _lib.atomic_write_json(out_path, anchor)
    print(f"segments={len(chain)} last_sealed_date={anchor['last_sealed_date']}")
    _lib.print_verdict("OBS", True)
    return 0


# --------------------------------------------------------------------------- report

def cmd_report(args) -> int:
    paths = _lib.record_paths()
    segs = sorted(paths["segments"].glob("*.jsonl")) if paths["segments"].is_dir() else []
    prices = (_lib.load_config("model-prices", {}) or {}).get("per_million_tokens", {}) or {}

    agg: dict[str, dict] = {}
    for seg in segs:
        date = seg.stem
        for ev in _lib.read_jsonl(seg):
            model = ev.get("gen_ai.request.model") or "(unknown)"
            sid = ev.get("session_id") or "(unknown)"
            key = {"model": model, "day": date, "session": sid}[args.by]
            row = agg.setdefault(key, {"events": 0, "by_model": {}})
            row["events"] += 1
            bucket = row["by_model"].setdefault(
                model, {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
            for cls, attr in CLASS_ATTR.items():
                v = ev.get(attr)
                if isinstance(v, int):
                    bucket[cls] += v

    print(f"{args.by:<28}{'events':>8}{'input':>12}{'output':>12}{'cost_usd':>12}")
    for key in sorted(agg):
        row = agg[key]
        cost = _compute_cost(row["by_model"], prices)
        inp = sum(m["input"] for m in row["by_model"].values())
        outp = sum(m["output"] for m in row["by_model"].values())
        cost_disp = f"{cost['usd']:.4f}" if cost["known"] else "unknown"
        print(f"{str(key)[:28]:<28}{row['events']:>8}{inp:>12}{outp:>12}{cost_disp:>12}")
    _lib.print_verdict("OBS", True)
    return 0


# --------------------------------------------------------------------------- size

def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def cmd_size(args) -> int:
    """Report record size by top-level child of RECORD_ROOT. Never deletes anything -
    retention/expiry is `seal`'s job (and off by default); this command only looks and
    warns. Walking root.iterdir() (rather than a fixed list of the named subtrees) is
    deliberate: cursors.json and anything else that lands directly at the record root must
    still count toward the total and the warn threshold, not go invisible."""
    cfg = _lib.load_config("observe", {})
    warn_mb = float(cfg.get("size_warn_mb", 2048) or 2048)
    root = _lib.record_paths()["root"]
    total = 0
    print(f"{'subtree':<14}{'size':>12}")
    if root.is_dir():
        for child in sorted(root.iterdir()):
            n = _dir_size(child)
            total += n
            print(f"{child.name:<14}{_lib.human_bytes(n):>12}")
    print(f"{'TOTAL':<14}{_lib.human_bytes(total):>12}  (record_root={root})")
    warn = (total / (1024 * 1024)) > warn_mb
    if warn:
        print(f"WARN: record size exceeds size_warn_mb={warn_mb}")
    _lib.print_verdict("OBS", True, warn)
    return 0


# --------------------------------------------------------------------------- analyze

def _collect_recent_raw(paths: dict, limit) -> list:
    """The most recent `limit` raw events, newest files first.

    Retention is deliberately infinite, so the record only ever grows. Reading every sealed-raw
    file into one list and slicing afterwards would mean loading the entire history to look at
    the last few hundred events, which eventually just runs out of memory. Walk newest-first and
    stop as soon as we have enough.
    """
    want = int(limit) if limit else 0
    collected: list = []

    def take(files, reader):
        for f in files:
            if want and len(collected) >= want:
                return
            collected.extend(reader(f))

    # Spool is the newest tier (not yet sealed), then sealed-raw newest date first.
    if paths["spool"].is_dir():
        take(sorted(paths["spool"].glob("*.jsonl"), reverse=True), _lib.read_jsonl)
    if paths["sealed_raw"].is_dir():
        take(sorted(paths["sealed_raw"].glob("*.jsonl*"), reverse=True), _read_sealed_raw)

    collected.sort(key=lambda e: str(e.get("_obs_ts") or ""))
    return collected[-want:] if want else collected


# --------------------------------------------------------------------------- analysis engine
#
# The retrospective labeling engine: ONE protocol (OpenAI-compatible /chat/completions), which
# covers OpenRouter, Ollama (/v1), OpenAI, vLLM, LM Studio and everything else that speaks it.
# Structured output via response_format json_schema, with a fallback for servers that reject
# response_format; the reply is mechanically parsed, repaired and validated against the taxonomy.
# Nothing here is "analyzed by Claude": it is a tool call with a schema, end to end.
#
# The API key comes from the ENVIRONMENT ONLY (observe.json is committed to git):
#   export ANALYZE_API_KEY=...        preferred, provider-agnostic
#   (OPENROUTER_API_KEY / OPENAI_API_KEY are honored as fallbacks)
# A localhost base_url (Ollama, LM Studio, vLLM) needs no key at all.

ANALYZE_KEY_ENVS = ("ANALYZE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")

# Base URLs for the legacy provider names, so an existing config keeps working.
PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}

MAX_EVENT_CHARS = 2000  # a single captured event can carry a whole file; cap what we send


def _label_schema(taxonomy: list) -> dict:
    """The structured-output contract. `i` indexes into the batch as sent; `labels` are drawn
    from the configured taxonomy and nothing else."""
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "labels": {"type": "array", "items": {"type": "string", "enum": list(taxonomy)}},
                        "summary": {"type": "string"},
                    },
                    "required": ["i", "labels", "summary"],
                    "additionalProperties": False,
                },
            },
            "batch_summary": {"type": "string"},
        },
        "required": ["labels", "batch_summary"],
        "additionalProperties": False,
    }


def _json_repair(text: str):
    """Extract the first JSON object from a model reply that may wrap it in prose or fences."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _validate_labels(obj, batch_len: int, taxonomy: list):
    """Mechanical validation: indexes in range, labels inside the taxonomy, summaries are text.
    Anything off-contract invalidates the whole batch - a half-trusted label set is worse than
    a failed batch, because downstream aggregation cannot tell which half to believe."""
    if not isinstance(obj, dict) or not isinstance(obj.get("labels"), list):
        return None
    allowed = set(taxonomy)
    rows = []
    for row in obj["labels"]:
        if not isinstance(row, dict):
            return None
        i = row.get("i")
        labels = row.get("labels")
        if not isinstance(i, int) or not (0 <= i < batch_len):
            return None
        if not isinstance(labels, list) or not all(isinstance(x, str) and x in allowed for x in labels):
            return None
        rows.append({"i": i, "labels": labels, "summary": str(row.get("summary", ""))[:500]})
    return {"labels": rows, "batch_summary": str(obj.get("batch_summary", ""))[:1000]}


def _serialize_batch(batch: list) -> str:
    lines = []
    for i, ev in enumerate(batch):
        text = json.dumps(ev, ensure_ascii=False, default=str)
        if len(text) > MAX_EVENT_CHARS:
            text = text[:MAX_EVENT_CHARS] + f'... [truncated {len(text) - MAX_EVENT_CHARS} chars]"}}'
        lines.append(f"[{i}] {text}")
    return "\n".join(lines)


def _http_post_json(url: str, payload: dict, api_key, timeout: int) -> dict:
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _analyze_one_batch(base_url: str, model: str, api_key, batch: list,
                       taxonomy: list, timeout: int):
    """One chunk -> one structured completion -> validated labels, or None on failure.

    Tries response_format json_schema first; a server that rejects it (HTTP 4xx) gets one
    retry with the schema embedded in the instructions instead. Either way the reply passes
    through _json_repair and _validate_labels - the schema request is an optimization, the
    validation is the guarantee.
    """
    import urllib.error

    url = base_url.rstrip("/") + "/chat/completions"
    schema = _label_schema(taxonomy)
    system = (
        "You label observability events from an agentic coding system. For EVERY event, choose "
        "zero or more labels from exactly this taxonomy: " + ", ".join(taxonomy) + ". "
        "Reply with JSON only, matching the provided schema. summary is one plain sentence."
    )
    user = "Events (one per line, prefixed by index):\n" + _serialize_batch(batch)
    base = {"model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0}

    attempts = [
        {**base, "response_format": {"type": "json_schema",
                                     "json_schema": {"name": "event_labels", "strict": True,
                                                     "schema": schema}}},
        {**base, "messages": [
            {"role": "system", "content": system + " Schema: " + json.dumps(schema)},
            {"role": "user", "content": user}]},
    ]
    for payload in attempts:
        try:
            reply = _http_post_json(url, payload, api_key, timeout)
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and payload is attempts[0]:
                continue  # server does not speak response_format; fall back once
            return None
        except Exception:  # noqa: BLE001 - network is unreliable; the caller counts failures
            return None
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        validated = _validate_labels(_json_repair(content), len(batch), taxonomy)
        if validated is not None:
            return validated
    return None


def analyze_status() -> dict:
    """The console's view of the engine: configured or not, where the key should come from,
    whether one is present (a BOOLEAN - the key itself never leaves the environment), and what
    products exist. Imported by consolectl so the two can never disagree on 'configured'."""
    cfg = _lib.load_config("observe", {})
    acfg = cfg.get("analyze", {}) or {}
    provider = acfg.get("provider", "none")
    base_url = acfg.get("base_url") or PROVIDER_BASE_URLS.get(provider)
    model = acfg.get("model")
    key_env = next((e for e in ANALYZE_KEY_ENVS if os.environ.get(e)), None)
    local = bool(base_url) and ("localhost" in base_url or "127.0.0.1" in base_url)
    configured = bool(base_url) and bool(model) and (local or key_env is not None)
    products = []
    analysis_dir = _lib.record_paths()["analysis"]
    if analysis_dir.is_dir():
        products = sorted(p.name for p in analysis_dir.glob("*-labels.json"))
    status = _lib.read_json(analysis_dir / ".run-status.json", {}) or {}
    return {
        "configured": configured,
        "base_url": base_url,
        "model": model,
        "key_env": key_env,               # which env var satisfied the check, or null
        "key_envs_accepted": list(ANALYZE_KEY_ENVS),
        "key_required": not local,
        "taxonomy": acfg.get("taxonomy") or [],
        "products": len(products),
        "last_product": products[-1] if products else None,
        "run": {"state": status.get("state"), "started": status.get("started"),
                "finished": status.get("finished"), "detail": status.get("detail")},
        "command": "python3 .claude/tools/obsctl.py analyze",
    }


def _write_run_status(paths: dict, **fields) -> None:
    try:
        current = _lib.read_json(paths["analysis"] / ".run-status.json", {}) or {}
        current.update(fields)
        _lib.atomic_write_json(paths["analysis"] / ".run-status.json", current)
    except Exception:  # noqa: BLE001 - status is telemetry; it fails open
        pass


def cmd_analyze(args) -> int:
    """The ONE sanctioned agent pathway over raw: structured-output labeling of the record.

    Reads recent sealed-raw + spool, chunks per analyze.max_events_per_batch, and sends the
    chunks CONCURRENTLY (analyze.parallel workers) to the OpenAI-compatible endpoint. Every
    reply is schema-validated mechanically; nothing here is judged by Claude. Products land in
    RECORD_ROOT/analysis/ and NEVER auto-enter the repo - promotion is a human-gated copy,
    enforced simply by this command never writing anywhere under the project root.

    Unconfigured (no base_url/model, or a remote endpoint with no key in the environment)
    degrades to a clear guided no-op at exit 0: a missing analysis feature must never break
    the work. --dry-run reports what WOULD be sent - counts and taxonomy only, never content.
    """
    from concurrent.futures import ThreadPoolExecutor

    status = analyze_status()
    acfg = (_lib.load_config("observe", {}).get("analyze") or {})
    taxonomy = status["taxonomy"]
    batch_size = int(acfg.get("max_events_per_batch", 200) or 200)
    parallel = max(1, int(acfg.get("parallel", 4) or 4))
    timeout = int(acfg.get("timeout_s", 90) or 90)
    paths = _lib.record_paths()

    events = _collect_recent_raw(paths, args.limit)
    n_batches = (len(events) + batch_size - 1) // batch_size if events else 0

    if args.dry_run:
        print(f"DRY-RUN: would send {len(events)} raw event(s) in {n_batches} chunk(s) of up to "
              f"{batch_size}, {parallel} in parallel, to {status['base_url']!r} "
              f"model={status['model']!r} taxonomy={taxonomy} -- raw content withheld")
        _lib.print_verdict("OBS", True)
        return 0

    if not status["configured"]:
        print(
            "analyze: not configured - this is fine, it is an optional feature. To enable:\n"
            "  1. Set analyze.base_url and analyze.model in .claude/config/observe.json.\n"
            "     Any OpenAI-compatible endpoint works: https://openrouter.ai/api/v1 (get a key\n"
            "     at openrouter.ai/keys), https://api.openai.com/v1 (platform.openai.com), or a\n"
            "     local server like Ollama at http://localhost:11434/v1 (no key needed).\n"
            "  2. For remote endpoints, put the key in your environment, never in config:\n"
            "     export ANALYZE_API_KEY=sk-...   (add it to your shell profile)\n"
            "  3. Rerun this command. --dry-run shows what would be sent."
        )
        _lib.print_verdict("OBS", True)
        return 0

    if not events:
        print("analyze: no raw events available in sealed-raw/spool -- no-op.")
        _lib.print_verdict("OBS", True)
        return 0

    api_key = os.environ.get(status["key_env"]) if status["key_env"] else None
    batches = [events[i:i + batch_size] for i in range(0, len(events), batch_size)]
    _write_run_status(paths, state="running", started=_lib.utc_now(), finished=None,
                      detail=f"{len(batches)} chunk(s), {parallel} parallel")

    def work(chunk):
        return _analyze_one_batch(status["base_url"], status["model"], api_key,
                                  chunk, taxonomy, timeout)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(work, batches))

    labeled_events, batch_summaries, failed = [], [], 0
    offset = 0
    for chunk, result in zip(batches, results):
        if result is None:
            failed += 1
        else:
            batch_summaries.append(result["batch_summary"])
            for row in result["labels"]:
                ev = chunk[row["i"]]
                labeled_events.append({
                    "_obs_ts": ev.get("_obs_ts"), "session_id": ev.get("session_id"),
                    "hook_event_name": ev.get("hook_event_name"),
                    "labels": row["labels"], "summary": row["summary"],
                })
        offset += len(chunk)

    ts_id = re.sub(r"[-:]", "", _lib.utc_now())
    out_path = paths["analysis"] / f"{ts_id}-labels.json"
    _lib.atomic_write_json(out_path, {
        "generated_at": _lib.utc_now(), "engine": "openai-compatible",
        "base_url": status["base_url"], "model": status["model"], "taxonomy": taxonomy,
        "n_events": len(events), "n_batches": len(batches), "n_failed": failed,
        "events": labeled_events, "batch_summaries": batch_summaries,
    })
    _write_run_status(paths, state="failed" if failed == len(batches) else "done",
                      finished=_lib.utc_now(),
                      detail=f"{len(batches) - failed}/{len(batches)} chunk(s) ok, "
                             f"{len(labeled_events)} event(s) labeled")
    print(f"analyze: {len(batches) - failed}/{len(batches)} chunk(s) ok, "
          f"{len(labeled_events)} event(s) labeled -> {out_path}")
    print("(RECORD_ROOT/analysis - promotion into the repo is a human-gated copy, never "
          "automatic. `obsctl story` folds these labels into the story feed's analysis lane.)")
    _lib.print_verdict("OBS", failed < len(batches) or not batches, warn=failed > 0)
    return 0 if failed < len(batches) or not batches else 1


# --------------------------------------------------------------------------- contracts

# The rollup's contract, in the same flat dotted form as STORY_CONTRACT below.
#
# This exists because the drift it prevents already happened during this system's own build:
# the console's reader looked for `totals` and `cost_known` while this writer produced `tokens`
# and `cost.known`, so every token figure on the console silently read zero. Nothing failed;
# the number was just wrong. Any consumer of a rollup validates against THIS list, and
# test_contracts.py round-trips a real rollup through the console payload so the two sides
# cannot drift again.
ROLLUP_CONTRACT = [
    "date", "generated_at", "sessions",
    "events.total", "events.by_event", "events.errors",
    "tokens.input", "tokens.output", "tokens.cache_read", "tokens.cache_creation",
    "tokens.by_model",
    "cost.usd", "cost.known", "cost.unknown_models", "cost.unpriced_classes", "cost.billing",
]


# --------------------------------------------------------------------------- story

# The renderer's contract. "foo[]" means "for every item in the foo list" (an empty list
# trivially satisfies whatever comes after it - there's nothing to check). Kept as one flat
# list, not nested code, so a test can walk it against BOTH a real story-feed.json and
# whatever the renderer expects, and the two can never silently drift apart.
STORY_CONTRACT = [
    "v", "generated_at",
    "clock.wall_min", "clock.wall_max", "clock.tokens_max",
    "points[].date", "points[].t_tokens_cum",
    "points[].operations.events", "points[].operations.errors",
    "points[].operations.sessions", "points[].operations.tokens_out",
    "points[].operations.cost_usd",
    "points[].anatomy.agents", "points[].anatomy.skills", "points[].anatomy.hooks",
    "points[].anatomy.tools", "points[].anatomy.cards", "points[].anatomy.components",
    "points[].data",
    "points[].analysis",
    "markers[].date", "markers[].t_tokens_cum", "markers[].kind", "markers[].text",
    "markers[].ref",
]

MARKER_ACTIONS = {"milestone": "milestone", "decision": "decision", "tooling": "tooling"}


def _contract_path_ok(node, parts: list) -> bool:
    if not parts:
        return True
    part = parts[0]
    if part.endswith("[]"):
        key = part[:-2]
        if not isinstance(node, dict) or key not in node or not isinstance(node[key], list):
            return False
        return all(_contract_path_ok(item, parts[1:]) for item in node[key])
    if not isinstance(node, dict) or part not in node:
        return False
    return _contract_path_ok(node[part], parts[1:])


def validate_story_contract(feed: dict, contract: list = STORY_CONTRACT) -> bool:
    """True iff every dotted path in `contract` resolves in `feed`. Used both as a
    pre-write self-check here and by the test suite, so producer and renderer are
    guaranteed to agree on the schema."""
    return all(_contract_path_ok(feed, path.split(".")) for path in contract)


def _load_rollups() -> list:
    rdir = _lib.iff_dir() / "obs" / "rollups"
    out = []
    if rdir.is_dir():
        for f in sorted(rdir.glob("*.json")):
            r = _lib.read_json(f, None)
            if r:
                out.append(r)
    return sorted(out, key=lambda r: r.get("date", ""))


def _count_anatomy() -> dict:
    """Component counts as of RIGHT NOW. We do not have (and do not fabricate) a per-day
    history of the system's own anatomy - every past point in the story carries this SAME
    snapshot forward, which is honest about what we can source cheaply versus what would
    require walking git history per day (a heavier feature this tool deliberately skips)."""
    cd = _lib.claude_dir()
    agents = len(list((cd / "agents").glob("*.md"))) if (cd / "agents").is_dir() else 0
    skills = len(list((cd / "skills").glob("*/SKILL.md"))) if (cd / "skills").is_dir() else 0
    hooks = sum(1 for p in (cd / "hooks").iterdir() if p.is_file()) if (cd / "hooks").is_dir() else 0
    tools = (sum(1 for p in (cd / "tools").glob("*.py") if not p.name.startswith("_"))
             if (cd / "tools").is_dir() else 0)
    cards = len(list((cd / "system-map" / "cards").glob("*.json"))) if (cd / "system-map" / "cards").is_dir() else 0
    return {"agents": agents, "skills": skills, "hooks": hooks, "tools": tools, "cards": cards,
            "components": agents + skills + hooks + tools + cards}


def _count_data_series() -> dict:
    """memory.json's data_series, each counted RIGHT NOW (same carry-forward caveat as
    anatomy - see _count_anatomy)."""
    mem = _lib.load_config("memory", {}) or {}
    series_list = (mem.get("data_series") or {}).get("series") or []
    root = _lib.project_root()
    out = {}
    for s in series_list:
        name, kind, rel_path = s.get("name"), s.get("kind"), s.get("path")
        if not name or not rel_path:
            continue
        p = root / rel_path
        if kind == "file_count":
            out[name] = len(list(p.glob("*"))) if p.is_dir() else 0
        elif kind == "jsonl_lines":
            out[name] = len(_lib.read_jsonl(p)) if p.exists() else 0
        else:
            out[name] = 0
    return out


def _cum_at(date: str, ordered_dates: list, cum_by_date: dict) -> int:
    """Cumulative output tokens as of `date`: the running total at the latest rollup date
    <= date, or 0 if `date` precedes every rollup we have."""
    best = 0
    for d in ordered_dates:
        if d <= date:
            best = cum_by_date[d]
        else:
            break
    return best


def _load_markers(ordered_dates: list, cum_by_date: dict) -> list:
    out = []
    for ev in _lib.journal_read():
        action = ev.get("action")
        if action not in MARKER_ACTIONS:
            continue
        date = str(ev.get("ts") or "")[:10]
        text = ev.get("text") or ev.get("title") or ev.get("what") or ""
        ref = ev.get("id") or ev.get("evidence") or ""
        out.append({
            "date": date,
            "t_tokens_cum": _cum_at(date, ordered_dates, cum_by_date),
            "kind": MARKER_ACTIONS[action],
            "text": text,
            "ref": str(ref) if ref else "",
        })
    return out


def _load_analysis_labels_by_date() -> dict:
    """Fold analysis products into per-date label counts for the story's analysis lane.

    This is the 'programmatic postprocessing' half of the engine: the model labels raw events
    (obsctl analyze), and this function - plain code, no model - aggregates those labels by
    UTC date so the story can plot them. Newest product wins per date; absent products mean
    empty dicts, and the story renders identically to a system with analysis turned off.
    """
    analysis_dir = _lib.record_paths()["analysis"]
    if not analysis_dir.is_dir():
        return {}
    by_date: dict = {}
    for product_path in sorted(analysis_dir.glob("*-labels.json")):
        product = _lib.read_json(product_path, {}) or {}
        for ev in product.get("events") or []:
            ts = str(ev.get("_obs_ts") or "")
            date = ts[:10]
            if len(date) != 10:
                continue
            bucket = by_date.setdefault(date, {})
            for label in ev.get("labels") or []:
                bucket[label] = bucket.get(label, 0) + 1
    return by_date


def cmd_story(args) -> int:
    rollups = _load_rollups()
    ordered_dates = [r["date"] for r in rollups]
    tokens_out_by_date = {r["date"]: int((r.get("tokens") or {}).get("output") or 0) for r in rollups}
    running = 0
    cum_by_date = {}
    for d in ordered_dates:
        running += tokens_out_by_date.get(d, 0)
        cum_by_date[d] = running

    anatomy = _count_anatomy()
    data_lane = _count_data_series()
    analysis_by_date = _load_analysis_labels_by_date()

    points = []
    for r in rollups:
        d = r["date"]
        ops = r.get("events") or {}
        tok = r.get("tokens") or {}
        cost = r.get("cost") or {}
        points.append({
            "date": d,
            "t_tokens_cum": cum_by_date.get(d, 0),
            "operations": {
                "events": int(ops.get("total") or 0),
                "errors": int(ops.get("errors") or 0),
                "sessions": int(r.get("sessions") or 0),
                "tokens_out": int(tok.get("output") or 0),
                "cost_usd": cost.get("usd"),
            },
            "anatomy": dict(anatomy),
            "data": dict(data_lane),
            "analysis": analysis_by_date.get(d, {}),
        })

    markers = _load_markers(ordered_dates, cum_by_date)
    clock = {
        "wall_min": ordered_dates[0] if ordered_dates else "",
        "wall_max": ordered_dates[-1] if ordered_dates else "",
        "tokens_max": running,
    }
    feed = {"v": 1, "generated_at": _lib.utc_now(), "clock": clock, "points": points, "markers": markers}

    ok = validate_story_contract(feed)
    out_path = _lib.state_dir() / "story-feed.json"
    _lib.atomic_write_json(out_path, feed)
    print(f"points={len(points)} markers={len(markers)} contract_ok={ok} -> {out_path}")
    _lib.print_verdict("OBS", ok)
    return 0 if ok else 1


# --------------------------------------------------------------------------- selftest

def _run_selftest_roundtrip() -> bool:
    """spool -> seal -> rollup -> anchor on an ISOLATED scratch record (its own project
    root + record root), never the live one - a selftest that chmods today's real segment,
    truncates the live spool, or overwrites the committed anchor would be worse than no
    selftest at all."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="obsctl-selftest-") as td:
        tmp_root = Path(td) / "proj"
        tmp_record = Path(td) / "proj_claude_iff"
        (tmp_root / ".claude" / "config").mkdir(parents=True, exist_ok=True)
        (tmp_root / ".claude-iff" / "obs" / "rollups").mkdir(parents=True, exist_ok=True)
        real_cfg_dir = _lib.config_dir()
        for name in ("observe", "model-prices"):
            src = real_cfg_dir / f"{name}.json"
            if src.exists():
                shutil.copy(src, tmp_root / ".claude" / "config" / f"{name}.json")

        saved = {k: os.environ.get(k) for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_IFF_RECORD_ROOT")}
        os.environ["CLAUDE_PROJECT_DIR"] = str(tmp_root)
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(tmp_record)
        _lib.clear_config_cache()
        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            ev = {"_obs_ts": f"{yesterday}T00:00:00Z", "_obs_source": "selftest",
                  "hook_event_name": "SessionStart", "session_id": "selftest-session"}
            _lib.append_jsonl(tmp_record / "spool" / "selftest.jsonl", ev)

            if main(["seal"]) != 0:
                return False
            if main(["rollup", "--date", yesterday]) != 0:
                return False
            if main(["anchor"]) != 0:
                return False

            anchor = _lib.read_json(_lib.iff_dir() / "obs" / "anchor.json", {}) or {}
            seg_path = _lib.record_paths()["segments"] / f"{yesterday}.jsonl"
            if not seg_path.exists():
                return False
            expect = _lib.sha256_file(seg_path)
            entry = next((c for c in anchor.get("chain", []) if c.get("date") == yesterday), None)
            return bool(entry) and entry.get("sha256") == expect
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _lib.clear_config_cache()


def _verify_real_anchor() -> tuple:
    """Read-only check that the REAL, committed anchor's hashes match the REAL sealed
    segments on disk. Never writes anything. Returns (ok, warn)."""
    anchor = _lib.read_json(_lib.iff_dir() / "obs" / "anchor.json", None)
    if anchor is None:
        return True, True  # nothing anchored yet is not a failure, just unverified
    paths = _lib.record_paths()
    for entry in anchor.get("chain", []):
        seg = paths["segments"] / f"{entry.get('date')}.jsonl"
        if _lib.sha256_file(seg) != entry.get("sha256"):
            return False, False
    return True, False


def cmd_selftest(args) -> int:
    roundtrip_ok = _run_selftest_roundtrip()
    anchor_ok, warn = _verify_real_anchor()
    ok = roundtrip_ok and anchor_ok
    print(f"selftest: roundtrip={'ok' if roundtrip_ok else 'FAIL'} "
          f"real_anchor_verify={'ok' if anchor_ok else 'FAIL'}{' (warn: nothing anchored yet)' if warn else ''}")
    _lib.print_verdict("OBS", ok, warn and ok)
    return 0 if ok else 1


# --------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="obsctl", description="The OBSERVE spine CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="~/.claude/projects transcripts -> spool (token metadata only)")

    sp = sub.add_parser("seal", help="spool -> allowlisted segments (the LAW 3 boundary)")
    sp.add_argument("--date", help="seal only this UTC date (default: every date before today)")

    rp = sub.add_parser("rollup", help="segments -> .claude-iff/obs/rollups/<date>.json")
    rp.add_argument("--date", help="UTC date to roll up (default: today)")

    sub.add_parser("anchor", help="sealed segments -> .claude-iff/obs/anchor.json (tamper evidence)")

    rep = sub.add_parser("report", help="aggregate segments and print a table")
    rep.add_argument("--by", choices=["model", "day", "session"], required=True)

    sub.add_parser("story", help="build .claude/state/story-feed.json")
    sub.add_parser("size", help="record size by subtree (never deletes)")

    an = sub.add_parser("analyze", help="the one sanctioned agent pathway over raw")
    an.add_argument("--limit", type=int, default=None)
    an.add_argument("--dry-run", action="store_true")

    sub.add_parser("selftest", help="spool->seal->rollup->anchor round trip on a scratch record")
    return p


COMMANDS = {
    "ingest": cmd_ingest, "seal": cmd_seal, "rollup": cmd_rollup, "anchor": cmd_anchor,
    "report": cmd_report, "story": cmd_story, "size": cmd_size, "analyze": cmd_analyze,
    "selftest": cmd_selftest,
}


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
