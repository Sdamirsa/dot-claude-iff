#!/usr/bin/env python3
"""_fixture.py - the shared test fixture: a throwaway project with a throwaway record root.

Every tool test builds the same scaffold, so a test can never pass because it invented a
convenient layout. The shipped config files are copied in verbatim, which means the tests
exercise the defaults we actually ship rather than a hand-written stand-in.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
CLAUDE_DIR = TOOLS_DIR.parent
REPO_ROOT = CLAUDE_DIR.parent

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _lib  # noqa: E402  (path must be set first)

SHIPPED_CONFIGS = ("memory", "policy", "observe", "console", "registry", "model-prices",
                   "brainstorm", "publish", "phases", "orchestration")


class FixtureCase(unittest.TestCase):
    """Base case: self.root is a fresh project, self.record is a fresh record root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="claude-iff-test-")
        base = Path(self._tmp.name)
        self.root = base / "proj"
        self.record = base / "proj_claude_iff"
        for sub in ("config", "state/handshakes", "system-map/cards", "console", "tasks", "research"):
            (self.root / ".claude" / sub).mkdir(parents=True, exist_ok=True)
        (self.root / ".claude-iff" / "obs" / "rollups").mkdir(parents=True, exist_ok=True)
        for name in SHIPPED_CONFIGS:
            src = CLAUDE_DIR / "config" / f"{name}.json"
            if src.exists():
                shutil.copy(src, self.root / ".claude" / "config" / f"{name}.json")
        layers = CLAUDE_DIR / "system-map" / "layers.json"
        if layers.exists():
            shutil.copy(layers, self.root / ".claude" / "system-map" / "layers.json")
        # The real console template, so a fixture can exercise consolectl.build() end to end
        # against the page we actually ship rather than a stand-in that cannot drift with it.
        template = CLAUDE_DIR / "console" / "console.template.html"
        if template.exists():
            shutil.copy(template, self.root / ".claude" / "console" / "console.template.html")

        self._saved_env = {
            key: os.environ.get(key)
            for key in ("CLAUDE_PROJECT_DIR", "CLAUDE_IFF_RECORD_ROOT", "CLAUDE_SESSION_ID")
        }
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.record)
        os.environ["CLAUDE_SESSION_ID"] = "test-session"
        _lib.clear_config_cache()

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _lib.clear_config_cache()
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------------

    def write_config(self, name: str, obj) -> Path:
        path = self.root / ".claude" / "config" / f"{name}.json"
        _lib.atomic_write_json(path, obj)
        _lib.clear_config_cache()
        return path

    def spool_event(self, session: str = "s1", **fields) -> dict:
        """Append one raw capture event exactly as the hook would."""
        event = {"_obs_ts": _lib.utc_now(), "session_id": session}
        event.update(fields)
        _lib.append_jsonl(self.record / "spool" / f"{session}.jsonl", event)
        return event

    def journal(self, action: str, **fields) -> dict:
        return _lib.journal_append(action, **fields)

    def linked_worktree(self, name: str = "todo-note", branch: str | None = None) -> Path:
        """Lay out a LINKED git worktree of the fixture project the way `git worktree add`
        does, without git: `.git/` in the project (main), `.git/worktrees/<name>/HEAD` on the
        branch, and `.claude/worktrees/<name>/` holding a `.git` FILE that points back plus a
        copy of `.claude/` (config, tools, state) - what a Cview worker finds when its session
        starts there. Returns the worktree path; point CLAUDE_PROJECT_DIR at it to BE the
        worker (`as_worktree`). For the real thing, `git_worktree`."""
        branch = branch or f"worktree-{name}"
        git_dir = self.root / ".git"
        wt_git = git_dir / "worktrees" / name
        wt_git.mkdir(parents=True, exist_ok=True)
        (wt_git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
        (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
        wt = self.root / ".claude" / "worktrees" / name
        wt.mkdir(parents=True, exist_ok=True)
        (wt / ".git").write_text(f"gitdir: {wt_git.as_posix()}\n", encoding="utf-8")
        for sub in ("config", "tools", "state", "system-map", "console"):
            src = self.root / ".claude" / sub
            if src.is_dir():
                shutil.copytree(src, wt / ".claude" / sub, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("worktrees", "__pycache__"))
        for name_ in ("CLAUDE.md", "STATUS.md"):
            src = self.root / ".claude" / name_
            if src.is_file():
                shutil.copy(src, wt / ".claude" / name_)
        return wt

    def as_worktree(self, wt: Path) -> None:
        """Make `wt` the project root for the rest of the test, as a session started inside
        it would have it (hooks get CLAUDE_PROJECT_DIR = the worktree)."""
        os.environ["CLAUDE_PROJECT_DIR"] = str(wt)
        _lib.clear_config_cache()

    def git_worktree(self, name: str = "todo-note") -> Path:
        """The real thing: `git init` the fixture project, commit its .claude/ (the hooks and
        tools copied from this repo, so the worktree holds them too), then `git worktree add
        .claude/worktrees/<name> -b worktree-<name>`. Skips the test when git is missing.
        Returns the worktree path."""
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        for sub in ("hooks", "tools"):
            shutil.copytree(CLAUDE_DIR / sub, self.root / ".claude" / sub, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="f@x",
                   GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="f@x")

        def git(*args):
            res = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True,
                                 text=True, timeout=60, env=env, check=False)
            if res.returncode != 0:
                self.skipTest(f"git {' '.join(args)} failed: {res.stderr.strip()[:200]}")
            return res.stdout

        git("init", "-q", "-b", "main")
        git("config", "core.autocrlf", "false")
        git("add", "-A")
        git("commit", "-q", "-m", "fixture")
        wt = self.root / ".claude" / "worktrees" / name
        git("worktree", "add", "-q", wt.as_posix(), "-b", f"worktree-{name}")
        return wt

    def grant_ticket(self, skill: str = "project-memory", ts: str | None = None,
                     session_id: str = "test-session") -> dict:
        """Write the ritual ticket exactly as hooks/ritual-ticket.sh does when the user types
        /project-memory: `checkctl run` and `complete` refuse without a fresh one. Tests grant
        it here; there is deliberately no flag or env var that skips the check."""
        ticket = {"skill": skill, "ts": ts or _lib.utc_now(), "session_id": session_id,
                  "event": "UserPromptSubmit"}
        _lib.atomic_write_json(_lib.state_dir() / "ritual-ticket.json", ticket, durable=True)
        return ticket
