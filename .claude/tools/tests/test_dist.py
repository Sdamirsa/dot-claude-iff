#!/usr/bin/env python3
"""test_dist.py - the distribution zips must carry the system and never this project's history.

A zip that leaks the source's journal, queue, or filled CLAUDE.md hands every adopter another
project's memory; a zip that goes stale hands them last month's system. So: exclusions proven,
placeholder form proven, determinism proven (identical content, identical bytes, write-gated).
"""

from __future__ import annotations

import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import FixtureCase  # noqa: E402

import _lib  # noqa: E402
import distctl  # noqa: E402


class DistCase(FixtureCase):
    def setUp(self):
        super().setUp()
        claude = self.root / ".claude"
        (claude / "skills" / "adopt").mkdir(parents=True, exist_ok=True)
        (claude / "skills" / "adopt" / "CLAUDE.template.md").write_text(
            "# {{PROJECT_NAME}}\n\n{{MISSION}}\n", encoding="utf-8")
        (claude / "tasks").mkdir(exist_ok=True)
        (claude / "tasks" / "_template.md").write_text("# Task: {{TITLE}}\n", encoding="utf-8")
        (claude / "tasks" / "20260101-old-work.md").write_text("# Task: old\n", encoding="utf-8")
        (claude / "CLAUDE.md").write_text("# filled guide of THE SOURCE\n", encoding="utf-8")
        (claude / "STATUS.md").write_text("source status\n", encoding="utf-8")
        (claude / "Project-log.jsonl").write_text('{"date":"x","type":"note","title":"src"}\n')
        (claude / "console").mkdir(exist_ok=True)
        (claude / "console" / "console.html").write_text("<built page>", encoding="utf-8")
        self.journal("pointer", text="source pointer that must not ship")

    def names(self, zip_name: str) -> list:
        with zipfile.ZipFile(self.root / ".claude" / "dist" / zip_name) as z:
            return z.namelist()

    def read(self, zip_name: str, entry: str) -> str:
        with zipfile.ZipFile(self.root / ".claude" / "dist" / zip_name) as z:
            return z.read(entry).decode("utf-8")


class TestPayload(DistCase):
    def test_no_project_history_ships(self):
        distctl.build(self.root)
        for zip_name, prefix in (("dot-claude-iff-fresh.zip", ""),
                                 ("dot-claude-iff-adopt-kit.zip", "dot-claude-iff-kit/")):
            names = self.names(zip_name)
            with self.subTest(zip=zip_name):
                self.assertFalse(any("/state/" in n for n in names),
                                 "state (journal, queue, heartbeat) is this project's memory")
                self.assertFalse(any(n.endswith("console.html") for n in names),
                                 "derived surfaces are rebuilt by the target's own ritual")
                self.assertFalse(any("old-work" in n for n in names),
                                 "the source's tasks are its history, not the system")
                self.assertIn(f"{prefix}.claude/tasks/_template.md", names,
                              "the scaffold template does ship")
                self.assertFalse(any("/dist/" in n for n in names), "no zip recursion")

    def test_identity_files_are_reset(self):
        distctl.build(self.root)
        text = self.read("dot-claude-iff-fresh.zip", ".claude/CLAUDE.md")
        self.assertIn("{{PROJECT_NAME}}", text, "CLAUDE.md ships in placeholder form")
        self.assertNotIn("THE SOURCE", text)
        self.assertEqual(self.read("dot-claude-iff-fresh.zip", ".claude/Project-log.jsonl"), "")
        self.assertIn("Adoption in progress",
                      self.read("dot-claude-iff-fresh.zip", ".claude/STATUS.md"))

    def test_guides_present_and_distinct(self):
        distctl.build(self.root)
        fresh = self.read("dot-claude-iff-fresh.zip", "START-HERE.md")
        self.assertIn("skip the copy phase", fresh)
        adopt = self.read("dot-claude-iff-adopt-kit.zip", "ADOPT.md")
        self.assertIn("merge, never overwrite", adopt)
        self.assertIn("OUTSIDE the repo", adopt)

    def test_kit_is_folder_wrapped_so_it_cannot_clobber(self):
        distctl.build(self.root)
        names = self.names("dot-claude-iff-adopt-kit.zip")
        self.assertTrue(all(n == "ADOPT.md" or n.startswith("dot-claude-iff-kit/") for n in names),
                        "the kit unzips into its own folder, never into a repo's root")

    def test_deterministic_and_write_gated(self):
        first = distctl.build(self.root)
        self.assertTrue(all(r["wrote"] for r in first.values()))
        second = distctl.build(self.root)
        self.assertFalse(any(r["wrote"] for r in second.values()),
                         "identical content must produce identical bytes and skip the write")


class TestBilling(FixtureCase):
    def test_subscription_makes_empty_prices_a_non_warning(self):
        import checkctl
        import consolectl
        import obsctl
        self.write_config("model-prices", {"billing": "subscription", "per_million_tokens": {}})
        result = checkctl.check_price_table()
        self.assertEqual(result.status, checkctl.OK)
        self.assertIn("not applicable", result.message)

        self.spool_event(session="s1", hook_event_name="llm.usage", _obs_source="transcript",
                         **{"gen_ai.request.model": "m", "gen_ai.usage.output_tokens": 7})
        obsctl.main(["seal", "--date", _lib.today()])
        obsctl.main(["rollup", "--date", _lib.today()])
        rollup = json.loads((_lib.iff_dir() / "obs" / "rollups" / f"{_lib.today()}.json").read_text())
        self.assertEqual(rollup["cost"]["billing"], "subscription")

        payload = consolectl.payload()
        self.assertEqual(payload["tokens"]["billing"], "subscription")
        self.assertEqual(payload["tokens"]["total"]["output"], 7, "token counts still tracked")
        self.assertNotIn("price table empty - costs read unknown", payload["warnings"])

    def test_api_billing_keeps_the_loud_warning(self):
        import checkctl
        self.write_config("model-prices", {"billing": "api", "per_million_tokens": {}})
        result = checkctl.check_price_table()
        self.assertEqual(result.status, checkctl.WARN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
