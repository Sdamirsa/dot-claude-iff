#!/usr/bin/env python3
"""test_export.py - the visibility knob, the managed .gitignore block, and `distctl export`.

The knob decides whether .claude/ reaches git; the block is how it acts, on every install path
(clone, fresh zip, adopt kit - the kit path used to deliver no safety net at all); the export is
the one deliberate way a clean copy reaches a public repository. Each behaviour below has a test
that can fail: the hard floor is attacked through publish.json, the secrets gate gets a planted
key, the mirror gets a hostile manifest, and every git call the export makes is recorded.

The key-shaped string is ASSEMBLED AT RUNTIME, as in test_secrets.py: a literal one in this file
would trip outside scanners.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import distctl  # noqa: E402

GIT = shutil.which("git")


def fake_github_token() -> str:
    return "gh" + "p_" + ("aB3dE5gH7jK9mN2pQ4sT6vW8yZ1cF0" * 2)[:36]


def run(fn, argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(argv)
    return code, buf.getvalue()


def snapshot(base: Path) -> dict:
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file()}


class KnobCase(FixtureCase):
    def set_visibility(self, value) -> None:
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        if value is None:
            cfg.pop("visibility", None)
        else:
            cfg["visibility"] = value
        self.write_config("memory", cfg)

    def set_include(self, globs) -> None:
        cfg = _lib.read_json(self.root / ".claude" / "config" / "publish.json", {}) or {}
        cfg["include"] = list(globs)
        self.write_config("publish", cfg)

    def plant(self, rel: str, text: str = "x\n", base: Path | None = None) -> Path:
        """LF on every OS (bytes, not text mode), so a test means the same thing everywhere."""
        path = (base or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def gitignore(self) -> str:
        return (self.root / ".gitignore").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the block

class TestGitignoreBlock(KnobCase):
    PER_USER = (".env", "*.env", ".env*.local", "settings.local.json", "__pycache__/", "*.pyc")
    DERIVED = (".claude/console/*.pid", ".claude/console/*.log", ".claude/state/heartbeat.json",
               ".claude/worktrees/")

    def test_tracked_ignores_only_per_user_and_derived_files(self):
        lines = distctl.render_gitignore_block("tracked").splitlines()
        self.assertEqual(lines[0], distctl.GITIGNORE_BEGIN)
        self.assertEqual(lines[-1], distctl.GITIGNORE_END)
        for rule in self.PER_USER + self.DERIVED:
            with self.subTest(rule=rule):
                self.assertIn(rule, lines)
        self.assertNotIn(".claude/", lines)
        self.assertNotIn(".claude-iff/", lines)

    def test_ignored_ignores_both_trees_and_keeps_the_safety_net(self):
        lines = distctl.render_gitignore_block("ignored").splitlines()
        for rule in (".claude/", ".claude-iff/") + self.PER_USER:
            with self.subTest(rule=rule):
                self.assertIn(rule, lines)

    def test_absent_knob_reads_tracked(self):
        self.set_visibility(None)
        self.assertEqual(_lib.visibility(self.root), "tracked")
        self.assertEqual(distctl.apply_gitignore(self.root), (True, "tracked"))
        self.assertEqual(self.gitignore(), distctl.render_gitignore_block("tracked"))

    def test_unknown_value_refuses_and_writes_nothing(self):
        self.set_visibility("public")
        with self.assertRaises(_lib.LibError):
            distctl.apply_gitignore(self.root)
        code, out = run(distctl.main, ["gitignore", "--apply"])
        self.assertEqual(code, 2)
        self.assertIn("GITIGNORE_FAIL", out)
        self.assertFalse((self.root / ".gitignore").exists())

    def test_apply_appends_after_the_users_lines_and_is_idempotent(self):
        mine = "node_modules/\n# keep me\n*.log\n"
        self.plant(".gitignore", mine)
        self.assertEqual(distctl.apply_gitignore(self.root), (True, "tracked"))
        text = self.gitignore()
        self.assertTrue(text.startswith(mine), "the user's lines must stay exactly as they were")
        self.assertTrue(text.endswith(distctl.render_gitignore_block("tracked")))
        before = (self.root / ".gitignore").read_bytes()
        self.assertEqual(distctl.apply_gitignore(self.root), (False, "tracked"))
        self.assertEqual((self.root / ".gitignore").read_bytes(), before)

    def test_a_visibility_switch_rewrites_only_the_block(self):
        """Mixed line endings on purpose: a CRLF line above, an LF line the user added below.
        Not one byte outside the block may change, in either direction."""
        path = self.root / ".gitignore"
        path.write_bytes(b"top/\r\n")
        distctl.apply_gitignore(self.root)
        with open(path, "ab") as fh:
            fh.write(b"\nbottom/\n")  # a rule the user added after the block
        original = path.read_bytes()
        self.set_visibility("ignored")
        self.assertEqual(distctl.apply_gitignore(self.root), (True, "ignored"))
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"top/\r\n") and raw.endswith(b"\r\n\nbottom/\n"), raw[-40:])
        text = self.gitignore()
        self.assertIn(distctl.render_gitignore_block("ignored"), text)
        self.assertEqual(text.count(distctl.GITIGNORE_BEGIN), 1)
        self.set_visibility("tracked")
        distctl.apply_gitignore(self.root)
        self.assertEqual(path.read_bytes(), original)

    def test_crlf_checkout_stays_crlf(self):
        (self.root / ".gitignore").write_bytes(b"build-cache/\r\n")
        distctl.apply_gitignore(self.root)
        raw = (self.root / ".gitignore").read_bytes()
        self.assertTrue(raw.startswith(b"build-cache/\r\n"))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "a bare LF was mixed into a CRLF file")
        self.assertEqual(distctl.apply_gitignore(self.root)[0], False)

    def test_the_old_kit_lines_are_swapped_for_the_block(self):
        self.plant(".gitignore", "mine/\n\n" + distctl.LEGACY_GITIGNORE)
        distctl.apply_gitignore(self.root)
        text = self.gitignore()
        self.assertEqual(text, "mine/\n\n" + distctl.render_gitignore_block("tracked"))

    def test_a_half_block_refuses(self):
        self.plant(".gitignore", "mine/\n" + distctl.GITIGNORE_BEGIN + "\n.env\n")
        before = (self.root / ".gitignore").read_bytes()
        with self.assertRaises(_lib.LibError):
            distctl.apply_gitignore(self.root)
        self.assertEqual((self.root / ".gitignore").read_bytes(), before)

    def test_report_mode_writes_nothing_and_exits_1_until_applied(self):
        code, out = run(distctl.main, ["gitignore"])
        self.assertEqual(code, 1)
        self.assertIn("run with --apply", out)
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertEqual(run(distctl.main, ["gitignore", "--apply"])[0], 0)
        code, out = run(distctl.main, ["gitignore"])
        self.assertEqual(code, 0)
        self.assertIn("GITIGNORE_OK", out)


# --------------------------------------------------------------------------- install paths

class TestInstallPaths(KnobCase):
    """Clone, fresh zip and adopt kit must all end with the same block, written by the target's
    own installed distctl (what /adopt Phase 3 runs). Real tools, real zips, a real subprocess."""

    def setUp(self):
        super().setUp()
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        cfg["distribution"] = {"enabled": True}
        self.write_config("memory", cfg)
        tools = self.root / ".claude" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        for name in ("_lib.py", "distctl.py", "checkctl.py"):
            shutil.copy(CLAUDE_DIR / "tools" / name, tools / name)
        distctl.build(self.root, quiet=True)
        self.dist = self.root / ".claude" / "dist"
        self.base = self.root.parent

    def target(self, name: str, user_lines: str | None) -> Path:
        target = self.base / name
        target.mkdir()
        if user_lines is not None:
            (target / ".gitignore").write_text(user_lines, encoding="utf-8")
        return target

    def answer_and_apply(self, target: Path, visibility: str) -> str:
        cfg_path = target / ".claude" / "config" / "memory.json"
        cfg = _lib.read_json(cfg_path, {})
        cfg["visibility"] = visibility
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target))
        res = subprocess.run([sys.executable, str(target / ".claude" / "tools" / "distctl.py"),
                              "gitignore", "--apply"], env=env, capture_output=True, text=True,
                             timeout=60, check=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return (target / ".gitignore").read_text(encoding="utf-8")

    def test_fresh_zip_gitignore_is_the_block(self):
        with zipfile.ZipFile(self.dist / "dot-claude-iff-fresh.zip") as z:
            text = z.read(".gitignore").decode()
        lines = text.splitlines()
        self.assertEqual((lines[0], lines[-1]), (distctl.GITIGNORE_BEGIN, distctl.GITIGNORE_END),
                         "the fresh zip must carry the managed block, markers and all")
        self.assertIn(".env*.local", lines)
        self.assertEqual(text, distctl.render_gitignore_block("tracked"))
        with zipfile.ZipFile(self.dist / "dot-claude-iff-adopt-kit.zip") as z:
            self.assertEqual(z.read("dot-claude-iff-kit/.gitignore").decode(),
                             distctl.render_gitignore_block("tracked"))

    def test_every_path_ends_with_the_same_block_for_both_values(self):
        mine = "node_modules/\n.venv/\n"
        for visibility in ("tracked", "ignored"):
            block = distctl.render_gitignore_block(visibility)
            results = {}
            # Kit: unzip outside the target, copy its .claude/ in (Phase 3), apply.
            kit_dir = self.base / f"kit-{visibility}"
            with zipfile.ZipFile(self.dist / "dot-claude-iff-adopt-kit.zip") as z:
                z.extractall(kit_dir)
            kit_target = self.target(f"kit-target-{visibility}", mine)
            shutil.copytree(kit_dir / "dot-claude-iff-kit" / ".claude", kit_target / ".claude")
            self.assertNotIn(".env", (kit_target / ".gitignore").read_text().splitlines(),
                             "precondition: the target starts without the safety net")
            results["kit"] = self.answer_and_apply(kit_target, visibility)
            # Fresh zip: unzipped at the root of a new repo; its .gitignore is rewritten in place.
            fresh = self.target(f"fresh-{visibility}", None)
            with zipfile.ZipFile(self.dist / "dot-claude-iff-fresh.zip") as z:
                z.extractall(fresh)
            results["fresh"] = self.answer_and_apply(fresh, visibility)
            # Clone: the source's .claude/ copied into an existing repo, then applied.
            clone_target = self.target(f"clone-{visibility}", mine)
            shutil.copytree(self.root / ".claude", clone_target / ".claude",
                            ignore=shutil.ignore_patterns("dist", "state", "__pycache__"))
            results["clone"] = self.answer_and_apply(clone_target, visibility)
            for path, text in results.items():
                with self.subTest(visibility=visibility, path=path):
                    self.assertTrue(text.endswith(block), f"{path} does not end with the block")
                    self.assertEqual(text.count(distctl.GITIGNORE_BEGIN), 1)
                    self.assertIn(".env", text.splitlines(), "the secrets safety net is missing")
            self.assertTrue(results["kit"].startswith(mine) and results["clone"].startswith(mine),
                            "the target's own lines must survive")
            self.assertEqual(results["fresh"], block)


# --------------------------------------------------------------------------- shadowing check

@unittest.skipUnless(GIT, "git not available")
class TestShadowingRespectsVisibility(KnobCase):
    def setUp(self):
        super().setUp()
        if self.git("init", "-q").returncode != 0:
            self.skipTest("git init failed")
        self.plant(".claude/STATUS.md", "status\n")
        self.plant(".claude-iff/README.md", "readme\n")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True,
                              check=False)

    def test_tracked_still_warns_on_an_ignored_tree(self):
        self.plant(".gitignore", ".claude/\n")
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.WARN)

    def test_ignored_with_the_block_is_quiet(self):
        self.set_visibility("ignored")
        distctl.apply_gitignore(self.root)
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.SKIP, result.message)
        self.assertIn("visibility is ignored", result.message)

    def test_ignored_without_the_block_says_so(self):
        self.set_visibility("ignored")
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertIn("gitignore --apply", result.message)

    def test_ignored_but_still_tracked_names_the_human_step(self):
        self.set_visibility("ignored")
        distctl.apply_gitignore(self.root)
        self.assertEqual(self.git("add", "-f", ".claude/STATUS.md").returncode, 0)
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertIn("git rm -r --cached", result.message)
        self.assertIn(".claude/STATUS.md", result.details)

    def test_unknown_visibility_warns(self):
        self.set_visibility("sometimes")
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.WARN)


# --------------------------------------------------------------------------- export

class TestHardFloor(unittest.TestCase):
    """The always-exclude list, one path per entry, plus controls that must still export."""

    FLOOR = (".claude/state/journal.jsonl", ".claude/state/handshakes/T1.json",
             ".claude/Project-log.jsonl", ".claude/LESSONS.jsonl", ".claude/STATUS.md",
             ".claude/tasks/_template.md", ".claude/research/notes.md",
             ".claude/worktrees/t1/.claude/tools/x.py", ".claude/reference/private/brand.md",
             ".claude-iff/obs/anchor.json", ".claude-iff/obs/rollups/2026-01-01.json",
             "settings.local.json", ".claude/settings.local.json", "app/settings.local.json",
             ".env", "web/.env", "prod.env", "config/staging.env", ".env.local",
             "web/.env.production.local", ".claude-export-manifest.json", "vendor/.git/config")
    CONTROLS = (".claude/skills/x/SKILL.md", ".claude/settings.json", ".env.production",
                ".claude-iff/README.md", "README.md", ".claude/reference/glossary.md")

    def test_every_floor_entry_is_excluded(self):
        for rel in self.FLOOR:
            with self.subTest(path=rel):
                self.assertTrue(distctl.always_excluded(rel))

    def test_controls_are_not(self):
        for rel in self.CONTROLS:
            with self.subTest(path=rel):
                self.assertFalse(distctl.always_excluded(rel))


@unittest.skipUnless(GIT, "git not available")
class ExportCase(KnobCase):
    def setUp(self):
        super().setUp()
        if self.git("init", "-q").returncode != 0:
            self.skipTest("git init failed")
        self.target = self.root.parent / "public"
        self.target.mkdir()
        for rel in ("README.md", "src/app.py", "docs/guide.md",
                    ".claude/skills/demo/SKILL.md", ".claude/agents/helper.md",
                    ".claude/STATUS.md", ".claude/Project-log.jsonl", ".claude/LESSONS.jsonl",
                    ".claude/tasks/20260101-work.md", ".claude/research/r.md",
                    ".claude/state/journal.jsonl", ".claude/reference/private/brand.md",
                    ".claude/worktrees/t1/.claude/STATUS.md", ".claude/settings.local.json",
                    ".claude-iff/README.md", ".claude-iff/obs/rollups/2026-01-01.json"):
            self.plant(rel, f"content of {rel}\n")
        distctl.apply_gitignore(self.root)  # tracked: per-user and derived files ignored

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True,
                              check=False)

    def export(self, *extra) -> tuple[int, str]:
        return run(distctl.main, ["export", "--to", str(self.target), *extra])

    def exported(self) -> set:
        return set(snapshot(self.target)) - {distctl.EXPORT_MANIFEST}

    def manifest(self) -> dict:
        return json.loads((self.target / distctl.EXPORT_MANIFEST).read_text(encoding="utf-8"))


class TestExportCopies(ExportCase):
    def test_project_exports_and_claude_stays_back_by_default(self):
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertIn("EXPORT_OK", out)
        self.assertEqual(self.exported(), {"README.md", "src/app.py", "docs/guide.md", ".gitignore"})
        self.assertIn("publish.json does not include", out)

    def test_include_publishes_exactly_what_it_lists(self):
        self.set_include([".claude/skills/", ".claude/agents/*.md", ".claude-iff/README.md"])
        self.assertEqual(self.export()[0], 0)
        got = {p for p in self.exported() if p.startswith(".claude")}
        self.assertEqual(got, {".claude/skills/demo/SKILL.md", ".claude/agents/helper.md",
                               ".claude-iff/README.md"})

    def test_publish_json_cannot_widen_past_the_floor(self):
        self.set_include(["*", ".claude/", ".claude-iff/", ".claude/state/", ".claude/tasks/",
                          ".claude/research/", ".claude/worktrees/", ".claude/reference/private/",
                          ".claude-iff/obs/", ".claude/STATUS.md", ".claude/*.jsonl",
                          ".claude/settings.local.json"])
        code, out = self.export()
        self.assertEqual(code, 0, out)
        got = self.exported()
        for rel in (".claude/STATUS.md", ".claude/Project-log.jsonl", ".claude/LESSONS.jsonl",
                    ".claude/tasks/20260101-work.md", ".claude/research/r.md",
                    ".claude/state/journal.jsonl", ".claude/reference/private/brand.md",
                    ".claude/worktrees/t1/.claude/STATUS.md", ".claude/settings.local.json",
                    ".claude-iff/obs/rollups/2026-01-01.json"):
            with self.subTest(path=rel):
                self.assertNotIn(rel, got)
        self.assertIn(".claude/skills/demo/SKILL.md", got, "the include itself still works")
        self.assertIn("held back (always excluded): .claude/STATUS.md", out)

    def test_tracked_lets_git_veto_an_ignored_file(self):
        self.plant(".claude/skills/draft/SKILL.md", "unfinished\n")
        with open(self.root / ".gitignore", "ab") as fh:
            fh.write(b".claude/skills/draft/\n")
        self.set_include([".claude/skills/"])
        self.assertEqual(self.export()[0], 0)
        self.assertIn(".claude/skills/demo/SKILL.md", self.exported())
        self.assertNotIn(".claude/skills/draft/SKILL.md", self.exported())

    def test_ignored_reads_claude_from_the_filesystem(self):
        self.set_visibility("ignored")
        distctl.apply_gitignore(self.root)
        listed = self.git("ls-files", "--others", "--exclude-standard").stdout
        self.assertNotIn(".claude/", listed, "precondition: git no longer lists .claude/")
        self.set_include([".claude/skills/", ".claude/"])
        code, out = self.export()
        self.assertEqual(code, 0, out)
        got = self.exported()
        self.assertIn(".claude/skills/demo/SKILL.md", got)
        self.assertIn(".claude/agents/helper.md", got)
        self.assertNotIn(".claude/reference/private/brand.md", got)
        self.assertNotIn(".claude/STATUS.md", got)
        exported_ignore = (self.target / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(distctl.render_gitignore_block("tracked"), exported_ignore,
                      "the release repo must not inherit a rule hiding what was published")
        self.assertIn(distctl.render_gitignore_block("ignored"), self.gitignore(),
                      "the source's own .gitignore is never rewritten by an export")

    def test_mcp_json_exports_only_without_literal_credentials(self):
        self.plant(".mcp.json", json.dumps({"mcpServers": {"gh": {
            "command": "gh-mcp", "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}}}) + "\n")
        self.assertEqual(self.export()[0], 0)
        self.assertIn(".mcp.json", self.exported())
        literal = "plain" + "Literal" + "Value77"
        self.plant(".mcp.json", json.dumps({"mcpServers": {"svc": {
            "command": "svc", "env": {"SERVICE_API_KEY": literal}}}}) + "\n")
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertNotIn(".mcp.json", self.exported())
        self.assertIn(".mcp.json holds literal credential values", out)
        self.assertNotIn(literal, out)


class TestManifestAndMirror(ExportCase):
    def test_manifest_lists_paths_version_and_a_date_only(self):
        self.assertEqual(self.export()[0], 0)
        data = self.manifest()
        self.assertEqual(set(data), {"_comment", "source_system_version", "date", "files"})
        self.assertEqual(data["files"], sorted(self.exported()))
        registry = _lib.read_json(self.root / ".claude" / "config" / "registry.json", {})
        self.assertEqual(data["source_system_version"], registry["system_version"])
        self.assertRegex(data["date"], r"^\d{4}-\d{2}-\d{2}$")
        text = (self.target / distctl.EXPORT_MANIFEST).read_text(encoding="utf-8")
        for machine in (str(self.root), self.root.as_posix(), str(Path.home())):
            self.assertNotIn(machine, text)

    def test_an_unchanged_export_leaves_everything_alone(self):
        self.assertEqual(self.export()[0], 0)
        before = snapshot(self.target)
        code, out = self.export()
        self.assertEqual(code, 0)
        self.assertIn("0 to add, 0 to update, 0 to remove", out)
        self.assertEqual(snapshot(self.target), before, "the manifest was rewritten for nothing")

    def test_mirror_drops_what_left_and_keeps_what_it_never_listed(self):
        self.assertEqual(self.export()[0], 0)
        self.plant("ci.yml", "the public repo's own file\n", base=self.target)
        self.plant(".git/HEAD", "ref: refs/heads/main\n", base=self.target)
        (self.root / "src" / "app.py").unlink()
        self.plant("src/new.py", "new\n")
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertIn("remove src/app.py", re.sub(r"\s+", " ", out))
        self.assertFalse((self.target / "src" / "app.py").exists())
        self.assertTrue((self.target / "src" / "new.py").exists())
        self.assertTrue((self.target / "ci.yml").exists(), "a file the manifest never listed was removed")
        self.assertTrue((self.target / ".git" / "HEAD").exists())
        self.assertNotIn("src/app.py", self.manifest()["files"])

    def test_a_hostile_manifest_cannot_reach_outside_or_into_git(self):
        outside = self.plant("outside.txt", "keep\n", base=self.root.parent)
        git_config = self.plant(".git/config", "[core]\n", base=self.target)
        hostile = ["../outside.txt", ".git/config", "a/../../outside.txt", "/etc/hostname",
                   "C:/Windows/win.ini", "src\\..\\..\\outside.txt", distctl.EXPORT_MANIFEST, 42]
        (self.target / distctl.EXPORT_MANIFEST).write_text(
            json.dumps({"files": hostile}), encoding="utf-8")
        code, out = self.export()
        self.assertEqual(code, 0, out)
        self.assertTrue(outside.exists())
        self.assertTrue(git_config.exists())
        self.assertIn("ignored unsafe manifest entry", out)

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        self.assertEqual(self.export()[0], 0)
        (self.root / "src" / "app.py").unlink()
        self.plant("src/new.py", "new\n")
        self.plant("README.md", "changed\n")
        before = snapshot(self.target)
        code, out = self.export("--dry-run")
        self.assertEqual(code, 0, out)
        flat = re.sub(r"\s+", " ", out)
        for line in ("add src/new.py", "update README.md", "remove src/app.py",
                     "dry run: nothing written"):
            with self.subTest(line=line):
                self.assertIn(line, flat)
        self.assertEqual(snapshot(self.target), before)


class TestRefusals(ExportCase):
    def assert_refused(self, target: Path, needle: str) -> None:
        before = snapshot(self.root.parent)
        code, out = run(distctl.main, ["export", "--to", str(target)])
        self.assertNotEqual(code, 0, out)
        self.assertIn("EXPORT_FAIL", out)
        self.assertIn(needle, out)
        self.assertEqual(snapshot(self.root.parent), before, "a refused export wrote something")

    def test_missing_target(self):
        self.assert_refused(self.root.parent / "nowhere", "does not exist")
        self.assertFalse((self.root.parent / "nowhere").exists())

    def test_target_is_a_file(self):
        self.assert_refused(self.plant("a-file", base=self.root.parent), "not a directory")

    def test_target_is_the_project(self):
        self.assert_refused(self.root, "the project itself")

    def test_target_inside_the_project(self):
        (self.root / "public-copy").mkdir()
        self.assert_refused(self.root / "public-copy", "inside the project")

    def test_project_inside_the_target(self):
        self.assert_refused(self.root.parent, "project is inside the target")

    def test_source_not_a_git_work_tree(self):
        shutil.rmtree(self.root / ".git")
        self.assert_refused(self.target, "not the top of a git work tree")

    def test_unknown_visibility(self):
        self.set_visibility("half")
        self.assert_refused(self.target, "visibility")

    def test_a_folder_where_a_file_goes(self):
        (self.target / "README.md").mkdir()
        self.assert_refused(self.target, "README.md")

    def test_planted_key_refuses_with_path_line_and_pattern_only(self):
        token = fake_github_token()
        self.plant("src/config.py", f"import os\nTOKEN = '{token}'\n")
        self.assert_refused(self.target, "FAIL src/config.py:2 github_token")
        code, out = self.export()
        self.assertNotIn(token, out)
        self.assertNotIn(token[4:20], out)

    def test_a_key_in_a_held_back_file_does_not_block(self):
        """The gate scans what would be published, not the whole project."""
        self.plant(".claude/research/scratch.md", f"key {fake_github_token()}\n")
        code, out = self.export()
        self.assertEqual(code, 0, out)


class TestNoGitWrites(ExportCase):
    def test_only_read_only_git_runs_and_the_targets_git_is_untouched(self):
        self.plant(".git/HEAD", "ref: refs/heads/main\n", base=self.target)
        self.set_include([".claude/skills/"])
        real_run = subprocess.run
        calls = []

        def spy(argv, *args, **kwargs):
            if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).stem == "git":
                calls.append(list(argv[1:]))
            return real_run(argv, *args, **kwargs)

        git_before = snapshot(self.target / ".git")
        with mock.patch.object(subprocess, "run", side_effect=spy):
            code, out = self.export()
            self.assertEqual(code, 0, out)
            (self.root / "README.md").unlink()
            self.assertEqual(self.export()[0], 0)
        self.assertTrue(calls, "the spy saw no git call at all; it is not watching")
        for argv in calls:
            sub = next(a for a in argv if not a.startswith("-"))
            with self.subTest(git=argv):
                self.assertIn(sub, ("ls-files", "rev-parse"))
        self.assertEqual(snapshot(self.target / ".git"), git_before)


class TestRegistration(KnobCase):
    def test_knobs_have_registry_cards_and_the_lint_binds_them(self):
        keys = {e["key"]: e for e in _lib.load_config("registry")["entries"]}
        self.assertEqual(keys["memory.visibility"]["enum"], list(_lib.VISIBILITY_VALUES))
        self.assertEqual(keys["publish.include"]["default"], [])
        shutil.copytree(CLAUDE_DIR / "agents", self.root / ".claude" / "agents")
        result = checkctl.check_config_registry()
        self.assertNotEqual(result.status, checkctl.FAIL, result.details)
        self.assertFalse([d for d in result.details if "visibility" in d or "publish." in d],
                         result.details)

    def test_shipped_defaults(self):
        self.assertEqual(_lib.read_json(CLAUDE_DIR / "config" / "memory.json", {})
                         .get("visibility"), "tracked")
        publish = _lib.read_json(CLAUDE_DIR / "config" / "publish.json", None)
        self.assertIsInstance(publish.get("include"), list)
        self.assertIn("only", " ".join(publish["_comment"]).lower(),
                      "the comment must say the list can only narrow")

    def test_cards_and_probe(self):
        cards = CLAUDE_DIR / "system-map" / "cards"
        for card_id, path in (("config.publish", ".claude/config/publish.json"),
                              ("reference.public-private", ".claude/reference/public-private.md")):
            with self.subTest(card=card_id):
                card = json.loads((cards / f"{card_id}.json").read_text(encoding="utf-8"))
                self.assertEqual(card["path"], path)
        names = {r.name for r in checkctl.probe()}
        self.assertTrue({"config.publish", "reference.public-private"} <= names)

    def test_reference_doc_names_the_pattern_and_the_commands(self):
        text = (CLAUDE_DIR / "reference" / "public-private.md").read_text(encoding="utf-8")
        for needle in ("private", "public", "distctl.py export --to", "--dry-run",
                       "gitignore --apply", "never commits", "RECORD_ROOT", "tracked", "ignored",
                       "publish.json", distctl.EXPORT_MANIFEST):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_adopt_skill_asks_visibility_and_applies_the_block(self):
        text = (CLAUDE_DIR / "skills" / "adopt" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("Track `.claude/` in git?", text)
        for needle in ("gh repo view --json visibility", "PUBLIC", "distctl.py gitignore --apply",
                       "reference/public-private.md", "distctl.py export"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
