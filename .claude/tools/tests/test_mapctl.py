#!/usr/bin/env python3
"""test_mapctl.py - contract tests for the system map: scan / lint / compile / show.

The whole point of mapctl is that the map stays TRUE: a component with no card, a card whose
path is gone, an edge that points nowhere, are worth failing CHECK over (the ERROR tier); a
card nobody has placed into a layer yet, or whose relations are still empty, is worth knowing
about but never worth blocking on (the WARN tier). These tests are organized around that split,
plus the two anti-rot properties scan and compile must hold: a rescan must never clobber a
human's curation, and a compile with nothing new to say must never touch the file on disk.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

TOOLS_DIR = CLAUDE_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _lib  # noqa: E402
import mapctl  # noqa: E402

VERDICT_RE = re.compile(r"^MAP_(OK|WARN|FAIL)$", re.MULTILINE)


def minimal_card(id_, kind="data", layer=None, path=None, reads=None, writes=None,
                  invokes=None, glyphs=None, auto=None):
    card = {
        "id": id_, "kind": kind, "layer": layer, "package": None, "title": id_, "path": path,
        "description": "", "reads": reads or [], "writes": writes or [], "invokes": invokes or [],
        "flows": [], "glyphs": glyphs or [],
        "auto": {"discovered": "2026-08-01", "hash": "", "suggested_reads": [], "suggested_writes": []},
    }
    if auto:
        card["auto"].update(auto)
    return card


class MapctlCase(FixtureCase):
    """Runs mapctl in-process (it is a pure stdlib CLI module, no subprocess needed)."""

    def run_cli(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mapctl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def assertVerdict(self, stdout: str, state: str) -> None:
        m = VERDICT_RE.search(stdout)
        self.assertIsNotNone(m, f"no MAP_* verdict token in output:\n{stdout!r}")
        self.assertEqual(m.group(1), state)

    def cards_dir(self) -> Path:
        return self.root / ".claude" / "system-map" / "cards"

    def map_path(self) -> Path:
        return self.root / ".claude" / "system-map" / "map.json"

    def write_hook(self, name: str, body: str | None = None) -> Path:
        p = self.root / ".claude" / "hooks" / f"{name}.sh"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body or f"#!/usr/bin/env bash\n# {name} does a thing.\n# more detail here.\necho hi\n",
                      encoding="utf-8")
        return p

    def write_protocol(self, name: str, heading: str = "Foo protocol", body: str = "Does a thing.") -> Path:
        p = self.root / ".claude" / "protocols" / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {heading}\n\n{body}\n", encoding="utf-8")
        return p

    def write_tool(self, name: str, body: str) -> Path:
        p = self.root / ".claude" / "tools" / f"{name}.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def write_card(self, card: dict) -> Path:
        path = self.cards_dir() / f"{card['id']}.json"
        _lib.atomic_write_json(path, card)
        return path

    def read_card(self, id_: str):
        return _lib.read_json(self.cards_dir() / f"{id_}.json")


# --------------------------------------------------------------------------- 1. scan creates stubs

class TestScanCreatesStub(MapctlCase):
    def test_scan_creates_stub_card_with_null_layer(self):
        self.write_hook("foo")
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "OK")

        card = self.read_card("hook.foo")
        self.assertIsNotNone(card, "scan must create a card for a newly discovered hook")
        self.assertEqual(card["kind"], "code")
        self.assertIsNone(card["layer"], "scan must never guess a layer")
        self.assertEqual(card["path"], ".claude/hooks/foo.sh")
        self.assertIn("foo does a thing", card["description"])
        self.assertEqual(card["reads"], [])
        self.assertEqual(card["writes"], [])
        self.assertEqual(card["invokes"], [])
        self.assertTrue(card["auto"]["hash"], "a real source file must get a real content hash")
        self.assertIn("CREATED", out)
        self.assertIn("hook.foo", out)

    def test_scan_also_creates_declared_store_and_human_cards(self):
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)
        journal = self.read_card("store.journal")
        self.assertIsNotNone(journal)
        self.assertIsNone(journal["layer"])
        self.assertEqual(journal["auto"]["hash"], "", "declared cards never carry a source hash")
        self.assertTrue(journal["auto"].get("lazy"), "a declared store must be exempt from the ghost check")
        maintainer = self.read_card("human.maintainer")
        self.assertIsNotNone(maintainer)
        self.assertEqual(maintainer["kind"], "human")


# --------------------------------------------------------------------------- 2. scan preserves curation

class TestScanPreservesCuratedFields(MapctlCase):
    def test_layer_and_reads_survive_a_rescan(self):
        self.write_hook("foo")
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)

        card = self.read_card("hook.foo")
        card["layer"] = "GOVERN"
        card["reads"] = ["config.policy"]
        card["glyphs"] = ["envelope"]
        self.write_card(card)

        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "OK")

        after = self.read_card("hook.foo")
        self.assertEqual(after["layer"], "GOVERN", "a rescan must not clobber a curated layer")
        self.assertEqual(after["reads"], ["config.policy"], "a rescan must not clobber curated relations")
        self.assertEqual(after["glyphs"], ["envelope"], "a rescan must not clobber curated glyphs")
        # auto.* stays live: still refreshed from the real source file.
        self.assertTrue(after["auto"]["hash"])

    def test_description_hand_edit_survives_a_rescan(self):
        self.write_hook("foo")
        self.run_cli("scan")
        card = self.read_card("hook.foo")
        card["description"] = "a maintainer wrote this by hand"
        self.write_card(card)

        self.run_cli("scan")
        after = self.read_card("hook.foo")
        self.assertEqual(after["description"], "a maintainer wrote this by hand")


# --------------------------------------------------------------------------- 3. lint ERROR tier

class TestLintErrors(MapctlCase):
    def test_dangling_edge_is_an_error(self):
        self.write_hook("a")
        self.run_cli("scan")
        card = self.read_card("hook.a")
        card["reads"] = ["hook.nonexistent"]
        self.write_card(card)

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 1)
        self.assertVerdict(out, "FAIL")
        self.assertIn("hook.a", out)
        self.assertIn("hook.nonexistent", out)
        self.assertIn("dangling edge", out)

    def test_ghost_card_is_an_error(self):
        path = self.write_hook("b")
        self.run_cli("scan")
        path.unlink()

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 1)
        self.assertVerdict(out, "FAIL")
        self.assertIn("hook.b", out)
        self.assertIn("ghost", out)

    def test_unknown_layer_is_an_error(self):
        self.write_hook("c")
        self.run_cli("scan")
        card = self.read_card("hook.c")
        card["layer"] = "NOT-A-REAL-LAYER"
        self.write_card(card)

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 1)
        self.assertVerdict(out, "FAIL")
        self.assertIn("hook.c", out)
        self.assertIn("NOT-A-REAL-LAYER", out)


# --------------------------------------------------------------------------- 4. lint WARN tier

class TestLintWarnings(MapctlCase):
    def test_null_layer_warns_but_does_not_fail(self):
        self.write_hook("foo")
        self.run_cli("scan")

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "WARN")
        self.assertIn("hook.foo", out)
        self.assertIn("layer is null", out)

    def test_empty_relations_with_suggestions_warns_but_does_not_fail(self):
        self.write_tool(
            "probe",
            '"""probe.py - a test double that touches the journal."""\n'
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))\n"
            "import _lib\n\n"
            "def touch():\n"
            '    _lib.journal_append("note", text="hi")\n'
            '    return _lib.state_dir() / "journal.jsonl"\n',
        )
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)

        card = self.read_card("tool.probe")
        self.assertEqual(card["reads"], [], "curated relations start empty, never guessed")
        self.assertEqual(card["writes"], [])
        self.assertIn("store.journal", card["auto"]["suggested_writes"])
        self.assertIn("store.journal", card["auto"]["suggested_reads"])

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "WARN")
        self.assertIn("tool.probe", out)
        self.assertIn("auto.suggested", out)


# --------------------------------------------------------------------------- 5. compile write-gating

class TestCompileWriteGated(MapctlCase):
    def test_second_compile_with_nothing_new_touches_nothing(self):
        # Hand-built, path: null cards - never flips `exists`, so this isolates the write-gate
        # from generator-freshness noise (map_scan/map_compile churn is a separate concern).
        self.write_card(minimal_card("human.tester", kind="human", path=None))
        self.write_card(minimal_card("external.thing", kind="external", path=None,
                                      invokes=["human.tester"]))

        code, out, err = self.run_cli("compile")
        self.assertIn(code, (0, 1))  # missing-card noise from shipped configs is not under test
        map1 = json.loads(self.map_path().read_text())
        gen1 = map1["generated_at"]
        mtime1 = self.map_path().stat().st_mtime_ns

        code, out, err = self.run_cli("compile")
        map2 = json.loads(self.map_path().read_text())
        self.assertEqual(gen1, map2["generated_at"], "an unchanged compile must not rewrite the file")
        self.assertEqual(self.map_path().stat().st_mtime_ns, mtime1)
        self.assertEqual(map1, map2)
        self.assertIn("unchanged, not rewritten", out)

    def test_compile_is_deterministic_given_identical_cards(self):
        self.write_card(minimal_card("human.tester", kind="human", path=None))
        self.run_cli("compile")
        map1 = json.loads(self.map_path().read_text())

        # Force a rewrite by touching generated_at indirectly (delete and rebuild), then compare
        # everything except the timestamp field.
        self.map_path().unlink()
        self.run_cli("compile")
        map2 = json.loads(self.map_path().read_text())

        strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}
        self.assertEqual(strip(map1), strip(map2))


# --------------------------------------------------------------------------- 6. missing card

class TestMissingCardIsError(MapctlCase):
    def test_component_file_without_a_card_is_a_lint_error(self):
        self.write_protocol("foo")

        code, out, err = self.run_cli("lint")
        self.assertEqual(code, 1)
        self.assertVerdict(out, "FAIL")
        self.assertIn("protocol.foo", out)
        self.assertIn("no card", out)

    def test_scan_then_lint_clears_the_missing_card_error(self):
        self.write_protocol("foo")
        self.run_cli("scan")

        code, out, err = self.run_cli("lint")
        self.assertNotIn("protocol.foo: component exists with no card", out)


# --------------------------------------------------------------------------- verdict contract

class TestVerdictContract(MapctlCase):
    def test_every_subcommand_prints_exactly_one_verdict_token(self):
        self.write_hook("x")
        self.run_cli("scan")
        for argv in (("scan",), ("lint",), ("compile",), ("show",), ("show", "--id", "hook.x"),
                     ("show", "--id", "does.not.exist")):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                tokens = VERDICT_RE.findall(out)
                self.assertEqual(len(tokens), 1, f"{argv} printed {len(tokens)} verdict tokens:\n{out!r}")

    def test_usage_error_exits_2(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)


class TestUndeclaredStoreCard(MapctlCase):
    """A store card with no KNOWN_STORES entry is invisible to scan's bookkeeping: it never
    refreshes and never ghosts, it just silently drops out of the accounting (how store.dist
    hid for a release). Scan must say so instead."""

    def test_scan_warns_on_a_store_card_mapctl_does_not_declare(self):
        self.write_card(minimal_card("store.bogus", path=".claude/bogus"))
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("UNDECLARED", out)
        self.assertIn("store.bogus", out)
        self.assertVerdict(out, "WARN")

    def test_every_shipped_store_and_human_card_is_declared(self):
        declared = {s["id"] for s in mapctl.KNOWN_STORES} | {mapctl.HUMAN["id"]}
        cards = CLAUDE_DIR / "system-map" / "cards"
        shipped = {p.stem for p in cards.glob("store.*.json")}
        shipped |= {p.stem for p in cards.glob("human.*.json")}
        self.assertLessEqual(shipped, declared,
                             f"cards shipped for undeclared stores: {sorted(shipped - declared)}")



if __name__ == "__main__":
    unittest.main(verbosity=2)
