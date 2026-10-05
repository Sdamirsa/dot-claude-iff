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
import re
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
# `context_inputs` adds the guides, rules and imports ctxmap discovers: a folder guide can live
# in any folder, so those inputs cannot be declared here, only found.
GENERATORS = {
    "map_scan": {
        "tool": "mapctl.py",
        "args": ["scan"],
        "inputs": [".claude/agents", ".claude/skills", ".claude/hooks", ".claude/tools",
                   ".claude/protocols", ".claude/config"],
        "context_inputs": True,
        "output": ".claude/system-map/cards",
    },
    "map_compile": {
        "tool": "mapctl.py",
        "args": ["compile"],
        "inputs": [".claude/system-map/cards", ".claude/system-map/layers.json"],
        "context_inputs": True,
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
    paths = [root / p for p in spec["inputs"]]
    if spec.get("context_inputs"):
        try:
            import ctxmap
            paths += [root / p for p in ctxmap.context_input_paths()]
        except Exception:  # noqa: BLE001 - the ledger informs; a discovery bug must not stop POLISH
            pass
    return _lib.sha256_paths(paths)


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


def check_context_health() -> Result:
    """Folder context: nested guides, path-scoped rules, imports, the always-on budget.
    ctxmap.py is the engine (the same one `mapctl context` and the console use); this is only
    the CHECK binding. A project with no nested guides and no rules is OK, not a warning."""
    import ctxmap
    status, message, details = ctxmap.health_summary()
    return Result("context_health", status, message, details)


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
    if rel.startswith((".claude/reference/private/", ".claude/worktrees/")):
        return True
    if rel == ".claude/state/heartbeat.json":  # rewritten every turn; the Stop hook recreates it
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
    the system's own deliberate ignores. Under memory.json `visibility: ignored` the two trees
    are ignored on purpose, so there is nothing to shadow and the check is quiet."""
    root = _lib.project_root()
    if not (root / ".git").exists():
        return Result("gitignore_shadowing", SKIP, "not a git repository")
    try:
        if _lib.visibility(root) == "ignored":
            return _visibility_ignored_result(root)
    except _lib.LibError as exc:
        return Result("gitignore_shadowing", WARN, str(exc))
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


def _visibility_ignored_result(root: Path) -> Result:
    """`visibility: ignored`: a shadow is impossible by design, so the check stays quiet. It
    speaks only when the knob and git disagree: the managed block is missing (the trees would
    reach the next `git add -A`), or files under them are still tracked from before."""
    try:
        # Paths as arguments, not --stdin: text-mode stdin on Windows sends CRLF and git keeps
        # the CR as part of the path.
        res = subprocess.run(["git", "check-ignore", "--no-index", "--",
                              ".claude/STATUS.md", ".claude-iff/README.md"], capture_output=True,
                             text=True, timeout=30, cwd=str(root), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("gitignore_shadowing", SKIP, f"git unavailable: {exc}")
    if res.returncode not in (0, 1):
        return Result("gitignore_shadowing", SKIP, f"git check-ignore failed (exit {res.returncode})")
    ignored = {line.strip() for line in res.stdout.splitlines() if line.strip()}
    missing = [t for t, probe in ((".claude/", ".claude/STATUS.md"),
                                  (".claude-iff/", ".claude-iff/README.md")) if probe not in ignored]
    if missing:
        return Result("gitignore_shadowing", WARN,
                      f"visibility is ignored but git does not ignore {', '.join(missing)}: run "
                      f"`python3 .claude/tools/distctl.py gitignore --apply`")
    tracked = _git_ls(root, "--cached", "--", ".claude", ".claude-iff") or []
    if tracked:
        return Result("gitignore_shadowing", WARN,
                      f"visibility is ignored but {len(tracked)} file(s) under .claude/ or "
                      f".claude-iff/ are still tracked; untracking them is a human step that "
                      f"keeps the files: `git rm -r --cached .claude .claude-iff`", tracked[:15])
    return Result("gitignore_shadowing", SKIP,
                  "visibility is ignored: .claude/ and .claude-iff/ are gitignored on purpose")


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


def check_changelog_parity() -> Result:
    """Every released version keeps its pinned section in CHANGELOG.md. The release steps in
    the project-memory skill write the section; this is the mechanical half of that promise,
    so a tag can never quietly outrun its changelog. Home-repo-only: an adopting project has
    its own history and no CHANGELOG.md - same fail-closed knob as the home-only generators.
    """
    if not distribution_enabled():
        return Result("changelog_parity", SKIP,
                      "home-repo-only, disabled (memory.json distribution.enabled)")
    root = _lib.project_root()
    stamp = _lib.system_version()
    if not _lib.parse_version(stamp):
        return Result("changelog_parity", FAIL,
                      f"system_version {stamp!r} is not X.Y.Z or X.Y.Z-(alpha|beta|rc).N",
                      ["fix system_version in .claude/config/registry.json"])
    versions = {"v" + stamp}
    tags = _lib.git_output(["tag", "-l", "v*"], root=root) or ""
    versions |= {t for t in tags.split() if _lib.parse_version(t, tag=True)}
    try:
        text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return Result("changelog_parity", FAIL, "CHANGELOG.md is missing at the repo root",
                      [f"expected sections for: {', '.join(sorted(versions))}"])
    # Exact heading match (_lib.changelog_section): '## v0.3.0-alpha.1' must not satisfy v0.3.0.
    missing = sorted(v for v in versions if _lib.changelog_section(text, v) is None)
    if missing:
        return Result("changelog_parity", FAIL,
                      f"{len(missing)} released version(s) lack a CHANGELOG.md section",
                      [f"missing '## {v}'" for v in missing])
    return Result("changelog_parity", OK, f"{len(versions)} version(s) pinned in CHANGELOG.md")


# --------------------------------------------------------------------------- secrets placement
#
# A key in a committed file is the one mistake a later commit cannot undo: deleting the line
# leaves the key in history, so the only real fix is rotation. scan_secrets() is a plain
# function over any root, so another caller (an export folder, a release tree) applies the
# exact same rules. A finding names path, line, pattern and severity and NEVER the matched text
# or any slice of it: findings are printed to terminals and stored in memory-run.json, which is
# committed, and a check that leaked what it caught would be an incident of its own.
# Where keys DO belong: .claude/reference/secrets.md.

# Left boundary for the sk- families, so "risk-..." or "task-..." never reads as a key.
_SK = r"(?<![A-Za-z0-9_-])sk-"
SECRET_PATTERNS = (
    ("anthropic_key", re.compile(_SK + r"ant-[A-Za-z0-9_-]{20,}")),
    ("openrouter_key", re.compile(_SK + r"or-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(_SK + r"(?:(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{20,})")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{22,}")),
    ("aws_access_key", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}")),
    ("private_key", re.compile(r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
)
# Generic: a key-like NAME assigned a long literal. Weaker evidence than a vendor prefix, so it
# WARNs rather than blocks (incompleteness informs, incorrectness stops).
_SECRET_WORD = r"(?:api[_-]?key|apikey|secret|token|passw(?:or)?d)(?![a-z])"
GENERIC_QUOTED = re.compile(r"(?i)" + _SECRET_WORD
                            + r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*([\"'])([^\"'\s]{20,})\1")
GENERIC_BARE = re.compile(r"(?i)^\s*(?:export\s+|-\s+)?[A-Za-z0-9_.-]*?" + _SECRET_WORD
                          + r"[A-Za-z0-9_.-]*\s*[:=]\s*([^\s\"'#]{20,})\s*$")
SECRET_PRAGMA = "iff:allow-secret"
# The suite plants key-shaped fixtures (built at runtime); its folder is never scanned.
SECRET_SCAN_SKIP = (".claude/tools/tests/",)
# Never descended into, in any scan mode: git internals and other checkouts (agent worktrees).
# The record folder joins these at scan time when it sits inside the scanned root.
SECRET_SCAN_PRUNE = (".git/", ".claude/worktrees/")
SECRET_SCAN_PRUNE_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
                           ".mypy_cache", ".pytest_cache"}
SECRET_SCAN_MAX_BYTES = 2 * 1024 * 1024

# .mcp.json / settings.json structure: a NAME that says "credential". Split on _ - . and
# camelCase first, so MAX_TOKENS (a count) is not GITHUB_TOKEN (a credential).
_KEYLIKE_NAME = re.compile(r"(?:^|_)(?:api_?key|apikey|token|secret|client_?secret|password|"
                           r"passwd|pat|authorization|credentials?|private_?key|access_?key|"
                           r"bearer|cookie)(?:$|_)", re.I)
_ENV_EXPANSION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}")


def _finding(rel: str, line: int, pattern: str, severity: str) -> dict:
    return {"path": rel, "line": line, "pattern": pattern, "severity": severity}


def _under(rel: str, prefix: str) -> bool:
    """rel equals prefix or sits beneath it. normcase, per L-9: case-blind on Windows."""
    a = os.path.normcase(rel)
    b = os.path.normcase(prefix.rstrip("/"))
    return a == b or a.startswith(b + os.path.normcase("/"))


def _is_local_secret_file(rel: str) -> bool:
    """The per-user homes: their VALUES are never read; they must simply never be committed.
    `.env.production` and friends are not here: many stacks commit those on purpose, so they
    are scanned like any other file."""
    name = rel.rsplit("/", 1)[-1]
    return name in (".env", "settings.local.json") or (name.startswith(".env.") and name.endswith(".local"))


def _vendor_placeholder(match: str) -> bool:
    low = match.lower()
    return any(mark in low for mark in ("example", "xxxxxxxx", "placeholder", "redacted"))


def _generic_value_suspicious(value: str) -> bool:
    low = value.lower()
    if any(mark in low for mark in ("example", "placeholder", "your", "xxxx", "changeme",
                                    "change-me", "dummy", "redacted", "<", ">", "{{", "${",
                                    "$(", "://", "...", "***")):
        return False
    if value.startswith(("$", "%")) or re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        return False  # a reference to a variable, or a variable's NAME, not a value
    return bool(re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value))


def _vendor_match(value: str):
    for name, rx in SECRET_PATTERNS:
        for m in rx.finditer(value):
            if not _vendor_placeholder(m.group(0)):
                return name
    return None


def _scan_lines(rel: str, text: str) -> list:
    out = []
    for no, line in enumerate(text.split("\n"), 1):
        if SECRET_PRAGMA in line:
            continue
        vendor = [name for name, rx in SECRET_PATTERNS
                  if any(not _vendor_placeholder(m.group(0)) for m in rx.finditer(line))]
        out.extend(_finding(rel, no, name, FAIL) for name in vendor)
        if vendor:
            continue  # a vendor prefix already says more than the generic rule could
        for rx in (GENERIC_QUOTED, GENERIC_BARE):
            if any(_generic_value_suspicious(m.group(m.lastindex)) for m in rx.finditer(line)):
                out.append(_finding(rel, no, "generic_secret", WARN))
                break
    return out


def _keylike(name) -> bool:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name))
    return bool(_KEYLIKE_NAME.search(text.replace("-", "_").replace(".", "_")))


def _value_verdict(value):
    """For a value held under a key-like name: None when fine, else (pattern, severity).
    Fine means empty, a pure ${VAR} / ${VAR:-default} expansion, an expansion behind a short
    scheme word ("Bearer ${TOKEN}"), or too short / boolean / numeric to be a credential."""
    if not isinstance(value, str) or not value.strip():
        return None
    vendor = _vendor_match(value)
    if vendor:
        return vendor, FAIL
    residual = _ENV_EXPANSION.sub("", value).strip()
    if residual != value.strip():
        return ("literal", WARN) if re.search(r"[A-Za-z0-9_+/=-]{8,}", residual) else None
    if len(residual) < 8 or residual.isdigit() or residual.lower() in (
            "true", "false", "yes", "no", "on", "off", "null", "none"):
        return None
    return "literal", WARN


def _locate(lines: list, *needles) -> int:
    """Line number of the first needle found, for a finding inside parsed JSON. The needle
    may be the value itself: it is searched for, never reported."""
    for needle in needles:
        if not needle:
            continue
        for no, line in enumerate(lines, 1):
            if needle in line:
                return no
    return 1


def _mcp_pairs(spec: dict) -> list:
    """(name, value, locator) for every named value an MCP server entry carries."""
    import urllib.parse
    pairs = []
    for block in ("env", "headers"):
        named = spec.get(block)
        for key, value in named.items() if isinstance(named, dict) else ():
            pairs.append((key, value, json.dumps(value)[1:-1] if isinstance(value, str) else ""))
    args = spec.get("args") if isinstance(spec.get("args"), list) else []
    i = 0
    while i < len(args):
        arg = args[i]
        if isinstance(arg, str):
            flag = re.match(r"^-{1,2}([A-Za-z0-9_.-]+)=(.*)$", arg, re.S)
            bare = re.match(r"^-{1,2}([A-Za-z0-9_.-]+)$", arg)
            assign = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", arg, re.S)
            if flag:
                pairs.append((flag.group(1), flag.group(2), json.dumps(arg)[1:-1]))
            elif bare and i + 1 < len(args) and isinstance(args[i + 1], str) \
                    and not args[i + 1].startswith("-"):
                pairs.append((bare.group(1), args[i + 1], json.dumps(args[i + 1])[1:-1]))
                i += 1
            elif assign:
                pairs.append((assign.group(1), assign.group(2), json.dumps(arg)[1:-1]))
        i += 1
    url = spec.get("url")
    if isinstance(url, str):
        parts = urllib.parse.urlsplit(url)
        locator = json.dumps(url)[1:-1]
        pairs += [(k, v, locator) for k, v in urllib.parse.parse_qsl(parts.query)]
        try:
            if parts.password:
                pairs.append(("password", parts.password, locator))
        except ValueError:
            pass
    return pairs


def _scan_mcp(rel: str, text: str) -> list:
    try:
        data = json.loads(text)
    except ValueError:
        return [_finding(rel, 1, "mcp_unparsable", WARN)]
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    lines, out = text.split("\n"), []
    for spec in servers.values():
        if not isinstance(spec, dict):
            continue
        for name, value, locator in _mcp_pairs(spec):
            verdict = _value_verdict(value) if _keylike(name) else None
            if verdict:
                pattern, severity = verdict
                out.append(_finding(rel, _locate(lines, locator, json.dumps(name)),
                                    "mcp_literal" if pattern == "literal" else pattern, severity))
    return out


def _scan_settings(rel: str, text: str) -> list:
    """Committed settings.json: its env block is shared with everyone who clones the repo."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return []
    lines, out = text.split("\n"), []
    for name, value in env.items():
        if not isinstance(value, str):
            continue
        vendor = _vendor_match(value)
        verdict = (vendor, FAIL) if vendor else (_value_verdict(value) if _keylike(name) else None)
        if verdict:
            pattern, severity = verdict
            out.append(_finding(rel, _locate(lines, json.dumps(value)[1:-1], json.dumps(name)),
                                "settings_env_literal" if pattern == "literal" else pattern, severity))
    return out


def _git_ls(root: Path, *args) -> list | None:
    try:
        res = subprocess.run(["git", "ls-files", "-z", *args], cwd=str(root),
                             capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [p for p in res.stdout.decode("utf-8", errors="replace").split("\0") if p]


def _is_git_toplevel(root: Path) -> bool:
    """git mode only when root IS a work tree's top: a broken .git inside some other repo would
    otherwise list that repo's (empty) view of this folder and read as a clean scan."""
    top = _lib.git_output(["rev-parse", "--show-toplevel"], root=root)
    try:
        return bool(top) and os.path.samefile(top, root)
    except OSError:
        return False


def _walk_files(root: Path, prune: list) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if d not in SECRET_SCAN_PRUNE_NAMES
                             and not any(_under(rel_dir + d, p) for p in prune))
        out.extend(rel_dir + name for name in filenames)
    return sorted(out)


def _secret_scan(root, files=None, *, allow_local: bool = False, allow_paths=None):
    """scan_secrets() plus the scan stats the ritual reports. Returns (findings, stats)."""
    import fnmatch
    root = Path(root)
    if allow_paths is None:
        configured = _lib.config_get("policy", "secrets.allow_paths", [])
        allow_paths = [str(p) for p in configured] if isinstance(configured, list) else []
    prune = list(SECRET_SCAN_PRUNE)
    try:
        record_rel = _lib.record_root().resolve().relative_to(root.resolve()).as_posix()
        if record_rel != ".":
            prune.append(record_rel + "/")
    except (ValueError, OSError):
        pass  # the usual case: the record is a sibling folder, outside any scanned root

    tracked: set = set()
    if files is not None:
        mode, rels = "files", []
        for f in files:
            p = Path(f)
            if p.is_absolute():
                try:
                    p = p.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
            rels.append(p.as_posix())
    else:
        listed = None
        if (root / ".git").exists() and _is_git_toplevel(root):
            # Tracked plus untracked-but-not-ignored: exactly what the next `git add -A` carries.
            cached = _git_ls(root, "--cached")
            others = _git_ls(root, "--others", "--exclude-standard")
            if cached is not None and others is not None:
                tracked, listed = set(cached), sorted(set(cached) | set(others))
        mode, rels = ("git", listed) if listed is not None else ("walk", _walk_files(root, prune))

    stats = {"mode": mode, "scanned": 0, "allowed": 0, "skipped_large": 0, "skipped_binary": 0}
    findings = []
    for rel in rels:
        if any(_under(rel, p) for p in prune) or any(_under(rel, p) for p in SECRET_SCAN_SKIP) \
                or any(part in SECRET_SCAN_PRUNE_NAMES for part in rel.split("/")[:-1]):
            continue
        if any(_under(rel, p) or fnmatch.fnmatch(rel, p) for p in allow_paths):
            stats["allowed"] += 1
            continue
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        if _is_local_secret_file(rel):
            if rel in tracked:
                findings.append(_finding(rel, 0, "local_secret_file_tracked", FAIL))
            elif not allow_local:
                findings.append(_finding(rel, 0, "local_secret_file", FAIL))
            continue  # the sanctioned home's values are never read
        try:
            if path.stat().st_size > SECRET_SCAN_MAX_BYTES:
                stats["skipped_large"] += 1
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            stats["skipped_binary"] += 1
            continue
        text = raw.decode("utf-8", errors="replace")
        stats["scanned"] += 1
        findings.extend(_scan_lines(rel, text))
        if os.path.normcase(rel) == os.path.normcase(".mcp.json"):
            findings.extend(_scan_mcp(rel, text))
        elif os.path.normcase(rel) == os.path.normcase(".claude/settings.json"):
            findings.extend(_scan_settings(rel, text))

    seen, unique = set(), []
    for f in findings:
        key = (f["path"], f["line"], f["pattern"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    strong = {(f["path"], f["line"]) for f in unique if f["pattern"] != "generic_secret"}
    unique = [f for f in unique if f["pattern"] != "generic_secret" or (f["path"], f["line"]) not in strong]
    unique.sort(key=lambda f: (f["path"], f["line"], f["pattern"]))
    return unique, stats


def scan_secrets(root, files=None, *, allow_local: bool = False, allow_paths=None) -> list:
    """Scan a tree for secrets in the wrong place. Returns findings, each
    {path (relative, posix), line (0 = whole file), pattern, severity (FAIL|WARN)} and never
    the matched value.

    Scope: `files` (relative to root) when given; else, when root is a git work tree, tracked
    plus untracked-not-ignored files (what `git add -A` would commit); else a pruned walk.
    Never enters .git/, .claude/worktrees/ or the record folder; skips the suite's tests
    folder, binaries, files over SECRET_SCAN_MAX_BYTES, lines carrying `iff:allow-secret` and
    paths in policy.json secrets.allow_paths (or `allow_paths` when passed).

    Per-user secret files (.env, .env*.local, settings.local.json) are never read. Tracked,
    they always FAIL. Present but untracked, they FAIL unless allow_local=True - the ritual
    passes True and asserts their ignore status separately; a folder about to be published
    (an export) should keep the strict default."""
    return _secret_scan(root, files, allow_local=allow_local, allow_paths=allow_paths)[0]


def secret_ignore_rows(root) -> list:
    """(status, rel, message) per per-user secret file present - at the root, at
    .claude/settings.local.json, or anywhere git sees it unignored: it must be gitignored.
    SKIP outside a git repo. A TRACKED one is scan_secrets()'s finding, not a row."""
    import fnmatch
    root = Path(root)
    rels = [p.name for p in sorted(root.glob(".env*")) if p.is_file() and _is_local_secret_file(p.name)]
    if (root / ".claude" / "settings.local.json").is_file():
        rels.append(".claude/settings.local.json")
    is_git = (root / ".git").exists()
    if is_git:
        rels += [r for r in _git_ls(root, "--others", "--exclude-standard") or []
                 if _is_local_secret_file(r) and r not in rels
                 and not any(_under(r, p) for p in SECRET_SCAN_PRUNE)]
    allow = _lib.config_get("policy", "secrets.allow_paths", [])
    allow = [str(p) for p in allow] if isinstance(allow, list) else []
    rels = [r for r in rels if not any(_under(r, p) or fnmatch.fnmatch(r, p) for p in allow)]
    if not rels:
        return []
    if not is_git:
        return [(SKIP, rel, "present; not a git repository, so its ignore status cannot be checked")
                for rel in rels]
    tracked = set(_git_ls(root, "--cached") or [])
    rels = [r for r in rels if r not in tracked]
    if not rels:
        return []
    try:
        res = subprocess.run(["git", "check-ignore", "--", *rels], cwd=str(root),
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return [(SKIP, rel, f"git unavailable ({type(exc).__name__})") for rel in rels]
    if res.returncode not in (0, 1):  # 0 = some ignored, 1 = none ignored, else an error
        return [(SKIP, rel, f"git check-ignore failed (exit {res.returncode})") for rel in rels]
    ignored = {os.path.normcase(line.strip()) for line in res.stdout.splitlines() if line.strip()}
    return [(OK, rel, "gitignored") if os.path.normcase(rel) in ignored
            else (FAIL, rel, "exists and is NOT gitignored: the next `git add -A` commits it")
            for rel in rels]


def check_secrets_placement() -> Result:
    root = _lib.project_root()
    findings, stats = _secret_scan(root, allow_local=True)
    rows = secret_ignore_rows(root)
    order = {FAIL: 0, WARN: 1, SKIP: 2}
    lines = [(f["severity"], f"{f['severity']} {f['path']}{':' + str(f['line']) if f['line'] else ''} "
                             f"{f['pattern']}") for f in findings]
    lines += [(status, f"{status} {rel}: {msg}") for status, rel, msg in rows if status != OK]
    details = [text for _status, text in sorted(lines, key=lambda x: order.get(x[0], 3))]
    fails = sum(1 for status, _ in lines if status == FAIL)
    warns = sum(1 for status, _ in lines if status == WARN)
    scope = {"git": "tracked + unignored files", "walk": "a walk of the tree (not a git repo)",
             "files": "the given files"}[stats["mode"]]
    if fails:
        return Result("secrets_placement", FAIL,
                      f"{fails} secret(s) in the wrong place ({scope}): keys belong in the env "
                      f"block of .claude/settings.local.json or behind ${{VAR}} in .mcp.json "
                      f"(.claude/reference/secrets.md); a key that reached a commit must be "
                      f"rotated, not just deleted", details)
    if warns:
        return Result("secrets_placement", WARN,
                      f"{warns} possible secret(s) to review ({scope}); a false alarm takes an "
                      f"`{SECRET_PRAGMA}` comment or a policy.json secrets.allow_paths entry", details)
    ignored = sum(1 for status, _r, _m in rows if status == OK)
    tail = f"; {ignored} per-user secret file(s) gitignored" if ignored else ""
    return Result("secrets_placement", OK,
                  f"{stats['scanned']} file(s) scanned ({scope}), no key-shaped strings{tail}", details)


CHECKS = {
    "journal_parses": check_journal_parses,
    "heartbeat_present": check_heartbeat,
    "generator_freshness": check_generator_freshness,
    "cards_lint": check_cards_lint,
    "context_health": check_context_health,
    "config_registry_lint": check_config_registry,
    "price_table": check_price_table,
    "record_size": check_record_size,
    "needs_human_sync": check_needs_human_sync,
    "task_reality": check_task_reality,
    "no_machine_paths": check_no_machine_paths,
    "gitignore_shadowing": check_gitignore_shadowing,
    "theme_token_parity": check_theme_token_parity,
    "changelog_parity": check_changelog_parity,
    "secrets_placement": check_secrets_placement,
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
        ("tool.ctxmap", ".claude/tools/ctxmap.py"),
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
        ("skill.adhd", ".claude/skills/adhd/SKILL.md"),
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
        ("config.brainstorm", ".claude/config/brainstorm.json"),
        ("config.publish", ".claude/config/publish.json"),
        ("map.layers", ".claude/system-map/layers.json"),
        ("console.template", ".claude/console/console.template.html"),
        ("console.server", ".claude/console/console.py"),
        ("guide.claude-md", ".claude/CLAUDE.md"),
        ("guide.readme", ".claude/README.md"),
        ("memory.status", ".claude/STATUS.md"),
        ("memory.log", ".claude/Project-log.jsonl"),
        ("memory.lessons", ".claude/LESSONS.jsonl"),
        ("reference.glossary", ".claude/reference/glossary.md"),
        ("reference.secrets", ".claude/reference/secrets.md"),
        ("reference.public-private", ".claude/reference/public-private.md"),
        ("iff.readme", ".claude-iff/README.md"),
    ]
    results = []
    for name, path in expected:
        results.append(Result(name, OK if (root / path).exists() else FAIL, path))
    return results


# --------------------------------------------------------------------------- doctor
#
# `checkctl doctor`: can this machine run the system at all? One row per prerequisite, each
# non-OK row with a one-line fix. READ-ONLY by contract: no state file, no run record, no
# record folder is created - a diagnostic that writes is one more thing that can break.
# Features this install may not have yet (mode, phase, ritual ticket) read as SKIP, not FAIL.

MIN_PYTHON = (3, 8)
EXPECTED_HOOKS = ("session-start.sh", "heartbeat.sh", "obs-capture.sh", "policy-gate.sh",
                  "post-write-validate.sh")
DOCTOR_CONFIGS = ("memory", "policy", "observe", "console", "registry", "model-prices")
WORK_MODES = ("freestyle", "guided", "fableous")
LIFECYCLE_PHASES = ("plan", "build", "review", "deploy")
# Path fragments of the common sync clients (lowercased, posix form). A heuristic: an OK row
# says "no marker found", never "not synced".
CLOUD_SYNC_MARKERS = ("dropbox", "onedrive", "icloud", "google drive", "googledrive",
                      "/mobile documents/", "/library/cloudstorage/")


def _path_under(child: Path, parent: Path) -> bool:
    try:
        a = os.path.normcase(str(Path(child).resolve()))
        b = os.path.normcase(str(Path(parent).resolve()))
    except OSError:
        return False
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _windows_documents_redirect() -> Path | None:
    """Known Folder Move: Documents silently redirected into OneDrive while the visible path
    still reads C:/Users/<name>/Documents. Read-only registry lookup; None off Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, "Personal")
    except (ImportError, OSError):
        return None
    expanded = os.path.expandvars(str(value))
    return Path(expanded) if "onedrive" in expanded.lower() else None


def cloud_sync_reason(path: Path) -> str | None:
    text = str(path).replace("\\", "/").lower()
    for marker in CLOUD_SYNC_MARKERS:
        if marker in text:
            return f"its path contains '{marker.strip('/')}'"
    redirected = _windows_documents_redirect()
    if redirected is not None:
        bases = [redirected]
        if os.environ.get("USERPROFILE"):
            bases.append(Path(os.environ["USERPROFILE"]) / "Documents")
        if any(_path_under(path, base) for base in bases):
            return "it sits under Documents, which Windows redirects into OneDrive"
    return None


def _doctor_python():
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return Result("python", OK, f"{text} (needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)"), ""
    return (Result("python", FAIL, f"{text} is older than {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"),
            f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")


def _doctor_python3():
    import shutil
    exe = shutil.which("python3")
    fix = ("make `python3` resolve to a real Python 3 in bash (on Windows the Microsoft Store "
           "stub answers instead: install from python.org or add a python3 shim)")
    if not exe:
        return Result("python3", FAIL, "`python3` is not on PATH, and every hook shells out to it"), fix
    try:
        res = subprocess.run([exe, "-c", "import sys; print(sys.version_info[0])"],
                             capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("python3", FAIL, f"`python3` could not run: {type(exc).__name__}"), fix
    if res.returncode == 0 and res.stdout.strip() == "3":
        return Result("python3", OK, "`python3` runs Python 3 (the hooks can execute)"), ""
    return Result("python3", FAIL, "`python3` on PATH does not run Python 3"), fix


def _doctor_bash():
    found = _lib.find_bash()
    if found:
        return Result("bash", OK, "bash found (the hooks are bash scripts)"), ""
    return (Result("bash", FAIL, "no usable bash: no hook can run (on Windows the WSL launcher "
                                 "in System32 does not count)"),
            "install bash (on Windows: Git for Windows, which ships Git Bash)")


def _doctor_git():
    import shutil
    if not shutil.which("git"):
        return (Result("git", WARN, "git is not on PATH: history, push and the ritual's git checks "
                                    "are unavailable"), "install git")
    version = _lib.git_output(["--version"]) or "git"
    if not (_lib.project_root() / ".git").exists():
        return (Result("git", WARN, f"{version}; this project is not a git repository (gitignore "
                                    f"and secrets-ignore checks SKIP)"), "git init")
    return Result("git", OK, f"{version}; project is a git repository"), ""


def _doctor_config():
    bad = []
    for name in DOCTOR_CONFIGS:
        path = _lib.config_dir() / f"{name}.json"
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            bad.append(f"{name}.json missing")
        except (OSError, ValueError):
            bad.append(f"{name}.json does not parse")
    if bad:
        return (Result("config", FAIL, "; ".join(bad)),
                "fix the JSON or restore the file from the kit (a malformed policy.json drops "
                "the gate to its protected-paths fallback)")
    return Result("config", OK, f"{len(DOCTOR_CONFIGS)} config file(s) parse"), ""


def _doctor_hooks():
    settings = _lib.claude_dir() / "settings.json"
    fix = "restore the hooks block of .claude/settings.json and .claude/hooks/ from the kit"
    if not settings.exists():
        return Result("hooks", FAIL, "no .claude/settings.json: no hook is wired"), fix
    data = _lib.read_json(settings)
    if not isinstance(data, dict):
        return Result("hooks", FAIL, ".claude/settings.json does not parse"), fix
    commands = []
    for groups in (data.get("hooks") or {}).values() if isinstance(data.get("hooks"), dict) else ():
        for group in groups if isinstance(groups, list) else ():
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else ():
                if isinstance(hook, dict) and hook.get("command"):
                    commands.append(str(hook["command"]))
    if not commands:
        return Result("hooks", FAIL, ".claude/settings.json wires no hook commands"), fix
    hooks_dir = _lib.claude_dir() / "hooks"
    scripts = sorted({m for c in commands for m in re.findall(r"\.claude/hooks/([A-Za-z0-9_.-]+)", c)})
    missing = [s for s in scripts if not (hooks_dir / s).is_file()]
    # The command string runs the script directly, so on POSIX it needs its executable bit.
    not_exec = [s for s in scripts if os.name == "posix" and (hooks_dir / s).is_file()
                and not os.access(hooks_dir / s, os.X_OK)]
    if missing or not_exec:
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + \
                ([f"not executable: {', '.join(not_exec)}"] if not_exec else [])
        return (Result("hooks", FAIL, "wired hook script(s) cannot run - " + "; ".join(parts)),
                "restore .claude/hooks/ from the kit; on Linux/macOS: chmod +x .claude/hooks/*.sh")
    unwired = [s for s in EXPECTED_HOOKS if s not in scripts]
    if unwired:
        return Result("hooks", WARN, f"shipped hook(s) not wired: {', '.join(unwired)}"), fix
    return Result("hooks", OK, f"{len(commands)} hook command(s) wired, {len(scripts)} script(s) "
                               f"present (Claude Code still asks you to trust them once)"), ""


def _doctor_record_root():
    fix = ("set CLAUDE_IFF_RECORD_ROOT in the repo-root .env (or policy.record_root) to a "
           "writable folder outside the repo")
    try:
        rr = _lib.record_root()
    except Exception as exc:  # noqa: BLE001 - a resolver that raises is the finding
        return Result("record_root", FAIL, f"cannot be resolved: {type(exc).__name__}: {exc}"), fix
    shown = _lib.tilde(rr)
    if _path_under(rr, _lib.project_root()):
        return Result("record_root", FAIL, f"{shown} is INSIDE the repo, where git can reach the "
                                           f"verbatim record"), fix
    if rr.exists():
        if not rr.is_dir():
            return Result("record_root", FAIL, f"{shown} exists but is not a directory"), fix
        if not os.access(rr, os.W_OK):
            return Result("record_root", FAIL, f"{shown} is not writable"), fix
        return Result("record_root", OK, f"{shown} (exists, writable)"), ""
    parent = rr.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not os.access(parent, os.W_OK):
        return Result("record_root", FAIL, f"{shown} cannot be created: {_lib.tilde(parent)} is "
                                           f"not writable"), fix
    return Result("record_root", OK, f"{shown} (created on first capture)"), ""


def _doctor_cloud_sync():
    try:
        reason = cloud_sync_reason(_lib.record_root())
    except Exception as exc:  # noqa: BLE001
        return Result("record_cloud_sync", SKIP, f"not checked: {type(exc).__name__}"), ""
    if reason:
        return (Result("record_cloud_sync", WARN, f"the record root looks cloud-synced ({reason}): "
                                                  f"verbatim prompts and file contents would upload"),
                "point CLAUDE_IFF_RECORD_ROOT (repo-root .env) at a folder outside the synced tree")
    return Result("record_cloud_sync", OK, "no cloud-sync marker in the record root's path"), ""


def _doctor_console():
    import socket
    cfg = _lib.load_config("console")
    host = str(cfg.get("host", "127.0.0.1"))
    if not (host in ("::1", "localhost") or host.startswith("127.")):
        return (Result("console", FAIL, f"console.host is {host!r}: the console serves live state "
                                        f"and must bind loopback only"),
                "set host to 127.0.0.1 in .claude/config/console.json")
    port = _lib.console_port(cfg)
    if not 1 <= port <= 65535:
        return (Result("console", FAIL, f"console.port {port} is not a valid port"),
                'set port to "auto" in .claude/config/console.json')
    how = "explicit" if isinstance(cfg.get("port"), int) else "derived from the folder name"
    try:
        with socket.create_connection((host, port), timeout=0.25):
            live = "something is listening there"
    except OSError:
        live = "nothing listening yet (session start autostarts it)"
    return Result("console", OK, f"{_lib.console_url(cfg)} (port {how}); {live}"), ""


def _doctor_heartbeat():
    result = check_heartbeat()
    result.name = "heartbeat"
    fix = "" if result.status == OK else ("trust the project's hooks when Claude Code asks (or via "
                                          "/hooks), then end one turn")
    return result, fix


def _doctor_secrets():
    result = check_secrets_placement()
    fix = "" if result.status == OK else ("move keys to the env block of .claude/settings.local.json "
                                          "or ${VAR} in .mcp.json; see .claude/reference/secrets.md")
    return result, fix


def _session_block() -> dict:
    data = _lib.read_json(_lib.state_dir() / "session.json", {}) or {}
    session = data.get("session") if isinstance(data, dict) else None
    return session if isinstance(session, dict) else {}


def _doctor_mode():
    mode = _session_block().get("mode")
    if not mode:
        return (Result("mode", SKIP, "no mode recorded: freestyle applies"),
                "none needed; `statectl mode` sets one where this install has it")
    if mode in WORK_MODES:
        return Result("mode", OK, f"mode: {mode}"), ""
    return Result("mode", WARN, f"unknown mode {mode!r}"), f"set one of: {', '.join(WORK_MODES)}"


def _doctor_phase():
    phase = _session_block().get("phase")
    if phase in LIFECYCLE_PHASES:
        return Result("phase", OK, f"phase: {phase}"), ""
    note = f"session phase {phase!r} is not a lifecycle phase" if phase else "no lifecycle phase recorded"
    return (Result("phase", SKIP, f"{note} ({'|'.join(LIFECYCLE_PHASES)})"),
            "none needed; `statectl phase` sets one where this install has it")


def _doctor_ritual_ticket():
    path = _lib.state_dir() / "ritual-ticket.json"
    if not path.exists():
        return (Result("ritual_ticket", SKIP, "no ritual ticket (one is written when you type "
                                              "/project-memory or /adopt)"), "none needed")
    data = _lib.read_json(path)
    if not isinstance(data, dict):
        return Result("ritual_ticket", WARN, "ritual-ticket.json does not parse"), \
            "type /project-memory to issue a fresh one"
    stamp = next((data[k] for k in ("ts", "issued", "issued_at", "created") if data.get(k)), None)
    age = _lib.age_seconds(stamp) if stamp else None
    if age is None:
        import time
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            age = None
    when = f"issued {int(age // 60)} min ago" if age is not None else "issue time unreadable"
    return Result("ritual_ticket", OK, f"present, {when}"), ""


DOCTOR_ROWS = (
    _doctor_python, _doctor_python3, _doctor_bash, _doctor_git, _doctor_config, _doctor_hooks,
    _doctor_record_root, _doctor_cloud_sync, _doctor_console, _doctor_heartbeat,
    _doctor_secrets, _doctor_mode, _doctor_phase, _doctor_ritual_ticket,
)


def doctor() -> list:
    """[(Result, fix_hint)], one per prerequisite. A row that raises is a FAIL row (fail
    closed), never a crashed doctor."""
    rows = []
    for fn in DOCTOR_ROWS:
        name = fn.__name__.replace("_doctor_", "")
        try:
            rows.append(fn())
        except Exception as exc:  # noqa: BLE001
            rows.append((Result(name, FAIL, f"row raised {type(exc).__name__}: {exc}"),
                         "report this: the doctor itself is broken here"))
    return rows


def render_doctor(rows: list) -> None:
    print("DOCTOR (read-only: nothing is written)")
    width = max(len(r.name) for r, _ in rows)
    for result, fix in rows:
        print(f"[{result.status:<4}] {result.name:<{width}}  {result.message}")
        pad = " " * (width + 9)
        for line in result.details[:10] if result.status != OK else []:
            print(f"{pad}{line}")
        if fix and result.status != OK:
            print(f"{pad}fix: {fix}")
    print(f"\n{summarize([r for r, _ in rows])}")


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
    doctor_cmd = sub.add_parser("doctor", help="read-only: can this machine run the system? one "
                                               "row per prerequisite, exit 1 on any FAIL")
    doctor_cmd.add_argument("--json", action="store_true")
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

    if args.command == "doctor":
        rows = doctor()
        failed = any(r.status == FAIL for r, _ in rows)
        warned = any(r.status == WARN for r, _ in rows)
        if args.json:
            print(json.dumps([dict(r.as_dict(), fix=fix) for r, fix in rows], indent=2))
        else:
            render_doctor(rows)
        _lib.print_verdict("CHECK", not failed, warn=warned)
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
