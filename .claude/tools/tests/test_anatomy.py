#!/usr/bin/env python3
"""test_anatomy.py - `mapctl anatomy`: the docs site's interactive anatomy page.

The page is only worth publishing if it stays TRUE and stays SAFE: it carries every component
of the compiled map with a link that points at a real file (on GitHub when origin is there,
relative otherwise; gates fail closed on a path pointing nowhere), its payload parses, it asks
nothing of the network beyond the docs site's own fonts and github.com links, and it is built
by a registered, home-repo-only generator so it can never lag the system. The cascade itself
(levels, breadcrumb, Escape, the deep link, the embed flag) is exercised in a browser by the
lead; here its hooks are pinned by presence.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase  # noqa: E402

TOOLS_DIR = CLAUDE_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _lib  # noqa: E402
import checkctl  # noqa: E402
import mapctl  # noqa: E402

BASE = "https://github.com/owner/repo/blob/main/"
TEMPLATE = CLAUDE_DIR / "console" / "anatomy.template.html"
DATA_RE = re.compile(r'<script id="am-data" type="application/json">(.*?)</script>', re.DOTALL)
# What the page may reference: relative paths inside the docs site, github.com, Google Fonts.
ALLOWED_HOSTS = ("https://github.com/", "https://fonts.googleapis.com/", "https://fonts.gstatic.com/")


def payload_of(html: str) -> dict:
    m = DATA_RE.search(html)
    assert m, "no am-data block in the page"
    return json.loads(m.group(1))


def without_payload(html: str) -> str:
    return DATA_RE.sub("", html)


def references(text: str) -> list:
    """Every src=, href=, url( and fetch( target in the markup, CSS and script."""
    refs = re.findall(r'\b(?:src|href)\s*=\s*["\']([^"\']*)["\']', text)
    refs += re.findall(r'url\(\s*["\']?([^"\')]*)', text)
    refs += re.findall(r'\bfetch\s*\(\s*([^)]*)', text)
    refs += re.findall(r'@import\s+["\']([^"\']+)', text)
    return refs


def card(id_, kind="code", layer="GOVERN", path=None, reads=(), writes=(), invokes=(), lazy=False):
    auto = {"discovered": "2026-11-10", "hash": "", "suggested_reads": [], "suggested_writes": []}
    if lazy:
        auto["lazy"] = True
    return {"id": id_, "kind": kind, "layer": layer, "package": None, "title": id_.title(),
            "path": path, "description": f"What {id_} does.", "reads": list(reads),
            "writes": list(writes), "invokes": list(invokes), "flows": [], "glyphs": [],
            "auto": auto}


class AnatomyCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copy(TEMPLATE, self.root / ".claude" / "console" / "anatomy.template.html")
        self.base = BASE
        self.link_calls = []

        def fake_link_base(root=None, branch=None):
            self.link_calls.append(branch)
            return self.base

        for target, value in (("link_base", fake_link_base), ("anatomy_tracked", lambda root: None)):
            patcher = mock.patch.object(mapctl, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def touch(self, rel: str) -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
        return rel

    def write_cards(self, cards: list) -> None:
        for c in cards:
            _lib.atomic_write_json(self.root / ".claude" / "system-map" / "cards" / f"{c['id']}.json", c)
        layers = _lib.read_json(self.root / ".claude" / "system-map" / "layers.json")
        map_obj = mapctl.build_map({c["id"]: c for c in cards}, layers, [], [])
        _lib.atomic_write_json(self.root / ".claude" / "system-map" / "map.json", map_obj)

    def small_project(self) -> list:
        cards = [
            card("tool.statectl", layer="MEMORY", path=self.touch(".claude/tools/statectl.py"),
                 reads=["store.journal"], writes=["store.journal"], invokes=["tool._lib"]),
            card("tool._lib", layer=None, path=self.touch(".claude/tools/_lib.py")),
            card("hook.policy-gate", path=self.touch(".claude/hooks/policy-gate.sh"),
                 invokes=["tool._lib"]),
            card("agent.anatomist", kind="agent", layer="EVOLVE",
                 path=self.touch(".claude/agents/anatomist.md"), reads=["store.cards"]),
            card("store.journal", kind="data", layer="MEMORY", lazy=True,
                 path=self.touch(".claude/state/journal.jsonl")),
            card("store.cards", kind="data", layer="EVOLVE", lazy=True, path=".claude/system-map/cards"),
            card("store.session", kind="data", layer="MEMORY", lazy=True, path=".claude/state/session.json"),
            card("human.maintainer", kind="human", layer=None, lazy=True),
        ]
        self.write_cards(cards)
        return cards

    def run_cli(self, *argv: str):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = mapctl.main(list(argv))
        return code, out.getvalue()

    def page_path(self) -> Path:
        return self.root / "docs" / "anatomy.html"

    def page(self) -> str:
        return self.page_path().read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the page

class TestAnatomyPage(AnatomyCase):
    def test_builds_from_the_fixture_and_carries_every_map_node_with_a_link(self):
        self.small_project()
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        self.assertIn("MAP_", out)
        html = self.page()
        data = payload_of(html)
        map_ids = [n["id"] for n in _lib.read_json(self.root / ".claude" / "system-map" / "map.json")["nodes"]]
        self.assertEqual([n["id"] for n in data["nodes"]], map_ids, "every map node, in map order")
        by_id = {n["id"]: n for n in data["nodes"]}
        for cid in map_ids:
            node = by_id[cid]
            with self.subTest(node=cid):
                if node["path"] and (self.root / node["path"]).exists():
                    self.assertEqual(node["url"], BASE + node["path"])
        self.assertIsNone(by_id["human.maintainer"]["path"])
        self.assertIsNone(by_id["human.maintainer"]["url"])
        self.assertEqual(by_id["tool._lib"]["layer"], "UNPLACED")
        self.assertEqual([lay["id"] for lay in data["layers"]],
                         ["MEMORY", "GOVERN", "OBSERVE", "CONSOLE", "EVOLVE", "UNPLACED"])
        self.assertTrue(all(lay["blurb"] for lay in data["layers"]), "every tile carries its blurb")
        self.assertIn(["tool.statectl", "store.journal", "writes"], data["edges"])
        self.assertIn(["hook.policy-gate", "tool._lib", "invokes"], data["edges"])

    def test_the_link_is_on_github_with_a_base_and_relative_without(self):
        self.small_project()
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.link_calls, ["main"], "the published page links the main branch")
        node = next(n for n in payload_of(self.page())["nodes"] if n["id"] == "tool.statectl")
        self.assertEqual(node["url"], BASE + ".claude/tools/statectl.py")
        self.assertIn("file links on GitHub", out)

        self.base = None
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        data = payload_of(self.page())
        node = next(n for n in data["nodes"] if n["id"] == "tool.statectl")
        self.assertEqual(node["url"], "../.claude/tools/statectl.py", "relative to docs/")
        self.assertIsNone(data["base"])
        self.assertIn("file links relative", out)

        code, out = self.run_cli("anatomy", "--out", "site/deep/map.html")
        self.assertEqual(code, 0, out)
        deep = payload_of((self.root / "site" / "deep" / "map.html").read_text(encoding="utf-8"))
        node = next(n for n in deep["nodes"] if n["id"] == "tool.statectl")
        self.assertEqual(node["url"], "../../.claude/tools/statectl.py")

    def test_every_linked_path_exists_in_the_tree(self):
        self.small_project()
        for base in (BASE, None):
            self.base = base
            code, out = self.run_cli("anatomy")
            self.assertEqual(code, 0, out)
            linked = [n for n in payload_of(self.page())["nodes"] if n["url"]]
            self.assertTrue(linked)
            for node in linked:
                with self.subTest(base=base, node=node["id"]):
                    rel = node["url"][len(BASE):] if base else node["url"][len("../"):]
                    self.assertTrue((self.root / rel).exists(), rel)
        session = next(n for n in payload_of(self.page())["nodes"] if n["id"] == "store.session")
        self.assertIsNone(session["url"], "a lazy store not written yet gets no link")
        self.assertIn("store.session has no .claude/state/session.json yet", out)

    def test_a_path_pointing_nowhere_fails_closed_and_writes_nothing(self):
        cards = self.small_project()
        cards.append(card("tool.ghost", path=".claude/tools/ghost.py"))
        self.write_cards(cards)
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 1, out)
        self.assertIn("tool.ghost: path does not exist", out)
        self.assertIn("MAP_FAIL", out)
        self.assertFalse(self.page_path().exists())

    def test_a_missing_map_or_template_fails_closed(self):
        self.small_project()
        (self.root / ".claude" / "system-map" / "map.json").unlink()
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 1, out)
        self.assertIn("run `mapctl.py compile`", out)
        self.assertFalse(self.page_path().exists())

        self.small_project()
        (self.root / ".claude" / "console" / "anatomy.template.html").write_text("<p>no token</p>",
                                                                                  encoding="utf-8")
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 1, out)
        self.assertIn(mapctl.ANATOMY_TOKEN, out)
        self.assertFalse(self.page_path().exists())

    def test_an_untracked_file_gets_no_github_link(self):
        """A gitignored store exists on the checkout but 404s on GitHub: no link, marked local."""
        self.small_project()
        with mock.patch.object(mapctl, "anatomy_tracked",
                               lambda root: {".claude/tools/statectl.py", ".claude/tools/_lib.py",
                                             ".claude/hooks/policy-gate.sh",
                                             ".claude/agents/anatomist.md"}):
            code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        by_id = {n["id"]: n for n in payload_of(self.page())["nodes"]}
        self.assertEqual(by_id["tool.statectl"]["url"], BASE + ".claude/tools/statectl.py")
        self.assertIsNone(by_id["store.journal"]["url"])
        self.assertTrue(by_id["store.journal"]["local"])
        self.assertTrue(mapctl.anatomy_linkable(".claude/tools", {".claude/tools/x.py"}),
                        "a folder holding tracked files is linkable")
        self.assertFalse(mapctl.anatomy_linkable(".claude/tool", {".claude/tools/x.py"}))

    def test_the_page_requests_nothing_but_fonts_and_github(self):
        self.small_project()
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        html = self.page()
        markup = without_payload(html)
        refs = references(markup)
        self.assertIn("tour.css", refs, "the docs brand stylesheet is linked, relatively")
        for ref in refs:
            with self.subTest(ref=ref):
                if re.match(r"^[a-z][a-z0-9+.-]*:|^//", ref, re.IGNORECASE):
                    self.assertTrue(ref.startswith(ALLOWED_HOSTS), ref)
        self.assertNotRegex(markup, r"\bfetch\s*\(", "no runtime fetch: the payload is inlined")
        self.assertNotIn("XMLHttpRequest", markup)
        self.assertNotIn("http://", markup)
        for node in payload_of(html)["nodes"]:
            if node["url"]:
                with self.subTest(url=node["url"]):
                    self.assertTrue(node["url"].startswith("https://github.com/")
                                    or not re.match(r"^[a-z]+:|^//", node["url"]))

    def test_the_reference_scan_can_fail(self):
        refs = references('<img src="https://cdn.example/x.png"><style>a{background:url(http://x/y)}'
                          '</style><script>fetch("https://api.example")</script>')
        self.assertEqual(len(refs), 3)
        self.assertFalse(any(r.startswith(ALLOWED_HOSTS) for r in refs))

    def test_the_payload_parses_and_carries_no_auto_block(self):
        self.small_project()
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        html = self.page()
        data = payload_of(html)
        self.assertEqual(set(data), {"v", "project", "compiled", "base", "arch_url", "layers",
                                     "nodes", "edges"})
        allowed = {"id", "label", "title", "kind", "layer", "path", "url", "description", "box", "local"}
        for node in data["nodes"]:
            with self.subTest(node=node["id"]):
                self.assertLessEqual(set(node), allowed)
        self.assertNotIn('"auto"', DATA_RE.search(html).group(1))
        self.assertNotIn("discovered", DATA_RE.search(html).group(1))

    def test_card_text_cannot_break_out_of_the_data_block(self):
        cards = self.small_project()
        cards[0]["description"] = "evil </script><script>alert(1)</script> <!-- x"
        self.write_cards(cards)
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        html = self.page()
        self.assertEqual(html.count("</script>"), 3, "head script, data block, page script")
        node = next(n for n in payload_of(html)["nodes"] if n["id"] == "tool.statectl")
        self.assertEqual(node["description"], cards[0]["description"])

    def test_an_unchanged_rerun_rewrites_nothing(self):
        self.small_project()
        self.assertEqual(self.run_cli("anatomy")[0], 0)
        before = self.page_path().stat().st_mtime_ns
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        self.assertIn("unchanged, not rewritten", out)
        self.assertEqual(self.page_path().stat().st_mtime_ns, before)

    def test_the_architecture_spec_names_each_folded_box(self):
        self.small_project()
        with mock.patch.dict(sys.modules, {"graphctl": None}):
            self.assertEqual(self.run_cli("architecture")[0], 0)
        code, out = self.run_cli("anatomy")
        self.assertEqual(code, 0, out)
        by_id = {n["id"]: n for n in payload_of(self.page())["nodes"]}
        self.assertEqual(by_id["store.journal"]["box"], ".claude/state/")
        self.assertNotIn("box", by_id["tool.statectl"])

    def test_labels_that_read_alike_carry_their_prefix(self):
        nodes = [{"id": i, "label": mapctl.arch_label(i)} for i in
                 ("protocol.orchestration", "store.orchestration", "hook.ritual-ticket",
                  "store.ritual_ticket", "tool.statectl", "skill.adopt")]
        mapctl.anatomy_disambiguate(nodes)
        self.assertEqual([n["label"] for n in nodes],
                         ["orchestration (protocol)", "orchestration (store)", "ritual-ticket (hook)",
                          "ritual_ticket (store)", "statectl", "/adopt"])

    def test_exactly_one_verdict_token(self):
        self.small_project()
        code, out = self.run_cli("anatomy")
        self.assertEqual(len(re.findall(r"\bMAP_(?:OK|WARN|FAIL)\b", out)), 1, out)


# --------------------------------------------------------------------------- the template

class TestAnatomyTemplate(unittest.TestCase):
    def setUp(self) -> None:
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_one_token_and_the_cascade_hooks_are_present(self):
        self.assertEqual(self.text.count(mapctl.ANATOMY_TOKEN), 1)
        for needle in ("embed=1", "am-embed", '"#node="', "hashchange", '"Escape"', "aria-current",
                       "Open on GitHub", 'target: "_blank"', 'rel: "noopener"', "Read by",
                       "Written by", "Invoked by", "data-layer"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)
        self.assertIn(".am-embed .am-top", self.text, "the embed flag hides the page's own topbar")

    def test_both_themes_and_reduced_motion_without_tour_css(self):
        for needle in ('href="tour.css"', "prefers-color-scheme: dark", ':root[data-theme="dark"]',
                       "prefers-reduced-motion", "var(--agent, #d97a2b)", "var(--bg, #FAF9F6)",
                       "var(--bg, #15171b)", "max-width: 720px"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)
        self.assertRegex(self.text, r"body \{[^}]*background: var\(--am-bg\)[^}]*color: var\(--am-ink\)")

    def test_the_two_dark_blocks_define_the_same_tokens(self):
        def block(marker):
            start = self.text.index(marker)
            i = begin = self.text.index("{", start)
            depth = 0
            while True:
                depth += {"{": 1, "}": -1}.get(self.text[i], 0)
                if depth == 0:
                    return set(re.findall(r"(--am-[\w-]+)\s*:", self.text[begin:i]))
                i += 1
        light = block(":root {")
        media = block("@media (prefers-color-scheme: dark)")
        explicit = block(':root[data-theme="dark"]')
        self.assertTrue(media)
        self.assertEqual(media, explicit)
        self.assertLessEqual(media, light)
        colour = {t for t in light if t not in ("--am-mono", "--am-sans")}
        self.assertEqual(colour, media, "every colour token has a dark value in both blocks")


# --------------------------------------------------------------------------- this repo

class TestThisRepo(FixtureCase):
    """The real map, built in memory against the real tree (nothing is written)."""

    def test_the_real_map_builds_and_every_link_points_at_a_real_file(self):
        import os
        os.environ["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)  # FixtureCase.tearDown restores it
        _lib.clear_config_cache()
        if not (REPO_ROOT / ".claude" / "system-map" / "map.json").exists():
            self.skipTest("map.json is not compiled here")
        warnings, errors = [], []
        with mock.patch.object(mapctl, "anatomy_tracked", lambda root: None):
            data = mapctl.build_anatomy(None, "../", warnings, errors)
        self.assertEqual(errors, [])
        map_ids = {n["id"] for n in _lib.read_json(REPO_ROOT / ".claude" / "system-map" / "map.json")["nodes"]}
        self.assertLessEqual(map_ids, {n["id"] for n in data["nodes"]})
        for node in data["nodes"]:
            if node["url"]:
                with self.subTest(node=node["id"]):
                    self.assertTrue((REPO_ROOT / node["url"][len("../"):]).exists(), node["url"])
        html = mapctl.render_anatomy(data, TEMPLATE)
        self.assertEqual(payload_of(html), json.loads(json.dumps(data)))


# --------------------------------------------------------------------------- registration

class TestRegistration(unittest.TestCase):
    def test_the_generator_is_registered_home_only_and_in_polish(self):
        spec = checkctl.GENERATORS["anatomy_build"]
        self.assertEqual((spec["tool"], spec["args"]), ("mapctl.py", ["anatomy", "--out", "docs/anatomy.html"]))
        self.assertEqual(checkctl.GENERATOR_TAGS[spec["tool"]], "MAP")
        self.assertEqual(spec["output"], "docs/anatomy.html")
        self.assertEqual(set(spec["inputs"]), {
            ".claude/system-map/architecture.json", ".claude/system-map/map.json",
            ".claude/system-map/layers.json", ".claude/system-map/cards",
            ".claude/console/anatomy.template.html"})
        for inp in spec["inputs"]:
            self.assertFalse(spec["output"] == inp or spec["output"].startswith(inp.rstrip("/") + "/"))
        self.assertIn("anatomy_build", checkctl.HOME_ONLY_GENERATORS)
        polish = _lib.read_json(CLAUDE_DIR / "config" / "memory.json")["phases"]["polish"]
        self.assertEqual(polish.index("anatomy_build"), polish.index("demo_build") + 1)
        self.assertLess(polish.index("map_compile"), polish.index("anatomy_build"))
        self.assertLess(polish.index("architecture_build"), polish.index("anatomy_build"))

    def test_the_output_is_derived_and_the_template_is_probed_and_carded(self):
        derived = _lib.read_json(CLAUDE_DIR / "config" / "policy.json")["derived_files"]["paths"]
        self.assertIn("docs/anatomy.html", derived)
        self.assertIn(("console.anatomy-template", ".claude/console/anatomy.template.html"),
                      [(r.name, r.message) for r in checkctl.probe()])
        self.assertIn("console.anatomy-template", mapctl.SINGLETON_IDS)
        card_obj = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "console.anatomy-template.json")
        self.assertEqual(card_obj["path"], ".claude/console/anatomy.template.html")
        tool = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "tool.mapctl.json")
        self.assertIn("anatomy", tool["description"])
        self.assertIn("console.anatomy-template", tool["reads"])

    def test_the_landing_page_embeds_the_map(self):
        index = REPO_ROOT / "docs" / "index.html"
        if not index.exists():
            self.skipTest("home-repo-only: docs/ is the source repo's site")
        text = index.read_text(encoding="utf-8")
        frame = re.search(r"<iframe[^>]*>", text)
        self.assertIsNotNone(frame)
        for needle in ('src="anatomy.html?embed=1"', 'loading="lazy"', "title="):
            self.assertIn(needle, frame.group(0))
        self.assertLess(text.index('id="anatomy"'), text.index('id="overview"'))
        self.assertIn('href="anatomy.html"', text)
        self.assertIn('href="anatomy.html"', (REPO_ROOT / "docs" / "understand.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
