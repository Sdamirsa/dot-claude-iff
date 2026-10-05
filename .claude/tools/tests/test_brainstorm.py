#!/usr/bin/env python3
"""test_brainstorm.py - the communication block and the vendored /adhd brainstorm skill.

Two promises are cheap to make and easy to rot. The Communication block is a discipline the
agent reads every session, so it must exist in the live guide AND in the template adopters get,
stay short enough to be read, and keep the honesty escape hatch. The /adhd skill is third-party
code under MIT: its licence notice must travel with it, its tuning must live in config where the
ritual can change it, and every knob the skill names must exist and have a registry card, or
the skill reads a key nobody can find and the registry documents a knob nobody reads.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import mapctl  # noqa: E402

GUIDES = {
    "CLAUDE.md": CLAUDE_DIR / "CLAUDE.md",
    "CLAUDE.template.md": CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md",
}
BLOCK_HEADING = "## Communication"
BLOCK_LINE_CAP = 10

SKILL_DIR = CLAUDE_DIR / "skills" / "adhd"
SKILL = SKILL_DIR / "SKILL.md"
CONFIG = CLAUDE_DIR / "config" / "brainstorm.json"
REQUIRED_KNOBS = ("branch_count", "frames", "branch_model", "critic_model")

# Upstream rendered `[N7 V8 F9]` chips from a 0-10 rating and a weighted sum: three numbers
# from one model pass, presented as measurement. Any of these shapes coming back is a regression.
SCORE_CHIP_PATTERNS = (
    r"\[\s*N\s*\d+\s+V\s*\d+\s+F\s*\d+\s*\]",
    r"\bN\d+\s+V\d+\s+F\d+\b",
    r"\b0\s*(?:to|-)\s*10\b",
    r"weighted score",
    r"novelty\s*0?\.\d",
)


def communication_block(text: str) -> list:
    """The block's lines, heading included, trailing blank lines dropped; [] when absent."""
    lines = text.splitlines()
    if BLOCK_HEADING not in lines:
        return []
    start = lines.index(BLOCK_HEADING)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    return block


def skill_config_keys(text: str) -> set:
    """Config keys the skill names, written as `brainstorm.<key>` in backticks."""
    return set(re.findall(r"`brainstorm\.([A-Za-z_][A-Za-z0-9_]*)`", text))


class TestCommunicationBlock(FixtureCase):
    def test_present_in_both_guides_and_within_the_cap(self):
        for name, path in GUIDES.items():
            with self.subTest(guide=name):
                block = communication_block(path.read_text(encoding="utf-8"))
                self.assertTrue(block, f"{name} has no '{BLOCK_HEADING}' section")
                self.assertGreater(len(block), 2, f"{name}: the Communication section is empty")
                self.assertLessEqual(len(block), BLOCK_LINE_CAP,
                                     f"{name}: Communication block is {len(block)} lines, cap is "
                                     f"{BLOCK_LINE_CAP}; a discipline nobody finishes reading "
                                     f"is not one")

    def test_block_carries_every_rule(self):
        markers = (
            "verdict first", "at most four", "exactly one", "pick", "one-line reason",
            "rejected", '"quick"', '"just"', "three lines or fewer",
            '"not enough evidence, missing x" is a valid pick',
            "failure output", "skipped steps", "honesty.md",
        )
        for name, path in GUIDES.items():
            block = " ".join(communication_block(path.read_text(encoding="utf-8"))).lower()
            block = re.sub(r"\s+", " ", block)
            for marker in markers:
                with self.subTest(guide=name, marker=marker):
                    self.assertIn(marker, block, f"{name}: Communication block lost '{marker}'")

    def test_guide_and_template_carry_the_same_block(self):
        """The template is what every adopter's guide starts from: a block edited in one place
        only means adopters and this repo follow different disciplines."""
        live, template = (communication_block(p.read_text(encoding="utf-8")) for p in GUIDES.values())
        self.assertEqual(live, template, "CLAUDE.md and CLAUDE.template.md Communication blocks differ")

    def test_both_guides_point_at_adhd(self):
        """disable-model-invocation drops the skill's description from the agent's context, so
        the 'you may suggest /adhd' rule only reaches the agent through the guide."""
        for name, path in GUIDES.items():
            with self.subTest(guide=name):
                self.assertIn("`/adhd`", path.read_text(encoding="utf-8"),
                              f"{name} never mentions /adhd; the agent cannot suggest what it cannot see")


class TestVendoredSkill(FixtureCase):
    def test_licence_notice_travels_with_the_skill(self):
        licence = (SKILL_DIR / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Copyright (c) 2026 ADHD contributors", licence)
        self.assertIn("The above copyright notice and this permission notice shall be included",
                      licence, "MIT requires the permission notice in every copy")

    def test_upstream_record_names_source_and_revision(self):
        upstream = (SKILL_DIR / "UPSTREAM.md").read_text(encoding="utf-8")
        self.assertIn("https://github.com/UditAkhourii/adhd", upstream)
        self.assertRegex(upstream, r"\b[0-9a-f]{40}\b", "UPSTREAM.md must pin the fetched commit")
        self.assertIn("MIT", upstream)

    def test_skill_keeps_the_notice_and_is_user_invoked(self):
        text = SKILL.read_text(encoding="utf-8")
        fm = mapctl.parse_frontmatter(text)
        self.assertEqual(fm.get("name"), "adhd")
        self.assertEqual(fm.get("disable-model-invocation"), "true",
                         "about ten agent calls per run: the user decides when, not the model")
        self.assertEqual(fm.get("license"), "MIT")
        self.assertIn("suggest", fm.get("description", "").lower())
        self.assertIn("https://github.com/UditAkhourii/adhd", text)
        self.assertIn("Copyright (c) 2026 ADHD contributors", text)

    def test_no_score_chips_left(self):
        text = SKILL.read_text(encoding="utf-8")
        for pattern in SCORE_CHIP_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text, re.IGNORECASE),
                                  f"invented-precision scoring is back in SKILL.md: {pattern}")

    def test_honesty_outranks_brevity_and_the_pick(self):
        text = re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8")).lower()
        self.assertIn(".claude/protocols/honesty.md", text)
        self.assertIn('"not enough evidence, missing x" is a valid pick', text)

    def test_tuning_section_routes_changes_through_evolve(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("## Tuning", text)
        tuning = text.split("## Tuning", 1)[1]
        self.assertIn("EVOLVE", tuning)
        self.assertIn(".claude/config/brainstorm.json", tuning)
        self.assertIn(".claude/protocols/evolution.md", tuning)
        evolution = (CLAUDE_DIR / "protocols" / "evolution.md").read_text(encoding="utf-8")
        self.assertIn("brainstorm.json", evolution,
                      "the protocol EVOLVE follows must say brainstorm.json is tunable")


class TestBrainstormConfig(FixtureCase):
    def setUp(self):
        super().setUp()
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.skill_keys = skill_config_keys(SKILL.read_text(encoding="utf-8"))
        registry = json.loads((CLAUDE_DIR / "config" / "registry.json").read_text(encoding="utf-8"))
        self.cards = {e.get("key"): e for e in registry.get("entries") or []}
        self.watched = (registry.get("lint") or {}).get("watched_files") or []

    def test_the_mandated_knobs_are_read_by_the_skill(self):
        for knob in REQUIRED_KNOBS:
            with self.subTest(knob=knob):
                self.assertIn(knob, self.skill_keys, f"SKILL.md never reads brainstorm.{knob}")

    def test_every_key_the_skill_names_exists_and_has_a_card(self):
        self.assertTrue(self.skill_keys, "SKILL.md names no `brainstorm.<key>`: nothing is tunable")
        for key in sorted(self.skill_keys):
            with self.subTest(key=key):
                self.assertIn(key, self.config, f"SKILL.md reads brainstorm.{key}, absent from config")
                card = self.cards.get(f"brainstorm.{key}")
                self.assertIsNotNone(card, f"brainstorm.{key} has no registry card")
                self.assertEqual(card.get("target"),
                                 {"kind": "config", "file": "brainstorm", "path": key})

    def test_every_config_key_is_read_by_the_skill(self):
        """A knob the skill never reads is a dial wired to nothing."""
        knobs = {k for k in self.config if not k.startswith("_")}
        self.assertEqual(knobs - self.skill_keys, set(),
                         "brainstorm.json holds keys SKILL.md never names")

    def test_frames_are_well_formed(self):
        frames = self.config["frames"]
        count = self.config["branch_count"]
        self.assertIsInstance(count, int)
        self.assertGreater(count, 0)
        self.assertLessEqual(count, len(frames), "more branches than frames to give them")
        names = [f.get("name") for f in frames]
        self.assertEqual(len(names), len(set(names)), "duplicate frame names")
        for frame in frames:
            with self.subTest(frame=frame.get("name")):
                self.assertTrue(frame.get("name") and frame.get("vantage"))
                self.assertIsInstance(frame.get("tags"), list)
                self.assertTrue(frame["tags"])
        self.assertTrue(any("wild" in f["tags"] for f in frames),
                        "the skill always picks one wild frame; the catalogue must hold one")
        for knob in ("branch_model", "critic_model"):
            with self.subTest(knob=knob):
                self.assertIsInstance(self.config[knob], str)
                self.assertTrue(self.config[knob].strip())

    def _brainstorm_only_registry(self) -> None:
        """The shipped registry narrowed to the brainstorm cards, so the lint's verdict is about
        them alone (the fixture has no agent files, and the lint truncates its details)."""
        registry = _lib.load_config("registry")
        registry["entries"] = [e for e in registry["entries"]
                               if (e.get("target") or {}).get("file") == "brainstorm"]
        registry["lint"]["watched_files"] = ["brainstorm"]
        self.write_config("registry", registry)

    def test_registry_lint_binds_every_brainstorm_card(self):
        self.assertIn("brainstorm", self.watched, "an unwatched config never warns on a new knob")
        self._brainstorm_only_registry()
        result = checkctl.check_config_registry()
        self.assertEqual(result.status, checkctl.OK, result.details)

    def test_registry_lint_catches_a_dropped_knob(self):
        """The binding is real: remove a knob the registry documents and the lint must FAIL."""
        self._brainstorm_only_registry()
        cfg = _lib.load_config("brainstorm")
        cfg.pop("critic_model")
        self.write_config("brainstorm", cfg)
        result = checkctl.check_config_registry()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any("brainstorm.critic_model" in d for d in result.details), result.details)

    def test_registry_lint_flags_an_uncarded_knob(self):
        self._brainstorm_only_registry()
        cfg = _lib.load_config("brainstorm")
        cfg["ideas_per_branch"] = 6
        self.write_config("brainstorm", cfg)
        result = checkctl.check_config_registry()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertTrue(any("brainstorm.ideas_per_branch" in d for d in result.details), result.details)


class TestWiring(FixtureCase):
    def test_probe_ledger_names_skill_and_config(self):
        ledger = {r.name: r.message for r in checkctl.probe()}
        self.assertEqual(ledger.get("skill.adhd"), ".claude/skills/adhd/SKILL.md")
        self.assertEqual(ledger.get("config.brainstorm"), ".claude/config/brainstorm.json")

    def test_system_map_cards_exist(self):
        for cid, path in (("skill.adhd", ".claude/skills/adhd/SKILL.md"),
                          ("config.brainstorm", ".claude/config/brainstorm.json")):
            with self.subTest(card=cid):
                card_path = CLAUDE_DIR / "system-map" / "cards" / f"{cid}.json"
                self.assertTrue(card_path.exists(), f"{cid} has no system-map card")
                card = json.loads(card_path.read_text(encoding="utf-8"))
                self.assertEqual(card.get("id"), cid)
                self.assertEqual(card.get("path"), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
