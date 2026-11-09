#!/usr/bin/env python3
"""test_architecture.py - `mapctl architecture`: the spec built from the cards, and the Mermaid
renderer.

The diagram is only worth committing if it stays TRUE and stays READABLE: every box points at a
real file (a box pointing nowhere fails the build, gates fail closed), the folding rules keep the
`.claude` scope near the size a person can read (stores fold into their folder, test files never
become boxes, the least-connected boxes of a group fold into one "other" box past the cap), and
the rendered Mermaid names only declared boxes and links only into the repo. The code scopes
come from graphctl (built separately); a fake module proves the wiring without it.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import types
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
NODE_RE = re.compile(r'^ {4}([A-Za-z0-9_]+)(?:\["|\(\["|\[\("|\(\("|\[\[")(.*)(?:"\]|"\]\)|"\)\]|"\)\)|"\]\])$')
EDGE_RE = re.compile(r"^ {2}([A-Za-z0-9_]+) (?:-->|-\.->)\|[^|\"]*\| ([A-Za-z0-9_]+)$")
CLICK_RE = re.compile(r'^ {2}click ([A-Za-z0-9_]+) "([^"]+)"$')
SUBGRAPH_RE = re.compile(r'^ {2}subgraph g_[A-Za-z0-9_]+\["[^"]*"\]$')


def mermaid_blocks(md: str) -> list:
    return re.findall(r"```mermaid\n(.*?)\n```", md.replace("\r\n", "\n"), re.DOTALL)


def parse_mermaid(block: str) -> dict:
    """A strict reader of exactly what the renderer may emit: any other line is a failure."""
    lines = block.split("\n")
    assert lines[0] == "flowchart LR", lines[0]
    nodes, edges, clicks, depth = [], [], [], 0
    for line in lines[1:]:
        if SUBGRAPH_RE.match(line):
            depth += 1
        elif line == "  end":
            depth -= 1
        elif NODE_RE.match(line):
            assert depth == 1, f"box outside a subgraph: {line}"
            nodes.append(NODE_RE.match(line).group(1))
        elif EDGE_RE.match(line):
            edges.append(EDGE_RE.match(line).groups())
        elif CLICK_RE.match(line):
            clicks.append(CLICK_RE.match(line).groups())
        else:
            raise AssertionError(f"unexpected Mermaid line: {line!r}")
    assert depth == 0, "unbalanced subgraph/end"
    return {"nodes": nodes, "edges": edges, "clicks": clicks}


def card(id_, kind="code", layer="GOVERN", path=None, reads=(), writes=(), invokes=(), lazy=False,
         external=False):
    auto = {"discovered": "2026-11-04", "hash": "", "suggested_reads": [], "suggested_writes": []}
    if lazy:
        auto["lazy"] = True
    if external:
        auto["external_to_repo"] = True
    return {"id": id_, "kind": kind, "layer": layer, "package": None, "title": id_, "path": path,
            "description": "", "reads": list(reads), "writes": list(writes),
            "invokes": list(invokes), "flows": [], "glyphs": [], "auto": auto}


class ArchCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        # Never the real graphctl: these tests prove the wiring, not T19's extraction.
        patcher = mock.patch.dict(sys.modules, {"graphctl": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(mapctl, "link_base", lambda root=None: BASE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def touch(self, rel: str) -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
        return rel

    def write_card(self, c: dict) -> None:
        _lib.atomic_write_json(self.root / ".claude" / "system-map" / "cards" / f"{c['id']}.json", c)

    def small_project(self) -> None:
        self.write_card(card("tool.statectl", path=self.touch(".claude/tools/statectl.py"),
                             layer="MEMORY", reads=["store.journal"],
                             writes=["store.journal", "store.session"], invokes=["tool._lib"]))
        self.write_card(card("tool._lib", path=self.touch(".claude/tools/_lib.py"), layer=None,
                             reads=["config.policy"]))
        self.write_card(card("hook.policy-gate", path=self.touch(".claude/hooks/policy-gate.sh"),
                             reads=["config.policy"], invokes=["tool._lib"]))
        self.write_card(card("store.journal", kind="data", layer="MEMORY", lazy=True,
                             path=self.touch(".claude/state/journal.jsonl")))
        self.write_card(card("store.session", kind="data", layer="MEMORY", lazy=True,
                             path=".claude/state/session.json"))
        self.write_card(card("config.policy", kind="data", path=self.touch(".claude/config/policy.json")))
        self.write_card(card("human.maintainer", kind="human", layer=None, lazy=True))

    def run_cli(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mapctl.main(list(argv))
        return code, out.getvalue()

    def spec(self) -> dict:
        return _lib.read_json(self.root / ".claude" / "system-map" / "architecture.json")


# --------------------------------------------------------------------------- folding rules

class TestFoldingRules(unittest.TestCase):
    def test_test_files_are_recognised(self):
        for path in (".claude/tools/tests/test_mapctl.py", "src/tests/helpers.py", "test/x.sh",
                     "pkg/test_core.py", "pkg/core_test.py"):
            with self.subTest(path=path):
                self.assertTrue(mapctl.is_test_path(path))
        for path in (".claude/tools/mapctl.py", "docs/testing.md", None, "", "src/contest.py"):
            with self.subTest(path=path):
                self.assertFalse(mapctl.is_test_path(path))

    def test_data_cards_fold_into_their_folder_and_code_never_does(self):
        self.assertEqual(mapctl.fold_folder_of(card("s", kind="data", path=".claude/state/a.json")),
                         "folder.state")
        self.assertEqual(mapctl.fold_folder_of(card("s", kind="data", path=".claude-iff/obs/anchor.json")),
                         "folder.claude-iff")
        self.assertEqual(mapctl.fold_folder_of(card("s", kind="data", path=".claude/system-map/cards")),
                         "folder.system-map")
        self.assertEqual(mapctl.fold_folder_of(card("r", kind="data", external=True)), "folder.record")
        self.assertIsNone(mapctl.fold_folder_of(card("t", kind="code", path=".claude/state/x.py")))
        self.assertIsNone(mapctl.fold_folder_of(card("l", kind="data", path=".claude/LESSONS.jsonl")))
        self.assertIsNone(mapctl.fold_folder_of(card("p", kind="data", path=".claude/statefoo/x")))

    def test_fold_cards_takes_the_majority_layer_and_drops_tests(self):
        cards = {c["id"]: c for c in (
            card("store.a", kind="data", layer="GOVERN", path=".claude/state/a"),
            card("store.b", kind="data", layer="MEMORY", path=".claude/state/b"),
            card("store.c", kind="data", layer="MEMORY", path=".claude/state/c"),
            card("tool.x", path=".claude/tools/x.py"),
            card("tool.test_x", path=".claude/tools/tests/test_x.py"),
        )}
        boxes, member_of = mapctl.fold_cards(cards, {"MEMORY": 1, "GOVERN": 2})
        self.assertEqual(sorted(boxes), ["folder.state", "tool.x"])
        self.assertEqual(boxes["folder.state"]["group"], "memory")
        self.assertEqual(boxes["folder.state"]["members"], ["store.a", "store.b", "store.c"])
        self.assertEqual(boxes["folder.state"]["path"], ".claude/state")
        self.assertNotIn("tool.test_x", member_of)

    def test_a_tie_goes_to_the_earlier_layer(self):
        cards = {c["id"]: c for c in (
            card("store.a", kind="data", layer="EVOLVE", path=".claude/state/a"),
            card("store.b", kind="data", layer="GOVERN", path=".claude/state/b"),
        )}
        boxes, _ = mapctl.fold_cards(cards, {"GOVERN": 2, "EVOLVE": 5})
        self.assertEqual(boxes["folder.state"]["group"], "govern")

    def test_edges_aggregate_with_weights_and_self_edges_drop(self):
        relations = [("a1", "s1", "writes"), ("a1", "s2", "writes"), ("a1", "s1", "reads"),
                     ("s1", "s2", "reads"), ("a1", "ghost", "invokes")]
        box_of = {"a1": "a", "s1": "folder", "s2": "folder"}
        edges = mapctl.aggregate_edges(relations, box_of)
        self.assertEqual(edges, [
            {"from": "a", "to": "folder", "label": "reads", "style": "dashed", "weight": 1},
            {"from": "a", "to": "folder", "label": "writes", "style": "solid", "weight": 2},
        ])

    def boxes(self, spec: dict) -> dict:
        return {bid: {"id": bid, "group": g, "label": bid, "path": f".claude/{g}/{bid}",
                      "kind": kind, "_lazy": False} for bid, (g, kind) in spec.items()}

    def test_the_cap_folds_the_lowest_degree_boxes_into_one_other_per_group(self):
        boxes = self.boxes({"hub": ("govern", "code"), "a": ("govern", "code"),
                            "b": ("govern", "code"), "c": ("govern", "code"),
                            "d": ("memory", "code"), "f": ("memory", "folder")})
        edges = [{"from": "hub", "to": x} for x in ("a", "d", "f")] + [{"from": "d", "to": "f"}]
        out, remap = mapctl.cap_boxes(boxes, edges, 4)
        self.assertLessEqual(len(out), 4)
        self.assertIn("other.govern", out)
        self.assertEqual(out["other.govern"]["members"], ["a", "b", "c"])
        self.assertEqual(out["other.govern"]["path"], ".claude/govern")
        self.assertEqual(remap["b"], "other.govern")
        self.assertEqual(remap["hub"], "hub")
        self.assertIn("f", out, "a folder box is never folded again")

    def test_a_fold_of_planned_cards_is_itself_planned(self):
        """PR-22: an adopter with many planned (lazy) cards folded them into an "other" box
        marked not lazy, so its missing path blocked the whole spec instead of a WARN."""
        boxes = self.boxes({"a": ("g", "data"), "b": ("g", "data"), "c": ("g", "data"),
                            "d": ("h", "code")})
        for bid in ("a", "b", "c"):
            boxes[bid]["_lazy"] = True
            boxes[bid]["path"] = None
        out, _ = mapctl.cap_boxes(boxes, [], 2)
        self.assertIn("other.g", out)
        self.assertTrue(out["other.g"]["_lazy"])
        boxes["b"]["_lazy"] = False
        out, _ = mapctl.cap_boxes(boxes, [], 2)
        self.assertFalse(out["other.g"]["_lazy"], "one real member keeps the fold real")

    def test_under_the_cap_nothing_folds_and_a_lone_other_is_undone(self):
        boxes = self.boxes({"a": ("govern", "code"), "b": ("memory", "code"), "c": ("memory", "code")})
        out, remap = mapctl.cap_boxes(boxes, [], 3)
        self.assertEqual(sorted(out), ["a", "b", "c"])
        # cap 2: folding a alone (other.govern) saves nothing, b+c into other.memory does.
        out, remap = mapctl.cap_boxes(boxes, [], 2)
        self.assertEqual(sorted(out), ["a", "other.memory"])
        self.assertEqual(remap["a"], "a")

    def test_max_nodes_knob_reads_bad_values_as_the_default(self):
        self.assertEqual(mapctl.arch_max_nodes({"architecture": {"max_nodes": 12}}), 12)
        for bad in ("abc", 0, -1, True, None, [3]):
            with self.subTest(value=bad):
                self.assertEqual(mapctl.arch_max_nodes({"architecture": {"max_nodes": bad}}),
                                 mapctl.ARCH_DEFAULT_MAX_NODES)
        self.assertEqual(mapctl.arch_scopes({}), mapctl.ARCH_DEFAULT_SCOPES)
        with self.assertRaises(ValueError):
            mapctl.arch_scopes({"architecture": {"scopes": "claude"}})


# --------------------------------------------------------------------------- ids and links

class TestIdsAndLinks(unittest.TestCase):
    def test_mermaid_ids_are_safe_unique_and_documented(self):
        ids = mapctl.mermaid_ids(["tool.statectl", "agent.retro-analyst", "tool._lib", "a.b", "a_b",
                                  "9lives"])
        self.assertEqual(ids["tool.statectl"], "tool_statectl")
        self.assertEqual(ids["agent.retro-analyst"], "agent_retro_analyst")
        self.assertEqual(ids["tool._lib"], "tool__lib")
        self.assertEqual(ids["9lives"], "n_9lives")
        self.assertEqual(len(set(ids.values())), len(ids), ids)
        for safe in ids.values():
            self.assertRegex(safe, r"^[A-Za-z][A-Za-z0-9_]*$")

    def test_github_remotes_parse_and_others_do_not(self):
        for url in ("https://github.com/owner/repo.git", "https://github.com/owner/repo",
                    "git@github.com:owner/repo.git", "ssh://git@github.com/owner/repo.git",
                    "https://user@github.com/owner/repo.git/"):
            with self.subTest(url=url):
                self.assertEqual(mapctl.parse_github_remote(url), "owner/repo")
        for url in ("", None, "https://gitlab.com/owner/repo.git", "/srv/git/repo.git",
                    "https://github.com.evil.example/owner/repo"):
            with self.subTest(url=url):
                self.assertIsNone(mapctl.parse_github_remote(url))

    def test_link_base_uses_origin_and_branch_and_never_raises(self):
        answers = {("remote", "get-url", "origin"): "git@github.com:o/r.git",
                   ("rev-parse", "--abbrev-ref", "HEAD"): "dev"}
        with mock.patch.object(_lib, "git_output", lambda args, root=None: answers[tuple(args)]):
            self.assertEqual(mapctl.link_base(), "https://github.com/o/r/blob/dev/")
        with mock.patch.object(_lib, "git_output", lambda args, root=None: ""):
            self.assertIsNone(mapctl.link_base())
        self.assertEqual(mapctl.node_url(".claude/tools/x.py", None), "../.claude/tools/x.py")
        self.assertEqual(mapctl.node_url("a b.md", BASE), BASE + "a%20b.md")


# --------------------------------------------------------------------------- the command

class TestArchitectureCommand(ArchCase):
    def test_writes_both_files_with_the_contract_shape(self):
        self.small_project()
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        spec = self.spec()
        self.assertEqual(spec["v"], 1)
        self.assertEqual(set(spec), {"v", "generated_at", "project", "scopes"})
        scope = spec["scopes"][0]
        self.assertEqual((scope["id"], scope["root"], scope["source"]), ("claude", ".claude", "cards"))
        for node in scope["nodes"]:
            self.assertLessEqual({"id", "group", "label", "path", "kind"}, set(node))
            self.assertNotIn("_lazy", node)
        for edge in scope["edges"]:
            self.assertEqual(set(edge), {"from", "to", "label", "style", "weight"})
        self.assertEqual([g["id"] for g in scope["groups"]], ["memory", "govern", "unplaced"],
                         "groups follow the layer order, unplaced last")
        ids = {n["id"] for n in scope["nodes"]}
        self.assertEqual(ids, {"tool.statectl", "tool._lib", "hook.policy-gate", "folder.state",
                               "folder.config", "human.maintainer"})
        statectl_state = [e for e in scope["edges"] if (e["from"], e["to"]) == ("tool.statectl", "folder.state")]
        self.assertEqual({(e["label"], e["weight"]) for e in statectl_state}, {("reads", 1), ("writes", 2)})
        self.assertEqual(scope["stats"], {"nodes": 6, "edges": len(scope["edges"]), "folded": 3})
        md = (self.root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertEqual(len(mermaid_blocks(md)), 1)

    def test_a_missing_path_fails_the_build_and_names_it(self):
        self.small_project()
        self.write_card(card("tool.ghost", path=".claude/tools/ghost.py"))
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 1, out)
        self.assertIn(".claude/tools/ghost.py", out)
        self.assertIn("MAP_FAIL", out)
        self.assertFalse((self.root / ".claude" / "system-map" / "architecture.json").exists())
        self.assertFalse((self.root / "docs" / "ARCHITECTURE.md").exists())

    def test_a_lazy_store_not_written_yet_warns_and_loses_its_link(self):
        self.small_project()
        self.write_card(card("store.dist", kind="data", layer="EVOLVE", lazy=True, path=".claude/dist"))
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        self.assertIn("store.dist has no .claude/dist yet", out)
        node = next(n for n in self.spec()["scopes"][0]["nodes"] if n["id"] == "store.dist")
        self.assertIsNone(node["path"])

    def test_without_graphctl_the_code_scope_is_skipped_with_a_warning(self):
        self.small_project()
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        self.assertIn("WARN   scope 'tools' skipped: graphctl.py is not installed", out)
        self.assertIn("MAP_WARN", out)
        self.assertEqual([s["id"] for s in self.spec()["scopes"]], ["claude"])

    def test_an_unchanged_rerun_rewrites_nothing(self):
        self.small_project()
        self.assertEqual(self.run_cli("architecture")[0], 0)
        path = self.root / ".claude" / "system-map" / "architecture.json"
        before = path.read_text(encoding="utf-8")
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("(unchanged, not rewritten)"), 2, out)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_max_nodes_caps_the_scope(self):
        self.small_project()
        for i in range(6):
            self.write_card(card(f"tool.t{i}", path=self.touch(f".claude/tools/t{i}.py")))
        cfg = _lib.load_config("console")
        cfg.setdefault("architecture", {})["max_nodes"] = 8
        self.write_config("console", cfg)
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        scope = self.spec()["scopes"][0]
        self.assertLessEqual(scope["stats"]["nodes"], 8)
        other = next(n for n in scope["nodes"] if n["id"] == "other.govern")
        self.assertTrue(set(other["members"]) <= {f"tool.t{i}" for i in range(6)} | {"hook.policy-gate"})
        self.assertTrue((self.root / other["path"]).exists())

    def test_an_unknown_scope_source_fails_closed(self):
        self.small_project()
        cfg = _lib.load_config("console")
        cfg["architecture"] = {"scopes": [{"id": "x", "root": ".", "source": "guess"}]}
        self.write_config("console", cfg)
        code, out = self.run_cli("architecture")
        self.assertEqual(code, 1, out)
        self.assertIn("unknown source 'guess'", out)


class TestFakeGraphctl(ArchCase):
    """The tier-2 wiring, proven before graphctl.py exists: a module in sys.modules that
    returns a small scope in the architecture shape."""

    def fake(self, nodes, edges=(), calls=None):
        module = types.ModuleType("graphctl")

        def architecture_scope(root):
            if calls is not None:
                calls.append(root)
            return {"groups": [{"id": "tools", "title": "TOOLS", "blurb": "the CLIs"}],
                    "nodes": nodes, "edges": list(edges), "stats": {"folded": 1}}

        module.architecture_scope = architecture_scope
        return mock.patch.dict(sys.modules, {"graphctl": module})

    def test_a_code_scope_lands_in_the_spec_and_the_markdown(self):
        self.small_project()
        nodes = [{"id": "f.statectl", "group": "tools", "label": "statectl.py",
                  "path": ".claude/tools/statectl.py", "kind": "file"},
                 {"id": "f.lib", "group": "tools", "label": "_lib.py",
                  "path": ".claude/tools/_lib.py", "kind": "file"}]
        edges = [{"from": "f.statectl", "to": "f.lib", "label": "imports", "style": "solid", "weight": 3},
                 {"from": "f.lib", "to": "f.lib", "label": "calls", "style": "solid", "weight": 1},
                 {"from": "f.lib", "to": "f.nowhere", "label": "calls", "style": "solid", "weight": 1}]
        calls = []
        with self.fake(nodes, edges, calls):
            code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, [".claude/tools"])
        self.assertNotIn("skipped", out)
        scope = next(s for s in self.spec()["scopes"] if s["id"] == "tools")
        self.assertEqual(scope["source"], "code")
        self.assertEqual(scope["stats"], {"folded": 1, "nodes": 2, "edges": 1})
        self.assertEqual(len(scope["edges"]), 1, "self-edges and dangling edges are dropped")
        md = (self.root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        blocks = mermaid_blocks(md)
        self.assertEqual(len(blocks), 2)
        parsed = parse_mermaid(blocks[1])
        self.assertEqual(sorted(parsed["nodes"]), ["f_lib", "f_statectl"])

    def test_a_code_scope_with_a_bogus_path_fails_closed(self):
        self.small_project()
        nodes = [{"id": "f.ghost", "group": "tools", "label": "ghost.py",
                  "path": ".claude/tools/ghost.py", "kind": "file"}]
        with self.fake(nodes):
            code, out = self.run_cli("architecture")
        self.assertEqual(code, 1, out)
        self.assertIn(".claude/tools/ghost.py", out)

    def test_a_graphctl_that_raises_is_skipped_with_a_warning(self):
        self.small_project()
        module = types.ModuleType("graphctl")

        def architecture_scope(root):
            raise RuntimeError("no graph yet")

        module.architecture_scope = architecture_scope
        with mock.patch.dict(sys.modules, {"graphctl": module}):
            code, out = self.run_cli("architecture")
        self.assertEqual(code, 0, out)
        self.assertIn("RuntimeError: no graph yet", out)


# --------------------------------------------------------------------------- this repo

class TestThisRepo(FixtureCase):
    """The real cards, built in memory against the real tree (nothing is written)."""

    def setUp(self) -> None:
        super().setUp()
        import os
        os.environ["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)  # FixtureCase.tearDown restores it
        _lib.clear_config_cache()
        patcher = mock.patch.dict(sys.modules, {"graphctl": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        warnings, errors = [], []
        self.spec_obj = mapctl.build_architecture(warnings, errors)
        self.assertEqual(errors, [])
        self.scope = next(s for s in self.spec_obj["scopes"] if s["id"] == "claude")

    def test_the_claude_scope_is_readable_and_every_path_exists(self):
        self.assertGreaterEqual(len(self.scope["nodes"]), 15)
        self.assertLessEqual(len(self.scope["nodes"]), 40)
        for node in self.scope["nodes"]:
            if node["path"] is not None:
                with self.subTest(node=node["id"]):
                    self.assertTrue((REPO_ROOT / node["path"]).exists(), node["path"])
        tests = [n for n in self.scope["nodes"] if mapctl.is_test_path(n.get("path"))]
        self.assertEqual(tests, [])

    def test_the_rendered_mermaid_names_only_declared_boxes_and_links_into_the_repo(self):
        md = mapctl.render_architecture_md(self.spec_obj, BASE)
        blocks = mermaid_blocks(md)
        self.assertEqual(len(blocks), len(self.spec_obj["scopes"]))
        parsed = parse_mermaid(blocks[0])
        declared = set(parsed["nodes"])
        self.assertEqual(len(declared), len(self.scope["nodes"]))
        self.assertTrue(parsed["edges"])
        for src, dst in parsed["edges"]:
            self.assertIn(src, declared)
            self.assertIn(dst, declared)
        self.assertTrue(parsed["clicks"])
        for nid, url in parsed["clicks"]:
            self.assertIn(nid, declared)
            self.assertTrue(url.startswith(BASE), url)
        relative = parse_mermaid(mermaid_blocks(mapctl.render_architecture_md(self.spec_obj, None))[0])
        for _nid, url in relative["clicks"]:
            self.assertTrue(url.startswith("../"), url)

    def test_the_committed_markdown_parses(self):
        path = REPO_ROOT / "docs" / "ARCHITECTURE.md"
        if not path.exists():
            self.skipTest("docs/ARCHITECTURE.md is not built here")
        for block in mermaid_blocks(path.read_text(encoding="utf-8")):
            parsed = parse_mermaid(block)
            declared = set(parsed["nodes"])
            for src, dst in parsed["edges"]:
                self.assertIn(src, declared)
                self.assertIn(dst, declared)
            for nid, url in parsed["clicks"]:
                self.assertIn(nid, declared)
                self.assertRegex(url, r"^(https://github\.com/[^/]+/[^/]+/blob/|\.\./)")


# --------------------------------------------------------------------------- registration

class TestRegistration(unittest.TestCase):
    def test_the_generator_is_registered_before_the_console(self):
        spec = checkctl.GENERATORS["architecture_build"]
        self.assertEqual((spec["tool"], spec["args"]), ("mapctl.py", ["architecture"]))
        self.assertEqual(spec["output"], ".claude/system-map/architecture.json")
        self.assertIn(".claude/system-map/cards", spec["inputs"])
        self.assertIn(".claude/config/console.json", spec["inputs"])
        for inp in spec["inputs"]:
            self.assertFalse(spec["output"].startswith(inp.rstrip("/") + "/") or spec["output"] == inp,
                             "the output must not sit inside its own input set")
        polish = _lib.read_json(CLAUDE_DIR / "config" / "memory.json")["phases"]["polish"]
        self.assertLess(polish.index("architecture_build"), polish.index("console_build"))

    def test_outputs_are_derived_knobs_are_carded_and_the_console_may_read_the_spec(self):
        derived = _lib.read_json(CLAUDE_DIR / "config" / "policy.json")["derived_files"]["paths"]
        self.assertIn(".claude/system-map/architecture.json", derived)
        self.assertIn("docs/ARCHITECTURE.md", derived)
        console = _lib.read_json(CLAUDE_DIR / "config" / "console.json")
        self.assertEqual(console["architecture"]["scopes"], mapctl.ARCH_DEFAULT_SCOPES)
        self.assertEqual(console["architecture"]["max_nodes"], mapctl.ARCH_DEFAULT_MAX_NODES)
        self.assertTrue(any(p.endswith("system-map/architecture.json") for p in console["read_allowlist"]))
        keys = {e["key"]: e for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]}
        self.assertEqual(keys["console.architecture.max_nodes"]["default"], mapctl.ARCH_DEFAULT_MAX_NODES)
        self.assertEqual(keys["console.architecture.scopes"]["default"], mapctl.ARCH_DEFAULT_SCOPES)
        self.assertEqual(keys["console.architecture.scopes"]["target"]["path"], "architecture.scopes")

    def test_the_spec_is_a_declared_store_with_a_card(self):
        store = next(s for s in mapctl.KNOWN_STORES if s["id"] == "store.architecture")
        self.assertEqual(store["path"], ".claude/system-map/architecture.json")
        card_obj = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "store.architecture.json")
        self.assertEqual(card_obj["path"], store["path"])
        writer = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "tool.mapctl.json")
        self.assertIn("store.architecture", writer["writes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
