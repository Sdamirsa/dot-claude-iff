#!/usr/bin/env python3
"""test_graph.py - graphctl: the code graph and its architecture collapse (T19, PR-15 tier 2).

The walker on a fixture tree (a class, a method, a function, an import, a call, an inherits,
a `source` line), the graphify normaliser on a fake graph.json under the fixture record root
(same schema out), the CLI (the pinned extraction command as a WARN, counts, exit codes, and
no uvx/graphify process ever started), `architecture_scope` (folders, files, weighted edges,
tests excluded, the max_nodes fold, every path on disk), and the registrations (probe, card,
knob, gitignore line).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase

import _lib
import checkctl
import graphctl

NODE_KEYS = {"id", "kind", "label", "path", "line", "end_line"}
EDGE_KEYS = {"from", "to", "type", "confidence"}

A_PY = '''\
import os
from pkg import b
from pkg.b import helper
import pkg.c as c


class Base:
    def run(self):
        return self.step()

    def step(self):
        return helper()


class Child(Base):
    pass


def top():
    b.util()
    c.thing()
    os.getcwd()
    Base()
    unknown()
'''

B_PY = "def helper():\n    return 1\n\n\ndef util():\n    return helper()\n"
C_PY = "def thing():\n    pass\n"
RUN_SH = '#!/usr/bin/env bash\nsource "$(dirname "$0")/lib.sh"\n. ./missing.sh\necho done\n'
LIB_SH = "#!/usr/bin/env bash\necho lib\n"


class GraphCase(FixtureCase):
    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_tree(self) -> None:
        self.write("pkg/__init__.py", "")
        self.write("pkg/a.py", A_PY)
        self.write("pkg/b.py", B_PY)
        self.write("pkg/c.py", C_PY)
        self.write("scripts/run.sh", RUN_SH)
        self.write("scripts/lib.sh", LIB_SH)
        self.write("pkg/tests/test_a.py", "from pkg import a\n\n\ndef test_top():\n    a.top()\n")

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = graphctl.main(list(argv))
        return code, out.getvalue()

    def graphify_file(self) -> Path:
        return self.record / "graph" / "graphify-out" / "graph.json"

    def write_graphify(self, obj) -> Path:
        path = self.graphify_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    @staticmethod
    def edge_set(graph: dict, etype: str) -> set:
        return {(e["from"], e["to"]) for e in graph["edges"] if e["type"] == etype}

    def assertSchema(self, graph: dict) -> None:
        self.assertEqual(graph["v"], graphctl.SCHEMA_VERSION)
        self.assertTrue(graph["nodes"] and graph["edges"])
        for node in graph["nodes"]:
            self.assertEqual(set(node), NODE_KEYS, node)
            self.assertIn(node["kind"], graphctl.NODE_KINDS, node)
            self.assertNotIn("\\", node["path"])
        ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            self.assertEqual(set(edge), EDGE_KEYS, edge)
            self.assertIn(edge["type"], graphctl.EDGE_TYPES, edge)
            self.assertIn(edge["from"], ids)
            self.assertIn(edge["to"], ids)
            self.assertTrue(0.0 <= edge["confidence"] <= 1.0)
        self.assertEqual(graph["stats"]["nodes"], len(graph["nodes"]))
        self.assertEqual(graph["stats"]["edges"], len(graph["edges"]))


# --------------------------------------------------------------------------- the walker

class TestWalker(GraphCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_tree()
        self.graph = graphctl.build_graph()
        self.nodes = {n["id"]: n for n in self.graph["nodes"]}

    def test_schema_and_source(self):
        self.assertSchema(self.graph)
        self.assertEqual(self.graph["source"], "walker")
        self.assertFalse(self.graph["graphify"]["found"])

    def test_class_function_method_nodes_with_line_spans(self):
        base = self.nodes["pkg/a.py::Base"]
        self.assertEqual((base["kind"], base["label"], base["path"]), ("class", "Base", "pkg/a.py"))
        self.assertEqual((base["line"], base["end_line"]), (7, 12))
        run = self.nodes["pkg/a.py::Base.run"]
        self.assertEqual((run["kind"], run["label"], run["line"]), ("method", ".run()", 8))
        top = self.nodes["pkg/a.py::top"]
        self.assertEqual((top["kind"], top["label"]), ("function", "top()"))
        self.assertEqual(self.nodes["pkg/a.py"]["kind"], "file")
        self.assertEqual(self.nodes["pkg/a.py"]["label"], "a.py")
        self.assertIn(("pkg/a.py", "pkg/a.py::Base"), self.edge_set(self.graph, "contains"))
        self.assertIn(("pkg/a.py::Base", "pkg/a.py::Base.run"), self.edge_set(self.graph, "contains"))

    def test_imports_resolve_to_in_repo_files_only(self):
        imports = self.edge_set(self.graph, "imports")
        self.assertIn(("pkg/a.py", "pkg/b.py"), imports)
        self.assertIn(("pkg/a.py", "pkg/c.py"), imports)
        self.assertFalse([e for e in imports if e[0] == "pkg/a.py" and "os" in e[1]])

    def test_calls_resolve_in_file_or_imported_module_else_dropped(self):
        calls = {(e["from"], e["to"]): e["confidence"] for e in self.graph["edges"]
                 if e["type"] == "calls"}
        for pair in (("pkg/a.py::Base.run", "pkg/a.py::Base.step"),      # self.method()
                     ("pkg/a.py::Base.step", "pkg/b.py::helper"),         # from-import
                     ("pkg/a.py::top", "pkg/b.py::util"),                 # module attribute
                     ("pkg/a.py::top", "pkg/c.py::thing"),                # import ... as
                     ("pkg/a.py::top", "pkg/a.py::Base"),                 # same file
                     ("pkg/b.py::util", "pkg/b.py::helper")):
            with self.subTest(pair=pair):
                self.assertIn(pair, calls)
                self.assertEqual(calls[pair], graphctl.CALL_CONFIDENCE)
        # os.getcwd() and unknown() resolve to nothing in the repo: no edge at all
        from_top = {dst for src, dst in calls if src == "pkg/a.py::top"}
        self.assertEqual(from_top, {"pkg/b.py::util", "pkg/c.py::thing", "pkg/a.py::Base"})

    def test_inherits(self):
        self.assertIn(("pkg/a.py::Child", "pkg/a.py::Base"), self.edge_set(self.graph, "inherits"))

    def test_shell_source_lines_become_imports(self):
        self.assertEqual(self.nodes["scripts/run.sh"]["kind"], "file")
        imports = self.edge_set(self.graph, "imports")
        self.assertIn(("scripts/run.sh", "scripts/lib.sh"), imports)
        self.assertEqual([e for e in imports if e[0] == "scripts/run.sh"],
                         [("scripts/run.sh", "scripts/lib.sh")], "missing.sh is not in the repo")

    def test_tests_stay_in_the_graph(self):
        self.assertIn("pkg/tests/test_a.py", self.nodes)

    def test_unparseable_python_keeps_its_file_node(self):
        self.write("pkg/broken.py", "def (:\n")
        graph = graphctl.build_graph()
        ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("pkg/broken.py", ids)
        self.assertFalse([i for i in ids if i.startswith("pkg/broken.py::")])

    def test_record_surface_and_caches_are_not_walked(self):
        self.write(".claude-iff/obs/x.py", "def f():\n    pass\n")
        self.write("pkg/__pycache__/a.py", "def f():\n    pass\n")
        paths = {n["path"] for n in graphctl.build_graph()["nodes"]}
        self.assertNotIn(".claude-iff/obs/x.py", paths)
        self.assertNotIn("pkg/__pycache__/a.py", paths)


# --------------------------------------------------------------------------- graphify

def fake_graphify(root: Path, schema_version=1) -> dict:
    """graphify 0.9.102's node-link shape as the research note measured it."""
    abs_b = (root / "pkg" / "b.py").as_posix()
    return {
        "directed": True, "multigraph": False,
        "graph": {"schema_version": schema_version, "built_at_commit": "abc123"},
        "nodes": [
            {"id": "a_py", "label": "a.py", "file_type": "code", "source_file": "pkg/a.py",
             "source_location": "L1", "community": 0, "_origin": "ast"},
            {"id": "a_base", "label": "Base", "file_type": "code", "source_file": "pkg/a.py",
             "source_location": "L7", "_callable_class": True, "_callable": True},
            {"id": "a_run", "label": ".run()", "file_type": "code", "source_file": "pkg/a.py",
             "source_location": "L8", "_callable": True},
            {"id": "a_top", "label": "top()", "file_type": "code", "source_file": "pkg/a.py",
             "source_location": "L19", "_callable": True},
            {"id": "b_helper", "label": "helper()", "file_type": "code", "source_file": abs_b,
             "source_location": "L1", "_callable": True},
            {"id": "doc1", "label": "Run the step.", "file_type": "rationale",
             "source_file": "pkg/a.py", "source_location": "L9"},
            {"id": "os", "label": "os", "file_type": "concept", "external": True,
             "type": "external", "source_location": None},
            {"id": "sh_env", "label": "CLAUDE_PROJECT_DIR", "file_type": "code",
             "source_file": "scripts/run.sh", "source_location": "L3"},
            {"id": "sh_file", "label": "run.sh", "file_type": "code",
             "source_file": "scripts/run.sh", "source_location": "L1"},
        ],
        "links": [
            {"source": "a_py", "target": "a_base", "relation": "contains", "confidence": "EXTRACTED"},
            {"source": "a_base", "target": "a_run", "relation": "method", "confidence": "EXTRACTED"},
            {"source": "a_py", "target": "a_top", "relation": "contains", "confidence": "EXTRACTED"},
            {"source": "a_top", "target": "b_helper", "relation": "calls", "confidence": "INFERRED"},
            {"source": "a_run", "target": "b_helper", "relation": "indirect_call",
             "confidence": "EXTRACTED", "confidence_score": 0.9},
            {"source": "a_py", "target": "os", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "a_py", "target": "pkg/b.py", "relation": "imports_from",
             "confidence": "EXTRACTED"},
            {"source": "doc1", "target": "a_run", "relation": "rationale_for",
             "confidence": "EXTRACTED"},
            {"source": "a_top", "target": "a_base", "relation": "references",
             "confidence": "EXTRACTED"},
        ],
    }


class TestGraphifyNormaliser(GraphCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_tree()

    def test_same_schema_comes_out(self):
        self.write_graphify(fake_graphify(self.root))
        graph = graphctl.build_graph()
        self.assertSchema(graph)
        self.assertEqual(graph["source"], "graphify")
        self.assertTrue(graph["graphify"]["found"])
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual({k: nodes[k]["kind"] for k in ("a_py", "a_base", "a_run", "a_top", "sh_file")},
                         {"a_py": "file", "a_base": "class", "a_run": "method", "a_top": "function",
                          "sh_file": "file"})
        self.assertEqual(nodes["a_top"]["line"], 19)
        self.assertEqual(nodes["b_helper"]["path"], "pkg/b.py", "an absolute source_file is made relative")
        self.assertIn("pkg/b.py", nodes, "a file with symbols but no file node gets one")
        for dropped in ("doc1", "os", "sh_env"):
            self.assertNotIn(dropped, nodes)
        conf = {(e["from"], e["to"], e["type"]): e["confidence"] for e in graph["edges"]}
        self.assertEqual(conf[("a_base", "a_run", "contains")], 1.0)
        self.assertEqual(conf[("a_top", "b_helper", "calls")], 0.8)
        self.assertEqual(conf[("a_run", "b_helper", "calls")], 0.9)
        self.assertEqual(conf[("a_py", "pkg/b.py", "imports")], 1.0)
        self.assertNotIn(("a_top", "a_base", "references"), conf)
        self.assertEqual(set(graph["stats"]["types"]), set(graphctl.EDGE_TYPES))

    def _root_alias(self) -> Path:
        """Another spelling of self.root: on Windows its 8.3 short name (what the GitHub runner's
        temp folder is called), elsewhere a symlink; skip when the OS offers neither."""
        import ctypes
        import sys
        if sys.platform == "win32":
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetShortPathNameW(str(self.root), buf, 1024)
            short = Path(buf.value) if n else None
            if short and str(short).lower() != str(self.root).lower():
                return short
            self.skipTest("no 8.3 short name for the fixture root on this volume")
        alias = self.root.parent / "proj-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"no symlink here: {exc}")
        return alias

    def test_an_absolute_source_file_spelled_through_an_alias_of_the_root_is_made_relative(self):
        """GitHub's Windows runner names its temp folder by an 8.3 short name (RUNNER~1), so the
        root resolves to one spelling while graphify's absolute source_file carries another;
        the node was dropped and two tests went red there only. Here the alias is a symlink
        to the root (skipped where the OS refuses one); the CI runner covers the 8.3 case."""
        alias = self._root_alias()
        data = fake_graphify(self.root)
        for node in data["nodes"]:
            if node["id"] == "b_helper":
                node["source_file"] = (alias / "pkg" / "b.py").as_posix()
        self.write_graphify(data)
        nodes = {n["id"]: n for n in graphctl.build_graph()["nodes"]}
        self.assertIn("b_helper", nodes)
        self.assertEqual(nodes["b_helper"]["path"], "pkg/b.py")

    def test_networkx_edges_key_is_read_too(self):
        data = fake_graphify(self.root)
        data["edges"] = data.pop("links")
        self.write_graphify(data)
        self.assertIn(("a_top", "b_helper"), self.edge_set(graphctl.build_graph(), "calls"))

    def test_unknown_schema_version_fails_and_writes_nothing(self):
        self.write_graphify(fake_graphify(self.root, schema_version=2))
        code, out = self.cli("build")
        self.assertEqual(code, 1, out)
        self.assertIn("schema_version 2", out)
        self.assertIn("GRAPH_FAIL", out)
        self.assertFalse(graphctl.graph_path().exists())

    def test_missing_schema_version_fails(self):
        data = fake_graphify(self.root)
        del data["graph"]["schema_version"]
        self.write_graphify(data)
        with self.assertRaises(graphctl.GraphError):
            graphctl.build_graph()


# --------------------------------------------------------------------------- the CLI

class TestCli(GraphCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_tree()

    def test_build_without_graphify_warns_with_the_pinned_command(self):
        cfg = _lib.load_config("console")
        cfg["code_graph"] = {"graphify_version": "1.2.3"}
        self.write_config("console", cfg)
        code, out = self.cli("build")
        self.assertEqual(code, 0, out)
        self.assertIn("GRAPH_WARN", out)
        self.assertIn("uvx --from graphifyy==1.2.3 graphify extract . --code-only --out", out)
        self.assertIn((self.record / "graph").resolve().as_posix(), out.replace("\\", "/"))
        self.assertIn("--exclude .claude/worktrees", out)
        written = json.loads(graphctl.graph_path().read_text(encoding="utf-8"))
        self.assertSchema(written)
        self.assertIn(f"{written['stats']['nodes']} nodes, {written['stats']['edges']} edges", out)

    def test_the_tool_never_starts_uvx_or_graphify(self):
        self.write_graphify(fake_graphify(self.root))
        started = []

        class SpyPopen(subprocess.Popen):  # every process start goes through Popen
            def __init__(self, args, *a, **kw):
                started.append(args)
                super().__init__(args, *a, **kw)

        with mock.patch.object(subprocess, "Popen", SpyPopen), \
                mock.patch.object(os, "system", side_effect=AssertionError("os.system")):
            for verb in ("build", "update", "status"):
                self.cli(verb)
            os.remove(self.graphify_file())
            for verb in ("build", "update", "status"):
                self.cli(verb)
        names = {Path(str(argv[0] if isinstance(argv, (list, tuple)) else argv).split()[0]).stem.lower()
                 for argv in started}
        self.assertTrue(started, "the spy saw no process start at all (the file listing)")
        self.assertFalse(names & {"uvx", "uv", "graphify", "pip"}, started)

    def test_build_with_graphify_output_is_ok(self):
        self.write_graphify(fake_graphify(self.root))
        code, out = self.cli("build")
        self.assertEqual(code, 0, out)
        self.assertIn("GRAPH_OK", out)
        self.assertIn("source graphify", out)
        self.assertNotIn("uvx", out)

    def test_graphify_classes_stay_classes(self):
        """graphify 0.9.102 sets _callable on classes as well as _callable_class; the class
        flag must win, or every class is typed as a function and inherits edges join functions
        (found by the wave-3 verifier on this repo's real output)."""
        self.write_graphify(fake_graphify(self.root))
        self.assertEqual(self.cli("build")[0], 0)
        graph = json.loads(graphctl.graph_path().read_text(encoding="utf-8"))
        kinds = {n["label"]: n["kind"] for n in graph["nodes"]}
        self.assertEqual(kinds["Base"], "class")
        self.assertEqual(kinds.get("top", kinds.get("top()")), "function")
        self.assertEqual([n["label"] for n in graph["nodes"] if n["kind"] == "class"], ["Base"])

    def test_update_renormalises_then_rewalks(self):
        self.write_graphify(fake_graphify(self.root))
        self.assertEqual(self.cli("update")[0], 0)
        first = json.loads(graphctl.graph_path().read_text(encoding="utf-8"))
        self.assertEqual(first["source"], "graphify")
        data = fake_graphify(self.root)
        data["nodes"].append({"id": "a_extra", "label": "extra()", "file_type": "code",
                              "source_file": "pkg/a.py", "source_location": "L30"})
        self.write_graphify(data)
        code, out = self.cli("update")
        second = json.loads(graphctl.graph_path().read_text(encoding="utf-8"))
        self.assertEqual(code, 0, out)
        self.assertEqual(second["stats"]["nodes"], first["stats"]["nodes"] + 1)
        os.remove(self.graphify_file())
        code, out = self.cli("update")
        self.assertEqual(code, 0, out)
        self.assertIn("source walker", out)
        self.assertNotIn("uvx", out, "update gives no extraction advice")
        self.assertEqual(json.loads(graphctl.graph_path().read_text(encoding="utf-8"))["source"],
                         "walker")

    def test_status_before_and_after(self):
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("not built", out)
        self.assertIn("graphctl.py build", out)
        self.assertIn("output not found", out)
        self.assertIn("GRAPH_WARN", out)
        self.cli("build")
        graph = graphctl.load_graph()
        code, out = self.cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn(f"{graph['stats']['nodes']} nodes, {graph['stats']['edges']} edges", out)
        self.assertIn("ago", out)
        self.assertIn("source walker", out)
        self.assertIn("GRAPH_OK", out)
        self.write_graphify(fake_graphify(self.root))
        code, out = self.cli("status")
        self.assertIn("output found at", out)
        self.assertIn("graph/graphify-out/graph.json", out.replace("\\", "/"))

    def test_the_tool_never_writes_under_the_record_root(self):
        self.cli("build")
        self.cli("status")
        self.assertFalse(self.record.exists() and any(self.record.rglob("*")),
                         list(self.record.rglob("*")) if self.record.exists() else [])


# --------------------------------------------------------------------------- architecture_scope

class TestArchitectureScope(GraphCase):
    def setUp(self) -> None:
        super().setUp()
        hub = "def core():\n    return 1\n"
        self.write("app/core/hub.py", hub)
        self.write("app/core/mid.py", "import hub\n\n\ndef m():\n    hub.core()\n    hub.core()\n")
        self.write("app/core/leaf.py", "import hub\n\n\ndef l():\n    hub.core()\n")
        for name in ("u1", "u2", "u3", "u4"):
            self.write(f"app/util/{name}.py",
                       f"from app.core import hub\n\n\ndef {name}():\n    return hub.core()\n")
        self.write("app/util/lone.py", "def alone():\n    return 0\n")
        self.write("app/tests/test_hub.py", "from app.core import hub\n\n\ndef test():\n    hub.core()\n")
        self.write("app/util/test_util.py", "def test():\n    pass\n")
        self.write("other/x.py", "def x():\n    pass\n")
        self.graph = graphctl.build_graph()

    def scope(self, **kw):
        return graphctl.architecture_scope("app", graph=self.graph, **kw)

    def test_groups_nodes_edges_shape(self):
        scope = self.scope()
        self.assertEqual([g["title"] for g in scope["groups"]], ["app/core", "app/util"])
        for g in scope["groups"]:
            self.assertEqual(set(g), {"id", "title", "blurb"})
        paths = {n["path"] for n in scope["nodes"]}
        self.assertEqual(paths, {"app/core/hub.py", "app/core/mid.py", "app/core/leaf.py",
                                 "app/util/u1.py", "app/util/u2.py", "app/util/u3.py",
                                 "app/util/u4.py", "app/util/lone.py"})
        group_ids = {g["id"] for g in scope["groups"]}
        for n in scope["nodes"]:
            self.assertEqual(set(n), {"id", "group", "label", "path", "kind"})
            self.assertEqual(n["kind"], "file")
            self.assertIn(n["group"], group_ids)
        by_path = {n["path"]: n["id"] for n in scope["nodes"]}
        arrows = {(e["from"], e["to"], e["label"]): e for e in scope["edges"]}
        mid, hub = by_path["app/core/mid.py"], by_path["app/core/hub.py"]
        self.assertEqual(arrows[(mid, hub, "imports")]["weight"], 1)
        self.assertEqual(arrows[(mid, hub, "calls")]["weight"], 1, "one caller-callee pair")
        self.assertEqual(arrows[(mid, hub, "calls")]["style"], "dashed")
        self.assertEqual(arrows[(by_path["app/util/u1.py"], hub, "imports")]["style"], "solid")
        for e in scope["edges"]:
            self.assertIn(e["label"], ("imports", "calls"))
            self.assertIsInstance(e["weight"], int)
            self.assertNotEqual(e["from"], e["to"])
        self.assertEqual(scope["stats"], {"nodes": 8, "edges": len(scope["edges"]), "folded": 0})

    def test_tests_and_out_of_scope_files_are_excluded(self):
        paths = {n["path"] for n in self.scope()["nodes"]}
        self.assertNotIn("app/tests/test_hub.py", paths)
        self.assertNotIn("app/util/test_util.py", paths)
        self.assertNotIn("other/x.py", paths)

    def test_max_nodes_folds_lowest_degree_files_per_folder(self):
        scope = self.scope(max_nodes=4)
        self.assertLessEqual(len(scope["nodes"]), 4)
        others = [n for n in scope["nodes"] if n["kind"] == "folder"]
        self.assertTrue(others)
        for n in others:
            self.assertTrue((self.root / n["path"]).is_dir(), n)
            self.assertTrue(n["label"].startswith("other ("))
        kept = {n["path"] for n in scope["nodes"] if n["kind"] == "file"}
        self.assertIn("app/core/hub.py", kept, "the highest-degree file survives the fold")
        self.assertNotIn("app/util/lone.py", kept, "a degree-0 file is folded first")
        self.assertGreater(scope["stats"]["folded"], 0)
        self.assertEqual(scope["stats"]["nodes"], len(scope["nodes"]))
        ids = {n["id"] for n in scope["nodes"]}
        for e in scope["edges"]:
            self.assertIn(e["from"], ids)
            self.assertIn(e["to"], ids)
            self.assertNotEqual(e["from"], e["to"])

    def test_cap_below_the_folder_count_stops_at_one_box_per_folder(self):
        scope = self.scope(max_nodes=1)
        self.assertEqual(len(scope["nodes"]), 2)
        self.assertEqual({n["kind"] for n in scope["nodes"]}, {"folder"})

    def test_every_returned_path_exists_even_from_a_stale_graph(self):
        (self.root / "app/util/u4.py").unlink()
        scope = self.scope()
        self.assertNotIn("app/util/u4.py", {n["path"] for n in scope["nodes"]})
        for n in scope["nodes"]:
            self.assertTrue((self.root / n["path"]).exists(), n)

    def test_loads_the_written_graph_or_builds_one_in_memory(self):
        self.assertFalse(graphctl.graph_path().exists())
        in_memory = graphctl.architecture_scope("app")
        self.assertFalse(graphctl.graph_path().exists(), "architecture_scope never writes")
        self.assertEqual(in_memory["stats"]["nodes"], 8)
        graphctl.write_graph(self.graph)
        self.assertEqual(graphctl.architecture_scope("./app/")["nodes"], in_memory["nodes"])

    def test_deterministic(self):
        self.assertEqual(self.scope(max_nodes=5), self.scope(max_nodes=5))


# --------------------------------------------------------------------------- registrations

class TestRegistrations(unittest.TestCase):
    def test_probe_entry(self):
        self.assertIn(("tool.graphctl", ".claude/tools/graphctl.py"),
                      [(r.name, r.message) for r in checkctl.probe()])

    def test_card(self):
        card = json.loads((CLAUDE_DIR / "system-map" / "cards" / "tool.graphctl.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(card["path"], ".claude/tools/graphctl.py")
        self.assertEqual(card["kind"], "code")

    def test_pin_knob_and_registry_card(self):
        console = json.loads((CLAUDE_DIR / "config" / "console.json").read_text(encoding="utf-8"))
        pin = console["code_graph"]["graphify_version"]
        self.assertRegex(pin, r"^\d+\.\d+\.\d+$")
        registry = json.loads((CLAUDE_DIR / "config" / "registry.json").read_text(encoding="utf-8"))
        entry = next(e for e in registry["entries"] if e["key"] == "console.code_graph.graphify_version")
        self.assertEqual(entry["target"], {"kind": "config", "file": "console",
                                           "path": "code_graph.graphify_version"})
        self.assertEqual(entry["default"], pin)

    def test_gitignore_ignores_the_graph(self):
        # The line sits next to the managed block in the home repo's own .gitignore; an
        # adopter's .gitignore is the managed block alone (distctl renders it).
        memory = _lib.read_json(REPO_ROOT / ".claude" / "config" / "memory.json", {}) or {}
        if not (memory.get("distribution") or {}).get("enabled", False):
            self.skipTest("not the home repo: its .gitignore is the adopter's own")
        lines =(REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".claude/system-map/code-graph.json", [l.strip() for l in lines])


if __name__ == "__main__":
    unittest.main(verbosity=2)
