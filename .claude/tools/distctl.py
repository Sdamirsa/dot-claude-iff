#!/usr/bin/env python3
"""distctl.py - build the distribution zips: the system, packaged for other repos.

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
"""

from __future__ import annotations

import argparse
import io
import json
import os
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
# Directories where only the scaffold travels; the content is this project's, not the system's.
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
3. Run the first /project-memory.

## Blockers / open decisions

- none

## Watch-outs

- none yet: lessons are earned, not inherited
"""

START_HERE = """# Start here

You unzipped the dot-claude-iff system into a fresh repo. One session sets it up:

1. `git init` if you have not already (the system assumes a git repo).
2. Open Claude Code in this directory. When it asks whether to trust this project's hooks,
   say yes: the hooks are the heartbeat, the capture lane and the policy gate, and without
   trust they silently do not run.
3. Paste this to the agent:

   > Finish adopting the dot-claude-iff system into this repo. The files are already
   > installed, so skip the copy phase: read .claude/skills/adopt/SKILL.md and run its
   > phases 2 (frame questions), 4 (adapt: fill CLAUDE.md's placeholders from this repo,
   > reset STATUS to reality), 5 (verify, including the hooks-fire probe) and 6 (first
   > ritual) against this repo.

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

The kit is a complete, self-seeding copy of the system; after adoption, your repo can itself
be the source for the next adoption. The manual ships at .claude/README.md.
"""

GITIGNORE = """# Safety net: secrets and machine-local settings
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
            or (parts[0] in TEMPLATE_ONLY_DIRS and parts[-1] != "_template.md"))


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
    CHECK step), and in someone else's project they would run our suite in their ritual."""
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data
    dist = cfg.get("distribution")
    if not isinstance(dist, dict):
        dist = {}
        cfg["distribution"] = dist
    dist["enabled"] = False
    steps = cfg.get("project_steps")
    if isinstance(steps, dict):
        for kind, value in steps.items():
            if isinstance(value, list):
                steps[kind] = []
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

    fresh = payload + [("START-HERE.md", START_HERE.encode()), (".gitignore", GITIGNORE.encode())]
    kit = [(f"dot-claude-iff-kit/{p}", d) for p, d in payload]
    kit += [("ADOPT.md", ADOPT_MD.encode()),
            ("dot-claude-iff-kit/.gitignore", GITIGNORE.encode())]

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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the distribution zips (a registered generator).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build both zips into .claude/dist/")
    sub.add_parser("verify", help="rebuild into a scratch dir and compare with .claude/dist/ "
                                  "byte for byte (writes nothing)")
    args = parser.parse_args(argv)

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
