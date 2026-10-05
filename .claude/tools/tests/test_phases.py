#!/usr/bin/env python3
"""test_phases.py - modes, lifecycle phases, phase exit checks and the proposal box.

The milestone contract: phase = what work is allowed, mode = how organised the work is. Later
tasks build on these names, so the tests pin them: the stored values and aliases, the journal
fields, the four exit checks on passing AND failing fixtures, the gate in `statectl phase`
(organised modes refuse on FAIL unless --override; freestyle and a first phase just record),
the SessionStart block, deploy drift, and the proposal box.
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
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import consolectl  # noqa: E402
import mapctl  # noqa: E402
import statectl  # noqa: E402

PY = Path(sys.executable).as_posix()
HOOKS = CLAUDE_DIR / "hooks"
GIT = shutil.which("git")
STATE_RE = re.compile(r"^STATE_(OK|WARN|FAIL)$", re.MULTILINE)


def cmd(code: str) -> str:
    """A backticked Test command running this interpreter (never a bare `python3`, which on
    Windows can be the Store stub)."""
    return f'`"{PY}" -c "{code}"`'


class PhaseCase(FixtureCase):
    def statectl(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = statectl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def checkctl(self, *argv: str):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = checkctl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue()

    def verdict(self, out: str) -> str:
        found = STATE_RE.findall(out)
        self.assertEqual(len(found), 1, f"expected exactly one STATE_* token:\n{out}")
        return found[0]

    def tasks_dir(self) -> Path:
        return self.root / ".claude" / "tasks"

    def write_task(self, name: str, *, title: str, status: str | None = "todo",
                   dod: str | None = "the widget renders", test: str | None = None,
                   subdir: str = "") -> Path:
        parts = [f"# Task: {title}", ""]
        if status is not None:
            parts += [f"_Created 2026-10-05 · Status: {status}_", ""]
        parts += ["## Goal", "", "Do the thing.", ""]
        if dod is not None:
            parts += [f"**Definition of done:** {dod}", ""]
        if test is not None:
            parts += [f"**Test:** {test}", ""]
        parts += ["## Plan", "", "- [ ] step, done when: it is", ""]
        path = self.tasks_dir() / subdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(parts), encoding="utf-8")
        return path

    def phase_events(self) -> list:
        return [e for e in _lib.journal_read() if e.get("action") == "phase"]

    def set_phase(self, mode: str, phase: str) -> None:
        self.journal("mode", value=mode)
        self.journal("phase", value=phase)

    @staticmethod
    def statuses(results) -> dict:
        return {r.name: r.status for r in results}


# --------------------------------------------------------------------------- vocabulary

class TestVocabulary(PhaseCase):
    def test_stored_values_aliases_and_labels(self):
        self.assertEqual(_lib.MODES, ("freestyle", "guided-solo", "fableous-orchestrated"))
        for given, stored in (("guided", "guided-solo"), ("solo", "guided-solo"),
                              ("fableous", "fableous-orchestrated"),
                              ("orchestrated", "fableous-orchestrated"),
                              ("Guided-Solo", "guided-solo"), ("freestyle", "freestyle")):
            with self.subTest(given=given):
                self.assertEqual(_lib.normalize_mode(given), stored)
        self.assertIsNone(_lib.normalize_mode("chaotic"))
        self.assertEqual([_lib.mode_label(m) for m in _lib.MODES],
                         ["Freestyle", "Guided Solo", "Fableous Orchestrated"])

    def test_phases_and_legacy_text(self):
        self.assertEqual(_lib.LIFECYCLE_PHASES, ("plan", "build", "review", "deploy"))
        self.assertEqual(_lib.normalize_phase(" Build "), "build")
        self.assertIsNone(_lib.normalize_phase("implementation"))

    def test_unset_mode_is_freestyle_and_phase_is_none(self):
        self.assertEqual(_lib.current_mode(), "freestyle")
        self.assertIsNone(_lib.current_phase())
        self.assertFalse(_lib.lifecycle_state()["mode_set"])

    def test_journal_vocabulary_has_both_dials(self):
        self.assertIn("mode", _lib.JOURNAL_ACTIONS)
        self.assertIn("phase", _lib.JOURNAL_ACTIONS)


# --------------------------------------------------------------------------- statectl mode

class TestModeCommand(PhaseCase):
    def test_aliases_are_stored_as_full_names(self):
        for given, stored in (("guided", "guided-solo"), ("orchestrated", "fableous-orchestrated"),
                              ("solo", "guided-solo"), ("freestyle", "freestyle")):
            with self.subTest(given=given):
                code, out, _ = self.statectl("mode", given)
                self.assertEqual(code, 0)
                self.assertEqual(self.verdict(out), "OK")
                last = [e for e in _lib.journal_read() if e["action"] == "mode"][-1]
                self.assertEqual(last["value"], stored)
                self.assertEqual(_lib.current_mode(), stored)

    def test_unknown_mode_is_a_usage_error_and_records_nothing(self):
        code, _out, err = self.statectl("mode", "chaotic")
        self.assertEqual(code, 2)
        self.assertIn("guided-solo", err)
        self.assertEqual(_lib.journal_read(), [])


# --------------------------------------------------------------------------- statectl phase

class TestPhaseGate(PhaseCase):
    def test_freestyle_records_without_running_any_exit_check(self):
        with mock.patch.object(checkctl, "phase_exit", side_effect=AssertionError("ran")):
            self.assertEqual(self.statectl("phase", "plan")[0], 0)
            code, out, _ = self.statectl("phase", "build")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.verdict(out), "OK")
        last = self.phase_events()[-1]
        self.assertEqual((last["value"], last["from"]), ("build", "plan"))
        self.assertNotIn("override", last)

    def test_first_phase_runs_no_check_even_when_organised(self):
        self.journal("mode", value="guided-solo")
        with mock.patch.object(checkctl, "phase_exit", side_effect=AssertionError("ran")):
            code, out, _ = self.statectl("phase", "build")
        self.assertEqual(code, 0, out)
        self.assertNotIn("from", self.phase_events()[-1], "unset -> build has no from")

    def test_guided_solo_refuses_a_failed_exit(self):
        self.set_phase("guided-solo", "plan")  # no task files: the plan exit fails
        before = len(self.phase_events())
        code, out, _ = self.statectl("phase", "build")
        self.assertEqual(code, 1)
        self.assertEqual(self.verdict(out), "FAIL")
        self.assertIn("refused", out)
        self.assertIn("task_files", out, "the failing exit rows are shown")
        self.assertEqual(len(self.phase_events()), before, "a refusal records nothing")
        self.assertEqual(_lib.current_phase(), "plan")

    def test_fableous_orchestrated_refuses_too(self):
        self.set_phase("fableous-orchestrated", "plan")
        self.assertEqual(self.statectl("phase", "build")[0], 1)
        self.assertEqual(_lib.current_phase(), "plan")

    def test_override_is_logged_in_the_event(self):
        self.set_phase("guided-solo", "plan")
        code, out, _ = self.statectl("phase", "build", "--override", "spike, plan later")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.verdict(out), "WARN")
        last = self.phase_events()[-1]
        self.assertEqual(last["value"], "build")
        self.assertEqual(last["from"], "plan")
        self.assertEqual(last["override"], "spike, plan later")

    def test_an_unneeded_override_is_not_recorded(self):
        self.set_phase("guided-solo", "plan")
        ok = [checkctl.Result("x.md", checkctl.OK, "fine")]
        with mock.patch.object(checkctl, "phase_exit", return_value=ok):
            code, out, _ = self.statectl("phase", "build", "--override", "just in case")
        self.assertEqual(code, 0)
        self.assertEqual(self.verdict(out), "OK")
        self.assertNotIn("override", self.phase_events()[-1])

    def test_review_exit_needs_a_signoff(self):
        self.set_phase("guided-solo", "review")
        self.assertEqual(self.statectl("phase", "deploy")[0], 1)
        code, out, _ = self.statectl("phase", "deploy", "--signoff", "tested login and export")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.phase_events()[-1]["signoff"], "tested login and export")

    def test_an_exit_check_that_crashes_fails_closed(self):
        self.set_phase("guided-solo", "deploy")
        with mock.patch.object(checkctl, "doctor", side_effect=RuntimeError("boom")):
            code, out, _ = self.statectl("phase", "plan")
        self.assertEqual(code, 1)
        self.assertIn("boom", out)
        self.assertEqual(_lib.current_phase(), "deploy")

    def test_same_phase_is_a_noop(self):
        self.set_phase("guided-solo", "build")
        before = len(self.phase_events())
        code, out, _ = self.statectl("phase", "build")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.phase_events()), before)

    def test_unknown_phase_is_a_usage_error(self):
        self.assertEqual(self.statectl("phase", "ship")[0], 2)


# --------------------------------------------------------------------------- plan exit

class TestPlanExit(PhaseCase):
    def register(self, task_id: str, milestone: str | None = "M1") -> None:
        argv = ["task", task_id, "--title", task_id, "--status", "todo"]
        if milestone:
            argv += ["--milestone", milestone]
        self.statectl(*argv)

    def test_passes_on_a_complete_task_registered_by_its_title_id(self):
        self.write_task("030a-t1-widget.md", title="T1 - the widget", test=cmd("pass"))
        self.register("T1")
        results = checkctl.phase_exit("plan")
        self.assertEqual(self.statuses(results), {"030a-t1-widget.md": checkctl.OK})
        code, out = self.checkctl("phase-exit", "--from", "plan")
        self.assertEqual(code, 0, out)
        self.assertIn("CHECK_OK", out)

    def test_the_file_stem_is_the_plan_task_id(self):
        self.write_task("20261005-widget.md", title="Widget", test=cmd("pass"))
        self.register("20261005-widget")
        self.assertEqual(self.statuses(checkctl.phase_exit("plan")),
                         {"20261005-widget.md": checkctl.OK})

    def test_no_open_task_file_fails(self):
        self.write_task("old.md", title="old", status="done", test=cmd("pass"))
        results = checkctl.phase_exit("plan")
        self.assertEqual(self.statuses(results), {"task_files": checkctl.FAIL})
        code, out = self.checkctl("phase-exit", "--from", "plan")
        self.assertEqual(code, 1)
        self.assertIn("CHECK_FAIL", out)

    def test_each_defect_fails_its_file(self):
        cases = {
            "placeholder_dod": dict(dod="<the verifiable condition>", test=cmd("pass")),
            "missing_dod": dict(dod=None, test=cmd("pass")),
            "no_test_line": dict(test=None),
            "two_spans": dict(test=cmd("pass") + " prints `TESTS_OK`"),
            "no_backticks": dict(test="run the tests"),
            "placeholder_test": dict(test="`<one command>`"),
        }
        for label, kwargs in cases.items():
            with self.subTest(defect=label):
                shutil.rmtree(self.tasks_dir())
                self.write_task("t9.md", title="T9 - x", **kwargs)
                self.register("T9")
                results = checkctl.phase_exit("plan")
                self.assertEqual(self.statuses(results), {"t9.md": checkctl.FAIL}, label)

    def test_unregistered_or_milestoneless_tasks_fail(self):
        self.write_task("t1.md", title="T1 - x", test=cmd("pass"))
        result = checkctl.phase_exit("plan")[0]
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("not registered", result.message)
        self.register("T1", milestone=None)
        result = checkctl.phase_exit("plan")[0]
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("no milestone", result.message)
        self.statectl("task", "T1", "--milestone", "M1")
        self.assertEqual(checkctl.phase_exit("plan")[0].status, checkctl.OK)

    def test_template_briefs_done_files_and_archive_are_not_task_files(self):
        shutil.copy(CLAUDE_DIR / "tasks" / "_template.md", self.tasks_dir() / "_template.md")
        (self.tasks_dir() / "brief.md").write_text("# Builder brief\n\nno status line\n", encoding="utf-8")
        self.write_task("done.md", title="D - done", status="done", dod=None)
        self.write_task("old.md", title="O - archived", dod=None, subdir="archive/20260101")
        self.write_task("t1.md", title="T1 - x", test=cmd("pass"))
        self.register("T1")
        self.assertEqual(self.statuses(checkctl.phase_exit("plan")), {"t1.md": checkctl.OK})


# --------------------------------------------------------------------------- build exit

class TestBuildExit(PhaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.statectl("milestone", "M1", "--title", "the first body of work")

    def task(self, task_id: str, test: str, status: str = "done", subdir: str = "",
             milestone: str = "M1") -> None:
        self.write_task(f"{task_id.lower()}.md", title=f"{task_id} - work", status=status,
                        test=test, subdir=subdir)
        self.statectl("task", task_id, "--title", task_id, "--status", status, "--milestone", milestone)

    def test_passes_when_every_task_is_done_and_green(self):
        self.task("T1", cmd("pass"))
        self.task("T2", cmd("pass"), subdir="archive/20261005")  # archived: its Test still runs
        self.task("T3", cmd("raise SystemExit(5)"), status="doing", milestone="M0")  # other milestone
        results = checkctl.phase_exit("build")
        self.assertEqual(self.statuses(results),
                         {"milestone": checkctl.OK, "T1": checkctl.OK, "T2": checkctl.OK})
        code, out = self.checkctl("phase-exit", "--from", "build")
        self.assertEqual(code, 0, out)

    def test_a_shared_test_command_runs_once(self):
        shared = cmd("open('runs.txt', 'a').write('x')")
        self.task("T1", shared)
        self.task("T2", shared)
        self.assertEqual(self.statuses(checkctl.phase_exit("build")),
                         {"milestone": checkctl.OK, "T1": checkctl.OK, "T2": checkctl.OK})
        self.assertEqual((self.root / "runs.txt").read_text(encoding="utf-8"), "x")

    def test_a_red_test_fails_with_its_output_tail(self):
        self.task("T1", cmd("print('boom-tail'); raise SystemExit(3)"))
        result = next(r for r in checkctl.phase_exit("build") if r.name == "T1")
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("exit 3", result.message)
        self.assertIn("boom-tail", "\n".join(result.details))

    def test_a_task_not_done_fails_and_its_test_does_not_run(self):
        marker = self.root / "ran.txt"
        self.task("T1", cmd("open('ran.txt', 'w').write('x')"), status="doing")
        result = next(r for r in checkctl.phase_exit("build") if r.name == "T1")
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("not done", result.message)
        self.assertFalse(marker.exists())

    def test_runs_from_the_repo_root_without_a_shell(self):
        # `&& exit 7` would fail the task under a shell; argv-only it is two inert arguments.
        self.task("T1", f'`"{PY}" -c "import os; open(\'cwd.txt\', \'w\').write(os.getcwd())" && exit 7`')
        result = next(r for r in checkctl.phase_exit("build") if r.name == "T1")
        self.assertEqual(result.status, checkctl.OK, result.message)
        written = (self.root / "cwd.txt").read_text(encoding="utf-8")
        self.assertEqual(os.path.normcase(os.path.realpath(written)),
                         os.path.normcase(os.path.realpath(str(self.root))))

    def test_a_missing_interpreter_is_a_clear_fail(self):
        self.task("T1", "`no-such-interpreter-iff --version`")
        result = next(r for r in checkctl.phase_exit("build") if r.name == "T1")
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("no-such-interpreter-iff", result.message)
        self.assertIn("not found", result.message)

    def test_the_timeout_knob_bounds_each_test(self):
        cfg = _lib.load_config("phases")
        self.write_config("phases", dict(cfg, build_test_timeout=1))
        self.task("T1", cmd("import time; time.sleep(8)"))
        result = next(r for r in checkctl.phase_exit("build") if r.name == "T1")
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("timed out after 1s", result.message)

    def test_no_task_under_the_milestone_fails(self):
        results = checkctl.phase_exit("build")
        self.assertEqual(self.statuses(results), {"milestone": checkctl.FAIL})
        self.assertIn("M1", results[0].message)

    def test_milestone_flag_and_current_milestone(self):
        self.task("T1", cmd("pass"), milestone="M0")
        self.assertEqual(checkctl.current_milestone(), "M1", "the latest milestone event wins")
        self.assertEqual(checkctl.phase_exit("build")[0].status, checkctl.FAIL)
        self.assertEqual(self.statuses(checkctl.phase_exit("build", milestone="M0")),
                         {"milestone": checkctl.OK, "T1": checkctl.OK})


# --------------------------------------------------------------------------- review / deploy

class TestReviewAndDeployExit(PhaseCase):
    def test_review_passes_only_with_a_signoff(self):
        self.assertEqual(checkctl.phase_exit("review")[0].status, checkctl.FAIL)
        self.assertEqual(checkctl.phase_exit("review", signoff="   ")[0].status, checkctl.FAIL)
        self.assertEqual(checkctl.phase_exit("review", signoff="tried it")[0].status, checkctl.OK)
        self.assertEqual(self.checkctl("phase-exit", "--from", "review")[0], 1)
        code, out = self.checkctl("phase-exit", "--from", "review", "--signoff", "tried it")
        self.assertEqual(code, 0, out)

    def test_deploy_follows_the_doctor(self):
        bad = [(checkctl.Result("bash", checkctl.FAIL, "bash missing"), "install bash"),
               (checkctl.Result("git", checkctl.WARN, "no git"), "install git")]
        good = [(checkctl.Result("python", checkctl.OK, "fine"), ""),
                (checkctl.Result("git", checkctl.WARN, "no git"), "install git")]
        with mock.patch.object(checkctl, "doctor", return_value=bad):
            result = checkctl.phase_exit("deploy")[0]
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("bash: bash missing", result.details)
        with mock.patch.object(checkctl, "doctor", return_value=good):
            self.assertEqual(checkctl.phase_exit("deploy")[0].status, checkctl.OK,
                             "a WARN row does not block deploy, only a FAIL")

    def test_deploy_agrees_with_the_real_doctor(self):
        doctor_failed = any(r.status == checkctl.FAIL for r, _fix in checkctl.doctor())
        self.assertEqual(checkctl.phase_exit("deploy")[0].status,
                         checkctl.FAIL if doctor_failed else checkctl.OK)


# --------------------------------------------------------------------------- phase-exit command

class TestPhaseExitCommand(PhaseCase):
    def snapshot(self) -> dict:
        return {p.relative_to(self.root.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.root.parent.rglob("*")) if p.is_file()}

    def test_phase_exit_writes_nothing(self):
        self.statectl("milestone", "M1", "--title", "m")
        before = self.snapshot()
        for phase in _lib.LIFECYCLE_PHASES:
            with self.subTest(phase=phase):
                self.checkctl("phase-exit", "--from", phase)
                self.checkctl("phase-exit", "--from", phase, "--json")
        self.assertEqual(self.snapshot(), before, "phase-exit wrote or changed a file")

    def test_json_output(self):
        code, out = self.checkctl("phase-exit", "--from", "review", "--signoff", "ok", "--json")
        payload = json.loads(out[:out.rindex("}") + 1])
        self.assertEqual(payload["phase"], "review")
        self.assertTrue(payload["pass"])
        self.assertEqual(payload["results"][0]["name"], "signoff")

    def test_shipped_config_names_bound_exits_and_short_contracts(self):
        spec = _lib.load_config("phases")["phases"]
        self.assertEqual(set(spec), set(_lib.LIFECYCLE_PHASES))
        for phase in _lib.LIFECYCLE_PHASES:
            with self.subTest(phase=phase):
                self.assertIn(spec[phase]["exit"], checkctl.PHASE_EXITS)
                self.assertTrue(spec[phase]["label"])
                self.assertTrue(1 <= len(spec[phase]["contract"]) <= 5)
                self.assertEqual(_lib.phase_contract(phase), spec[phase]["contract"])

    def test_an_unbound_exit_name_fails_closed(self):
        cfg = json.loads(json.dumps(_lib.load_config("phases")))
        cfg["phases"]["review"]["exit"] = "vibes"
        self.write_config("phases", cfg)
        result = checkctl.phase_exit("review", signoff="fine")[0]
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("PHASE_EXITS", result.message)

    def test_missing_phases_config_falls_back_to_the_code_bindings(self):
        (self.root / ".claude" / "config" / "phases.json").unlink()
        _lib.clear_config_cache()
        self.assertEqual(checkctl.phase_exit("review")[0].status, checkctl.FAIL)
        self.assertEqual(checkctl.phase_exit("review", signoff="ok")[0].status, checkctl.OK)
        self.assertEqual(_lib.phase_contract("build"), [])


# --------------------------------------------------------------------------- deploy drift

@unittest.skipUnless(GIT, "git not available")
class TestDeployDrift(PhaseCase):
    def git(self, *args, env=None):
        full = dict(os.environ, **(env or {}))
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               *args], cwd=str(self.root), capture_output=True, text=True,
                              env=full, check=False)

    def test_skips_outside_an_organised_deploy(self):
        self.assertEqual(checkctl.check_deploy_drift().status, checkctl.SKIP)  # freestyle
        self.set_phase("freestyle", "deploy")
        self.assertEqual(checkctl.check_deploy_drift().status, checkctl.SKIP)
        self.set_phase("guided-solo", "build")
        self.assertEqual(checkctl.check_deploy_drift().status, checkctl.SKIP)
        self.set_phase("guided-solo", "deploy")
        result = checkctl.check_deploy_drift()  # the fixture is not a git repo yet
        self.assertEqual(result.status, checkctl.SKIP)
        self.assertIn("git", result.message)

    def test_lists_new_files_outside_tests_and_docs(self):
        self.assertEqual(self.git("init", "-q").returncode, 0)
        old = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00 +0000",
               "GIT_COMMITTER_DATE": "2020-01-01T00:00:00 +0000"}
        self.git("add", "-A")
        self.assertEqual(self.git("commit", "-q", "-m", "before deploy", env=old).returncode, 0)
        self.set_phase("guided-solo", "deploy")
        for rel in ("src/feature.py", "src/committed.py", "tests/test_feature.py", "docs/guide.md",
                    "NOTES.md", "pkg/test_util.py", ".claude/state/extra.json"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        self.git("add", "src/committed.py")
        self.assertEqual(self.git("commit", "-q", "-m", "a feature during deploy").returncode, 0)
        result = checkctl.check_deploy_drift()
        self.assertEqual(result.status, checkctl.WARN, result.message)
        self.assertEqual(sorted(result.details), ["src/committed.py", "src/feature.py"])
        self.assertIn("proposal add", result.message)

    def test_bound_and_registered_in_the_check_phase(self):
        self.assertIn("deploy_drift", checkctl.CHECKS)
        self.assertIn("deploy_drift", checkctl.phase_steps("check"))


# --------------------------------------------------------------------------- SessionStart block

class TestSessionStartBlock(PhaseCase):
    """The real hook, under bash, against the fixture (the same harness test_hooks.py uses)."""

    def setUp(self) -> None:
        super().setUp()
        tools = self.root / ".claude" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLAUDE_DIR / "tools" / "_lib.py", tools / "_lib.py")
        cfg = json.loads((self.root / ".claude/config/console.json").read_text())
        cfg["autostart"] = False  # never spawn a server from the test suite
        (self.root / ".claude/config/console.json").write_text(json.dumps(cfg))
        self.contracts = {p: s["contract"] for p, s in _lib.load_config("phases")["phases"].items()}

    def run_hook(self) -> str:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root),
                   CLAUDE_IFF_RECORD_ROOT=str(self.record))
        res = subprocess.run(["bash", str(HOOKS / "session-start.sh")], input="{}",
                             capture_output=True, text=True, timeout=60, env=env, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def block(self, out: str) -> list:
        lines = out.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("MODE: "))
        block = [lines[start]]
        for ln in lines[start + 1:]:
            if not ln.startswith("  - "):
                break
            block.append(ln)
        return block

    def assert_no_contract_text(self, out: str) -> None:
        for phase, lines in self.contracts.items():
            for line in lines:
                self.assertNotIn(line, out, f"freestyle printed the {phase} contract")

    def test_freestyle_prints_only_the_mode_line(self):
        self.journal("session_start", session="s1")
        out = self.run_hook()
        self.assertEqual(self.block(out), ["MODE: Freestyle · PHASE: unset"])
        self.assert_no_contract_text(out)

    def test_freestyle_with_a_phase_is_still_one_line(self):
        self.journal("phase", value="build")
        out = self.run_hook()
        self.assertEqual(self.block(out), ["MODE: Freestyle · PHASE: build"])
        self.assert_no_contract_text(out)

    def test_guided_solo_prints_the_phase_contract(self):
        self.set_phase("guided-solo", "build")
        block = self.block(self.run_hook())
        self.assertTrue(block[0].startswith("MODE: Guided Solo · PHASE: build"), block[0])
        self.assertEqual(block[1:], [f"  - {line}" for line in self.contracts["build"]])
        self.assertLessEqual(len(block) - 1, 5)

    def test_fableous_orchestrated_prints_the_phase_contract(self):
        self.set_phase("fableous-orchestrated", "deploy")
        block = self.block(self.run_hook())
        self.assertTrue(block[0].startswith("MODE: Fableous Orchestrated · PHASE: deploy"), block[0])
        self.assertEqual(block[1:], [f"  - {line}" for line in self.contracts["deploy"]])

    def test_organised_without_a_phase_says_how_to_set_one(self):
        self.journal("mode", value="guided-solo")
        block = self.block(self.run_hook())
        self.assertEqual(len(block), 1)
        self.assertIn("statectl.py phase plan", block[0])

    def test_a_contract_is_capped_at_five_lines(self):
        cfg = json.loads(json.dumps(_lib.load_config("phases")))
        cfg["phases"]["plan"]["contract"] = [f"line {i}" for i in range(1, 8)]
        self.write_config("phases", cfg)
        self.set_phase("guided-solo", "plan")
        block = self.block(self.run_hook())
        self.assertEqual(block[1:], [f"  - line {i}" for i in range(1, 6)])

    def test_a_broken_phases_config_fails_open(self):
        (self.root / ".claude" / "config" / "phases.json").write_text("{ broken", encoding="utf-8")
        self.set_phase("guided-solo", "build")
        block = self.block(self.run_hook())
        self.assertEqual(block, ["MODE: Guided Solo · PHASE: build"])


# --------------------------------------------------------------------------- proposal box

class TestProposalBox(PhaseCase):
    def store_lines(self) -> list:
        return _lib.proposals_path().read_text(encoding="utf-8").splitlines()

    def test_add_list_resolve(self):
        code, out, _ = self.statectl("proposal", "add", "plugin packaging", "--source", "issue#13")
        self.assertEqual(code, 0)
        self.assertIn("PR-1", out.split())
        code, out, _ = self.statectl("proposal", "add", "a lint for X", "--source", "agent:builder-T4",
                                     "--kind", "evolve")
        self.assertIn("PR-2", out.split())
        records = {r["id"]: r for r in _lib.proposal_records()}
        self.assertEqual(records["PR-1"]["kind"], "feature", "kind defaults to feature")
        self.assertEqual(records["PR-2"]["source"], "agent:builder-T4")

        code, out, _ = self.statectl("proposal", "list")
        self.assertIn("PR-1 [open] feature · issue#13: plugin packaging", out)
        self.assertIn("PR-2", out)

        code, out, _ = self.statectl("proposal", "resolve", "PR-1", "--as", "planned",
                                     "--note", "milestone M2")
        self.assertEqual(code, 0)
        self.assertEqual(self.verdict(out), "OK")
        code, out, _ = self.statectl("proposal", "list")
        self.assertNotIn("PR-1", out)
        code, out, _ = self.statectl("proposal", "list", "--all")
        self.assertIn("PR-1 [planned]", out)
        self.assertIn("milestone M2", out)

    def test_ids_are_counted_from_the_store(self):
        self.statectl("proposal", "add", "a", "--source", "human")
        self.statectl("proposal", "add", "b", "--source", "human")
        self.statectl("proposal", "resolve", "PR-1", "--as", "rejected", "--note", "no")
        _code, out, _ = self.statectl("proposal", "add", "c", "--source", "human")
        self.assertIn("PR-3", out.split())
        _lib.append_jsonl(_lib.proposals_path(), {"ts": _lib.utc_now(), "op": "add", "id": "PR-7",
                                                  "text": "hand", "source": "human", "kind": "fix"})
        _code, out, _ = self.statectl("proposal", "add", "d", "--source", "human")
        self.assertIn("PR-8", out.split())

    def test_the_store_is_append_only(self):
        self.statectl("proposal", "add", "a", "--source", "human")
        first = self.store_lines()
        self.statectl("proposal", "resolve", "PR-1", "--as", "planned", "--note", "later")
        after = self.store_lines()
        self.assertEqual(after[:len(first)], first)
        self.assertEqual(len(after), len(first) + 1)
        self.assertEqual(json.loads(after[-1])["op"], "resolve")

    def test_source_and_kind_grammar(self):
        for source in ("issue#13", "human", "agent:verifier"):
            with self.subTest(source=source):
                self.assertEqual(self.statectl("proposal", "add", "x", "--source", source)[0], 0)
        for source in ("issue13", "bob", "agent:", "issue#"):
            with self.subTest(source=source):
                self.assertEqual(self.statectl("proposal", "add", "x", "--source", source)[0], 2)
        self.assertEqual(self.statectl("proposal", "add", "x", "--source", "human",
                                       "--kind", "chore")[0], 2)

    def test_resolve_needs_as_and_note_and_a_real_id(self):
        self.statectl("proposal", "add", "a", "--source", "human")
        self.assertEqual(self.statectl("proposal", "resolve", "PR-1", "--as", "planned")[0], 2)
        self.assertEqual(self.statectl("proposal", "resolve", "PR-1", "--note", "x")[0], 2)
        code, out, _ = self.statectl("proposal", "resolve", "PR-9", "--as", "planned", "--note", "x")
        self.assertEqual(code, 1)
        self.assertEqual(self.verdict(out), "FAIL")

    def test_resolving_twice_warns(self):
        self.statectl("proposal", "add", "a", "--source", "human")
        self.statectl("proposal", "resolve", "PR-1", "--as", "planned", "--note", "x")
        code, out, err = self.statectl("proposal", "resolve", "PR-1", "--as", "rejected", "--note", "y")
        self.assertEqual(code, 0)
        self.assertEqual(self.verdict(out), "WARN")
        self.assertEqual(_lib.proposal_records()[0]["status"], "rejected")


# --------------------------------------------------------------------------- template + registrations

class TestTaskTemplate(PhaseCase):
    TEMPLATE = CLAUDE_DIR / "tasks" / "_template.md"

    def test_template_has_the_status_and_test_lines(self):
        parsed = checkctl.parse_task_file(self.TEMPLATE)
        self.assertIsNotNone(parsed, "the template must carry a status line")
        self.assertEqual(parsed["status"], "todo")
        self.assertEqual(len(parsed["test_commands"]), 1)
        self.assertTrue(parsed["dod"].startswith("<"), "an unfilled copy must read as a placeholder")

    def test_console_reader_and_exit_check_agree_on_status(self):
        self.assertEqual(consolectl._parse_task_file(self.TEMPLATE)["status"], "todo")
        for path in sorted((CLAUDE_DIR / "tasks").glob("*.md")):
            parsed = checkctl.parse_task_file(path)
            with self.subTest(file=path.name):
                self.assertEqual(consolectl._parse_task_file(path)["status"],
                                 parsed["status"] if parsed else "")

    def test_task_reality_reads_the_template_and_a_fresh_copy(self):
        shutil.copy(self.TEMPLATE, self.tasks_dir() / "_template.md")
        self.assertEqual(checkctl.check_task_reality().status, checkctl.OK)
        shutil.copy(self.TEMPLATE, self.tasks_dir() / "20261005-new.md")
        self.assertIn(checkctl.check_task_reality().status, (checkctl.OK, checkctl.WARN))
        self.assertEqual(checkctl.phase_exit("plan")[0].status, checkctl.FAIL,
                         "an unfilled copy of the template cannot leave plan")


class TestRegistrations(PhaseCase):
    def test_proposal_store_is_declared_with_a_card(self):
        store = next(s for s in mapctl.KNOWN_STORES if s["id"] == "store.proposals")
        self.assertEqual(store["path"], ".claude/state/proposals.jsonl")
        card = json.loads((CLAUDE_DIR / "system-map" / "cards" / "store.proposals.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(card["path"], store["path"])

    def test_phases_config_has_a_card_registry_entries_and_a_probe(self):
        card = json.loads((CLAUDE_DIR / "system-map" / "cards" / "config.phases.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(card["path"], ".claude/config/phases.json")
        keys = {e["key"] for e in _lib.load_config("registry")["entries"]}
        for key in ("phases.phases", "phases.build_test_timeout", "phases.drift_ignore"):
            self.assertIn(key, keys)
        self.assertIn("config.phases", {r.name for r in checkctl.probe()})
        # Agent files present, so no dead-card error truncates the warning list.
        shutil.copytree(CLAUDE_DIR / "agents", self.root / ".claude" / "agents")
        lint = checkctl.check_config_registry()
        self.assertFalse([d for d in lint.details if d.startswith("phases.")], lint.details)

    def test_glossary_defines_the_three_terms(self):
        text = (CLAUDE_DIR / "reference" / "glossary.md").read_text(encoding="utf-8")
        for term in ("mode", "phase", "proposal"):
            self.assertRegex(text, rf"(?m)^- \*\*{term}(?: \([a-z]+\))?\*\*:", term)


if __name__ == "__main__":
    unittest.main(verbosity=2)
