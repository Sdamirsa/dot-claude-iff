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

SHIPPED_CONFIGS = ("memory", "policy", "observe", "console", "registry", "model-prices")


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
