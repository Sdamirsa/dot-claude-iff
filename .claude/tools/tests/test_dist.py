#!/usr/bin/env python3
"""test_dist.py - the distribution zips must carry the system and never this project's history.

A zip that leaks the source's journal, queue, or filled CLAUDE.md hands every adopter another
project's memory; a zip that goes stale hands them last month's system. So: exclusions proven,
placeholder form proven, determinism proven (identical content, identical bytes, write-gated,
whatever line endings the checkout used), and - in the home repo - the committed zips proven
equal to a rebuild, byte for byte.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import REPO_ROOT, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import distctl  # noqa: E402

GIT = shutil.which("git")
_COMMIT = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
           "commit", "-q", "--allow-empty", "-m"]


def _home_repo() -> bool:
    """The repo running this suite is dot-claude-iff's own source (distribution.enabled true
    in its REAL memory.json). Tests of the committed zips, the workflows and release-flow.md
    are about that repo; in an adopting project those files are absent by design."""
    cfg = _lib.read_json(REPO_ROOT / ".claude" / "config" / "memory.json", {}) or {}
    return bool((cfg.get("distribution") or {}).get("enabled", False))


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          check=False)


def uncommitted_payload(root: Path) -> list | None:
    """Payload sources with uncommitted edits (modified, staged, deleted or untracked), or None
    when git cannot say. Edits waiting for the ritual are not staleness yet: POLISH rebuilds
    the zips and PUBLISH commits both together."""
    res = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
               "--", ".claude", ".claude-iff/README.md")
    if res.returncode != 0:
        return None
    paths = []
    for token in res.stdout.split("\0"):
        if not token:
            continue
        # "XY path"; a rename's second token is the bare old path. Both count.
        paths.append(token[3:] if token[2:3] == " " else token)
    return sorted(p for p in paths if distctl.payload_source(p))


def zip_verdict(root: Path) -> tuple[str, list]:
    """("fresh", []) when the committed zips equal a rebuild; ("pending", edits) when they
    differ only because payload edits are not committed yet; ("stale", names) when the
    COMMITTED payload no longer matches the committed zips - the state CI must never pass."""
    stale = distctl.stale_zips(root)
    if not stale:
        return "fresh", []
    pending = uncommitted_payload(root)
    if pending:
        return "pending", pending
    return "stale", stale


class DistCase(FixtureCase):
    def setUp(self):
        super().setUp()
        claude = self.root / ".claude"
        # The fixture IS a home repo: set the knob explicitly rather than inheriting whatever
        # the repo running the suite ships (an adopting project ships it false).
        cfg = _lib.read_json(claude / "config" / "memory.json", {}) or {}
        cfg["distribution"] = {"enabled": True}
        self.write_config("memory", cfg)
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

    def test_worktrees_never_ship(self):
        """A worktree is a whole checkout of the repo (journal, filled CLAUDE.md, zips) parked
        under .claude/worktrees/; it must be pruned structurally, git or no git."""
        wt = self.root / ".claude" / "worktrees" / "t9" / ".claude"
        (wt / "tools").mkdir(parents=True)
        (wt / "tools" / "leak.py").write_text("WORKTREE COPY\n", encoding="utf-8")
        (wt / "CLAUDE.md").write_text("# filled guide of a worktree\n", encoding="utf-8")
        distctl.build(self.root)
        for zip_name in distctl.ZIP_NAMES:
            with self.subTest(zip=zip_name):
                self.assertFalse(any("worktrees" in n for n in self.names(zip_name)))

    def test_home_only_file_never_ships(self):
        ref = self.root / ".claude" / "reference"
        ref.mkdir(parents=True, exist_ok=True)
        (ref / "release-flow.md").write_text("# dev/main flow of THE SOURCE\n", encoding="utf-8")
        (ref / "glossary.md").write_text("ships\n", encoding="utf-8")
        distctl.build(self.root)
        for zip_name, prefix in (("dot-claude-iff-fresh.zip", ""),
                                 ("dot-claude-iff-adopt-kit.zip", "dot-claude-iff-kit/")):
            with self.subTest(zip=zip_name):
                names = self.names(zip_name)
                self.assertNotIn(f"{prefix}.claude/reference/release-flow.md", names)
                self.assertIn(f"{prefix}.claude/reference/glossary.md", names)

    def test_text_ships_lf_and_bytes_do_not_depend_on_checkout_eol(self):
        """A Windows checkout (core.autocrlf) reads CRLF where Linux reads LF. The same commit
        must build the same zip on both, and a .sh must reach bash with LF endings."""
        hooks = self.root / ".claude" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        files = {hooks / "beat.sh": b"#!/usr/bin/env bash\nset -u\nexit 0\n",
                 self.root / ".claude" / "reference" / "doc.md": b"# doc\n\nline\n"}
        blob = b"\x00\x01binary\r\nkept\r\n"
        (self.root / ".claude" / "reference").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "reference" / "blob.bin").write_bytes(blob)

        def build_with(eol: bytes, out: Path) -> dict:
            for path, data in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.replace(b"\n", eol))
            distctl.build(self.root, out_dir=out)
            return {n: (out / n).read_bytes() for n in distctl.ZIP_NAMES}

        lf = build_with(b"\n", self.root / "out-lf")
        crlf = build_with(b"\r\n", self.root / "out-crlf")
        self.assertEqual(lf, crlf, "zip bytes changed with the checkout's line endings")
        with zipfile.ZipFile(self.root / "out-crlf" / "dot-claude-iff-fresh.zip") as z:
            self.assertEqual(z.read(".claude/hooks/beat.sh"), files[hooks / "beat.sh"])
            self.assertNotIn(b"\r\n", z.read(".claude/reference/doc.md"))
            self.assertEqual(z.read(".claude/reference/blob.bin"), blob,
                             "a file holding NUL bytes is binary and must pass untouched")

    def test_headers_do_not_name_the_building_os(self):
        """ZipInfo stamps create_system from the BUILDING OS (0 on Windows), which changed the
        bytes per OS and made unzip drop the .sh mode bits; deflate would tie the bytes to the
        zlib build. Both are pinned."""
        hooks = self.root / ".claude" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "beat.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        distctl.build(self.root)
        with zipfile.ZipFile(self.root / ".claude" / "dist" / "dot-claude-iff-fresh.zip") as z:
            for info in z.infolist():
                with self.subTest(entry=info.filename):
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.getinfo(".claude/hooks/beat.sh").external_attr >> 16, 0o755)

    def test_shipped_kits_land_with_no_project_steps(self):
        """Mechanism 3: this repo's project_steps (its own suite as a CHECK step) must not run
        in an adopter's ritual."""
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        cfg["project_steps"] = {"_comment": "kept", "check": [
            {"name": "test_suite", "kind": "check", "argv": ["python3", "x.py"]}],
            "polish": [{"name": "p", "argv": ["true"]}], "generator": []}
        self.write_config("memory", cfg)
        distctl.build(self.root)
        for zip_name, entry in (("dot-claude-iff-fresh.zip", ".claude/config/memory.json"),
                                ("dot-claude-iff-adopt-kit.zip",
                                 "dot-claude-iff-kit/.claude/config/memory.json")):
            with self.subTest(zip=zip_name):
                steps = json.loads(self.read(zip_name, entry))["project_steps"]
                self.assertEqual(steps["check"], [])
                self.assertEqual(steps["polish"], [])
                self.assertEqual(steps["_comment"], "kept", "the shape still ships")

    def test_shipped_gitignore_untracks_heartbeat_and_worktrees(self):
        distctl.build(self.root)
        for zip_name, entry in (("dot-claude-iff-fresh.zip", ".gitignore"),
                                ("dot-claude-iff-adopt-kit.zip", "dot-claude-iff-kit/.gitignore")):
            with self.subTest(zip=zip_name):
                lines = self.read(zip_name, entry).splitlines()
                self.assertIn(".claude/state/heartbeat.json", lines)
                self.assertIn(".claude/worktrees/", lines)

    def test_shipped_gitignore_is_the_managed_block_whatever_the_home_visibility(self):
        """One renderer for every install path: the kits carry the block for the visibility
        they ship (tracked), never this repo's own choice."""
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        cfg["visibility"] = "ignored"
        self.write_config("memory", cfg)
        distctl.build(self.root)
        block = distctl.render_gitignore_block("tracked")
        for zip_name, entry in (("dot-claude-iff-fresh.zip", ".gitignore"),
                                ("dot-claude-iff-adopt-kit.zip", "dot-claude-iff-kit/.gitignore")):
            with self.subTest(zip=zip_name):
                self.assertEqual(self.read(zip_name, entry), block)

    def test_stale_zips_red_after_an_edit_green_after_a_rebuild(self):
        distctl.build(self.root)
        self.assertEqual(distctl.stale_zips(self.root), [])
        (self.root / ".claude" / "tasks" / "_template.md").write_text(
            "# Task: {{TITLE}} (edited)\n", encoding="utf-8")
        self.assertEqual(sorted(distctl.stale_zips(self.root)), sorted(distctl.ZIP_NAMES),
                         "a payload edit without a rebuild must read stale")
        distctl.build(self.root)
        self.assertEqual(distctl.stale_zips(self.root), [])

    def test_verify_writes_nothing(self):
        distctl.build(self.root)
        before = {n: (self.root / ".claude" / "dist" / n).read_bytes() for n in distctl.ZIP_NAMES}
        (self.root / ".claude" / "tasks" / "_template.md").write_text("# edited\n", encoding="utf-8")
        self.assertTrue(distctl.stale_zips(self.root))
        after = {n: (self.root / ".claude" / "dist" / n).read_bytes() for n in distctl.ZIP_NAMES}
        self.assertEqual(before, after, "verify must compare, never rebuild in place")


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

    def test_shipped_kits_land_publishing_nothing_and_tracked(self):
        """Mechanism 3: what this repo publishes from .claude/ and whether it tracks .claude/
        are its own calls. The kits land with publish.json's include empty and visibility at
        the default, so an adopter starts from "publish nothing" and /adopt asks the rest."""
        self.write_config("publish", {"_comment": ["kept"], "include": [".claude/skills/", ".claude/*.md"]})
        cfg = _lib.read_json(self.root / ".claude" / "config" / "memory.json", {}) or {}
        cfg["visibility"] = "ignored"
        self.write_config("memory", cfg)
        distctl.build(self.root)
        for prefix, zip_name in (("", "dot-claude-iff-fresh.zip"),
                                 ("dot-claude-iff-kit/", "dot-claude-iff-adopt-kit.zip")):
            with self.subTest(zip=zip_name):
                publish = json.loads(self.read(zip_name, f"{prefix}.claude/config/publish.json"))
                self.assertEqual(publish["include"], [])
                self.assertEqual(publish["_comment"], ["kept"], "the shape still ships")
                memory = json.loads(self.read(zip_name, f"{prefix}.claude/config/memory.json"))
                self.assertEqual(memory["visibility"], "tracked")

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
    """The payload rule: the working tree decides which files ship and what they contain;
    git only vetoes what it ignores. A gitignored file under .claude/ (the private reference
    tree that leaked in the field) must never reach the zips, and what the veto keeps out is
    reported, never silent. An untracked, NOT ignored file ships: the ritual's PUBLISH tracks
    it with `git add -A` right after POLISH builds the zips."""

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

    def test_gitignored_file_does_not_ship_and_is_reported(self):
        import contextlib
        import io
        ref = self.root / ".claude" / "reference"
        ref.mkdir(parents=True, exist_ok=True)
        (ref / "tracked.md").write_text("in\n", encoding="utf-8")
        (ref / "scratch.md").write_text("out\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".claude/reference/scratch.md\n", encoding="utf-8")
        self._git("add", "-A")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            distctl.build(self.root)
        names = self.names("dot-claude-iff-fresh.zip")
        self.assertIn(".claude/reference/tracked.md", names)
        self.assertNotIn(".claude/reference/scratch.md", names)
        self.assertIn("scratch.md", out.getvalue(),
                      "a file the ignore veto keeps out must be named, never silently dropped")

    def test_a_file_created_in_session_ships_in_the_same_ritual(self):
        """The staleness root cause. Under the old index-only rule a new, still-untracked file
        was skipped, the ledger stamped the (working-tree) inputs fresh, PUBLISH's `git add -A`
        then tracked the file, and the zips lacked it until some unrelated input changed."""
        ref = self.root / ".claude" / "reference"
        ref.mkdir(parents=True, exist_ok=True)
        (ref / "old.md").write_text("tracked\n", encoding="utf-8")
        self._git("add", "-A")
        (ref / "new-this-session.md").write_text("new\n", encoding="utf-8")  # untracked
        distctl.build(self.root)                                              # POLISH
        self.assertIn(".claude/reference/new-this-session.md",
                      self.names("dot-claude-iff-fresh.zip"))
        self._git("add", "-A")                                                # PUBLISH
        self.assertEqual(distctl.stale_zips(self.root), [],
                         "tracking the file must not leave the zips behind the commit")

    def test_worktrees_never_ship_even_when_git_lists_them(self):
        """Structural, not a gitignore courtesy: with NO ignore rule, git reports a worktree's
        files as untracked-not-ignored, and they still must not ship."""
        wt = self.root / ".claude" / "worktrees" / "t1" / ".claude" / "tools"
        wt.mkdir(parents=True)
        (wt / "copy.py").write_text("x\n", encoding="utf-8")
        distctl.build(self.root)
        self.assertFalse(any("worktrees" in n for n in self.names("dot-claude-iff-fresh.zip")))


class TestZipVerdict(DistCase):
    """The committed-zip guard's three states, proven on a fixture repo so the guard is shown
    able to fail: fresh -> pending (uncommitted payload edit, POLISH will rebuild) -> stale (the
    edit is COMMITTED without a rebuild: what CI must catch) -> fresh again after a rebuild."""

    def setUp(self):
        super().setUp()
        if not GIT:
            self.skipTest("git not available")
        if _git(self.root, "init", "-q").returncode != 0:
            self.skipTest("git init failed")

    def _commit(self, msg: str) -> None:
        self.assertEqual(_git(self.root, "add", "-A").returncode, 0)
        res = _git(self.root, *_COMMIT, msg)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_three_states(self):
        distctl.build(self.root)
        self._commit("system + zips")
        self.assertEqual(zip_verdict(self.root)[0], "fresh")

        (self.root / ".claude" / "tasks" / "_template.md").write_text(
            "# Task: {{TITLE}} v2\n", encoding="utf-8")
        verdict, detail = zip_verdict(self.root)
        self.assertEqual(verdict, "pending")
        self.assertIn(".claude/tasks/_template.md", detail)

        self._commit("payload edit committed without a rebuild")
        verdict, detail = zip_verdict(self.root)
        self.assertEqual(verdict, "stale")
        self.assertEqual(sorted(detail), sorted(distctl.ZIP_NAMES))

        distctl.build(self.root)
        self._commit("zips rebuilt")
        self.assertEqual(zip_verdict(self.root)[0], "fresh")

    def test_a_non_payload_edit_is_not_pending(self):
        distctl.build(self.root)
        self._commit("system + zips")
        (self.root / ".claude" / "STATUS.md").write_text("new status\n", encoding="utf-8")
        self.assertEqual(uncommitted_payload(self.root), [],
                         "STATUS.md is reset in the kits; editing it is not a payload edit")
        self.assertEqual(zip_verdict(self.root)[0], "fresh")


@unittest.skipUnless(_home_repo(), "home-repo-only: the committed zips live in dot-claude-iff")
class TestCommittedZips(unittest.TestCase):
    """The real zips at .claude/dist/: equal to a rebuild of this tree, byte for byte, and
    carrying what the boundary promises. One rebuild into a scratch dir serves every test."""

    @classmethod
    def setUpClass(cls):
        if not GIT:
            raise unittest.SkipTest("git not available: the payload rule needs it")
        cls._tmp = tempfile.TemporaryDirectory(prefix="claude-iff-dist-")
        cls.out = Path(cls._tmp.name) / "dist"
        distctl.build(REPO_ROOT, out_dir=cls.out, quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def names(self, zip_name: str) -> list:
        with zipfile.ZipFile(self.out / zip_name) as z:
            return z.namelist()

    def read(self, zip_name: str, entry: str) -> bytes:
        with zipfile.ZipFile(self.out / zip_name) as z:
            return z.read(entry)

    def test_committed_zips_equal_a_rebuild(self):
        mismatched = []
        for name in distctl.ZIP_NAMES:
            committed = REPO_ROOT / ".claude" / "dist" / name
            if not committed.exists() or committed.read_bytes() != (self.out / name).read_bytes():
                mismatched.append(name)
        if not mismatched:
            return
        pending = uncommitted_payload(REPO_ROOT)
        if pending:
            self.skipTest(f"zips behind {len(pending)} uncommitted payload edit(s), e.g. "
                          f"{pending[:3]}: POLISH (or distctl.py build) rebuilds them before "
                          f"the commit; on a clean tree this test is strict")
        self.fail(f"committed zips are stale: {mismatched}. The committed payload changed "
                  f"without a rebuild - run `python3 .claude/tools/distctl.py build` and commit "
                  f"the zips (`distctl.py verify` says fresh when done).")

    def test_release_flow_is_absent_from_both_zips(self):
        self.assertTrue((REPO_ROOT / ".claude" / "reference" / "release-flow.md").exists(),
                        "the home-only doc exists here; the point is that it never ships")
        for name in distctl.ZIP_NAMES:
            with self.subTest(zip=name):
                self.assertFalse(any(n.endswith("reference/release-flow.md")
                                     for n in self.names(name)))

    def test_no_worktree_or_state_enters_a_zip(self):
        for name in distctl.ZIP_NAMES:
            with self.subTest(zip=name):
                names = self.names(name)
                self.assertFalse(any("/worktrees/" in n for n in names))
                self.assertFalse(any("/state/" in n for n in names))

    def test_kits_drop_the_home_suite_step(self):
        home = _lib.read_json(REPO_ROOT / ".claude" / "config" / "memory.json", {})
        self.assertTrue(home["project_steps"]["check"], "the home repo runs its suite in CHECK")
        for name, entry in (("dot-claude-iff-fresh.zip", ".claude/config/memory.json"),
                            ("dot-claude-iff-adopt-kit.zip",
                             "dot-claude-iff-kit/.claude/config/memory.json")):
            with self.subTest(zip=name):
                cfg = json.loads(self.read(name, entry).decode("utf-8"))
                self.assertEqual(cfg["project_steps"]["check"], [])
                self.assertFalse(cfg["distribution"]["enabled"])
                self.assertEqual(cfg["visibility"], "tracked")

    def test_kits_ship_publish_nothing_and_the_managed_gitignore(self):
        block = distctl.render_gitignore_block("tracked").encode()
        for name, prefix in (("dot-claude-iff-fresh.zip", ""),
                             ("dot-claude-iff-adopt-kit.zip", "dot-claude-iff-kit/")):
            with self.subTest(zip=name):
                publish = json.loads(self.read(name, f"{prefix}.claude/config/publish.json"))
                self.assertEqual(publish["include"], [])
                self.assertEqual(self.read(name, f"{prefix}.gitignore"), block)
                self.assertIn(f"{prefix}.claude/reference/public-private.md", self.names(name),
                              "the public/private manual ships to adopters")

    def test_fresh_install_stop_hook_creates_the_heartbeat(self):
        """heartbeat.json is untracked and the kit ships no state/: the first Stop on a fresh
        install must create both, or heartbeat_present warns forever."""
        bash = _lib.find_bash()
        if not bash:
            self.skipTest("bash not available")
        with tempfile.TemporaryDirectory(prefix="claude-iff-fresh-") as tmp:
            proj, record = Path(tmp) / "proj", Path(tmp) / "proj_claude_iff"
            with zipfile.ZipFile(self.out / "dot-claude-iff-fresh.zip") as z:
                z.extractall(proj)
            state = proj / ".claude" / "state"
            self.assertFalse(state.exists(), "a fresh install starts with no state/")
            self.assertIn(".claude/state/heartbeat.json",
                          (proj / ".gitignore").read_text(encoding="utf-8").splitlines())
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj), CLAUDE_IFF_RECORD_ROOT=str(record))
            res = subprocess.run([bash, str(proj / ".claude" / "hooks" / "heartbeat.sh")],
                                 input=json.dumps({"hook_event_name": "Stop"}), env=env,
                                 capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            beat = _lib.read_json(state / "heartbeat.json")
            self.assertTrue(beat and beat.get("ts"), "the Stop hook did not create the heartbeat")

    def test_heartbeat_and_worktrees_are_gitignored_here(self):
        for path in (".claude/state/heartbeat.json", ".claude/worktrees/t1/.claude/x.py"):
            with self.subTest(path=path):
                res = _git(REPO_ROOT, "check-ignore", "--no-index", "-q", path)
                self.assertEqual(res.returncode, 0, f"{path} is not gitignored")


@unittest.skipUnless(_home_repo(), "home-repo-only: the workflows live in dot-claude-iff")
class TestWorkflows(unittest.TestCase):
    """The two workflows, read as text (no YAML parser in the stdlib) and, for the notes
    extraction, executed: the regex that picks a release's notes is the workflow's own code."""

    WF = REPO_ROOT / ".github" / "workflows"
    SUITE = "run: ${{ matrix.py }} .claude/tools/tests/run_tests.py -q"

    def text(self, name: str) -> str:
        return (self.WF / name).read_text(encoding="utf-8")

    def test_ci_runs_the_suite_on_push_and_pr_on_both_os(self):
        ci = self.text("ci.yml")
        lines = [ln.strip() for ln in ci.splitlines()]
        self.assertIn("push:", lines)
        self.assertIn("branches: [dev, main]", lines)
        self.assertIn("pull_request:", lines)
        for line in ("- os: ubuntu-latest", "py: python3", "- os: windows-latest", "py: python",
                     self.SUITE):
            with self.subTest(line=line):
                self.assertIn(line, lines)

    def test_release_gate_runs_the_same_suite_as_ci(self):
        lines = [ln.strip() for ln in self.text("release.yml").splitlines()]
        self.assertIn(self.SUITE, lines)
        self.assertIn("- os: windows-latest", lines)

    def test_prerelease_tags_publish_as_prerelease_never_latest(self):
        rel = self.text("release.yml")
        self.assertIn('python3 .claude/tools/_lib.py --release-kind "$TAG")" || exit 1', rel,
                      "a malformed tag must fail the job, not publish as stable")
        self.assertIn('if [ "$KIND" = "prerelease" ]; then', rel)
        self.assertIn('KIND_FLAGS="--prerelease --latest=false"', rel)
        create = rel[rel.index("gh release create"):]
        self.assertIn("$KIND_FLAGS", create, "the flags must reach gh release create")

    def _notes_script(self) -> str:
        rel = self.text("release.yml")
        start = rel.index("<<'PY'\n") + len("<<'PY'\n")
        end = rel.index("\n          PY\n", start)
        return "\n".join(ln[10:] for ln in rel[start:end].splitlines()) + "\n"

    def _notes(self, tag: str, changelog: str) -> str:
        with tempfile.TemporaryDirectory(prefix="claude-iff-notes-") as tmp:
            tools = Path(tmp) / ".claude" / "tools"
            tools.mkdir(parents=True)
            shutil.copy(REPO_ROOT / ".claude" / "tools" / "_lib.py", tools / "_lib.py")
            (Path(tmp) / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            script = Path(tmp) / "notes.py"
            script.write_text(self._notes_script(), encoding="utf-8")
            res = subprocess.run([sys.executable, str(script), tag], cwd=tmp, capture_output=True,
                                 text=True, timeout=30, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            return res.stdout.strip()

    def test_notes_match_the_exact_tag_only(self):
        log = ("# Changelog\n\n## Unreleased\n\n- next\n\n"
               "## v3.0.1 - 2026-02-01\n\n- three-oh-one\n\n"
               "## v0.3.0-alpha.10 - 2026-01-03\n\n- alpha ten\n\n"
               "## v0.3.0-alpha.1 - 2026-01-02\n\n- alpha one\n\n"
               "## v0.2.2 - 2026-01-01\n\n- two-two\n")
        self.assertEqual(self._notes("v3.0", log), "See CHANGELOG.md.",
                         "v3.0 must not pick up the v3.0.1 section")
        self.assertEqual(self._notes("v0.3.0", log), "See CHANGELOG.md.",
                         "a stable tag must not pick up its own pre-release's notes")
        self.assertEqual(self._notes("v0.3.0-alpha.1", log), "- alpha one")
        self.assertEqual(self._notes("v0.2.2", log), "- two-two")



if __name__ == "__main__":
    unittest.main(verbosity=2)
