#!/usr/bin/env python3
"""test_code_walk.py - the /code-walk skill: one folder, user-invoked, and a build that checks.

Three promises. The skill costs the agent's context nothing until the user types it, so it
must stay user-invoked and out of the always-loaded guides. A built page carries the commit it
was built from, because that stamp is where the next walk's diff starts. And a walk that no
longer matches the code fails the build instead of pointing at the wrong lines.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR  # noqa: E402

SKILL_DIR = CLAUDE_DIR / "skills" / "code-walk"
SCRIPTS = SKILL_DIR / "scripts"
WALK_PY = SKILL_DIR / "walk.py"

WALK = '''TITLE = "demo code walk"
VERSION = "v-test"

def ch():
    f = W.Fig(660, 200)
    f.box("hello", 20, 40, 200, 50, "hello()")
    return {"id": "hello", "title": "Hello", "svgs": [f.svg("hello")],
            "steps": [W.step("Greets", "`hello()` returns a word.", "app.py",
                             [W.span("app.py", "hello")], hl=["KEY"])]}

CHAPTERS = [ch]
'''


def run(*args, cwd):
    return subprocess.run([str(a) for a in args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class UserInvokedAndSelfContained(unittest.TestCase):
    def test_skill_is_user_invoked(self):
        head = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("disable-model-invocation: true", head)

    def test_the_always_loaded_guides_do_not_mention_it(self):
        for guide in (CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md"):
            self.assertNotIn("code-walk", guide.read_text(encoding="utf-8"), guide.name)

    def test_scripts_import_only_the_stdlib_and_each_other(self):
        local = {p.stem for p in SCRIPTS.glob("*.py")}
        for script in [WALK_PY, *SCRIPTS.glob("*.py")]:
            for line in script.read_text(encoding="utf-8").splitlines():
                if line.startswith(("import ", "from ")):
                    name = line.split()[1].split(".")[0]
                    self.assertTrue(name in sys.stdlib_module_names or name in local,
                                    f"{script.name}: {line}")


class BuildAndDiff(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
        self.git("init", "-q")
        self.commit("first")
        self.walk = self.repo / "docs" / "code-walk" / "v-test" / "code-walk.py"
        self.walk.parent.mkdir(parents=True)

    def git(self, *args):
        result = run("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                     "-c", "commit.gpgsign=false", *args, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def build(self, key):
        self.walk.write_text(WALK.replace("KEY", key), encoding="utf-8")
        return run(sys.executable, SCRIPTS / "walk_build.py", self.walk, "--rev", "HEAD",
                   cwd=self.repo)

    def test_page_is_one_offline_file_stamped_with_its_commit(self):
        base = self.git("rev-parse", "--short", "HEAD")
        result = self.build("hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.walk.parent / "code-walk.html").read_text(encoding="utf-8")
        self.assertIn(f'"commit":"{base}"', page)
        for remote in ('src="http', 'href="http', "@import", "url(http"):
            self.assertNotIn(remote, page, "the page must load nothing from the network")

    def test_a_step_that_lights_a_missing_box_fails_the_build(self):
        result = self.build("nope")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no box or edge", result.stderr)

    def test_walk_note_starts_from_the_commit_the_last_walk_was_built_on(self):
        self.assertEqual(self.build("hello").returncode, 0)
        base = self.git("rev-parse", "HEAD")
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "app.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
        self.commit("second")
        self.git("tag", "v2")
        result = run(sys.executable, WALK_PY, "note", "v2", cwd=self.repo / "docs")
        self.assertEqual(result.returncode, 0, result.stderr)
        note = (self.repo / "docs" / "code-walk" / "v2" / "WALK-NOTE.md").read_text(encoding="utf-8")
        self.assertIn(f"- base: `{base}`", note)
        self.assertIn(f"git diff -M {base} ", note)
        self.assertIn("| `new.py` | added | |", note)
        self.assertIn("| `app.py` | changed | |", note)
        self.assertNotIn("code-walk.html", note, "the walks folder is left out")
        again = run(sys.executable, WALK_PY, "note", "v2", cwd=self.repo)
        self.assertNotEqual(again.returncode, 0, "a filled-in note is never replaced silently")
        self.assertIn("--force", again.stderr)
        report = run(sys.executable, WALK_PY, "changes", "v2", cwd=self.repo)
        self.assertIn("changed: `hello`", report.stdout)

    def test_the_one_command_builds_a_tagged_version_from_its_own_code(self):
        tagged = self.git("rev-parse", "--short", "HEAD")
        self.git("tag", "v-test")
        (self.repo / "app.py").write_text("def hello():\n    return 'later'\n", encoding="utf-8")
        self.commit("after the tag")
        self.walk.write_text(WALK.replace("KEY", "hello"), encoding="utf-8")
        result = run(sys.executable, WALK_PY, "build", "v-test", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.walk.parent / "code-walk.html").read_text(encoding="utf-8")
        self.assertIn(f'"commit":"{tagged}"', page)
        self.assertIn("'hi'", page)
        self.assertNotIn("'later'", page, "a tagged version shows its own code")
        missing = run(sys.executable, WALK_PY, "build", "v-none", cwd=self.repo)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no walk file", missing.stderr)

    def test_handoff_keeps_one_meaning_in_this_system(self):
        """A handoff is the builder's envelope and the resume note; the walk's note is not one."""
        for path in [WALK_PY, *SKILL_DIR.glob("*.md"), *SCRIPTS.glob("*.py")]:
            text = path.read_text(encoding="utf-8").lower()
            if path.name != "UPSTREAM.md":
                self.assertNotIn("hand-off", text, path.name)
            self.assertNotIn("handoff", text, path.name)

    def test_a_crlf_checkout_of_an_unchanged_file_is_not_a_change(self):
        (self.repo / "app.py").write_bytes(b"def hello():\r\n    return 'hi'\r\n")
        report = run(sys.executable, WALK_PY, "changes", "--base", "HEAD", cwd=self.repo)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("0 changed", report.stdout)

    def test_no_walk_and_no_base_is_an_error_not_a_guess(self):
        result = run(sys.executable, SCRIPTS / "walk_diff.py", cwd=self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("give --old-rev", result.stderr)


if __name__ == "__main__":
    unittest.main()
