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

Zips are DETERMINISTIC: fixed timestamps, sorted entries, fixed permissions. Identical content
produces identical bytes, so the ritual's write-gating keeps rebuilds out of git noise.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _lib

DIST_DIR_NAME = "dist"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)  # determinism: content decides the bytes, not the clock

# What a distribution NEVER carries: this project's own history and derived surfaces.
EXCLUDE_DIRS = {"state", "dist", "__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {"console/console.html", "system-map/map.json", "settings.local.json"}
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

4. Open the console beside your terminal: `python3 .claude/console/console.py`, then
   http://127.0.0.1:7717/console.html - half the screen for it, half for Claude Code.

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
"""


def distribution_enabled(root: Path) -> bool:
    """The gate the zips live behind. In an adopting project the payload below would BE that
    project's private .claude/, so an absent knob reads as false: the leak fails closed."""
    cfg = _lib.read_json(root / ".claude" / "config" / "memory.json", {}) or {}
    dist = cfg.get("distribution") or {}
    return bool(dist.get("enabled", False))


def _tracked_claude_files(root: Path) -> set | None:
    """Repo-relative POSIX paths of git-tracked files under .claude/, or None when git (or a
    repository, or any tracked file there) is unavailable. The walk below intersects with
    this: the working tree supplies file CONTENT, git decides WHICH files ship, so nothing
    gitignored or untracked - a private reference tree, a stray .env, an editor artifact -
    can ride into the zips."""
    out = _lib.git_output(["ls-files", "-z", "--", ".claude"], root=root)
    if not out:
        return None
    return {name for name in out.split("\0") if name}


def _adopter_memory_config(data: bytes) -> bytes:
    """The shipped memory.json lands with the home-only generators OFF: the knob is what
    keeps an adopting project from packaging its own memory on its very first ritual."""
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data
    dist = cfg.get("distribution")
    if not isinstance(dist, dict):
        dist = {}
        cfg["distribution"] = dist
    dist["enabled"] = False
    return (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _payload_entries(root: Path) -> tuple[list, list]:
    """(entries, skipped_untracked): (archive_path, bytes) pairs for the system payload in
    deterministic order, plus the files the tracked-manifest rule kept out (reported, never
    silent - an untracked file that should ship needs a `git add`, not a mystery)."""
    claude = root / ".claude"
    tracked = _tracked_claude_files(root)
    entries, skipped_untracked = [], []
    for path in sorted(claude.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(claude).as_posix()
        parts = rel.split("/")
        if parts[0] in EXCLUDE_DIRS or any(p == "__pycache__" for p in parts):
            continue
        if rel in EXCLUDE_FILES or path.suffix == ".pyc":
            continue
        if any(rel == sub or rel.startswith(sub + "/") for sub in EXCLUDE_SUBDIRS):
            continue
        if parts[0] in TEMPLATE_ONLY_DIRS and path.name != "_template.md":
            continue
        if rel in RESET_FILES:
            continue
        if tracked is not None and f".claude/{rel}" not in tracked:
            skipped_untracked.append(f".claude/{rel}")
            continue
        data = path.read_bytes()
        if rel == "config/memory.json":
            data = _adopter_memory_config(data)
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
    return entries, skipped_untracked


def _write_zip(out_path: Path, entries: list) -> bool:
    """Deterministic zip; write-gated so an unchanged build never dirties the tree."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc_path, data in sorted(entries):
            info = zipfile.ZipInfo(arc_path, date_time=FIXED_DATE)
            info.external_attr = (0o755 if arc_path.endswith(".sh") else 0o644) << 16
            zf.writestr(info, data)
    new = buffer.getvalue()
    if out_path.exists() and out_path.read_bytes() == new:
        return False
    _lib.ensure_dir(out_path.parent)
    out_path.write_bytes(new)
    return True


def build(root: Path | None = None) -> dict:
    root = root or _lib.project_root()
    if not distribution_enabled(root):
        raise _lib.LibError(
            "distribution.enabled is false (or absent) in .claude/config/memory.json: refusing "
            "to package this repo's .claude/ into redistributable zips. This generator is "
            "home-repo-only; in an adopting project the zips would carry that project's "
            "private memory. Set the knob true only in the dot-claude-iff source repo."
        )
    dist = root / ".claude" / DIST_DIR_NAME
    payload, skipped_untracked = _payload_entries(root)
    for name in skipped_untracked:
        print(f"skipped (not git-tracked): {name}")

    fresh = payload + [("START-HERE.md", START_HERE.encode()), (".gitignore", GITIGNORE.encode())]
    kit = [(f"dot-claude-iff-kit/{p}", d) for p, d in payload]
    kit += [("ADOPT.md", ADOPT_MD.encode()),
            ("dot-claude-iff-kit/.gitignore", GITIGNORE.encode())]

    results = {}
    for name, entries in (("dot-claude-iff-fresh.zip", fresh), ("dot-claude-iff-adopt-kit.zip", kit)):
        out = dist / name
        wrote = _write_zip(out, entries)
        results[name] = {"path": out, "entries": len(entries), "wrote": wrote,
                         "bytes": out.stat().st_size}
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the distribution zips (a registered generator).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build both zips into .claude/dist/")
    args = parser.parse_args(argv)

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
