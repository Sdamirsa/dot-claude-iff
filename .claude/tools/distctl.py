#!/usr/bin/env python3
"""distctl.py - build the distribution zips, write the managed .gitignore block, and export a clean public copy.

Two artifacts land in .claude/dist/, and this tool is a REGISTERED GENERATOR (law 1): the zips
are rebuilt by every ritual, so what people download can never quietly lag what the repo ships.

  dot-claude-iff-fresh.zip      unzip into a NEW/empty repo root; START-HERE.md guides the
                                first session. CLAUDE.md ships in placeholder form, state
                                ships empty: a fresh start carries the system, never this
                                project's history.
  dot-claude-iff-adopt-kit.zip  for an EXISTING repo. Unzips to a dot-claude-iff-kit/ folder
                                (so it cannot clobber a repo it is unzipped next to) plus an
                                ADOPT.md carrying the one instruction to paste to the agent,
                                which then follows the adopt skill: merge, never overwrite.

Zips are DETERMINISTIC: fixed timestamps, sorted entries, fixed permissions, LF line endings
inside. Identical content produces identical bytes on every OS, so the ritual's write-gating
keeps rebuilds out of git noise and the committed zips can be checked against a rebuild
(`distctl.py verify`, and test_dist on every CI run). The payload rule and the release flow
are written down in .claude/reference/release-flow.md (home repo only).

Two more commands work in ANY project running the system (they are not behind the
distribution knob; .claude/reference/public-private.md is their manual):

  gitignore [--apply]           render the managed .gitignore block for memory.json's
                                `visibility` (tracked | ignored) and rewrite it in place
                                between its marker lines; the user's own lines are never
                                touched. The fresh zip's .gitignore IS this block, and /adopt
                                applies it on every install path.
  export --to <dir> [--dry-run] mirror a clean, publishable copy of the project into an
                                existing folder (a public release checkout): what git does not
                                ignore, plus only the .claude/ subset publish.json allows, never
                                the hard-excluded memory, behind the secrets scan. It writes
                                files and a manifest; it never commits, pushes or runs any git
                                write, and never touches the target's .git.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _lib

DIST_DIR_NAME = "dist"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)  # determinism: content decides the bytes, not the clock

# What a distribution NEVER carries: this project's own history and derived surfaces, and
# worktrees (build-time scratch checkouts of the whole repo, gitignored, never descended into).
EXCLUDE_DIRS = {"state", "dist", "worktrees", "__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {"console/console.html", "system-map/map.json", "settings.local.json"}
# Home-repo-only documents: committed here, meaningless in an adopting project. Dropped on the
# way into the zips (distribution-boundary mechanism 3); the adopt skill skips them on clones.
HOME_ONLY_FILES = {"reference/release-flow.md"}
# Nested trees that are private by convention (gitignored in the home repo). Excluded even
# on the no-git fallback path, where the tracked-files manifest cannot protect them.
EXCLUDE_SUBDIRS = ("reference/private",)
# Directories where only the scaffolds travel; the content is this project's, not the system's.
# A scaffold is a `_`-prefixed file at the directory's top (tasks/_template.md,
# tasks/_builder-brief.md), the same rule every task reader uses to tell it from a task.
TEMPLATE_ONLY_DIRS = {"tasks", "research"}
# Files replaced with fresh-start content rather than copied.
RESET_FILES = {"CLAUDE.md", "STATUS.md", "Project-log.jsonl", "LESSONS.jsonl"}

FRESH_STATUS = """# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

Adoption in progress: this .claude system was just unzipped and has not been adapted to this
project yet. Follow START-HERE.md, then delete this sentence during the first ritual.

## Active tasks

- none yet

## Next steps

1. Open Claude Code here and finish the adoption (see START-HERE.md).
2. Fill CLAUDE.md's placeholders from this repo's reality.
3. First ritual: the user types /project-memory (only the user can open it; the agent asks).

## Blockers / open decisions

- none

## Watch-outs

- none yet: lessons are earned, not inherited
"""

START_HERE = """# Start here

You unzipped the dot-claude-iff system into a fresh repo. One session sets it up:

1. `git init` if you have not already (the system assumes a git repo).
2. Open Claude Code in this directory. When it asks whether to trust this project's hooks,
   say yes: the hooks are the heartbeat, the capture lane, the policy gate and the ritual
   ticket, and without trust they silently do not run.
3. Type this as your prompt, starting with /adopt (typing the command yourself is what lets
   the system's ritual run: the agent cannot open it on its own):

   > /adopt Finish adopting the dot-claude-iff system into this repo. The files are already
   > installed, so skip the copy phase: run phases 2 (frame questions), the visibility step
   > at the end of 3 (record the answer, apply the .gitignore block), 4 (adapt: fill
   > CLAUDE.md's placeholders from this repo, reset STATUS to reality), 5 (verify, including
   > the hooks-fire probe) and 6 (first ritual) against this repo.

4. Open the console beside your terminal: `python3 .claude/console/console.py` prints your
   URL (http://<your-folder-name>.localhost:<derived-port>/console.html - the port derives
   from the folder name, so projects never fight over one default). Half the screen for it,
   half for Claude Code; every session start prints the URL again.

What you get: one place to interact (Claude Code), one place to control (the console), one
command to evolve (/project-memory). The manual is .claude/README.md.
"""

ADOPT_MD = """# Adopt dot-claude-iff into an existing repo

This kit installs a .claude operating system: one place to interact (Claude Code), one place
to control (a live console), one command to evolve (/project-memory). Your existing files are
merged, never overwritten; an existing .claude/ is the designed case, not a problem.

1. Unzip this kit anywhere OUTSIDE the repo you want to adopt into (for example next to it).
2. Open Claude Code in YOUR repo.
3. Paste this to the agent (adjust the path):

   > Adopt the dot-claude-iff system from <path-to>/dot-claude-iff-kit into this repo.
   > Follow <path-to>/dot-claude-iff-kit/.claude/skills/adopt/SKILL.md end to end. This
   > repo may already have a .claude directory: merge, never overwrite, and report every
   > conflict to me.

4. When Claude Code asks whether to trust the newly installed hooks, say yes; the adopt
   skill's verify phase probes that they actually fire.
5. Hooks installed mid-session start working in the next session. Open a new one in your
   repo and type /project-memory: that is the first ritual, and only you can open it.

The kit is a complete, self-seeding copy of the system; after adoption, your repo can itself
be the source for the next adoption. The manual ships at .claude/README.md.
"""

# --------------------------------------------------------------------------- the .gitignore block
#
# ONE renderer for every install path: the fresh zip's root .gitignore is this block, the adopt
# kit carries it, and /adopt writes it into the target with `gitignore --apply` whatever the
# source was (clone, fresh zip, kit). Before this, the kit path never delivered the secrets
# safety net to the target at all. The block sits between two marker lines so it can be
# rewritten idempotently (a visibility change, an upgrade) without touching the user's lines.

GITIGNORE_BEGIN = ("# >>> dot-claude-iff managed block: `python3 .claude/tools/distctl.py "
                   "gitignore --apply` rewrites it; put your own rules outside it")
GITIGNORE_END = "# <<< dot-claude-iff managed block"
_BLOCK_RE = re.compile(r"^# >>> dot-claude-iff managed block[^\n]*\n.*?"
                       r"^# <<< dot-claude-iff managed block[^\n]*(?:\n|\Z)", re.M | re.S)

_SAFETY_NET = """# Safety net: secrets and machine-local settings, wherever they appear
.env
*.env
.env*.local
settings.local.json
"""
_PY_CACHES = """# Python caches
__pycache__/
*.pyc
"""
_VISIBILITY_BODY = {
    "tracked": ("# visibility: tracked - .claude/ is committed with the project; only per-user "
                "and derived files stay out\n\n" + _SAFETY_NET + """
# Console runtime
.claude/console/*.pid
.claude/console/*.log

# Liveness signal, rewritten every turn by the Stop hook (which creates it when missing)
.claude/state/heartbeat.json

# Ritual ticket: minted by the prompt hook when the user types /project-memory, consumed at the end
.claude/state/ritual-ticket.json

# Orchestration runtime (agent start times, the delegation counter), rewritten by hooks
.claude/state/orchestration.json

# Agent worktrees (build-time scratch copies of the repo)
.claude/worktrees/

# Atomic-write leftovers (the temp file of a write that died before its rename)
.claude/**/*.tmp

""" + _PY_CACHES),
    "ignored": ("# visibility: ignored - the agent system stays in this checkout and never "
                "reaches git;\n# publish a clean copy with `distctl.py export` "
                "(.claude/reference/public-private.md)\n.claude/\n.claude-iff/\n\n"
                + _SAFETY_NET + "\n" + _PY_CACHES),
}
# What the kits wrote before the managed block existed, byte for byte: `gitignore --apply`
# swaps it for the block in place instead of leaving two copies side by side.
LEGACY_GITIGNORE = """# Safety net: secrets and machine-local settings
.env
*.env
settings.local.json

# Console runtime
.claude/console/*.pid
.claude/console/*.log

# Liveness signal, rewritten every turn by the Stop hook (which creates it when missing)
.claude/state/heartbeat.json

# Agent worktrees (build-time scratch copies of the repo)
.claude/worktrees/
"""


def render_gitignore_block(visibility: str) -> str:
    """The managed block for one visibility value, marker lines included, LF, ending in a
    newline. `tracked` ignores only per-user and derived files; `ignored` ignores .claude/ and
    .claude-iff/ whole. Both carry the secrets safety net and the python caches."""
    if visibility not in _VISIBILITY_BODY:
        raise _lib.LibError(f"unknown visibility {visibility!r}; expected one of "
                            f"{', '.join(_lib.VISIBILITY_VALUES)}")
    return f"{GITIGNORE_BEGIN}\n{_VISIBILITY_BODY[visibility]}{GITIGNORE_END}\n"


def merge_gitignore(text: str, block: str) -> str:
    """`text` with the managed block set to `block` (given LF; written in the file's own line
    ending, CRLF when the file has any): replaced in place when the markers are present, swapped
    for the legacy kit block when that is present verbatim, appended after one blank line
    otherwise. Every byte outside the block is kept as it was."""
    eol = "\r\n" if "\r\n" in text else "\n"
    block = block.replace("\n", eol)
    if _BLOCK_RE.search(text):
        return _BLOCK_RE.sub(lambda _m: block, text, count=1)
    if GITIGNORE_BEGIN.split(":")[0] in text:
        raise _lib.LibError(".gitignore holds the managed block's begin marker but not its end "
                            "marker; fix the block by hand (or delete it) and rerun")
    for legacy in (LEGACY_GITIGNORE, LEGACY_GITIGNORE.replace("\n", "\r\n")):
        if legacy in text:
            return text.replace(legacy, block, 1)
    if not text:
        return block
    return text + ("" if text.endswith(eol + eol) else eol if text.endswith("\n") else eol + eol) + block


def apply_gitignore(root: Path, write: bool = True) -> tuple[bool, str]:
    """Bring <root>/.gitignore's managed block in line with memory.json's `visibility`.
    Returns (changed, visibility). Bytes outside the block are preserved exactly, CRLF
    checkouts included; an unchanged file is never rewritten. write=False only reports."""
    vis = _lib.visibility(root)
    path = Path(root) / ".gitignore"
    raw = path.read_bytes() if path.exists() else b""
    text = raw.decode("utf-8", errors="surrogateescape")
    out = merge_gitignore(text, render_gitignore_block(vis)).encode("utf-8", errors="surrogateescape")
    if out == raw:
        return False, vis
    if write:
        path.write_bytes(out)
    return True, vis


def distribution_enabled(root: Path) -> bool:
    """The gate the zips live behind. In an adopting project the payload below would BE that
    project's private .claude/, so an absent knob reads as false: the leak fails closed."""
    cfg = _lib.read_json(root / ".claude" / "config" / "memory.json", {}) or {}
    dist = cfg.get("distribution") or {}
    return bool(dist.get("enabled", False))


def _shippable_claude_files(root: Path) -> set | None:
    """Repo-relative POSIX paths under .claude/ that git does NOT ignore (tracked, plus
    untracked-but-not-ignored), or None when git or a repository is unavailable.

    THE PAYLOAD RULE: the working tree decides which files ship and what they contain; git
    only vetoes what it ignores. Two facts force this rule. The generator ledger hashes the
    WORKING TREE to decide freshness, and the ritual commits with `git add -A` AFTER POLISH
    builds the zips. The old rule (ship only what the index tracks) skipped a file created
    in-session, the ledger stamped the tree fresh, PUBLISH then committed the file, and the
    zips lacked it until some unrelated input changed - three shipped files went missing that
    way. Under this rule a new file ships in the same ritual that commits it, and a gitignored
    one (the private reference tree, a stray .env) still never does. Entries are normcased
    (L-9): compare them with os.path.normcase on the other side too."""
    out = _lib.git_output(["ls-files", "-z", "--cached", "--others", "--exclude-standard",
                           "--", ".claude"], root=root)
    if not out:
        return None
    return {os.path.normcase(name) for name in out.split("\0") if name}


def _walk(claude: Path) -> list:
    """Every file under .claude/, pruning EXCLUDE_DIRS at the top level and __pycache__ at any
    depth BEFORE descending: a worktree is a whole checkout and must never even be read."""
    found = []
    for dirpath, dirnames, filenames in os.walk(claude):
        top = Path(dirpath) == claude
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not (top and d in EXCLUDE_DIRS)]
        found.extend(Path(dirpath) / name for name in filenames)
    return sorted(found)


def _excluded(rel: str) -> bool:
    """The structural rules (no git involved) for a POSIX path relative to .claude/."""
    parts = rel.split("/")
    return (parts[0] in EXCLUDE_DIRS or "__pycache__" in parts or rel.endswith(".pyc")
            or rel in EXCLUDE_FILES or rel in HOME_ONLY_FILES or rel in RESET_FILES
            or any(rel == sub or rel.startswith(sub + "/") for sub in EXCLUDE_SUBDIRS)
            or (parts[0] in TEMPLATE_ONLY_DIRS
                and not (len(parts) == 2 and parts[1].startswith("_"))))


def payload_source(repo_rel: str) -> bool:
    """Whether a repo-relative POSIX path is read into the zips (structural rules only, before
    git's ignore veto). Lets a caller tell a payload edit from any other edit."""
    if repo_rel == ".claude-iff/README.md":
        return True
    if not repo_rel.startswith(".claude/"):
        return False
    return not _excluded(repo_rel[len(".claude/"):])


def _lf(data: bytes) -> bytes:
    """Text ships with LF line endings whatever the checkout used. A Windows checkout
    (core.autocrlf) hands distctl CRLF bytes where Linux hands it LF, so the same commit used
    to build two different zips, and a .sh built on Windows broke bash everywhere else.
    Anything holding a NUL byte (git's own binary heuristic) passes through untouched."""
    if b"\0" in data:
        return data
    return data.replace(b"\r\n", b"\n")


def _adopter_memory_config(data: bytes) -> bytes:
    """The shipped memory.json lands with the home-only generators OFF: the knob is what
    keeps an adopting project from packaging its own memory on its very first ritual. Its
    project_steps lists land EMPTY: they are this repo's own commands (its test suite as a
    CHECK step), and in someone else's project they would run our suite in their ritual.
    `visibility` lands as the default ("tracked", the .gitignore block the kits carry): this
    repo's own choice is not the adopter's, and /adopt asks for theirs."""
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data
    dist = cfg.get("distribution")
    if not isinstance(dist, dict):
        dist = {}
        cfg["distribution"] = dist
    dist["enabled"] = False
    cfg["visibility"] = _lib.DEFAULT_VISIBILITY
    steps = cfg.get("project_steps")
    if isinstance(steps, dict):
        for kind, value in steps.items():
            if isinstance(value, list):
                steps[kind] = []
    return (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _adopter_publish_config(data: bytes) -> bytes:
    """The shipped publish.json lands with `include` EMPTY: an adopter publishes nothing from
    .claude/ until they decide otherwise. This repo's own list is its own publishing choice."""
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data
    if not isinstance(cfg, dict) or cfg.get("include") == []:
        return data
    cfg["include"] = []
    return (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


DEFAULT_CONSOLE_PORT = "auto"


def _adopter_console_config(data: bytes) -> bytes:
    """The shipped console.json lands with port "auto" (derived from the adopting repo's
    folder name - no per-project decision, no shared default to collide on) and with the
    system monitor OFF: shipping one machine's explicit port or monitoring preference would
    export this repo's local choices as everyone's defaults."""
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data
    monitor = cfg.get("monitor") if isinstance(cfg.get("monitor"), dict) else {}
    changed = cfg.get("port") != DEFAULT_CONSOLE_PORT or monitor.get("enabled", False)
    if not changed:
        return data
    cfg["port"] = DEFAULT_CONSOLE_PORT
    cfg["monitor"] = dict(monitor, enabled=False)
    return (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _payload_entries(root: Path) -> tuple[list, list]:
    """(entries, skipped_ignored): (archive_path, bytes) pairs for the system payload in
    deterministic order, plus the files git's ignore rules kept out (reported, never silent -
    see _shippable_claude_files for the rule)."""
    claude = root / ".claude"
    shippable = _shippable_claude_files(root)
    entries, skipped_ignored = [], []
    for path in _walk(claude):
        rel = path.relative_to(claude).as_posix()
        if _excluded(rel):
            continue
        if shippable is not None and os.path.normcase(f".claude/{rel}") not in shippable:
            skipped_ignored.append(f".claude/{rel}")
            continue
        data = path.read_bytes()
        if rel == "config/memory.json":
            data = _adopter_memory_config(data)
        elif rel == "config/console.json":
            data = _adopter_console_config(data)
        elif rel == "config/publish.json":
            data = _adopter_publish_config(data)
        entries.append((f".claude/{rel}", data))

    template = claude / "skills" / "adopt" / "CLAUDE.template.md"
    fresh_claude_md = template.read_bytes() if template.exists() else b"# {{PROJECT_NAME}}\n"
    entries += [
        (".claude/CLAUDE.md", fresh_claude_md),
        (".claude/STATUS.md", FRESH_STATUS.encode()),
        (".claude/Project-log.jsonl", b""),
        (".claude/LESSONS.jsonl", b""),
    ]
    iff_readme = root / ".claude-iff" / "README.md"
    if iff_readme.exists():
        entries.append((".claude-iff/README.md", iff_readme.read_bytes()))
    return entries, skipped_ignored


def _write_zip(out_path: Path, entries: list) -> bool:
    """Deterministic zip; write-gated so an unchanged build never dirties the tree.

    Every header field that could vary by machine is pinned. create_system defaults to the
    BUILDING OS (0 on Windows, 3 elsewhere), which changed the bytes and made unzip ignore the
    mode bits; 3 (unix) keeps .sh executable. Entries are STORED: deflate output depends on
    the zlib build (Windows CPython ships zlib-ng), so compressing would tie the bytes to the
    interpreter. (A ZipInfo entry was always stored; the old ZIP_DEFLATED argument was inert.)"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for arc_path, data in sorted(entries):
            info = zipfile.ZipInfo(arc_path, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o755 if arc_path.endswith(".sh") else 0o644) << 16
            zf.writestr(info, _lf(data))
    new = buffer.getvalue()
    if out_path.exists() and out_path.read_bytes() == new:
        return False
    _lib.ensure_dir(out_path.parent)
    out_path.write_bytes(new)
    return True


ZIP_NAMES = ("dot-claude-iff-fresh.zip", "dot-claude-iff-adopt-kit.zip")


def build(root: Path | None = None, out_dir: Path | None = None, quiet: bool = False) -> dict:
    """Build both zips into `out_dir` (default: <root>/.claude/dist)."""
    root = root or _lib.project_root()
    if not distribution_enabled(root):
        raise _lib.LibError(
            "distribution.enabled is false (or absent) in .claude/config/memory.json: refusing "
            "to package this repo's .claude/ into redistributable zips. This generator is "
            "home-repo-only; in an adopting project the zips would carry that project's "
            "private memory. Set the knob true only in the dot-claude-iff source repo."
        )
    dist = Path(out_dir) if out_dir is not None else root / ".claude" / DIST_DIR_NAME
    payload, skipped_ignored = _payload_entries(root)
    if not quiet:
        for name in skipped_ignored:
            print(f"skipped (gitignored): {name}")

    # Both kits carry the managed block for the visibility the shipped memory.json lands with;
    # /adopt re-renders it for the adopter's own answer.
    gitignore = render_gitignore_block(_lib.DEFAULT_VISIBILITY).encode()
    fresh = payload + [("START-HERE.md", START_HERE.encode()), (".gitignore", gitignore)]
    kit = [(f"dot-claude-iff-kit/{p}", d) for p, d in payload]
    kit += [("ADOPT.md", ADOPT_MD.encode()),
            ("dot-claude-iff-kit/.gitignore", gitignore)]

    results = {}
    for name, entries in zip(ZIP_NAMES, (fresh, kit)):
        out = dist / name
        wrote = _write_zip(out, entries)
        results[name] = {"path": out, "entries": len(entries), "wrote": wrote,
                         "bytes": out.stat().st_size}
    return results


def stale_zips(root: Path | None = None) -> list:
    """Names of the committed zips in <root>/.claude/dist that are missing or differ, byte for
    byte, from a rebuild of the current tree into a scratch directory. [] means fresh. Reads
    only; the committed copies are never touched."""
    root = root or _lib.project_root()
    with tempfile.TemporaryDirectory(prefix="distctl-verify-") as tmp:
        build(root, out_dir=Path(tmp), quiet=True)
        stale = []
        for name in ZIP_NAMES:
            committed = root / ".claude" / DIST_DIR_NAME / name
            if not committed.exists() or committed.read_bytes() != (Path(tmp) / name).read_bytes():
                stale.append(name)
    return stale


# --------------------------------------------------------------------------- export
#
# `export --to <dir>`: the private-working-repo / public-release-repo pattern
# (.claude/reference/public-private.md). The source is the project as git sees it (every file
# git does not ignore) with .claude/ and .claude-iff/ held back except what publish.json's
# `include` globs allow, under a hard floor no config can lift. The target is an existing
# folder, typically a checkout of the public repo; export mirrors into it and stops. Committing
# and pushing stay with the human, and nothing here runs a git write or enters the target's .git.

EXPORT_MANIFEST = ".claude-export-manifest.json"
SYSTEM_TREES = (".claude", ".claude-iff")
# The floor under publish.json, which can only NARROW: the project's own words (status, logs,
# lessons, tasks, research), its runtime state and the committed record surface, scratch
# checkouts, private reference material, and per-user secret files. No `include` entry lifts
# any of it; test_export proves that by trying.
ALWAYS_EXCLUDE_TREES = (".claude/state", ".claude/tasks", ".claude/research", ".claude/worktrees",
                        ".claude/reference/private", ".claude-iff/obs")
ALWAYS_EXCLUDE_FILES = (".claude/Project-log.jsonl", ".claude/LESSONS.jsonl", ".claude/STATUS.md",
                        EXPORT_MANIFEST)
ALWAYS_EXCLUDE_NAMES = ("settings.local.json", ".env", "*.env", ".env.local", ".env.*.local")
# Only when the secrets scanner finds a literal credential in it (a ${VAR}-only file exports).
MCP_CONFIG = ".mcp.json"


class ExportRefused(_lib.LibError):
    """An export that must not happen. `details` carries per-file lines (never file contents)."""

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.details = list(details or [])


def _norm(rel: str) -> str:
    return os.path.normcase(rel)


def _under(rel: str, prefix: str) -> bool:
    """rel equals prefix or sits beneath it; case-blind where the filesystem is (L-9)."""
    a, b = _norm(rel), _norm(prefix.rstrip("/"))
    return a == b or a.startswith(b + _norm("/"))


def always_excluded(rel: str) -> bool:
    """The hard floor, for a repo-relative POSIX path. A `.git` segment anywhere is excluded
    too: an export never reads from, or writes into, any repository's internals."""
    parts = rel.split("/")
    if any(_norm(part) == ".git" for part in parts):
        return True
    if any(_under(rel, tree) for tree in ALWAYS_EXCLUDE_TREES):
        return True
    if any(_norm(rel) == _norm(name) for name in ALWAYS_EXCLUDE_FILES):
        return True
    return any(fnmatch.fnmatch(parts[-1], pattern) for pattern in ALWAYS_EXCLUDE_NAMES)


def publish_includes(root: Path) -> tuple[list, str | None]:
    """(globs, warning) from <root>/.claude/config/publish.json. Absent, unreadable or
    malformed reads as [] - publish nothing from .claude/ - with a warning naming why: the
    safe direction for a publishing decision is less."""
    path = Path(root) / ".claude" / "config" / "publish.json"
    if not path.exists():
        return [], None
    cfg = _lib.read_json(path, None)
    include = cfg.get("include") if isinstance(cfg, dict) else None
    if not isinstance(include, list) or not all(isinstance(g, str) for g in include):
        return [], "publish.json is unreadable or its include is not a list of strings: publishing nothing from .claude/"
    return [g.strip() for g in include if g.strip()], None


def _included(rel: str, globs: list) -> bool:
    """fnmatch globs relative to the repo root (`*` also crosses `/`); a trailing `/` names a
    whole folder."""
    for glob in globs:
        glob = glob[2:] if glob.startswith("./") else glob
        if glob.endswith("/"):
            if _under(rel, glob):
                return True
        elif fnmatch.fnmatch(rel, glob):
            return True
    return False


def _walk_system_trees(root: Path, prune_names) -> list:
    """Every file under .claude/ and .claude-iff/, from the filesystem: under `visibility:
    ignored` git cannot list them. Hard-excluded trees (worktrees above all: whole checkouts)
    and cache/VCS folders are pruned before descending."""
    found = []
    for top in SYSTEM_TREES:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = sorted(d for d in dirnames if d not in prune_names
                                 and not any(_under(f"{rel_dir}/{d}", t) for t in ALWAYS_EXCLUDE_TREES))
            found.extend(f"{rel_dir}/{name}" for name in filenames)
    return sorted(found)


def _export_bytes(root: Path, rel: str) -> bytes:
    """What lands in the target for one file: its bytes, except the root .gitignore, whose
    managed block is re-rendered for `tracked`. A source kept `ignored` would otherwise hand
    the release repo a rule ignoring the very .claude/ files publish.json chose to publish."""
    data = (root / rel).read_bytes()
    if rel != ".gitignore":
        return data
    text = data.decode("utf-8", errors="surrogateescape")
    if not _BLOCK_RE.search(text):
        return data
    return merge_gitignore(text, render_gitignore_block("tracked")).encode("utf-8", errors="surrogateescape")


def _inside(child: Path, parent: Path) -> bool:
    """child is parent or beneath it, compared resolved and normcased (L-9)."""
    c, p = os.path.normcase(str(child.resolve())), os.path.normcase(str(parent.resolve()))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


def _check_target(root: Path, target: Path) -> None:
    if not target.exists():
        raise ExportRefused(f"target {target} does not exist: export writes into an existing "
                            f"folder (a checkout of the release repo) and never creates one")
    if not target.is_dir():
        raise ExportRefused(f"target {target} is not a directory")
    if _inside(target, root) and _inside(root, target):
        raise ExportRefused("target is the project itself: export copies the project OUT")
    if _inside(target, root):
        raise ExportRefused("target is inside the project: the next export would copy the copy, "
                            "and git would see it here")
    if _inside(root, target):
        raise ExportRefused("the project is inside the target: the mirror step could delete "
                            "the project's own files")


def _safe_manifest_rel(rel, target: Path) -> bool:
    """A previous manifest entry the mirror may act on: relative, normalised, outside any .git,
    and resolving inside the target. The manifest sits in a folder other people edit, so it is
    read as data that has to prove it is harmless."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
        return False
    if any(_norm(p) == ".git" for p in parts) or _norm(rel) == _norm(EXPORT_MANIFEST):
        return False
    return _inside(target / rel, target)


def _source_version(root: Path) -> str:
    cfg = _lib.read_json(root / ".claude" / "config" / "registry.json", {}) or {}
    return str(cfg.get("system_version", "0.0.0")) if isinstance(cfg, dict) else "0.0.0"


def plan_export(root: Path, target: Path) -> dict:
    """Everything an export would do, decided before anything is written. Raises ExportRefused
    for a bad target, a source that is not a git work tree, an unknown visibility, a secret in
    the planned set (FAIL findings, printed as path:line pattern, never the value) or a target
    shape the copy cannot honour safely."""
    import checkctl  # the secrets scanner lives there; reused, never copied
    root, target = Path(root), Path(target)
    _check_target(root, target)
    try:
        vis = _lib.visibility(root)
    except _lib.LibError as exc:
        raise ExportRefused(str(exc)) from None
    if not ((root / ".git").exists() and checkctl._is_git_toplevel(root)):
        raise ExportRefused("the project is not the top of a git work tree: export publishes "
                            "what git does not ignore, so it needs git to say what that is")
    listed = checkctl._git_ls(root, "--cached", "--others", "--exclude-standard")
    if listed is None:
        raise ExportRefused("git ls-files failed: cannot tell which files git ignores")
    globs, warning = publish_includes(root)
    notes = [warning] if warning else []

    not_ignored = {_norm(r) for r in listed}
    project = [r for r in listed if not any(_under(r, t) for t in SYSTEM_TREES)]
    system = _walk_system_trees(root, checkctl.SECRET_SCAN_PRUNE_NAMES)
    if vis == "tracked":
        system = [r for r in system if _norm(r) in not_ignored]  # git vetoes what it ignores
    held_back_system = [r for r in system if not _included(r, globs)]
    candidates = sorted(set(project) | {r for r in system if _included(r, globs)})

    record_rel = None
    try:
        record_rel = _lib.record_root().resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        pass  # the usual case: the record is a sibling folder, outside the project

    planned, held_back, skipped = [], [], []
    for rel in candidates:
        if always_excluded(rel) or (record_rel and _under(rel, record_rel)):
            held_back.append(rel)
            continue
        path = root / rel
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                skipped.append(f"{rel} (symlink: not followed)")
            continue
        planned.append(rel)
    if any(_norm(r) == _norm(MCP_CONFIG) for r in planned):
        mcp = next(r for r in planned if _norm(r) == _norm(MCP_CONFIG))
        if checkctl.scan_secrets(root, files=[mcp]):
            planned.remove(mcp)
            held_back.append(mcp)
            notes.append(f"{mcp} holds literal credential values: held back (move them behind "
                         f"${{VAR}}, see .claude/reference/secrets.md, and it exports)")

    findings = checkctl.scan_secrets(root, files=planned)
    fails = [f for f in findings if f["severity"] == checkctl.FAIL]
    if fails:
        raise ExportRefused(
            f"{len(fails)} secret(s) in files the export would publish; nothing was written. "
            f"Move each key out (.claude/reference/secrets.md) and rotate any that reached a "
            f"commit, or mark a false alarm with `{checkctl.SECRET_PRAGMA}`",
            [f"FAIL {f['path']}{':' + str(f['line']) if f['line'] else ''} {f['pattern']}" for f in fails])
    notes += [f"WARN {f['path']}:{f['line']} {f['pattern']} (review before you push)"
              for f in findings if f["severity"] == checkctl.WARN]

    previous, prev_version, prev_ok = [], None, False
    manifest_path = target / EXPORT_MANIFEST
    if manifest_path.exists():
        prev = _lib.read_json(manifest_path, None)
        if isinstance(prev, dict) and isinstance(prev.get("files"), list):
            previous, prev_version, prev_ok = prev["files"], prev.get("source_system_version"), True
        else:
            notes.append(f"{EXPORT_MANIFEST} in the target is unreadable: nothing is removed this run")
    unsafe = [r for r in previous if not _safe_manifest_rel(r, target)]
    notes += [f"ignored unsafe manifest entry: {r!r}" for r in unsafe]

    add, update, unchanged, payload = [], [], [], {}
    for rel in planned:
        dest = target / rel
        if not _inside(dest.parent, target):
            raise ExportRefused(f"{rel}: its folder in the target resolves outside the target "
                                f"(a symlink?); refusing to write through it")
        for parent in list(Path(rel).parents)[:-1]:
            if (target / parent).exists() and not (target / parent).is_dir():
                raise ExportRefused(f"{rel}: the target has a file where this needs a folder "
                                    f"({parent.as_posix()})")
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise ExportRefused(f"{rel}: the target has a folder or link where this file goes")
        data = _export_bytes(root, rel)
        payload[rel] = data
        if not dest.exists():
            add.append(rel)
        elif dest.read_bytes() != data:
            update.append(rel)
        else:
            unchanged.append(rel)
    keep = {_norm(r) for r in planned}
    remove = sorted(r for r in previous if r not in unsafe and _norm(r) not in keep
                    and ((target / r).is_file() or (target / r).is_symlink()))

    version = _source_version(root)
    return {"root": root, "target": target, "visibility": vis, "add": add, "update": update,
            "unchanged": unchanged, "remove": remove, "planned": planned, "payload": payload,
            "held_back": sorted(held_back), "held_back_system": len(held_back_system),
            "skipped": skipped, "notes": notes, "version": version,
            # Write-gated: an export that changes nothing leaves the manifest (and its date) alone.
            "manifest_current": prev_ok and not unsafe and previous == planned
                                and prev_version == version}


def export(root: Path, target: Path, dry_run: bool = False) -> dict:
    """Plan, then (unless dry_run) mirror: remove what the previous manifest listed and the
    new set drops, write what is new or changed, then the manifest. A file the manifest never
    listed is never removed; nothing under any .git is touched; git is only ever asked
    (ls-files, rev-parse), never told."""
    plan = plan_export(root, target)
    if dry_run:
        return plan
    target = plan["target"]
    for rel in plan["remove"]:
        (target / rel).unlink()
    for rel in plan["add"] + plan["update"]:
        dest = target / rel
        _lib.ensure_dir(dest.parent)
        dest.write_bytes(plan["payload"][rel])
        if os.name != "nt":  # keep the executable bit, nothing else (a read-only source stays writable here)
            os.chmod(dest, 0o755 if os.stat(plan["root"] / rel).st_mode & 0o111 else 0o644)
    if not plan["manifest_current"]:
        manifest = {
            "_comment": "Written by .claude/tools/distctl.py export: every file the last export "
                        "placed here. The next export removes the listed files that left the set "
                        "and never touches a file this list does not name. Commit it with the release.",
            "source_system_version": plan["version"],
            "date": _lib.today(),
            "files": plan["planned"],
        }
        (target / EXPORT_MANIFEST).write_bytes(
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return plan


def _print_plan(plan: dict, dry_run: bool) -> None:
    print(f"export ({plan['visibility']}): {len(plan['add'])} to add, {len(plan['update'])} to "
          f"update, {len(plan['remove'])} to remove, {len(plan['unchanged'])} unchanged")
    for verb in ("add", "update", "remove"):
        for rel in plan[verb]:
            print(f"  {verb:<7}{rel}")
    for rel in plan["held_back"]:
        print(f"held back (always excluded): {rel}")
    if plan["held_back_system"]:
        print(f"held back: {plan['held_back_system']} file(s) under .claude/ and .claude-iff/ "
              f"that .claude/config/publish.json does not include")
    for line in plan["skipped"] + plan["notes"]:
        print(f"note: {line}")
    if dry_run:
        print("dry run: nothing written")
    else:
        print("written. Review with `git status` in the target; committing and pushing are yours.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the distribution zips (a registered generator).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build both zips into .claude/dist/")
    sub.add_parser("verify", help="rebuild into a scratch dir and compare with .claude/dist/ "
                                  "byte for byte (writes nothing)")
    gi = sub.add_parser("gitignore", help="print the managed .gitignore block for memory.json's "
                                          "visibility; --apply writes it (exit 1 when not applied)")
    gi.add_argument("--apply", action="store_true",
                    help="rewrite the block in <root>/.gitignore (appended when absent)")
    exp = sub.add_parser("export", help="mirror a clean, publishable copy of the project into an "
                                        "existing folder; never commits, pushes or touches its .git")
    exp.add_argument("--to", required=True, metavar="DIR", help="an existing folder outside the project")
    exp.add_argument("--dry-run", action="store_true", help="print the add/update/remove plan, write nothing")
    args = parser.parse_args(argv)

    if args.command == "gitignore":
        root = _lib.project_root()
        try:
            changed, vis = apply_gitignore(root, write=args.apply)
        except _lib.LibError as exc:
            print(exc)
            _lib.print_verdict("GITIGNORE", False)
            return 2
        if args.apply:
            print(f"{'wrote' if changed else 'unchanged'} .gitignore managed block (visibility: {vis})")
            _lib.print_verdict("GITIGNORE", True)
            return 0
        print(render_gitignore_block(vis), end="")
        print(f".gitignore {'does not carry this block: run with --apply' if changed else 'carries this block'}")
        _lib.print_verdict("GITIGNORE", not changed)
        return 1 if changed else 0

    if args.command == "export":
        try:
            plan = export(_lib.project_root(), Path(args.to), dry_run=args.dry_run)
        except ExportRefused as exc:
            print(f"refused: {exc}")
            for line in exc.details:
                print(f"  {line}")
            _lib.print_verdict("EXPORT", False)
            return 1
        except _lib.LibError as exc:
            print(f"refused: {exc}")
            _lib.print_verdict("EXPORT", False)
            return 1
        _print_plan(plan, args.dry_run)
        _lib.print_verdict("EXPORT", True)
        return 0

    if args.command == "verify":
        try:
            stale = stale_zips()
        except _lib.LibError as exc:
            print(exc)
            _lib.print_verdict("DIST", False)
            return 2
        for name in stale:
            print(f"stale: .claude/{DIST_DIR_NAME}/{name} differs from a rebuild "
                  f"(run `distctl.py build` and commit the zips)")
        if not stale:
            print("fresh: the committed zips equal a rebuild of this tree")
        _lib.print_verdict("DIST", not stale)
        return 1 if stale else 0

    if args.command == "build":
        try:
            results = build()
        except _lib.LibError as exc:
            print(exc)
            _lib.print_verdict("DIST", False)
            return 2
        for name, r in results.items():
            state = "wrote" if r["wrote"] else "unchanged"
            print(f"{state} {name}: {r['entries']} entries, {_lib.human_bytes(r['bytes'])}")
        _lib.print_verdict("DIST", True)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
