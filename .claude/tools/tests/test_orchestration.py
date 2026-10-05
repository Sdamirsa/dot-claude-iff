#!/usr/bin/env python3
"""test_orchestration.py - the fableous-orchestrated mode: lead, builders, scouts, handoffs.

What is pinned here: ONE envelope contract (_lib.validate_envelope) and every repo envelope
meeting it; the stub's one time field; `checkctl handoff` (schema, status, files, recorded and
rerun tests, --root, read-only); the `statectl task --status done` guard in all three modes;
the builder stop check's logic (block, loop guard, other agents and modes untouched, stale and
foreign stubs); the delegation nudge (counting, exemptions, reset on dispatch, never a gate
decision); the registry lint that compares agent pins by value; and the registrations
(protocol, agents, cards, probe, knobs, glossary, scaffolds). The hook scripts themselves run
end to end in test_hooks.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import consolectl  # noqa: E402
import distctl  # noqa: E402
import mapctl  # noqa: E402
import statectl  # noqa: E402

PY = Path(sys.executable).as_posix()
STATE_RE = re.compile(r"^STATE_(OK|WARN|FAIL)$", re.MULTILINE)
CHECK_RE = re.compile(r"^CHECK_(OK|WARN|FAIL)$", re.MULTILINE)
ORCH = "fableous-orchestrated"


def iso(offset: float = 0.0) -> str:
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def py(code: str) -> str:
    """A recorded test command running this interpreter (never a bare `python3`)."""
    return f'"{PY}" -c "{code}"'


class OrchCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    # -- running the CLIs in-process --------------------------------------------------------
    def statectl(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = statectl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue()

    def checkctl(self, *argv: str):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = checkctl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue()

    def set_mode(self, mode: str) -> None:
        self.journal("mode", value=mode)

    # -- envelopes ----------------------------------------------------------------------------
    @staticmethod
    def envelope(task_id: str = "T1", **over) -> dict:
        env = {
            "agent_id": f"builder-{task_id}", "task_id": task_id, "status": "done",
            "agent": "builder", "model": "opus", "RESULT": "the widget renders",
            "files_changed": ["src/app.py"],
            "tests": [{"command": py("raise SystemExit(0)"), "exit_code": 0, "summary": "ok"}],
            "EVIDENCE": ["ran the test"], "DEVIATIONS": [], "needs_main": [],
        }
        env.update(over)
        return env

    def write_envelope(self, task_id: str = "T1", root: Path | None = None, **over) -> Path:
        path = _lib.envelope_path(task_id, root or self.root)
        _lib.atomic_write_json(path, self.envelope(task_id, **over))
        return path

    def task_events(self, task_id: str) -> list:
        return [e for e in _lib.journal_read() if e.get("action") == "task" and e.get("id") == task_id]


# =========================================================================== the envelope contract

class TestEnvelopeContract(OrchCase):
    def test_the_base_envelope_is_valid(self):
        base = {"agent_id": "verifier", "task_id": "v1", "status": "partial",
                "artifacts": ["a.md"], "notes": "STATUS: partial\nRESULT: x\nEVIDENCE: y"}
        self.assertEqual(_lib.validate_envelope(base), [])

    def test_missing_required_keys_are_named(self):
        errors = _lib.validate_envelope({"agent_id": "x"})
        self.assertTrue(any("task_id" in e for e in errors), errors)
        self.assertTrue(any(e.startswith("status") for e in errors), errors)
        self.assertEqual(_lib.validate_envelope([1, 2]), ["the envelope is not a JSON object"])

    def test_status_vocabulary_is_done_partial_blocked(self):
        for status in ("done", "partial", "blocked"):
            with self.subTest(status=status):
                env = {"agent_id": "a", "task_id": "t", "status": status}
                self.assertEqual(_lib.validate_envelope(env), [])
        for status in ("ok", "finished", "DONE"):
            with self.subTest(status=status):
                errors = _lib.validate_envelope({"agent_id": "a", "task_id": "t", "status": status})
                self.assertTrue(any("status must be one of" in e for e in errors), errors)

    def test_the_legacy_uppercase_STATUS_must_agree(self):
        ok = {"agent_id": "a", "task_id": "t", "status": "done", "STATUS": "ok"}
        self.assertEqual(_lib.validate_envelope(ok), [])
        clash = dict(ok, STATUS="partial")
        self.assertTrue(any("disagrees" in e for e in _lib.validate_envelope(clash)))
        junk = dict(ok, STATUS="finished")
        self.assertTrue(any("STATUS must be" in e for e in _lib.validate_envelope(junk)))
        legacy_only = {"task_id": "t", "agent": "builder", "STATUS": "ok"}
        errors = _lib.validate_envelope(legacy_only)
        self.assertTrue(any(e.startswith("agent_id") for e in errors), "STATUS is no substitute")

    def test_builder_keys_are_required_for_a_builder(self):
        self.assertEqual(_lib.validate_envelope(self.envelope()), [])
        for key in ("agent", "model", "files_changed", "tests", "needs_main"):
            with self.subTest(missing=key):
                env = self.envelope()
                del env[key]
                errors = _lib.validate_envelope(env, builder=True)
                self.assertTrue(any(e.startswith(key) for e in errors), (key, errors))
        base = {"agent_id": "a", "task_id": "t", "status": "blocked"}
        self.assertEqual(_lib.validate_envelope(base), [], "a non-builder needs no builder keys")
        self.assertTrue(_lib.validate_envelope(base, builder=True), "builder=True demands them")

    def test_test_entries_have_a_shape(self):
        bad = self.envelope(status="partial", tests=[
            "python3 x.py", {"command": "", "exit_code": 0, "summary": "s"},
            {"command": "x", "exit_code": True, "summary": "s"},
            {"command": "x", "exit_code": "0", "summary": "s"},
            {"command": "x", "exit_code": 0}])
        errors = _lib.validate_envelope(bad)
        for fragment in ("tests[0] must be an object", "tests[1].command", "tests[2].exit_code",
                         "tests[3].exit_code", "tests[4].summary"):
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in e for e in errors), errors)

    def test_done_means_tests_recorded_and_green(self):
        none = _lib.validate_envelope(self.envelope(tests=[]))
        self.assertTrue(any("no test is recorded" in e for e in none), none)
        red = self.envelope(tests=[{"command": "a", "exit_code": 0, "summary": ""},
                                   {"command": "b", "exit_code": 1, "summary": "1 failed"}])
        self.assertTrue(any("a recorded test failed" in e for e in _lib.validate_envelope(red)))
        red["status"] = "partial"
        self.assertEqual(_lib.validate_envelope(red), [], "partial may carry a failing test")

    def test_every_envelope_in_this_repo_meets_the_contract(self):
        """The T1..T9 envelopes of this milestone, and anything else shipped in the tree."""
        hs = CLAUDE_DIR / "state" / "handshakes"
        found = [p for p in sorted(hs.glob("*.json")) if not p.name.endswith(".stub.json")] \
            if hs.is_dir() else []
        for path in found:
            with self.subTest(envelope=path.name):
                obj = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(_lib.validate_envelope(obj), [])


# =========================================================================== the stub

class TestStub(OrchCase):
    def test_dispatched_at_is_the_one_name_and_old_stubs_still_read(self):
        hs = _lib.handshakes_dir()
        _lib.atomic_write_json(hs / "A.stub.json", {"task_id": "A", "agent": "builder",
                                                    "dispatched_at": "2026-10-05T10:00:00Z"})
        _lib.atomic_write_json(hs / "B.stub.json", {"agent": "scout", "since": "2026-10-05T11:00:00Z"})
        _lib.atomic_write_json(hs / "C.stub.json", {"agent_id": "verifier", "ts": "2026-10-05T12:00:00Z"})
        self.assertEqual(_lib.read_stub(hs / "A.stub.json")["dispatched_at"], "2026-10-05T10:00:00Z")
        b = _lib.read_stub(hs / "B.stub.json")
        self.assertEqual((b["task_id"], b["agent"], b["dispatched_at"]),
                         ("B", "scout", "2026-10-05T11:00:00Z"))
        self.assertEqual(_lib.read_stub(hs / "C.stub.json")["agent"], "verifier")
        flight = {f["task_id"]: f for f in consolectl.payload()["now"]["in_flight"]}
        self.assertEqual(flight["B"]["dispatched_at"], "2026-10-05T11:00:00Z")
        self.assertNotIn("since", flight["A"], "the console speaks the contract's name")

    def test_statectl_dispatch_writes_the_stub(self):
        code, out = self.statectl("dispatch", "T7", "--worktree", ".claude/worktrees/t7")
        self.assertEqual(code, 0, out)
        self.assertEqual(STATE_RE.findall(out), ["OK"])
        stub = json.loads(_lib.stub_path("T7").read_text(encoding="utf-8"))
        self.assertEqual(set(stub), {"task_id", "agent", "dispatched_at", "worktree"})
        self.assertEqual((stub["agent"], stub["worktree"]), ("builder", ".claude/worktrees/t7"))
        self.assertIsNotNone(_lib.parse_ts(stub["dispatched_at"]))
        notes = [e for e in _lib.journal_read() if e.get("action") == "note"]
        self.assertIn("dispatched builder for T7", notes[-1]["text"])
        self.assertEqual(consolectl.payload()["now"]["in_flight"][0]["task_id"], "T7")


# =========================================================================== checkctl handoff

class TestHandoffCheck(OrchCase):
    def statuses(self, results) -> dict:
        return {r.name: r.status for r in results}

    def test_a_valid_handoff_is_accepted(self):
        self.write_envelope()
        code, out = self.checkctl("handoff", "T1")
        self.assertEqual(code, 0, out)
        self.assertEqual(CHECK_RE.findall(out), ["OK"])
        self.assertIn("not rerun", out)

    def test_a_missing_envelope_fails_and_says_where(self):
        code, out = self.checkctl("handoff", "T1")
        self.assertEqual(code, 1)
        self.assertIn("no envelope at .claude/state/handshakes/T1.json", out)

    def test_an_unparseable_envelope_fails(self):
        _lib.envelope_path("T1").write_text("{ nope", encoding="utf-8")
        results = checkctl.handoff_check("T1")
        self.assertEqual(results[0].status, checkctl.FAIL)
        self.assertIn("does not parse", results[0].message)

    def test_schema_failures_are_listed(self):
        self.write_envelope(tests="all green, trust me")
        results = checkctl.handoff_check("T1")
        self.assertEqual(self.statuses(results)["schema"], checkctl.FAIL)
        self.assertTrue(any("tests must be a list" in d for d in results[0].details))

    def test_every_files_changed_path_must_exist_under_the_root(self):
        self.write_envelope(files_changed=["src/app.py", "src/gone.py", "/etc/passwd",
                                           "../outside.py", "C:/x.py"])
        results = {r.name: r for r in checkctl.handoff_check("T1")}
        self.assertEqual(results["files_changed"].status, checkctl.FAIL)
        details = "\n".join(results["files_changed"].details)
        self.assertIn("src/gone.py: does not exist", details)
        for bad in ("/etc/passwd", "../outside.py", "C:/x.py"):
            self.assertIn(f"{bad}: not a repo-relative path", details)
        self.assertNotIn("src/app.py", details)

    def test_a_partial_handoff_is_not_accepted(self):
        self.write_envelope(status="partial")
        statuses = self.statuses(checkctl.handoff_check("T1"))
        self.assertEqual(statuses["schema"], checkctl.OK)
        self.assertEqual(statuses["status"], checkctl.FAIL)

    def test_the_task_id_must_match_the_file(self):
        path = self.write_envelope()
        shutil.copy(path, _lib.envelope_path("T2"))
        results = checkctl.handoff_check("T2")
        self.assertEqual(results[0].status, checkctl.FAIL)
        self.assertTrue(any("named for 'T2'" in d for d in results[0].details))

    def test_run_reruns_each_test_and_compares_exit_codes(self):
        self.write_envelope(tests=[
            {"command": py("raise SystemExit(0)"), "exit_code": 0, "summary": "ok"},
            {"command": py("raise SystemExit(3)"), "exit_code": 0, "summary": "lied"}])
        results = {r.name: r for r in checkctl.handoff_check("T1", run=True)}
        self.assertEqual(results["rerun[0]"].status, checkctl.OK)
        self.assertEqual(results["rerun[1]"].status, checkctl.FAIL)
        self.assertIn("exit 3, but the envelope records 0", results["rerun[1]"].message)
        code, _ = self.checkctl("handoff", "T1", "--run")
        self.assertEqual(code, 1)

    def test_run_honours_the_timeout_knob(self):
        self.write_config("orchestration", {"nudge_after": 8, "handoff_test_timeout": 1})
        self.write_envelope(tests=[{"command": py("import time; time.sleep(10)"), "exit_code": 0,
                                    "summary": "slow"}])
        results = {r.name: r for r in checkctl.handoff_check("T1", run=True)}
        self.assertEqual(results["rerun[0]"].status, checkctl.FAIL)
        self.assertIn("timed out after 1s", results["rerun[0]"].message)

    def test_root_checks_inside_a_worktree_before_the_merge(self):
        wt = self.root / ".claude" / "worktrees" / "t1"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
        marker = "import os, sys; sys.exit(0 if os.path.exists('src/new.py') else 4)"
        self.write_envelope(root=wt, files_changed=["src/new.py"],
                            tests=[{"command": py(marker), "exit_code": 0, "summary": "ok"}])
        self.assertEqual(self.checkctl("handoff", "T1")[0], 1, "the main root has no envelope")
        code, out = self.checkctl("handoff", "T1", "--run", "--root", str(wt))
        self.assertEqual(code, 0, out)
        self.assertIn("as recorded", out, "the test ran with the worktree as its cwd")

    def test_handoff_is_read_only(self):
        self.write_envelope()

        def snapshot():
            return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        before = snapshot()
        self.checkctl("handoff", "T1", "--run")
        self.checkctl("handoff", "T9")
        self.assertEqual(snapshot(), before)

    def test_json_output(self):
        self.write_envelope()
        code, out = self.checkctl("handoff", "T1", "--json")
        data = json.loads(out[:out.rindex("}") + 1])
        self.assertEqual((code, data["task_id"], data["accept"]), (0, "T1", True))


# =========================================================================== the done guard

class TestDoneGuard(OrchCase):
    def test_freestyle_and_guided_solo_are_unchanged(self):
        for mode, task in (("freestyle", "T1"), ("guided-solo", "T2")):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                code, out = self.statectl("task", task, "--status", "done")
                self.assertEqual(code, 0, out)
                self.assertEqual(STATE_RE.findall(out), ["OK"])
                self.assertEqual(self.task_events(task)[-1]["status"], "done")

    def test_unset_mode_is_freestyle(self):
        self.assertEqual(self.statectl("task", "T1", "--status", "done")[0], 0)

    def test_orchestrated_refuses_without_an_envelope(self):
        self.set_mode(ORCH)
        code, out = self.statectl("task", "T1", "--status", "done")
        self.assertEqual(code, 1)
        self.assertEqual(STATE_RE.findall(out), ["FAIL"])
        self.assertIn("no envelope at", out)
        self.assertIn("--no-envelope", out, "the refusal names the way out")
        self.assertEqual(self.task_events("T1"), [], "a refusal records nothing")

    def test_orchestrated_accepts_a_valid_envelope_whose_tests_passed(self):
        self.set_mode(ORCH)
        self.write_envelope()
        code, out = self.statectl("task", "T1", "--status", "done", "--note", "merged")
        self.assertEqual(code, 0, out)
        self.assertEqual(STATE_RE.findall(out), ["OK"])
        event = self.task_events("T1")[-1]
        self.assertEqual(event["status"], "done")
        self.assertNotIn("no_envelope", event)

    def test_orchestrated_refuses_a_partial_or_red_handoff(self):
        self.set_mode(ORCH)
        for over in ({"status": "partial"},
                     {"tests": [{"command": "x", "exit_code": 1, "summary": "red"}]},
                     {"files_changed": ["src/never-written.py"]}):
            with self.subTest(over=over):
                self.write_envelope(**over)
                code, out = self.statectl("task", "T1", "--status", "done")
                self.assertEqual(code, 1, out)
        self.assertEqual(self.task_events("T1"), [])

    def test_no_envelope_records_why(self):
        self.set_mode(ORCH)
        code, out = self.statectl("task", "T1", "--status", "done",
                                  "--no-envelope", "two-line fix the lead made itself")
        self.assertEqual(code, 0, out)
        self.assertEqual(STATE_RE.findall(out), ["WARN"])
        self.assertEqual(self.task_events("T1")[-1]["no_envelope"], "two-line fix the lead made itself")

    def test_only_done_is_guarded(self):
        self.set_mode(ORCH)
        for status in ("todo", "doing", "blocked"):
            with self.subTest(status=status):
                self.assertEqual(self.statectl("task", "T1", "--status", status)[0], 0)
        code, out = self.statectl("task", "T1", "--status", "doing", "--no-envelope", "x")
        self.assertIn("not recorded", out)
        self.assertNotIn("no_envelope", self.task_events("T1")[-1])

    def test_a_broken_check_refuses(self):
        self.set_mode(ORCH)
        self.write_envelope()
        original = checkctl.handoff_check
        checkctl.handoff_check = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            code, out = self.statectl("task", "T1", "--status", "done")
        finally:
            checkctl.handoff_check = original
        self.assertEqual(code, 1, "a guard that cannot run fails closed")
        self.assertIn("could not run", out)


# =========================================================================== the builder stop check

class TestBuilderStopCheck(OrchCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_mode(ORCH)
        _lib.atomic_write_json(_lib.stub_path("T1"), {"task_id": "T1", "agent": "builder",
                                                      "dispatched_at": iso(-60)})

    def start(self, agent_id: str = "a1", agent_type: str = "builder") -> None:
        _lib.note_subagent_start({"hook_event_name": "SubagentStart", "agent_id": agent_id,
                                  "agent_type": agent_type})

    @staticmethod
    def stop(agent_id: str = "a1", agent_type: str = "builder", **extra) -> dict:
        return dict({"hook_event_name": "SubagentStop", "agent_id": agent_id,
                     "agent_type": agent_type, "stop_hook_active": False}, **extra)

    def test_a_builder_with_no_envelope_is_sent_back(self):
        self.start()
        reason = _lib.builder_stop_reason(self.stop())
        self.assertIsNotNone(reason)
        self.assertIn("T1", reason)
        self.assertIn("checkctl.py handoff", reason)

    def test_a_valid_envelope_written_since_the_start_lets_it_stop(self):
        self.start()
        self.write_envelope()
        self.assertIsNone(_lib.builder_stop_reason(self.stop()))

    def test_an_envelope_in_its_worktree_counts(self):
        self.start()
        self.write_envelope(root=self.root / ".claude" / "worktrees" / "t1")
        self.assertIsNone(_lib.builder_stop_reason(self.stop()))

    def test_an_invalid_envelope_does_not_count(self):
        self.start()
        self.write_envelope(tests=[])
        self.assertIsNotNone(_lib.builder_stop_reason(self.stop()))

    def test_an_envelope_older_than_the_dispatch_does_not_answer_it(self):
        path = self.write_envelope()
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.start()
        self.assertIsNotNone(_lib.builder_stop_reason(self.stop()), "a re-dispatch needs a new one")

    def test_a_task_answered_before_this_agent_began_is_closed(self):
        path = self.write_envelope()
        _lib.atomic_write_json(_lib.stub_path("T1"), {"task_id": "T1", "agent": "builder",
                                                      "dispatched_at": iso(-7200)})
        os.utime(path, (time.time() - 3600,) * 2)
        self.start()
        self.assertIsNone(_lib.builder_stop_reason(self.stop()))

    def test_a_stub_dispatched_after_this_agent_began_is_not_its_task(self):
        _lib.atomic_write_json(_lib.stub_path("T1"), {"task_id": "T1", "agent": "builder",
                                                      "dispatched_at": iso(+600)})
        self.start()
        self.assertIsNone(_lib.builder_stop_reason(self.stop()))

    def test_the_loop_guard_lets_it_stop(self):
        self.start()
        self.assertIsNone(_lib.builder_stop_reason(self.stop(stop_hook_active=True)))

    def test_other_agent_types_stop_untouched(self):
        for kind in ("verifier", "scout", "general-purpose", "anatomist", ""):
            with self.subTest(agent_type=kind):
                self.start("a2", kind)
                self.assertIsNone(_lib.builder_stop_reason(self.stop("a2", kind)))

    def test_other_modes_stop_untouched(self):
        self.start()
        for mode in ("freestyle", "guided-solo"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                self.assertIsNone(_lib.builder_stop_reason(self.stop()))

    def test_an_unknown_start_lets_it_stop(self):
        self.assertIsNone(_lib.builder_stop_reason(self.stop("never-started")))

    def test_the_transcript_dates_the_start_when_no_record_exists(self):
        transcript = self.root / "agent.jsonl"
        transcript.write_text("not json\n" + json.dumps({"timestamp": iso(-5), "type": "user"}) + "\n",
                              encoding="utf-8")
        payload = self.stop("never-started", agent_transcript_path=str(transcript))
        self.assertIsNotNone(_lib.builder_stop_reason(payload))

    def test_malformed_payloads_let_it_stop(self):
        self.start()
        for payload in (None, [], "x", {"agent_type": "builder"}, {"agent_type": ["builder"]}):
            with self.subTest(payload=payload):
                self.assertIsNone(_lib.builder_stop_reason(payload))

    def test_start_records_are_bounded(self):
        for i in range(_lib.AGENT_STARTS_KEPT + 5):
            self.start(f"a{i}")
        self.assertEqual(len(_lib.orchestration_state()["agents"]), _lib.AGENT_STARTS_KEPT)

    def test_nothing_is_recorded_outside_the_mode(self):
        self.set_mode("guided-solo")
        self.start("g1")
        self.assertFalse(_lib.orchestration_state_path().exists())


# =========================================================================== the delegation nudge

class TestDelegationNudge(OrchCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_mode(ORCH)
        self.write_config("orchestration", {"nudge_after": 3, "handoff_test_timeout": 60})

    def edit(self, rel: str, **extra):
        payload = dict({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                        "tool_input": {"file_path": str(self.root / rel)}, "cwd": str(self.root)},
                       **extra)
        return _lib.delegation_nudge(payload)

    def count(self) -> int:
        return int((_lib.orchestration_state().get("nudge") or {}).get("count", 0))

    def test_the_nudge_fires_at_the_threshold_once_then_counts_again(self):
        self.assertIsNone(self.edit("src/a.py"))
        self.assertIsNone(self.edit("src/b.py"))
        note = self.edit("src/c.py")
        self.assertIsNotNone(note)
        self.assertIn("DELEGATION NUDGE", note)
        self.assertIn("orchestration.md", note)
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.edit("src/d.py"))
        self.assertEqual(self.count(), 1)

    def test_bookkeeping_and_prose_do_not_count(self):
        for rel in (".claude/state/x.json", ".claude/tasks/030a-t1.md", "docs/index.html",
                    "README.md", "notes/plan.txt", ".claude/worktrees/t5/docs/a.py",
                    ".claude/worktrees/t5/.claude/state/handshakes/T5.json"):
            with self.subTest(rel=rel):
                self.assertIsNone(self.edit(rel))
        self.assertEqual(self.count(), 0)
        self.assertTrue(_lib.nudge_counts(".claude/worktrees/t5/.claude/tools/x.py"),
                        "code in a worktree is still code")

    def test_files_outside_the_project_do_not_count(self):
        outside = Path(self._tmp.name) / "elsewhere.py"
        payload = {"tool_input": {"file_path": str(outside)}, "cwd": str(self.root)}
        self.assertIsNone(_lib.delegation_nudge(payload))
        self.assertEqual(self.count(), 0)

    def test_sub_agent_edits_do_not_count(self):
        for key in ("agent_type", "agent_name"):
            for _ in range(4):
                self.assertIsNone(self.edit("src/a.py", **{key: "builder"}))
        self.assertEqual(self.count(), 0)

    def test_off_in_other_modes_and_at_zero(self):
        for mode in ("freestyle", "guided-solo"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                for _ in range(4):
                    self.assertIsNone(self.edit("src/a.py"))
        self.set_mode(ORCH)
        self.write_config("orchestration", {"nudge_after": 0, "handoff_test_timeout": 60})
        for _ in range(6):
            self.assertIsNone(self.edit("src/a.py"))
        self.assertFalse(_lib.orchestration_state_path().exists(), "off writes nothing")

    def test_a_dispatch_resets_the_count(self):
        self.edit("src/a.py")
        self.edit("src/b.py")
        _lib.note_subagent_start({"hook_event_name": "SubagentStart", "agent_id": "x",
                                  "agent_type": "scout"})
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.edit("src/c.py"))
        self.assertIsNone(self.edit("src/d.py"))
        self.assertIsNotNone(self.edit("src/e.py"))

    def test_a_corrupt_counter_starts_over_instead_of_failing(self):
        _lib.orchestration_state_path().write_text("{ not json", encoding="utf-8")
        self.assertIsNone(self.edit("src/a.py"))
        self.assertEqual(self.count(), 1)

    def test_the_advisory_channel_isolates_a_broken_source(self):
        payload = {"tool_input": {"file_path": str(self.root / "src" / "a.py")}, "cwd": str(self.root)}
        original = _lib.delegation_nudge
        _lib.delegation_nudge = lambda p: (_ for _ in ()).throw(OSError("disk full"))
        try:
            self.assertEqual(_lib.lead_advisories(payload), [])
        finally:
            _lib.delegation_nudge = original

    def test_advisory_output_is_additional_context(self):
        self.assertIsNone(_lib.advisory_output("PostToolUse", []))
        self.assertIsNone(_lib.advisory_output("PostToolUse", ["", None]))
        out = json.loads(_lib.advisory_output("PostToolUse", ["one", "two"]))
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                      "additionalContext": "one\n\ntwo"}})
        self.assertNotIn("decision", out)


# =========================================================================== registry lint by value

class TestAgentPinLint(OrchCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(CLAUDE_DIR / "agents", self.root / ".claude" / "agents")

    def agent_errors(self):
        result = checkctl.check_config_registry()
        return result, [d for d in result.details if d.startswith("agent.")]

    def test_the_shipped_pins_match_their_frontmatter(self):
        result, errors = self.agent_errors()
        self.assertEqual(errors, [])
        self.assertNotEqual(result.status, checkctl.FAIL, result.details)

    def test_a_deliberate_mismatch_fails_the_lint(self):
        for agent, field, wrong in (("builder", "model", "sonnet"), ("scout", "effort", "high"),
                                    ("verifier", "model", "haiku")):
            with self.subTest(agent=agent, field=field):
                path = self.root / ".claude" / "agents" / f"{agent}.md"
                shipped = path.read_text(encoding="utf-8")
                value = mapctl.parse_frontmatter(shipped)[field]
                path.write_text(shipped.replace(f"{field}: {value}", f"{field}: {wrong}", 1),
                                encoding="utf-8")
                try:
                    result, errors = self.agent_errors()
                    self.assertEqual(result.status, checkctl.FAIL)
                    self.assertTrue(any(e.startswith(f"agent.{agent}.{field}:") and wrong in e
                                        for e in errors), errors)
                finally:
                    path.write_text(shipped, encoding="utf-8")


# =========================================================================== registrations

class TestRegistrations(OrchCase):
    PROTOCOL = CLAUDE_DIR / "protocols" / "orchestration.md"

    def test_the_protocol_defines_roles_routing_card_dispatch_and_handoff(self):
        text = self.PROTOCOL.read_text(encoding="utf-8")
        for heading in ("## Roles", "## Routing", "## The task card", "## Dispatch", "## Handoff",
                        "## Waves", "## The lead never delegates", "## When not to orchestrate"):
            self.assertIn(heading, text)
        for phrase in ("git worktree add .claude/worktrees/<name> -b wt/<name> <branch>",
                       "statectl.py dispatch", "checkctl.py handoff <id> --run --root",
                       "--no-envelope", "_builder-brief.md", "report --by agent"):
            self.assertIn(phrase, text)
        for role, model in (("builder", "opus"), ("scout", "sonnet"), ("verifier", "inherit")):
            self.assertRegex(text, rf"\*\*{role}\*\* \| `agents/{role}.md`, {model}")
        others = [len(p.read_text(encoding="utf-8").splitlines())
                  for p in (CLAUDE_DIR / "protocols").glob("*.md") if p != self.PROTOCOL]
        self.assertLessEqual(len(text.splitlines()), max(others), "keep it to a protocol's size")

    def test_the_two_agents_carry_their_pins_and_tools(self):
        expect = {"builder": ("opus", "high", {"Read", "Grep", "Glob", "Bash", "Write", "Edit"}),
                  "scout": ("sonnet", "medium", {"Read", "Grep", "Glob", "Bash", "WebFetch",
                                                 "WebSearch"})}
        for name, (model, effort, tools) in expect.items():
            with self.subTest(agent=name):
                meta = mapctl.parse_frontmatter(
                    (CLAUDE_DIR / "agents" / f"{name}.md").read_text(encoding="utf-8"))
                self.assertEqual((meta["name"], meta["model"], meta["effort"]), (name, model, effort))
                self.assertEqual({t.strip() for t in meta["tools"].split(",")}, tools)
                body = (CLAUDE_DIR / "agents" / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("## Envelope duty", body)
                self.assertIn("## Never", body)

    def test_every_agent_pin_has_a_registry_card(self):
        keys = {e["key"]: e for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]}
        for path in sorted((CLAUDE_DIR / "agents").glob("*.md")):
            meta = mapctl.parse_frontmatter(path.read_text(encoding="utf-8"))
            for field in ("model", "effort"):
                if field in meta:
                    with self.subTest(agent=path.stem, field=field):
                        card = keys.get(f"agent.{path.stem}.{field}")
                        self.assertIsNotNone(card)
                        self.assertEqual(card["default"], meta[field])

    def test_knobs_cards_probe_hook_wiring_and_store(self):
        keys = {e["key"] for e in _lib.load_config("registry")["entries"]}
        self.assertLessEqual({"orchestration.nudge_after", "orchestration.handoff_test_timeout"}, keys)
        # Agent files present, so no dead-card error truncates the warning list.
        shutil.copytree(CLAUDE_DIR / "agents", self.root / ".claude" / "agents")
        lint = checkctl.check_config_registry()
        self.assertFalse([d for d in lint.details if d.startswith("orchestration.")], lint.details)
        names = {r.name: r.message for r in checkctl.probe()}
        for cid, path in (("agent.builder", ".claude/agents/builder.md"),
                          ("agent.scout", ".claude/agents/scout.md"),
                          ("protocol.orchestration", ".claude/protocols/orchestration.md"),
                          ("hook.handoff-guard", ".claude/hooks/handoff-guard.sh"),
                          ("config.orchestration", ".claude/config/orchestration.json"),
                          ("store.orchestration", ".claude/state/orchestration.json")):
            with self.subTest(component=cid):
                card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / f"{cid}.json")
                self.assertIsNotNone(card, f"{cid} needs its card")
                self.assertEqual(card["path"], path)
                if not cid.startswith("store."):
                    self.assertEqual(names.get(cid), path)
        store = next(s for s in mapctl.KNOWN_STORES if s["id"] == "store.orchestration")
        self.assertEqual(store["path"], ".claude/state/orchestration.json")
        settings = _lib.read_json(CLAUDE_DIR / "settings.json")
        for event in ("SubagentStart", "SubagentStop"):
            commands = [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
            self.assertTrue(any("handoff-guard.sh" in c for c in commands), event)
        self.assertIn("handoff-guard.sh", checkctl.EXPECTED_HOOKS)
        self.assertIn("orchestration", checkctl.DOCTOR_CONFIGS)

    def test_the_runtime_store_is_gitignored(self):
        self.assertIn(".claude/state/orchestration.json",
                      distctl.render_gitignore_block("tracked").splitlines())
        self.assertTrue(checkctl._deliberately_ignored(".claude/state/orchestration.json"))

    def test_the_glossary_defines_the_five_terms(self):
        text = (CLAUDE_DIR / "reference" / "glossary.md").read_text(encoding="utf-8")
        for term in ("lead", "builder", "scout", "task card", "handoff"):
            self.assertRegex(text, rf"(?m)^- \*\*{term}\*\*:", term)

    def test_the_guides_point_at_the_protocol(self):
        guide = (CLAUDE_DIR / "CLAUDE.md").read_text(encoding="utf-8")
        agents_line = guide[guide.index("Agents:"):guide.index("## Layout")]
        for name in ("`builder`", "`scout`", "orchestration.md"):
            self.assertIn(name, agents_line)
        adopt = (CLAUDE_DIR / "skills" / "adopt" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(adopt, r"statectl\.py mode[\s\S]{0,300}protocols/orchestration\.md")
        self.assertIn("handshake.md", self.PROTOCOL.read_text(encoding="utf-8"))
        self.assertIn("orchestration.md",
                      (CLAUDE_DIR / "protocols" / "handshake.md").read_text(encoding="utf-8"))

    def test_scaffolds_are_not_tasks_and_do_ship(self):
        tasks = self.root / ".claude" / "tasks"
        for name in ("_template.md", "_builder-brief.md"):
            shutil.copy(CLAUDE_DIR / "tasks" / name, tasks / name)
        self.assertEqual(checkctl.task_files(), [])
        self.assertEqual(consolectl._read_tasks(), [], "the WORK tab lists no scaffold")
        self.assertEqual(checkctl.check_task_reality().message, "0 active task(s), checkpoints match disk")
        self.assertIsNone(checkctl.parse_task_file(CLAUDE_DIR / "tasks" / "_builder-brief.md"),
                          "the brief has no status line either")
        for rel, ships in ((".claude/tasks/_template.md", True),
                           (".claude/tasks/_builder-brief.md", True),
                           (".claude/research/_template.md", True),
                           (".claude/tasks/030a-t5-fableous-mode.md", False),
                           (".claude/tasks/archive/x/_template.md", False)):
            with self.subTest(rel=rel):
                self.assertEqual(distctl.payload_source(rel), ships)

    def test_the_generic_brief_carries_the_envelope_and_the_worktree_rules(self):
        text = (CLAUDE_DIR / "tasks" / "_builder-brief.md").read_text(encoding="utf-8")
        for phrase in (".claude/worktrees/<wt>/", "Do not use git at all", "checkctl.py handoff",
                       '"agent_id": "builder-<TASK_ID>"', '"needs_main"'):
            self.assertIn(phrase, text)
        block = text[text.index("```json") + len("```json"):text.index("```", text.index("```json") + 7)]
        sample = json.loads(block.replace("done | partial | blocked", "done")
                            .replace('"command": "..."', '"command": "x"'))
        self.assertEqual(_lib.validate_envelope(sample), [], "the brief's own example is valid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
