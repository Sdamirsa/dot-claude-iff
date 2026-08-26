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


class TestDistributionGate(DistCase):
    """demo_build/dist_build are home-repo-only: in an adopting project they would package
    and publish that project's private memory. The gate must fail closed (absent knob =
    disabled), the ritual must SKIP rather than run them, and the shipped kits must land
    with the knob off so an adopter's very first ritual is already safe."""

    def _set_knob(self, value) -> None:
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        if value is None:
            cfg.pop("distribution", None)
        else:
            cfg["distribution"] = {"enabled": value}
        self.write_config("memory", cfg)

    def test_disabled_refuses_to_build(self):
        self._set_knob(False)
        with self.assertRaises(_lib.LibError):
            distctl.build(self.root)
        self.assertFalse((self.root / ".claude" / "dist").exists(),
                         "a refused build must leave nothing behind")

    def test_absent_knob_fails_closed(self):
        self._set_knob(None)
        with self.assertRaises(_lib.LibError):
            distctl.build(self.root)

    def test_shipped_kits_land_with_the_knob_off(self):
        distctl.build(self.root)
        for zip_name, entry in (("dot-claude-iff-fresh.zip", ".claude/config/memory.json"),
                                ("dot-claude-iff-adopt-kit.zip",
                                 "dot-claude-iff-kit/.claude/config/memory.json")):
            with self.subTest(zip=zip_name):
                cfg = json.loads(self.read(zip_name, entry))
                self.assertFalse(cfg["distribution"]["enabled"],
                                 "a kit installing with the knob on leaks on the first ritual")

    def test_shipped_kits_land_with_auto_port_and_monitor_off(self):
        cfg = _lib.read_json(self.root / ".claude" / "config" / "console.json", {}) or {}
        cfg["port"] = 7146                    # a home repo's decided-once port must not ship
        cfg["monitor"] = {"enabled": True}    # nor its monitoring preference
        self.write_config("console", cfg)
        distctl.build(self.root)
        for zip_name, entry in (("dot-claude-iff-fresh.zip", ".claude/config/console.json"),
                                ("dot-claude-iff-adopt-kit.zip",
                                 "dot-claude-iff-kit/.claude/config/console.json")):
            with self.subTest(zip=zip_name):
                shipped = json.loads(self.read(zip_name, entry))
                self.assertEqual(shipped["port"], "auto",
                                 "a kit shipping one machine's port just moves the collision")
                self.assertFalse(shipped["monitor"]["enabled"],
                                 "the monitor is opt-in; kits must land with it off")

    def test_ritual_reports_gated_generators_as_skipped(self):
        import checkctl
        self._set_knob(False)
        self.assertTrue(checkctl.generator_gated_off("dist_build"))
        self.assertTrue(checkctl.generator_gated_off("demo_build"))
        self.assertFalse(checkctl.generator_gated_off("console_build"))
        report = {name: status for name, status, _msg in checkctl.generator_freshness_report()}
        self.assertEqual(report["dist_build"], checkctl.SKIP)
        self.assertEqual(report["demo_build"], checkctl.SKIP)

    def test_polish_complete_does_not_demand_a_gated_generator(self):
        import checkctl
        self._set_knob(False)
        # Stage stand-in tools so tool_path().exists() is true and the gate (not the missing
        # tool) is what exempts the two home-only generators.
        for tool in ("distctl.py", "consolectl.py"):
            stub = self.root / ".claude" / "tools" / tool
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("# stand-in\n", encoding="utf-8")
        run = checkctl.start_run()
        checkctl.record_phase(run, "check", [], checkctl.OK)
        checkctl.record_phase(run, "polish", [], checkctl.OK)
        ok, why = checkctl.polish_complete(run)
        self.assertFalse(ok, "console_build (ungated, never ran) must still be demanded")
        self.assertIn("console_build", why)
        self.assertNotIn("dist_build", why)
        self.assertNotIn("demo_build", why)

    def test_private_reference_excluded_even_without_git(self):
        private = self.root / ".claude" / "reference" / "private"
        private.mkdir(parents=True)
        (private / "brand-guide.md").write_text("PERSONAL SECRET\n", encoding="utf-8")
        distctl.build(self.root)
        names = self.names("dot-claude-iff-fresh.zip")
        self.assertFalse(any("reference/private" in n for n in names),
                         "the private reference tree must not ship on the no-git fallback path")


class TestGitTrackedManifest(DistCase):
    """The working tree supplies file content; git decides WHICH files ship. A gitignored
    or untracked file under .claude/ (the private reference tree that leaked in the field)
    must never reach the zips, and what the manifest keeps out is reported, never silent."""

    def setUp(self):
        super().setUp()
        import shutil as _shutil
        import subprocess as _subprocess
        if not _shutil.which("git"):
            self.skipTest("git not available")
        r = _subprocess.run(["git", "init", "-q"], cwd=str(self.root),
                            capture_output=True, text=True, check=False)
        if r.returncode != 0:
            self.skipTest(f"git init failed: {r.stderr}")

    def _git(self, *args):
        import subprocess as _subprocess
        return _subprocess.run(["git", *args], cwd=str(self.root),
                               capture_output=True, text=True, check=False)

    def test_gitignored_private_reference_never_ships(self):
        private = self.root / ".claude" / "reference" / "private"
        private.mkdir(parents=True)
        (private / "brand-guide.md").write_text("PERSONAL SECRET\n", encoding="utf-8")
        (self.root / ".claude" / "reference" / "public.md").write_text("ships\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".claude/reference/private/\n", encoding="utf-8")
        self._git("add", "-A")
        distctl.build(self.root)
        names = self.names("dot-claude-iff-fresh.zip")
        self.assertNotIn(".claude/reference/private/brand-guide.md", names)
        self.assertIn(".claude/reference/public.md", names)

    def test_untracked_file_does_not_ship_and_is_reported(self):
        import contextlib
        import io
        ref = self.root / ".claude" / "reference"
        ref.mkdir(parents=True, exist_ok=True)
        (ref / "tracked.md").write_text("in\n", encoding="utf-8")
        self._git("add", "-A")
        (ref / "stray.md").write_text("out\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            distctl.build(self.root)
        names = self.names("dot-claude-iff-fresh.zip")
        self.assertIn(".claude/reference/tracked.md", names)
        self.assertNotIn(".claude/reference/stray.md", names)
        self.assertIn("stray.md", out.getvalue(),
                      "a file the manifest keeps out must be named, never silently dropped")



if __name__ == "__main__":
    unittest.main(verbosity=2)
