#!/usr/bin/env python3
"""checkctl.py - the ritual runner: CHECK, POLISH, PUBLISH mechanics for /project-memory.

Three things live here and nowhere else.

1. THE STEP REGISTRY. System steps are NAMES bound to argv in code. config/memory.json chooses
   which names run in which phase; it never carries a shell string for them. A data file that
   executes strings is a config file only until someone notices.

2. THE GENERATOR LEDGER (law 1, anti-rot). Every generator this system has is registered here
   with its inputs and its output, runs ONLY through the ritual, and records a content hash in
   state/generators.json afterwards. Freshness is decided by SHA-256, never by mtime: git does
   not preserve mtimes, so an mtime rule fires at random on every fresh clone. The rule exists
   because in the source system the one regenerator nobody wired into the ritual sat frozen for
   two and a half months while everything downstream quietly served stale data.

3. THE TRANSACTION. state/memory-run.json records run id, phase and step. PUBLISH refuses to
   run unless POLISH completed for the SAME run id, so a half-built set of derived surfaces can
   never be committed as though it were whole. `--resume` continues a run that died.

Steps report OK / WARN / FAIL. WARN never blocks: incompleteness informs, incorrectness stops.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
BLOCKING = (FAIL,)


class Result:
    __slots__ = ("name", "status", "message", "details")

    def __init__(self, name: str, status: str, message: str = "", details=None):
        self.name = name
        self.status = status
        self.message = message
        self.details = details or []

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "message": self.message, "details": self.details}


# --------------------------------------------------------------------------- generators

# name -> (argv builder, input paths, output path). Inputs may be files or directories.
GENERATORS = {
    "map_scan": {
        "tool": "mapctl.py",
        "args": ["scan"],
        "inputs": [".claude/agents", ".claude/skills", ".claude/hooks", ".claude/tools",
                   ".claude/protocols", ".claude/config"],
        "output": ".claude/system-map/cards",
    },
    "map_compile": {
        "tool": "mapctl.py",
        "args": ["compile"],
        "inputs": [".claude/system-map/cards", ".claude/system-map/layers.json"],
        "output": ".claude/system-map/map.json",
    },
    "story_build": {
        "tool": "obsctl.py",
        "args": ["story"],
        "inputs": [".claude-iff/obs/rollups", ".claude/state/journal.jsonl"],
        "output": ".claude/state/story-feed.json",
    },
    "console_build": {
        "tool": "consolectl.py",
        "args": ["build"],
        "inputs": [".claude/console/console.template.html", ".claude/system-map/map.json",
                   ".claude/state/session.json", ".claude/state/story-feed.json",
                   ".claude/Project-log.jsonl"],
        "output": ".claude/console/console.html",
    },
    # The demo console on the docs site: the SAME payload and template as the real console,
    # marked serverless. Registered here so the published demo can never lag the system.
    "demo_build": {
        "tool": "consolectl.py",
        "args": ["build", "--demo", "--out", "docs/demo/console.html"],
        "inputs": [".claude/console/console.template.html", ".claude/system-map/map.json",
                   ".claude/state/session.json", ".claude/state/story-feed.json",
                   ".claude/Project-log.jsonl"],
        "output": "docs/demo/console.html",
    },
    # Inputs deliberately enumerate the component trees rather than saying ".claude": the
    # output lives inside .claude/dist, and an output inside its own input set would hash
    # itself stale forever.
    "dist_build": {
        "tool": "distctl.py",
        "args": ["build"],
        "inputs": [".claude/tools", ".claude/hooks", ".claude/skills", ".claude/agents",
                   ".claude/protocols", ".claude/config", ".claude/reference",
                   ".claude/system-map/layers.json", ".claude/system-map/cards",
                   ".claude/console/console.template.html", ".claude/console/console.py",
                   ".claude/settings.json", ".claude/README.md", ".claude-iff/README.md"],
        "output": ".claude/dist",
    },
}

# The two home-repo-only generators: meaningful where publishing the kit and the docs demo
# is the point (dot-claude-iff's own source repo), a privacy leak everywhere else - in an
# adopting project they would package THAT project's private .claude/ into redistributable
# zips and render its real session state into docs/. Gated by memory.json's
# distribution.enabled; an ABSENT key reads as false so the leak fails closed. Kits ship
# the knob false; this repo's own config carries true.
HOME_ONLY_GENERATORS = ("demo_build", "dist_build")


def distribution_enabled() -> bool:
    dist = _lib.load_config("memory").get("distribution") or {}
    return bool(dist.get("enabled", False))


def generator_gated_off(name: str) -> bool:
    return name in HOME_ONLY_GENERATORS and not distribution_enabled()


# Publish-phase steps: mechanical, ordered, not generators (their outputs live in the record).
PUBLISH_STEPS = {
    "obs_ingest": ("obsctl.py", ["ingest"]),
    "obs_seal": ("obsctl.py", ["seal"]),
    "obs_rollup": ("obsctl.py", ["rollup"]),
    "obs_anchor": ("obsctl.py", ["anchor"]),
}


def tool_path(name: str) -> Path:
    return _lib.tools_dir() / name


def run_tool(name: str, args: list, timeout: int = 300) -> tuple[int, str]:
    path = tool_path(name)
    if not path.exists():
        return 127, f"{name} not present"
    try:
        res = subprocess.run(
            [sys.executable, str(path), *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_lib.project_root()), check=False,
        )
        return res.returncode, (res.stdout or "") + (res.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"{name} {' '.join(args)} timed out after {timeout}s"
    except OSError as exc:
        return 126, f"{name} could not run: {exc}"


def verdict_of(output: str, tag: str) -> str | None:
    """Read the <TAG>_OK|WARN|FAIL token a tool prints. A tool that prints none has crashed
    in a way it did not anticipate, and the caller treats that as failure (fail closed)."""
    for token, status in ((f"{tag}_FAIL", FAIL), (f"{tag}_WARN", WARN), (f"{tag}_OK", OK)):
        if token in output:
            return status
    return None


# --------------------------------------------------------------------------- run state

def run_state_path() -> Path:
    return _lib.state_dir() / "memory-run.json"


def load_run() -> dict:
    return _lib.read_json(run_state_path(), {}) or {}


def save_run(run: dict) -> None:
    run["updated"] = _lib.utc_now()
    _lib.atomic_write_json(run_state_path(), run, durable=True)


def start_run(resume: bool = False) -> dict:
    run = load_run()
    # Resume a run whatever its status, including 'failed': the phases of one ritual belong to
    # one record, and a failed CHECK must stay visible inside it rather than being escaped by
    # simply running the next phase. PUBLISH is what refuses; see polish_complete().
    if resume and run.get("run_id"):
        return run
    run = {
        "run_id": f"{_lib.utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}",
        "started": _lib.utc_now(),
        "status": "running",
        "phase": None,
        "step": None,
        "phases": {},
        "last_completed": run.get("last_completed"),
    }
    save_run(run)
    return run


def record_phase(run: dict, phase: str, results: list, status: str) -> None:
    run["phases"][phase] = {
        "status": status,
        "ts": _lib.utc_now(),
        "results": [r.as_dict() for r in results],
    }
    run["phase"] = phase
    save_run(run)


# --------------------------------------------------------------------------- generator ledger

def generators_path() -> Path:
    return _lib.state_dir() / "generators.json"


def generator_inputs_hash(spec: dict) -> str:
    root = _lib.project_root()
    return _lib.sha256_paths([root / p for p in spec["inputs"]])


def generator_output_hash(spec: dict) -> str:
    root = _lib.project_root()
    return _lib.sha256_paths([root / spec["output"]])


def read_ledger() -> dict:
    return _lib.read_json(generators_path(), {"generators": {}}) or {"generators": {}}


def stamp_generator(name: str, spec: dict, run_id: str) -> None:
    ledger = read_ledger()
    ledger.setdefault("generators", {})[name] = {
        "ran_at": _lib.utc_now(),
        "run_id": run_id,
        "inputs_hash": generator_inputs_hash(spec),
        "output_hash": generator_output_hash(spec),
    }
    _lib.atomic_write_json(generators_path(), ledger, durable=True)


def generator_freshness_report() -> list:
    """Which generators are stale, and which have never run at all.

    Stale at CHECK time is normal: POLISH is about to rebuild. The finding that matters is a
    generator that has NEVER run, or one whose output vanished, because that is rot starting.
    """
    ledger = read_ledger().get("generators", {})
    root = _lib.project_root()
    findings = []
    for name, spec in GENERATORS.items():
        if generator_gated_off(name):
            findings.append((name, SKIP, "home-repo-only, disabled (memory.json distribution.enabled)"))
            continue
        if not tool_path(spec["tool"]).exists():
            findings.append((name, SKIP, f"{spec['tool']} not installed"))
            continue
        entry = ledger.get(name)
        output = root / spec["output"]
        if not entry:
            findings.append((name, WARN, "has never run through the ritual"))
            continue
        if not output.exists():
            findings.append((name, WARN, f"output missing: {spec['output']}"))
            continue
        if entry.get("inputs_hash") != generator_inputs_hash(spec):
            findings.append((name, WARN, "inputs changed since the last run (POLISH will rebuild)"))
            continue
        findings.append((name, OK, f"fresh as of {entry.get('ran_at')}"))
    return findings


# --------------------------------------------------------------------------- checks

def check_journal_parses() -> Result:
    path = _lib.journal_path()
    if not path.exists():
        return Result("journal_parses", WARN, "no journal yet (statectl.py start opens one)")
    total = bad = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            total += 1
            try:
                json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    if bad == 0:
        return Result("journal_parses", OK, f"{total} events parse")
    # A torn LAST line is the expected shape of a crash and readers tolerate it; more than one
    # bad line means something wrote to the journal without going through append_jsonl.
    status = WARN if bad == 1 else FAIL
    return Result("journal_parses", status, f"{bad} of {total} journal lines do not parse")


def check_heartbeat() -> Result:
    hb = _lib.read_json(_lib.state_dir() / "heartbeat.json")
    if not hb:
        return Result("heartbeat_present", WARN,
                      "no heartbeat yet: the Stop hook may not be firing (project hooks need trust)")
    age = _lib.age_seconds(hb.get("ts", ""))
    if age is None:
        return Result("heartbeat_present", WARN, "heartbeat has no readable timestamp")
    return Result("heartbeat_present", OK, f"last turn ended {int(age // 60)} min ago")


def check_generator_freshness() -> Result:
    findings = generator_freshness_report()
    stale = [f for f in findings if f[1] == WARN]
    details = [f"{name}: {msg}" for name, status, msg in findings if status != OK]
    if not stale:
        return Result("generator_freshness", OK, f"{len(findings)} generators tracked", details)
    return Result("generator_freshness", WARN,
                  f"{len(stale)} generator(s) need a POLISH rebuild", details)


def check_cards_lint() -> Result:
    code, out = run_tool("mapctl.py", ["lint"])
    if code == 127:
        return Result("cards_lint", SKIP, "mapctl.py not installed")
    status = verdict_of(out, "MAP")
    if status is None:
        return Result("cards_lint", FAIL, "mapctl lint printed no verdict token", out.splitlines()[-5:])
    detail = [line for line in out.splitlines() if line.strip() and not line.startswith("MAP_")]
    return Result("cards_lint", status, f"mapctl lint says {status}", detail[:20])


def _walk_leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            yield from _walk_leaves(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, list):
        if all(not isinstance(x, (dict, list)) for x in node):
            yield prefix, node
    else:
        yield prefix, node


def _resolve(node, dotted: str):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def check_config_registry() -> Result:
    registry = _lib.load_config("registry")
    entries = registry.get("entries") or []
    lint = registry.get("lint") or {}
    watched = lint.get("watched_files") or []
    exempt = set(lint.get("exempt_keys") or [])
    errors, warnings = [], []

    for entry in entries:
        target = entry.get("target") or {}
        kind = target.get("kind")
        key = entry.get("key", "?")
        if kind == "config":
            cfg = _lib.load_config(target.get("file", ""))
            if not cfg:
                errors.append(f"{key}: config file '{target.get('file')}' missing or unreadable")
                continue
            _, found = _resolve(cfg, target.get("path", ""))
            if not found:
                errors.append(f"{key}: dead card, {target.get('file')}.json has no '{target.get('path')}'")
        elif kind == "agent-frontmatter":
            agent = _lib.claude_dir() / "agents" / f"{target.get('file')}.md"
            if not agent.exists():
                errors.append(f"{key}: agent file {_lib.rel(agent)} does not exist")
                continue
            field = str(target.get("path", ""))
            head = agent.read_text(encoding="utf-8")[:1200]
            if f"{field}:" not in head:
                warnings.append(f"{key}: {_lib.rel(agent)} frontmatter has no '{field}' field")
        else:
            warnings.append(f"{key}: unknown target kind {kind!r}")

    registered = {e.get("key") for e in entries}
    for name in watched:
        cfg = _lib.load_config(name)
        for dotted, _value in _walk_leaves(cfg):
            # Any segment matching an exempt key exempts the leaf: `analyze.taxonomy` is
            # structural data wherever it sits, not a knob that wants its own card.
            if dotted in exempt or any(part in exempt for part in dotted.split(".")):
                continue
            if f"{name}.{dotted}" not in registered:
                warnings.append(f"{name}.{dotted}: tunable has no registry card")

    if errors:
        return Result("config_registry_lint", FAIL, f"{len(errors)} dead card(s)", errors + warnings[:10])
    if warnings:
        return Result("config_registry_lint", WARN, f"{len(warnings)} unregistered tunable(s)", warnings[:20])
    return Result("config_registry_lint", OK, f"{len(entries)} knobs registered, none dead")


def check_price_table() -> Result:
    cfg = _lib.load_config("model-prices")
    prices = cfg.get("per_million_tokens") or {}
    billing = str(cfg.get("billing", "api"))
    if billing == "subscription":
        # Usage is included in a plan: dollar cost is not applicable, so an empty price table
        # is the CORRECT state, not a gap to warn about. Token counts are still tracked.
        return Result("price_table", OK,
                      "subscription billing: token counts tracked, dollar costs not applicable")
    if prices:
        return Result("price_table", OK, f"{len(prices)} model(s) priced")
    return Result(
        "price_table", WARN,
        "price table is EMPTY: every cost figure will read 'unknown'. Fill "
        ".claude/config/model-prices.json with verified prices (never guessed), or set "
        "billing to 'subscription' there if this usage is included in a plan.",
    )


def check_record_size() -> Result:
    root = _lib.record_root()
    if not root.exists():
        return Result("record_size", OK, "record root not created yet")
    total = 0
    parts = {}
    for child in sorted(root.iterdir()):
        size = 0
        if child.is_dir():
            for f in child.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except OSError:
                        continue
        elif child.is_file():
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
        parts[child.name] = size
        total += size
    detail = [f"{name}: {_lib.human_bytes(size)}" for name, size in sorted(parts.items(), key=lambda kv: -kv[1])]
    warn_mb = int(_lib.load_config("observe").get("size_warn_mb", 2048))
    # Tilde-form, never the absolute path: this message is stored in state/memory-run.json,
    # which is committed, and a machine's home directory does not belong in a repo.
    message = f"record is {_lib.human_bytes(total)} at {_lib.tilde(root)}"
    if total > warn_mb * 1024 * 1024:
        return Result("record_size", WARN,
                      message + f" (over {warn_mb} MB; raw is kept on purpose, nothing is deleted "
                                f"automatically, but you should know)", detail)
    return Result("record_size", OK, message, detail)


def check_needs_human_sync() -> Result:
    code, out = run_tool("statectl.py", ["refresh"])
    if code == 127:
        return Result("needs_human_sync", SKIP, "statectl.py not installed")
    status = verdict_of(out, "STATE")
    if status is None:
        return Result("needs_human_sync", FAIL, "statectl refresh printed no verdict token")
    board = _lib.read_json(_lib.state_dir() / "needs-human.json", {}) or {}
    counts = (board.get("counts") or {}).get("by_band") or {}
    sev0, sev1 = counts.get("SEV0", 0), counts.get("SEV1", 0)
    if sev0:
        return Result("needs_human_sync", WARN, f"{sev0} SEV0 blocker(s) open: surface these now")
    return Result("needs_human_sync", status if status != OK else OK,
                  f"queue synced ({sev1} SEV1 open)" if sev1 else "queue synced")


def check_task_reality() -> Result:
    """Checkpoints are claims, not facts: verify the state files a task says it left behind."""
    tasks_dir = _lib.claude_dir() / "tasks"
    if not tasks_dir.exists():
        return Result("task_reality", OK, "no tasks")
    problems, active = [], 0
    for path in sorted(tasks_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "Status: done" in text:
            continue
        active += 1
        for line in text.splitlines():
            # Checkpoints are written by humans in markdown, so the label and the value both
            # arrive wrapped in emphasis: "- **State files:** `a.py`, b.py". Strip the markup
            # from both sides or every path inherits a stray asterisk and "does not exist"
            # becomes a lie about the file rather than a fact about the checkpoint.
            stripped = line.strip().lstrip("-*+ ").strip()
            label, _, value = stripped.partition(":")
            if label.strip().strip("*_ ").lower() != "state files":
                continue
            for token in value.split(","):
                # The strip set needs the space: after "**State files:**" the partition
                # leaves "** `a.py`" as the first token, and without " " in the set the
                # strip stops at the space and the leading backtick survives into the path.
                candidate = token.strip().strip("*_` ").strip()
                if not candidate or candidate.lower() in ("none", "n/a", "-", "-"):
                    continue
                if not (_lib.project_root() / candidate).exists():
                    problems.append(f"{path.name}: checkpoint names {candidate}, which does not exist")
    if problems:
        return Result("task_reality", WARN, f"{len(problems)} checkpoint(s) disagree with disk", problems)
    return Result("task_reality", OK, f"{active} active task(s), checkpoints match disk")


def check_no_machine_paths() -> Result:
    """No committed file may name this machine's home directory.

    An absolute home path in a committed file discloses the machine and its user, and breaks
    on every other checkout. This scans the shippable trees for THIS machine's home prefix,
    which makes the check portable: every machine polices its own leakage. Found here because
    two real leaks (the console payload's project.root, the record-size message stored in
    memory-run.json) reached a root commit before anyone greped.
    """
    home = str(Path.home())
    if not home or home == "/":
        return Result("no_machine_paths", OK, "no home prefix to scan for")
    root = _lib.project_root()
    skip_dirs = {"__pycache__", "dist", ".git"}
    skip_suffixes = {".zip", ".pyc", ".gz"}
    offenders = []
    scan_roots = [root / ".claude", root / ".claude-iff", root / ".github", root / "docs"]
    scan_files = [root / ".gitignore"]
    for base in scan_roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix in skip_suffixes:
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            scan_files.append(path)
    for path in scan_files:
        try:
            if home in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(_lib.rel(path))
        except OSError:
            continue
    if offenders:
        return Result("no_machine_paths", FAIL,
                      f"{len(offenders)} committed file(s) embed this machine's home path; "
                      f"use _lib.tilde() for display strings, or move the value to env/.env",
                      offenders[:15])
    return Result("no_machine_paths", OK, "no home-directory paths in shippable trees")


def _deliberately_ignored(rel: str) -> bool:
    """The system's own intended ignores: private reference material, console runtime,
    machine-local settings, caches. Everything else in the shippable trees is meant to be
    trackable, so an ignore rule catching it is a shadow, not a choice."""
    if rel.startswith(".claude/reference/private/"):
        return True
    if rel.endswith((".pyc", ".tmp", ".pid", ".log")):
        return True
    if "__pycache__" in rel:
        return True
    if rel.endswith("settings.local.json") or rel.endswith("/.env"):
        return True
    return False


def check_gitignore_shadowing() -> Result:
    """A generic ignore pattern (dist/, build/, *.zip) matches at ANY depth, so a repo's
    .gitignore can silently untrack shipped .claude/ paths - the adoption kits under
    .claude/dist/ vanished from git exactly this way in the field and nothing warned. Ask
    git itself: check-ignore over the shippable trees, warn on any hit that is not one of
    the system's own deliberate ignores."""
    root = _lib.project_root()
    if not (root / ".git").exists():
        return Result("gitignore_shadowing", SKIP, "not a git repository")
    candidates = []
    for base in (root / ".claude", root / ".claude-iff"):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".claude/dist/") or _deliberately_ignored(rel):
                continue
            candidates.append(rel)
    # The dist zips are probed by NAME, existing or not: in the home repo they must stay
    # reachable by git, and probing the path catches the shadow before the first build does.
    if distribution_enabled():
        candidates += [".claude/dist/dot-claude-iff-fresh.zip",
                       ".claude/dist/dot-claude-iff-adopt-kit.zip"]
    if not candidates:
        return Result("gitignore_shadowing", OK, "nothing to probe")
    try:
        res = subprocess.run(
            ["git", "check-ignore", "-v", "--stdin"],
            input="\n".join(candidates) + "\n",
            capture_output=True, text=True, timeout=30, cwd=str(root), check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("gitignore_shadowing", SKIP, f"git unavailable: {exc}")
    if res.returncode not in (0, 1):  # 0 = some path ignored, 1 = none ignored
        return Result("gitignore_shadowing", SKIP,
                      f"git check-ignore failed (exit {res.returncode})")
    hits = [line for line in res.stdout.splitlines() if line.strip()]
    if hits:
        return Result("gitignore_shadowing", WARN,
                      f"{len(hits)} shippable path(s) are gitignored: an over-broad pattern "
                      f"(a generic dist/, build/ or *.zip) is silently untracking them",
                      hits[:15])
    return Result("gitignore_shadowing", OK, f"{len(candidates)} shippable path(s), none shadowed")


def check_theme_token_parity() -> Result:
    """The console template defines its dark palette twice (the prefers-color-scheme media
    block and the explicit [data-theme="dark"] selector), with different indentation. A token
    added to one block but not the other ships a page that renders wrong in exactly one theme
    state, silently - it happened twice in one day (L-7: --heading, then --edge-*). This makes
    the parity mechanical: the two blocks must define the identical set of custom properties.
    """
    import re
    template = _lib.console_dir() / "console.template.html"
    if not template.exists():
        return Result("theme_token_parity", SKIP, "console template not installed")
    text = template.read_text(encoding="utf-8", errors="replace")

    def block_tokens(start_marker: str) -> set | None:
        start = text.find(start_marker)
        if start == -1:
            return None
        depth = 0
        i = text.find("{", start)
        begin = i
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        return set(re.findall(r"(--[\w-]+)\s*:", text[begin:i]))

    media = block_tokens("@media (prefers-color-scheme: dark)")
    explicit = block_tokens(':root[data-theme="dark"]')
    if media is None or explicit is None:
        return Result("theme_token_parity", WARN, "could not locate both dark blocks in the template")
    only_media = sorted(media - explicit)
    only_explicit = sorted(explicit - media)
    if only_media or only_explicit:
        details = ([f"only in @media block: {t}" for t in only_media]
                   + [f"only in [data-theme] block: {t}" for t in only_explicit])
        return Result("theme_token_parity", FAIL,
                      f"{len(only_media) + len(only_explicit)} dark-theme token(s) defined in "
                      f"one block but not the other; the page renders wrong in exactly one "
                      f"theme state", details)
    return Result("theme_token_parity", OK, f"{len(media)} dark tokens, both blocks agree")


CHECKS = {
    "journal_parses": check_journal_parses,
    "heartbeat_present": check_heartbeat,
    "generator_freshness": check_generator_freshness,
    "cards_lint": check_cards_lint,
    "config_registry_lint": check_config_registry,
    "price_table": check_price_table,
    "record_size": check_record_size,
    "needs_human_sync": check_needs_human_sync,
    "task_reality": check_task_reality,
    "no_machine_paths": check_no_machine_paths,
    "gitignore_shadowing": check_gitignore_shadowing,
    "theme_token_parity": check_theme_token_parity,
}


# --------------------------------------------------------------------------- project steps

def project_steps(kind: str) -> list:
    steps = (_lib.load_config("memory").get("project_steps") or {}).get(kind) or []
    return [s for s in steps if isinstance(s, dict) and s.get("argv")]


def run_project_step(step: dict) -> Result:
    argv = step.get("argv") or []
    name = step.get("name") or (argv[0] if argv else "project-step")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return Result(name, FAIL, "argv must be a list of strings (never a shell string)")
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=int(step.get("timeout", 900)),
                             cwd=str(_lib.project_root()), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        status = FAIL if step.get("required", True) else WARN
        return Result(name, status, f"could not run: {exc}")
    if res.returncode == 0:
        return Result(name, OK, "passed")
    status = FAIL if step.get("required", True) else WARN
    tail = [ln for ln in (res.stdout + res.stderr).splitlines() if ln.strip()][-8:]
    return Result(name, status, f"exit {res.returncode}", tail)


# --------------------------------------------------------------------------- phases

def phase_steps(phase: str) -> list:
    return (_lib.load_config("memory").get("phases") or {}).get(phase) or []


def run_check(run: dict) -> list:
    results = []
    for name in phase_steps("check"):
        fn = CHECKS.get(name)
        if fn is None:
            results.append(Result(name, FAIL, "unknown check name in memory.json phases.check"))
            continue
        run["step"] = name
        save_run(run)
        try:
            results.append(fn())
        except Exception as exc:  # a check that crashes is a failed check, never a silent pass
            results.append(Result(name, FAIL, f"check raised {type(exc).__name__}: {exc}"))
    results.extend(run_project_step(s) for s in project_steps("check"))
    return results


def run_polish(run: dict) -> list:
    results = []
    for name in phase_steps("polish"):
        spec = GENERATORS.get(name)
        if spec is None:
            results.append(Result(name, FAIL, "unknown generator name in memory.json phases.polish"))
            continue
        run["step"] = name
        save_run(run)
        if generator_gated_off(name):
            results.append(Result(name, SKIP,
                                  "home-repo-only generator, disabled by memory.json "
                                  "distribution.enabled (correct outside the source repo)"))
            continue
        if not tool_path(spec["tool"]).exists():
            results.append(Result(name, SKIP, f"{spec['tool']} not installed"))
            continue
        code, out = run_tool(spec["tool"], spec["args"])
        tag = {"mapctl.py": "MAP", "obsctl.py": "OBS", "consolectl.py": "CONSOLE",
               "distctl.py": "DIST"}[spec["tool"]]
        status = verdict_of(out, tag)
        if status is None:
            results.append(Result(name, FAIL, f"{spec['tool']} printed no verdict token (exit {code})",
                                  out.splitlines()[-5:]))
            continue
        if status != FAIL:
            stamp_generator(name, spec, run.get("run_id", "?"))
        results.append(Result(name, status, f"{spec['tool']} {' '.join(spec['args'])}"))
    results.extend(run_project_step(s) for s in project_steps("polish"))
    return results


def polish_complete(run: dict) -> tuple[bool, str]:
    """PUBLISH's precondition. A half-built set of derived surfaces must never be committed as
    if it were whole, so publish refuses unless polish finished in THIS run."""
    phases = run.get("phases") or {}
    check = phases.get("check")
    if not check:
        return False, ("CHECK has not run in this ritual (run id " + str(run.get("run_id")) +
                       "). Publishing without checking is how a broken state gets committed.")
    if check.get("status") == FAIL:
        failed = [r.get("name") for r in check.get("results", []) if r.get("status") == FAIL]
        return False, ("CHECK failed in this ritual (" + ", ".join(failed) + "). Fix it and rerun "
                       "CHECK; do not publish over a failed check.")
    phase = phases.get("polish")
    if not phase:
        return False, "POLISH has not run in this ritual (run id " + str(run.get("run_id")) + ")"
    if phase.get("status") == FAIL:
        return False, "POLISH failed in this ritual; fix it and rerun rather than publishing"
    ledger = read_ledger().get("generators", {})
    missed = [
        name for name in phase_steps("polish")
        if name in GENERATORS
        and not generator_gated_off(name)
        and tool_path(GENERATORS[name]["tool"]).exists()
        and ledger.get(name, {}).get("run_id") != run.get("run_id")
    ]
    if missed:
        return False, "these generators did not run in this ritual: " + ", ".join(missed)
    return True, "polish complete"


def run_publish(run: dict) -> list:
    ok, why = polish_complete(run)
    if not ok:
        return [Result("polish_complete", FAIL, why)]
    results = [Result("polish_complete", OK, why)]
    for name in phase_steps("publish"):
        spec = PUBLISH_STEPS.get(name)
        if spec is None:
            results.append(Result(name, FAIL, "unknown publish step in memory.json phases.publish"))
            continue
        run["step"] = name
        save_run(run)
        tool, args = spec
        if not tool_path(tool).exists():
            results.append(Result(name, SKIP, f"{tool} not installed"))
            continue
        code, out = run_tool(tool, args)
        status = verdict_of(out, "OBS")
        if status is None:
            results.append(Result(name, FAIL, f"{tool} printed no verdict token (exit {code})",
                                  out.splitlines()[-5:]))
            continue
        results.append(Result(name, status, f"{tool} {' '.join(args)}"))
    return results


PHASES = {"check": run_check, "polish": run_polish, "publish": run_publish}


# --------------------------------------------------------------------------- reporting

def summarize(results: list) -> str:
    counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return f"{counts[OK]} ok · {counts[WARN]} warn · {counts[FAIL]} fail · {counts[SKIP]} skipped"


def render(results: list, phase: str) -> None:
    glyph = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", SKIP: " skip "}
    print(f"\n{phase.upper()}")
    for r in results:
        print(f"[{glyph[r.status]}] {r.name}: {r.message}")
        for line in r.details[:10]:
            print(f"            {line}")
    print(f"\n{summarize(results)}")


# --------------------------------------------------------------------------- probes

def probe() -> list:
    """The probe ledger: one existence-and-shape probe per shipped component.

    This is what makes "it is built" a mechanical claim rather than a rhetorical one.
    """
    root = _lib.project_root()
    expected = [
        ("tool._lib", ".claude/tools/_lib.py"),
        ("tool.statectl", ".claude/tools/statectl.py"),
        ("tool.obsctl", ".claude/tools/obsctl.py"),
        ("tool.mapctl", ".claude/tools/mapctl.py"),
        ("tool.consolectl", ".claude/tools/consolectl.py"),
        ("tool.checkctl", ".claude/tools/checkctl.py"),
        ("tool.distctl", ".claude/tools/distctl.py"),
        ("hook.session-start", ".claude/hooks/session-start.sh"),
        ("hook.heartbeat", ".claude/hooks/heartbeat.sh"),
        ("hook.obs-capture", ".claude/hooks/obs-capture.sh"),
        ("hook.policy-gate", ".claude/hooks/policy-gate.sh"),
        ("hook.post-write-validate", ".claude/hooks/post-write-validate.sh"),
        ("agent.anatomist", ".claude/agents/anatomist.md"),
        ("agent.retro-analyst", ".claude/agents/retro-analyst.md"),
        ("agent.verifier", ".claude/agents/verifier.md"),
        ("skill.project-memory", ".claude/skills/project-memory/SKILL.md"),
        ("skill.plan-task", ".claude/skills/plan-task/SKILL.md"),
        ("skill.adopt", ".claude/skills/adopt/SKILL.md"),
        ("protocol.handshake", ".claude/protocols/handshake.md"),
        ("protocol.human-gates", ".claude/protocols/human-gates.md"),
        ("protocol.honesty", ".claude/protocols/honesty.md"),
        ("protocol.evolution", ".claude/protocols/evolution.md"),
        ("config.memory", ".claude/config/memory.json"),
        ("config.policy", ".claude/config/policy.json"),
        ("config.observe", ".claude/config/observe.json"),
        ("config.console", ".claude/config/console.json"),
        ("config.registry", ".claude/config/registry.json"),
        ("config.model-prices", ".claude/config/model-prices.json"),
        ("map.layers", ".claude/system-map/layers.json"),
        ("console.template", ".claude/console/console.template.html"),
        ("console.server", ".claude/console/console.py"),
        ("guide.claude-md", ".claude/CLAUDE.md"),
        ("guide.readme", ".claude/README.md"),
        ("memory.status", ".claude/STATUS.md"),
        ("memory.log", ".claude/Project-log.jsonl"),
        ("memory.lessons", ".claude/LESSONS.jsonl"),
        ("reference.glossary", ".claude/reference/glossary.md"),
        ("iff.readme", ".claude-iff/README.md"),
    ]
    results = []
    for name, path in expected:
        results.append(Result(name, OK if (root / path).exists() else FAIL, path))
    return results


# --------------------------------------------------------------------------- cli

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ritual runner: CHECK, POLISH, PUBLISH mechanics.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser(
        "run",
        help="run one ritual phase",
        description="CHECK opens a ritual; POLISH and PUBLISH continue the one it opened. "
                    "That is what lets PUBLISH verify POLISH ran in the SAME run id.",
    )
    run_cmd.add_argument("--phase", required=True, choices=sorted(PHASES))
    run_cmd.add_argument("--resume", action="store_true",
                         help="continue the current run even when starting with --phase check")
    run_cmd.add_argument("--new", action="store_true",
                         help="force a fresh run id (abandons any ritual in progress)")
    run_cmd.add_argument("--json", action="store_true")

    sub.add_parser("status", help="show the current ritual run")
    sub.add_parser("generators", help="list registered generators and their freshness")
    probe_cmd = sub.add_parser("probe", help="one existence probe per shipped component")
    probe_cmd.add_argument("--json", action="store_true")
    complete = sub.add_parser("complete", help="mark the ritual complete (called at the end of EVOLVE)")
    complete.add_argument("--note", default="")

    args = parser.parse_args(argv)

    if args.command == "status":
        run = load_run()
        if not run:
            print("no ritual has run yet")
        else:
            print(json.dumps(run, indent=2))
        _lib.print_verdict("CHECK", True)
        return 0

    if args.command == "generators":
        for name, status, message in generator_freshness_report():
            print(f"[{status:>4}] {name}: {message}")
        _lib.print_verdict("CHECK", True)
        return 0

    if args.command == "probe":
        results = probe()
        failed = [r for r in results if r.status == FAIL]
        if args.json:
            print(json.dumps([r.as_dict() for r in results], indent=2))
        else:
            render(results, "probe")
        _lib.print_verdict("CHECK", not failed)
        return 1 if failed else 0

    if args.command == "complete":
        run = load_run()
        if not run:
            print("no ritual run to complete")
            _lib.print_verdict("CHECK", False)
            return 1
        run["status"] = "done"
        run["last_completed"] = _lib.utc_now()
        run["step"] = None
        save_run(run)
        try:
            _lib.journal_append("note", text=f"ritual complete ({run['run_id']}) {args.note}".strip())
        except Exception:
            pass
        print(f"ritual {run['run_id']} complete")
        _lib.print_verdict("CHECK", True)
        return 0

    # CHECK opens a ritual; the later phases continue it. Without this, every phase invocation
    # would mint a new run id and PUBLISH's same-run-id precondition could never be satisfied
    # in normal use, which would train people to bypass the very check that protects them.
    continues = args.resume or args.phase != "check"
    run = start_run(resume=continues and not args.new)
    results = PHASES[args.phase](run)
    failed = [r for r in results if r.status == FAIL]
    warned = [r for r in results if r.status == WARN]
    status = FAIL if failed else (WARN if warned else OK)
    record_phase(run, args.phase, results, status)
    if failed:
        run["status"] = "failed"
        save_run(run)

    if args.json:
        print(json.dumps({
            "run_id": run["run_id"], "phase": args.phase, "status": status,
            "results": [r.as_dict() for r in results],
        }, indent=2))
    else:
        render(results, args.phase)
        print(f"run {run['run_id']}")

    _lib.print_verdict("CHECK", not failed, warn=bool(warned))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
