#!/usr/bin/env python3
"""test_context.py - folder context (ctxmap.py): discovery, resolution, health, suggestions.

The engine answers one question - what does Claude Code load when it touches this file - and
three surfaces repeat the answer: `mapctl context`, the `context_health` CHECK step and the
console's Context list. These tests pin the model (glob and frontmatter semantics first,
because every later answer rests on them), then each surface, then the seams between them.
Each health finding has its own test, and each test is built so that it can fail.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import consolectl  # noqa: E402
import ctxmap  # noqa: E402
import mapctl  # noqa: E402

CASE_FOLDS = os.path.normcase("A") == "a"


def git_available() -> bool:
    return shutil.which("git") is not None


class ContextCase(FixtureCase):
    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def body(n: int, word: str = "line") -> str:
        return "".join(f"{word} {i}\n" for i in range(n))

    def mapctl(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mapctl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def codes(self, findings) -> list:
        return [f.code for f in findings]

    def git_init(self) -> None:
        if not git_available():
            self.skipTest("git not available")
        r = subprocess.run(["git", "init", "-q"], cwd=str(self.root), capture_output=True,
                           text=True, check=False)
        if r.returncode != 0:
            self.skipTest(f"git init failed: {r.stderr}")


# =========================================================================== the glob matcher

class GlobTests(unittest.TestCase):
    def m(self, pattern, path):
        return ctxmap.glob_match([pattern], path)

    def test_double_star_spans_any_depth_including_zero(self):
        self.assertTrue(self.m("src/**/*.ts", "src/a.ts"), "** must match zero folders")
        self.assertTrue(self.m("src/**/*.ts", "src/x/y/a.ts"))
        self.assertFalse(self.m("src/**/*.ts", "lib/a.ts"))
        self.assertTrue(self.m("**/*.md", "a.md"))
        self.assertTrue(self.m("**/*.md", "x/y/z.md"))
        self.assertTrue(self.m("src/**", "src/a/b/c.py"))
        self.assertTrue(self.m("a/**/b/**/c.txt", "a/b/c.txt"))
        self.assertTrue(self.m("a/**/b/**/c.txt", "a/1/2/b/3/c.txt"))

    def test_star_and_question_stay_inside_one_segment(self):
        self.assertTrue(self.m("src/*.ts", "src/a.ts"))
        self.assertFalse(self.m("src/*.ts", "src/x/a.ts"), "* must not cross a /")
        self.assertTrue(self.m("a?.md", "ab.md"))
        self.assertFalse(self.m("a?.md", "abc.md"))
        self.assertFalse(self.m("a?b", "a/b"), "? must not match a /")

    def test_root_anchored_without_a_folder(self):
        self.assertTrue(self.m("*.md", "README.md"))
        self.assertFalse(self.m("*.md", "docs/README.md"), "*.md is root-level markdown only")

    def test_brace_alternatives_expand_and_nest(self):
        self.assertTrue(self.m("src/*.{ts,tsx}", "src/a.ts"))
        self.assertTrue(self.m("src/*.{ts,tsx}", "src/a.tsx"))
        self.assertFalse(self.m("src/*.{ts,tsx}", "src/a.js"))
        self.assertEqual(sorted(ctxmap.expand_braces("{a,b}/{c,d}")), ["a/c", "a/d", "b/c", "b/d"])
        self.assertEqual(sorted(ctxmap.expand_braces("x{a,b{c,d}}")), ["xa", "xbc", "xbd"])
        self.assertEqual(ctxmap.expand_braces("x{a}y"), ["x{a}y"], "no comma: literal braces")
        self.assertTrue(self.m("{lib,src}/**/*.py", "lib/m/x.py"))

    def test_leading_dot_slash_and_trailing_slash(self):
        self.assertTrue(self.m("./src/*.py", "src/a.py"))
        self.assertTrue(self.m("src/*.py", "./src/a.py"))
        self.assertTrue(self.m("docs/", "docs/a/b.md"), "a trailing / means everything under")

    def test_case_follows_normcase(self):
        # Folded exactly where os.path.normcase folds: Windows yes, Linux no (L-9).
        self.assertEqual(self.m("SRC/*.PY", "src/a.py"), CASE_FOLDS)
        self.assertTrue(self.m("src/*.py", "src/a.py"))

    def test_bracket_expressions_escapes_and_invalid_brackets(self):
        self.assertTrue(self.m("v[0-9].md", "v3.md"))
        self.assertFalse(self.m("v[!0-9].md", "v3.md"))
        self.assertTrue(self.m(r"photos \[2024/**", "photos [2024/a.png"))
        self.assertFalse(self.m("photos [2024/**", "photos [2024/a.png"),
                         "a [ that never closes makes the pattern match nothing")
        self.assertIsNone(ctxmap.compile_glob("photos [2024/**"))

    def test_brace_budget_leaves_a_runaway_pattern_unexpanded(self):
        runaway = "{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/{a,b}/x.md"  # 1024
        self.assertGreater(len(ctxmap.expand_braces(runaway)), ctxmap.BRACE_BUDGET)
        self.assertFalse(self.m(runaway, "a/a/a/a/a/a/a/a/a/a/x.md"),
                         "over the budget the literal braces must match nothing")
        self.assertTrue(self.m("{a,b}/x.md", "b/x.md"))


# =========================================================================== frontmatter

def fm(*lines) -> str:
    return "\n".join(lines) + "\n# Rule\n\nbody\n"


class FrontmatterTests(unittest.TestCase):
    def read(self, text):
        return ctxmap.read_frontmatter(text)

    def test_no_frontmatter_or_no_paths_is_unscoped(self):
        self.assertEqual(self.read("# Rule\nbody\n")[0], "none")
        self.assertEqual(self.read(fm("---", "description: style notes", "---"))[0], "none")
        self.assertEqual(self.read(fm("---", "---"))[0], "none")

    def test_string_forms(self):
        self.assertEqual(self.read(fm("---", 'paths: "src/**/*.ts"', "---")), ("scoped", ["src/**/*.ts"], ""))
        self.assertEqual(self.read(fm("---", "paths: src/**/*.ts", "---"))[1], ["src/**/*.ts"])
        self.assertEqual(self.read(fm("---", "paths: 'lib/**' # comment", "---"))[1], ["lib/**"])
        self.assertEqual(self.read(fm("---", 'paths: "src/**/*.ts, lib/*.{ts,tsx}"', "---"))[1],
                         ["src/**/*.ts", "lib/*.{ts,tsx}"], "comma-separated, brace-aware")
        self.assertEqual(self.read(fm("---", "paths: src/*.{ts,tsx}", "---"))[1], ["src/*.{ts,tsx}"])

    def test_inline_list(self):
        self.assertEqual(self.read(fm("---", 'paths: ["src/**", lib/**, \'a b/*.md\']', "---")),
                         ("scoped", ["src/**", "lib/**", "a b/*.md"], ""))
        self.assertEqual(self.read(fm("---", 'paths: ["src/*.{ts,tsx}"]  # quoted braces', "---"))[1],
                         ["src/*.{ts,tsx}"])

    def test_block_list_indented_or_not_quoted_or_not(self):
        text = fm("---", "description: x", "paths:", '  - "src/api/**/*.ts"',
                  "  # a comment", "  - lib/**", "tags:", "  - y", "---")
        self.assertEqual(self.read(text), ("scoped", ["src/api/**/*.ts", "lib/**"], ""))
        self.assertEqual(self.read(fm("---", "paths:", "- 'a/**'", "- b/**", "---"))[1], ["a/**", "b/**"])

    def test_what_breaks_yaml_is_unparseable(self):
        cases = {
            "unquoted leading * (an alias)": fm("---", "paths: **/*.ts", "---"),
            "unquoted * in a block item": fm("---", "paths:", "  - **/*.py", "---"),
            "unquoted * in an inline item": fm("---", "paths: [**/*.py]", "---"),
            "unquoted braces in an inline item": fm("---", "paths: [src/*.{ts,tsx}]", "---"),
            "a mapping, not a list or string": fm("---", "paths: {a: b}", "---"),
            "never closes": "---\npaths: src/**\n# Rule\n",
            "tab indentation": fm("---", "paths:", "\t- src/**", "---"),
            "': ' in another key's plain value": fm("---", "description: Note: this", "paths: src/**", "---"),
            "empty": fm("---", "paths:", "---"),
            "empty inline list": fm("---", "paths: []", "---"),
            "null": fm("---", "paths: null", "---"),
            "twice": fm("---", "paths: a/**", "paths: b/**", "---"),
            "block scalar": fm("---", "paths: |", "  src/**", "---"),
            "not key: value": fm("---", "just words", "---"),
            "unclosed quote": fm("---", 'paths: "src/**', "---"),
            "a list item on the key's line": fm("---", "paths: - src/**", "---"),
        }
        for why, text in cases.items():
            with self.subTest(case=why):
                state, globs, reason = self.read(text)
                self.assertEqual(state, "unparseable", f"{why} must be unparseable, got {state} {globs}")
                self.assertTrue(reason)


# =========================================================================== discovery

class DiscoveryTests(ContextCase):
    def test_fixture_with_nested_guides_and_rules_is_fully_discovered(self):
        self.write(".claude/CLAUDE.md", "# root\n")
        self.write("src/CLAUDE.md", "# src\n")
        self.write("src/api/CLAUDE.md", "# api\n")
        self.write("src/api/CLAUDE.local.md", "# mine\n")
        self.write(".claude/rules/general.md", "# always\n")
        self.write(".claude/rules/py/python.md", fm("---", "paths: '**/*.py'", "---"))
        # never guides
        self.write(".claude/skills/adopt/CLAUDE.template.md", "# template\n")
        self.write(".claude/agents/CLAUDE.md", "# inside the system tree\n")
        self.write(".claude/worktrees/t1/src/CLAUDE.md", "# a worktree copy\n")
        self.write("node_modules/pkg/CLAUDE.md", "# vendored\n")
        self.write(".venv/lib/CLAUDE.md", "# venv\n")
        self.write("env/pyvenv.cfg", "home = x\n")
        self.write("env/lib/CLAUDE.md", "# a virtualenv found by its marker\n")
        self.write(".claude/rules/notes.txt", "not markdown\n")

        index = ctxmap.discover()
        self.assertEqual(index.source, "walk")
        self.assertEqual([g.path for g in index.root_guides], [".claude/CLAUDE.md"])
        self.assertEqual([g.path for g in index.folder_guides],
                         ["src/CLAUDE.md", "src/api/CLAUDE.md", "src/api/CLAUDE.local.md"])
        self.assertEqual([r.path for r in index.rules],
                         [".claude/rules/general.md", ".claude/rules/py/python.md"])
        self.assertEqual([r.scope for r in index.rules], ["always", "scoped"])
        self.assertTrue(index.folder_guides[2].local)

    def test_record_folder_inside_the_project_is_never_walked(self):
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.root / "rec")
        self.write("rec/raw/CLAUDE.md", "# captured content\n")
        self.write("src/CLAUDE.md", "# src\n")
        self.assertEqual([g.path for g in ctxmap.discover().folder_guides], ["src/CLAUDE.md"])

    def test_git_discovery_lists_untracked_and_skips_ignored(self):
        self.git_init()
        self.write(".gitignore", "build/\n")
        self.write("build/CLAUDE.md", "# generated, ignored\n")
        self.write("src/CLAUDE.md", "# untracked but not ignored\n")
        index = ctxmap.discover()
        self.assertEqual(index.source, "git")
        self.assertEqual([g.path for g in index.folder_guides], ["src/CLAUDE.md"])


# =========================================================================== resolution

class ResolveTests(ContextCase):
    """A three-level fixture: src/, src/api/, src/api/v2/, plus everything that must stay out."""

    def setUp(self):
        super().setUp()
        self.write(".claude/CLAUDE.md", "# root\nsee @../docs/conventions.md for style\n" + self.body(8))
        self.write("docs/conventions.md", self.body(5))
        self.write(".claude/rules/general.md", self.body(3))
        self.write(".claude/rules/python.md", fm("---", "paths:", '  - "**/*.py"', "---"))
        self.write(".claude/rules/web.md", fm("---", "paths: web/**", "---"))
        self.write("web/index.html", "<p>\n")
        self.write("src/CLAUDE.md", self.body(4))
        self.write("src/api/CLAUDE.md", self.body(6))
        self.write("src/api/CLAUDE.local.md", self.body(2))
        self.write("src/api/v2/CLAUDE.md", "v2 notes, plus @helpers.md\n" + self.body(2))
        self.write("src/api/v2/helpers.md", self.body(1))
        self.write("src/other/CLAUDE.md", self.body(9))

    def test_chain_order_for_a_three_level_path(self):
        result = ctxmap.resolve("src/api/v2/handler.py")
        order = [(i["path"], i["loads"]) for i in result["chain"]]
        self.assertEqual(order, [
            (".claude/CLAUDE.md", "launch"),
            ("docs/conventions.md", "launch"),
            (".claude/rules/general.md", "launch"),
            ("src/CLAUDE.md", "on-read"),
            ("src/api/CLAUDE.md", "on-read"),
            ("src/api/CLAUDE.local.md", "on-read"),
            ("src/api/v2/CLAUDE.md", "on-read"),
            ("src/api/v2/helpers.md", "on-read"),
            (".claude/rules/python.md", "on-match"),
        ])
        self.assertNotIn("src/other/CLAUDE.md", [p for p, _ in order])
        self.assertNotIn(".claude/rules/web.md", [p for p, _ in order])
        expected_total = sum(len((self.root / p).read_text(encoding="utf-8").splitlines())
                             for p, _ in order)
        self.assertEqual(result["total_lines"], expected_total)
        self.assertEqual(result["always_on_lines"], 10 + 5 + 3)
        imported = result["chain"][1]
        self.assertEqual(imported["via"], ".claude/CLAUDE.md", "an import names its importer")

    def test_a_root_level_file_gets_only_the_launch_set(self):
        paths = [i["path"] for i in ctxmap.resolve("setup.cfg")["chain"]]
        self.assertEqual(paths, [".claude/CLAUDE.md", "docs/conventions.md", ".claude/rules/general.md"])

    def test_cli_text_and_json(self):
        code, out, err = self.mapctl("context", "src/api/v2/handler.py")
        self.assertEqual(code, 0)
        self.assertIn("src/api/v2/CLAUDE.md", out)
        self.assertIn("total", out)
        self.assertEqual(out.count("MAP_OK"), 1)

        code, out, err = self.mapctl("context", "src/api/v2/handler.py", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)  # stdout is pure JSON; the verdict went to stderr
        self.assertEqual(data["chain"][-1]["path"], ".claude/rules/python.md")
        self.assertIn("MAP_OK", err)

    def test_cli_listing_and_suggest_print_one_verdict_each(self):
        for argv in (("context",), ("context", "--json"), ("context", "--suggest"),
                     ("context", "--suggest", "--json"), ("context", "src/a.py")):
            with self.subTest(argv=argv):
                code, out, err = self.mapctl(*argv)
                tokens = [t for t in ("MAP_OK", "MAP_WARN", "MAP_FAIL") for _ in range((out + err).count(t))]
                self.assertEqual(len(tokens), 1, f"{argv}: {out!r} {err!r}")

    def test_a_path_outside_the_project_fails(self):
        code, out, err = self.mapctl("context", "../elsewhere/x.py")
        self.assertEqual(code, 1)
        self.assertIn("MAP_FAIL", out)

    def test_personal_local_guide_loads_for_you_but_is_not_mapped(self):
        self.git_init()
        self.write(".gitignore", "CLAUDE.local.md\n")
        self.write("CLAUDE.local.md", self.body(2))
        self.assertNotIn("CLAUDE.local.md", [g.path for g in ctxmap.discover().guides()],
                         "a gitignored local guide must stay out of the committed map")
        chain = ctxmap.resolve("src/a.py")["chain"]
        mine = [i for i in chain if i["path"] == "CLAUDE.local.md"]
        self.assertEqual(len(mine), 1, "but the query must still list it: it does load")
        self.assertTrue(mine[0]["personal"])
        self.assertEqual(mine[0]["loads"], "launch")


# =========================================================================== health: one test per finding

class HealthTests(ContextCase):
    def findings(self):
        return ctxmap.health(ctxmap.discover())

    def test_empty_project_is_ok(self):
        status, message, details = ctxmap.health_summary()
        self.assertEqual((status, details), ("OK", []))
        self.assertEqual(checkctl.check_context_health().status, checkctl.OK)

    def test_root_guide_alone_is_ok(self):
        self.write(".claude/CLAUDE.md", "# guide\n\nSee `.claude/config/memory.json`.\n")
        self.assertEqual(self.findings(), [])

    def test_two_root_guides_fail(self):
        self.write(".claude/CLAUDE.md", "# one\n")
        self.write("CLAUDE.md", "# two\n")
        found = self.findings()
        self.assertEqual(self.codes(found), ["root_guide_multiple"])
        self.assertEqual(found[0].level, "FAIL")
        self.assertEqual(checkctl.check_context_health().status, checkctl.FAIL)

    def test_root_local_overlay_is_not_a_second_root_guide(self):
        self.write(".claude/CLAUDE.md", "# one\n")
        self.write("CLAUDE.local.md", "# mine\n")
        self.assertEqual(self.findings(), [])

    def test_unparseable_rule_frontmatter_warns(self):
        self.write("src/a.ts", "x\n")
        self.write(".claude/rules/ts.md", fm("---", "paths: **/*.ts", "---"))
        found = self.findings()
        self.assertEqual(self.codes(found), ["frontmatter_unparseable"])
        self.assertEqual(found[0].level, "WARN")
        self.assertIn("every file", found[0].message)

    def test_rule_whose_globs_match_no_file_warns(self):
        self.write("src/a.py", "x\n")
        self.write(".claude/rules/go.md", fm("---", "paths: '**/*.go'", "---"))
        self.assertEqual(self.codes(self.findings()), ["rule_matches_nothing"])
        self.write("cmd/main.go", "package main\n")
        self.assertEqual(self.findings(), [], "a rule that matches a file is healthy")

    def test_guide_over_the_size_target_warns_and_the_knob_is_read(self):
        self.write("src/CLAUDE.md", self.body(ctxmap.DEFAULT_SIZE_TARGET + 1))
        self.assertEqual(self.codes(self.findings()), ["file_over_size"])
        cfg = _lib.load_config("memory")
        cfg["context"] = dict(cfg.get("context") or {}, size_target_lines=500)
        self.write_config("memory", cfg)
        self.assertEqual(self.findings(), [], "a raised size_target_lines knob must be honoured")

    def test_always_on_budget_counts_imports_and_unscoped_rules_only(self):
        self.write(".claude/CLAUDE.md", "@extra.md\n" + self.body(9))       # 10 lines
        self.write(".claude/extra.md", self.body(10))                        # 10, imported
        self.write(".claude/rules/all.md", self.body(10))                    # 10, unscoped
        self.write(".claude/rules/py.md", fm("---", "paths: '**/*.py'", "---") + self.body(50))
        self.write("a.py", "x\n")
        cfg = _lib.load_config("memory")
        cfg["context"] = dict(cfg.get("context") or {}, always_on_budget_lines=30)
        self.write_config("memory", cfg)
        self.assertEqual(ctxmap.always_on_lines(ctxmap.discover()), 30)
        self.assertEqual(self.findings(), [], "exactly at the budget is within it")
        cfg["context"]["always_on_budget_lines"] = 29
        self.write_config("memory", cfg)
        found = self.findings()
        self.assertEqual(self.codes(found), ["always_on_over_budget"])
        self.assertIn("30 lines", found[0].message)

    def test_missing_import_fails(self):
        self.write(".claude/CLAUDE.md", "# root\nRead @docs/gone.md first.\nMention @alice is fine.\n")
        found = self.findings()
        self.assertEqual(self.codes(found), ["import_missing"])
        self.assertEqual(found[0].level, "FAIL")
        self.assertIn("docs/gone.md", found[0].message)

    def test_imports_in_code_are_not_imports(self):
        self.write(".claude/CLAUDE.md", "# root\nWrite `@docs/gone.md` literally.\n"
                                        "```\n@docs/also-gone.md\n```\nmail a@b.example.com\n")
        self.assertEqual(self.findings(), [])

    def test_import_deeper_than_four_hops_warns(self):
        self.write(".claude/CLAUDE.md", "@h1.md\n")
        for hop in range(1, 6):
            nxt = f"@h{hop + 1}.md\n" if hop < 5 else ""
            self.write(f".claude/h{hop}.md", f"hop {hop}\n{nxt}")
        index = ctxmap.discover()
        loaded = [cf.path for cf in index.loaded_imports(index.root_guides[0])]
        self.assertEqual(loaded, [f".claude/h{i}.md" for i in range(1, 5)], "hops 1-4 load")
        found = ctxmap.health(index)
        self.assertEqual(self.codes(found), ["import_too_deep"])
        self.assertIn("h5.md", found[0].message)
        self.assertEqual(found[0].level, "WARN")

    def test_stale_backticked_path_warns(self):
        self.write(".claude/CLAUDE.md", "# root\n\nThe engine is `src/engine/core.py` and "
                                        "its notes are `notes.md`.\n")
        found = self.findings()
        self.assertEqual(self.codes(found), ["stale_path"])
        self.assertIn("src/engine/core.py", found[0].message)
        self.assertIn("notes.md", found[0].message)
        self.write("src/engine/core.py", "x\n")
        self.write("docs/deep/notes.md", "x\n")  # a bare name found anywhere counts
        self.assertEqual(self.findings(), [])

    def test_stale_path_check_ignores_what_is_not_a_path(self):
        self.write("src/CLAUDE.md", "\n".join([
            "# src conventions",
            "- globs: `src/**/*.py`, `*.md`, `tests/test_?.py`",
            "- placeholders: `.claude/tools/tests/test_<name>.py`, `archive/YYYYMMDD/`",
            "- commands: `python3 tools/run.py --fast`, `make build`, `-q`",
            "- urls and homes: `https://example.com/a/b`, `~/notes/x.md`, `/abs/x.md`",
            "- code: `_lib.JOURNAL_ACTIONS`, `os.path.normcase`, `test_x.py::test_y`, `v0.2.2`",
            "- a sibling: `helper.py`, relative to this folder",
            "```",
            "see `fenced/never/checked.py`",
            "```",
        ]) + "\n")
        self.write("src/helper.py", "x\n")
        self.assertEqual(self.findings(), [])

    def test_gitignored_location_is_not_stale(self):
        self.git_init()
        self.write(".gitignore", "scratch/\n")
        self.write(".claude/CLAUDE.md", "# root\nLocal material lives in `scratch/`.\n")
        self.assertEqual(self.findings(), [], "a folder the repo ignores is legitimately absent")

    def test_nested_guide_repeating_its_ancestor_warns_with_a_count(self):
        shared = ["Never commit generated files to the repository root folder.",
                  "Run the full test suite before you report any task as done."]
        self.write(".claude/CLAUDE.md", "# root\n" + "\n".join(f"- {s}" for s in shared) + "\n")
        self.write("src/CLAUDE.md", "# src\n\n" + "\n".join(f"* {s}" for s in shared)
                   + "\nThis folder holds the parser and nothing else of note.\n")
        found = self.findings()
        self.assertEqual(self.codes(found), ["duplicate_lines"])
        self.assertEqual(found[0].path, "src/CLAUDE.md")
        self.assertIn("2 line(s)", found[0].message)

    def test_sibling_guides_are_not_ancestors(self):
        line = "Never commit generated files to the repository root folder.\n"
        self.write("src/a/CLAUDE.md", line)
        self.write("src/b/CLAUDE.md", line)
        self.assertEqual(self.findings(), [])

    def test_the_real_root_guide_is_not_noisy(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
        _lib.clear_config_cache()
        index = ctxmap.discover()
        self.assertTrue(index.root_guides, "this repo must have a root guide")
        noisy = [f.line() for f in ctxmap.health(index)
                 if f.path == index.root_guides[0].path]
        self.assertEqual(noisy, [], "context_health must be quiet on this repo's own guide")


# =========================================================================== suggest

class SuggestTests(ContextCase):
    def lessons(self, *rows) -> None:
        self.write(".claude/LESSONS.jsonl", "".join(json.dumps(r) + "\n" for r in rows))

    def log(self, *rows) -> None:
        self.write(".claude/Project-log.jsonl", "".join(json.dumps(r) + "\n" for r in rows))

    def lesson(self, id_, what):
        return {"id": id_, "date": "2026-10-01", "who": "agent", "what": what,
                "root_cause": "x", "prevention": "y", "active": True}

    def setUp(self):
        super().setUp()
        self.write("src/api/a.py", "x\n")
        self.write("src/api/b.py", "x\n")
        self.write("src/c.py", "x\n")
        self.write(".claude/hooks/gate.sh", "x\n")
        self.write(".claude/hooks/capture.sh", "x\n")

    def test_two_lessons_in_one_subtree_yield_exactly_one_suggestion(self):
        self.lessons(self.lesson("L-1", "src/api/a.py swallowed an error."),
                     self.lesson("L-2", "The handler in `src/api/b.py` returned None."))
        result = ctxmap.suggest()
        self.assertEqual(len(result["proposals"]), 1, result)
        p = result["proposals"][0]
        self.assertEqual((p["kind"], p["target"]), ("folder-guide", "src/api/CLAUDE.md"))
        self.assertEqual(p["entries"], ["L-1", "L-2"])
        code, out, err = self.mapctl("context", "--suggest")
        self.assertIn("src/api/CLAUDE.md", out)
        self.assertFalse((self.root / "src/api/CLAUDE.md").exists(), "suggest never creates")

    def test_one_entry_is_not_evidence_enough(self):
        self.lessons(self.lesson("L-1", "src/api/a.py and src/api/b.py both broke."))
        self.assertEqual(ctxmap.suggest()["proposals"], [])

    def test_a_covered_subtree_gets_no_suggestion(self):
        self.lessons(self.lesson("L-1", "src/api/a.py broke."), self.lesson("L-2", "src/api/b.py broke."))
        self.write("src/CLAUDE.md", "# src\n")
        self.assertEqual(ctxmap.suggest()["proposals"], [], "an ancestor guide covers it")
        (self.root / "src/CLAUDE.md").unlink()
        self.write(".claude/rules/api.md", fm("---", "paths: 'src/api/**'", "---"))
        self.assertEqual(ctxmap.suggest()["proposals"], [], "a scoped rule covers it too")

    def test_deeper_evidence_is_not_counted_again_for_an_ancestor(self):
        self.lessons(self.lesson("L-1", "src/api/a.py broke."), self.lesson("L-2", "src/api/b.py broke."),
                     self.lesson("L-3", "src/c.py broke."))
        folders = [p["folder"] for p in ctxmap.suggest()["proposals"]]
        self.assertEqual(folders, ["src/api"], "src/ has one unexplained entry, not three")

    def test_logged_mistakes_count_and_inside_claude_a_scoped_rule_is_proposed(self):
        self.log({"date": "2026-10-01", "type": "mistake", "title": "gate leaked",
                  "summary": "s", "artifacts": ["hooks/gate.sh"]},
                 {"date": "2026-10-02", "type": "decision", "title": "about hooks/capture.sh",
                  "summary": "a decision is not a mistake", "artifacts": ["hooks/capture.sh"]},
                 {"date": "2026-10-03", "type": "mistake", "title": "capture lost events",
                  "summary": "see .claude/hooks/capture.sh", "artifacts": []})
        props = ctxmap.suggest()["proposals"]
        self.assertEqual(len(props), 1, props)
        self.assertEqual(props[0]["kind"], "scoped-rule")
        self.assertEqual(props[0]["paths"], [".claude/hooks/**"])
        self.assertEqual(len(props[0]["entries"]), 2, "the decision entry must not count")


# =========================================================================== map, lint, ritual bindings

class MapIntegrationTests(ContextCase):
    def setUp(self):
        super().setUp()
        self.write(".claude/CLAUDE.md", "# root\n")
        self.write("src/CLAUDE.md", "# src\n")
        self.write("src/api/CLAUDE.md", "# api\n")
        self.write(".claude/rules/py.md", fm("---", "paths: '**/*.py'", "---"))
        self.write(".claude/rules/all.md", "# always\n")
        self.write("src/api/x.py", "x\n")

    def test_guides_and_rules_never_become_cards_and_lint_never_asks_for_one(self):
        code, out, err = self.mapctl("scan")
        self.assertEqual(code, 0)
        self.assertIn("CONTEXT    3 guide(s), 2 rule(s)", out)
        cards = [_lib.read_json(p, {}) for p in (self.root / ".claude/system-map/cards").glob("*.json")]
        paths = {c.get("path") for c in cards}
        for ctx_path in (".claude/CLAUDE.md", "src/CLAUDE.md", ".claude/rules/py.md"):
            self.assertNotIn(ctx_path, paths, f"scan made a card for {ctx_path}")
        code, out, err = self.mapctl("lint")
        errors = [ln for ln in out.splitlines() if "ERROR" in ln]
        self.assertFalse([e for e in errors if "CLAUDE.md" in e or "rules/" in e], errors)

    def test_compile_maps_every_guide_and_rule_as_a_context_entry(self):
        self.mapctl("scan")
        self.mapctl("compile")
        section = _lib.read_json(self.root / ".claude/system-map/map.json")["context"]
        self.assertEqual([(e["path"], e["kind"], e["loads"]) for e in section["entries"]], [
            (".claude/CLAUDE.md", "guide", "launch"),
            (".claude/rules/all.md", "rule", "launch"),
            ("src/CLAUDE.md", "guide", "on-read"),
            ("src/api/CLAUDE.md", "guide", "on-read"),
            (".claude/rules/py.md", "rule", "on-match"),
        ])
        self.assertEqual(section["root_guide"], ".claude/CLAUDE.md")
        self.assertEqual(section["counts"], {"guides": 3, "rules": 2, "imports": 0})
        # The second compile may still rewrite: store.map's own `exists` flips once map.json
        # is on disk (pre-existing, unrelated). From the third on, nothing may change.
        self.mapctl("compile")
        code, out, err = self.mapctl("compile")
        self.assertIn("unchanged, not rewritten", out, "the context section must be deterministic")

    def test_contract_paths_all_exist_in_a_real_section(self):
        section = ctxmap.map_section()
        for dotted in ctxmap.CONTEXT_CONTRACT:
            with self.subTest(path=dotted):
                node = section
                for part in dotted.split("."):
                    if part.endswith("[]"):
                        node = node[part[:-2]][0]
                    else:
                        self.assertIn(part, node)
                        node = node[part]

    def test_map_generators_notice_a_new_guide(self):
        for name in ("map_scan", "map_compile"):
            with self.subTest(generator=name):
                spec = checkctl.GENERATORS[name]
                before = checkctl.generator_inputs_hash(spec)
                self.write(f"lib/{name}/CLAUDE.md", "# new\n")
                self.assertNotEqual(checkctl.generator_inputs_hash(spec), before,
                                    f"{name}'s freshness must notice a new folder guide")


class RitualBindingTests(ContextCase):
    def test_context_health_is_registered_and_bound(self):
        self.assertIn("context_health", checkctl.phase_steps("check"))
        self.assertIs(checkctl.CHECKS["context_health"], checkctl.check_context_health)

    def test_the_check_reports_findings_as_details(self):
        self.write(".claude/rules/bad.md", fm("---", "paths: **/*.py", "---"))
        result = checkctl.check_context_health()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertTrue(any("frontmatter" in d for d in result.details), result.details)

    def test_every_knob_has_a_registry_card(self):
        result = checkctl.check_config_registry()
        self.assertFalse([d for d in result.details if "memory.context" in d], result.details)
        registry = _lib.load_config("registry")
        keys = {e["key"] for e in registry["entries"]}
        self.assertLessEqual({"memory.context.size_target_lines",
                              "memory.context.always_on_budget_lines"}, keys)

    def test_the_engine_has_a_probe_and_a_card(self):
        self.assertIn("tool.ctxmap", [r.name for r in checkctl.probe()])
        card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "tool.ctxmap.json")
        self.assertIsNotNone(card, "the new tool module needs its card")
        self.assertEqual(card["path"], ".claude/tools/ctxmap.py")


# =========================================================================== console

class ConsoleContextTests(ContextCase):
    def test_payload_context_degrades_on_an_empty_project(self):
        self.assertEqual(consolectl.payload()["context"], ctxmap.empty_section())

    def test_payload_carries_what_compile_derived(self):
        self.write(".claude/CLAUDE.md", "# root\n")
        self.write("src/CLAUDE.md", self.body(3))
        self.write(".claude/rules/x.md", fm("---", "paths: 'src/**'", "---"))
        self.mapctl("compile")
        compiled = _lib.read_json(self.root / ".claude/system-map/map.json")["context"]
        payload = consolectl.payload()["context"]
        self.assertEqual(payload["entries"], compiled["entries"])
        self.assertEqual(payload["always_on_lines"], compiled["always_on_lines"])
        self.assertEqual(payload["findings"], compiled["findings"])

    def test_template_reads_every_entry_field(self):
        import re
        template = (CLAUDE_DIR / "console" / "console.template.html").read_text(encoding="utf-8")
        self.assertIn("DATA.context", template)
        for leaf in ctxmap.ENTRY_FIELDS + ("always_on_lines", "always_on_budget", "fail", "warn"):
            with self.subTest(field=leaf):
                self.assertRegex(template, rf"\.{re.escape(leaf)}\b",
                                 f"the console never reads context field {leaf!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
