#!/usr/bin/env python3
"""test_statectl.py - the continuity engine's contract tests.

The whole point of statectl is that session.json/HANDOFF.md/needs-human.json can never
silently drift from the journal they are built from. So these tests are organized around
the specific ways drift could sneak back in: an action the projector forgets about, a torn
line from a crash, a partial-update that clobbers a field it should have left alone, a
needs-human severity that quietly gets de-escalated, and a rebuild that dirties files (and
therefore git) even when nothing actually changed.
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
import statectl  # noqa: E402

VERDICT_RE = re.compile(r"^STATE_(OK|WARN|FAIL)$", re.MULTILINE)


class StatectlCase(FixtureCase):
    """Runs statectl in-process (it is a pure stdlib CLI module, no subprocess needed)."""

    def run_cli(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = statectl.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def assertVerdict(self, stdout: str, state: str) -> None:
        m = VERDICT_RE.search(stdout)
        self.assertIsNotNone(m, f"no STATE_* verdict token in output:\n{stdout!r}")
        self.assertEqual(m.group(1), state)

    def session_json(self) -> dict:
        return _lib.read_json(self.root / ".claude" / "state" / "session.json")

    def needs_json(self) -> dict:
        return _lib.read_json(self.root / ".claude" / "state" / "needs-human.json")

    def handoff_text(self) -> str:
        return (self.root / ".claude" / "state" / "HANDOFF.md").read_text(encoding="utf-8")

    def journal_path(self) -> Path:
        return self.root / ".claude" / "state" / "journal.jsonl"


# --------------------------------------------------------------------------- 1. every action

class TestEveryActionIsProjected(StatectlCase):
    """An action written but not projected is the exact drift bug this system exists to avoid."""

    MINIMAL_FIELDS = {
        "session_start": {"session": "s1", "phase": "build"},
        "pointer": {"text": "do the next thing"},
        "task": {"id": "T1", "title": "write it", "status": "doing"},
        "milestone": {"id": "M1", "title": "shipped a thing"},
        "decision": {"text": "used approach X", "why": "simplest that works"},
        "loop": {"id": "L1", "text": "check this later", "status": "open"},
        "note": {"text": "just narrating"},
        "intent": {"state": "begin", "intent_id": "I1", "op": "write", "files": ["a.py"]},
        "config": {"changes": {"x": 1}, "via": "test"},
        "gate": {"question": "ok to proceed?", "kind": "blocking"},
        "tooling": {"change_type": "add-tool", "what": "statectl.py"},
    }

    def test_all_actions_present_and_covered_by_this_test(self):
        self.assertEqual(set(self.MINIMAL_FIELDS), set(_lib.JOURNAL_ACTIONS))

    def test_every_action_is_projected_without_crashing_and_is_observable(self):
        for action in _lib.JOURNAL_ACTIONS:
            with self.subTest(action=action):
                before = self.session_json() or {"counts": {"events": 0}}
                before_events = before["counts"]["events"]

                _lib.journal_append(action, **self.MINIMAL_FIELDS[action])
                code, out, err = self.run_cli("refresh")
                self.assertEqual(code, 0, f"refresh crashed on action {action!r}: {err}")

                proj = self.session_json()
                self.assertEqual(
                    proj["counts"]["events"], before_events + 1,
                    f"{action!r} was silently dropped: event count did not advance",
                )

        proj = self.session_json()
        self.assertEqual(proj["session"]["id"], "s1")
        self.assertEqual(proj["resume_pointer"], "do the next thing")
        self.assertTrue(any(t["id"] == "T1" for t in proj["tasks"]))
        self.assertTrue(any(m["id"] == "M1" for m in proj["recent_milestones"]))
        self.assertTrue(any(d["why"] == "simplest that works" for d in proj["recent_decisions"]))
        self.assertTrue(any(l["id"] == "L1" for l in proj["open_loops"]))
        self.assertTrue(any(i["intent_id"] == "I1" for i in proj["open_intents"]))
        self.assertTrue(any(g["question"] == "ok to proceed?" for g in proj["open_gates"]))
        self.assertTrue(any(t["what"] == "statectl.py" for t in proj["recent_tooling"]))
        self.assertGreaterEqual(proj["counts"]["milestones"], 1)
        self.assertGreaterEqual(proj["counts"]["decisions"], 1)
        self.assertGreaterEqual(proj["counts"]["tooling"], 1)


# --------------------------------------------------------------------------- 2. torn journal

class TestTornJournalTolerance(StatectlCase):
    def test_torn_last_line_does_not_break_refresh_or_resume(self):
        self.journal(action="session_start", session="s1", phase="build")
        self.journal(action="pointer", text="finish the thing")
        self.journal(action="loop", id="L1", text="watch this", status="open")

        with open(self.journal_path(), "a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-08-25T00:00:00Z", "action": "note", "text": "cut off mid-wri')

        code, out, err = self.run_cli("refresh")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "OK")
        proj = self.session_json()
        self.assertEqual(proj["resume_pointer"], "finish the thing")
        self.assertEqual(proj["counts"]["events"], 3, "the torn line must not be counted")

        code, out, err = self.run_cli("resume")
        self.assertEqual(code, 0)
        self.assertIn("finish the thing", out)

    def test_parseable_line_missing_ts_does_not_crash(self):
        """A torn *last* line is skipped by read_jsonl - but a complete, parseable JSON
        object missing the `ts` key is a different failure mode entirely (survives the
        tolerant read, then a bare `ev["ts"]` subscript would KeyError deep in refresh).
        """
        self.journal(action="session_start", session="s1")
        _lib.append_jsonl(self.journal_path(), {"action": "note"})  # valid JSON, no ts

        code, out, err = self.run_cli("refresh")
        self.assertEqual(code, 0, f"refresh must tolerate an event with no ts: {err}")
        self.assertEqual(self.session_json()["counts"]["events"], 2)

        code, out, err = self.run_cli("resume")
        self.assertEqual(code, 0, f"resume must tolerate an event with no ts: {err}")


# --------------------------------------------------------------------------- 3. task partial update

class TestTaskPartialUpdate(StatectlCase):
    def test_later_event_omitting_a_field_keeps_the_prior_value(self):
        self.run_cli("task", "T1", "--title", "write the thing", "--status", "todo", "--deps", "T0")
        self.run_cli("task", "T1", "--status", "doing")

        task = self.session_json()["tasks"][0]
        self.assertEqual(task["title"], "write the thing", "omitted --title must not clobber it")
        self.assertEqual(task["status"], "doing")
        self.assertEqual(task["deps"], ["T0"], "omitted --deps must not clobber it")

        self.run_cli("task", "T1", "--title", "write the thing (renamed)")
        task = self.session_json()["tasks"][0]
        self.assertEqual(task["status"], "doing", "omitted --status must not revert to default")
        self.assertEqual(task["title"], "write the thing (renamed)")

        self.run_cli("task", "T1", "--status", "done", "--deps", "")
        task = self.session_json()["tasks"][0]
        self.assertEqual(task["deps"], [], "an explicit empty --deps does replace the list")
        self.assertEqual(task["status"], "done")

    def test_counts_reflect_done_vs_open(self):
        self.run_cli("task", "T1", "--title", "a", "--status", "done")
        self.run_cli("task", "T2", "--title", "b", "--status", "todo")
        counts = self.session_json()["counts"]
        self.assertEqual(counts["tasks_total"], 2)
        self.assertEqual(counts["tasks_done"], 1)
        self.assertEqual(counts["tasks_open"], 1)


# --------------------------------------------------------------------------- 4. needs-human

class TestNeedsHumanLifecycle(StatectlCase):
    def test_open_amend_resolve_lifecycle(self):
        code, out, _ = self.run_cli("need", "open", "--title", "pick a library", "--category", "decide", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")
        self.assertEqual(code, 0)
        # ids are derived, not counted (concurrent `need open` used to hand out duplicates and
        # lose a question), so take the id the command reports rather than assuming a sequence.
        nid = next(tok for tok in out.split() if tok.startswith("NH-"))
        needs = self.needs_json()
        self.assertEqual(needs["counts"]["open"], 1)
        task = needs["tasks"][0]
        self.assertEqual(task["id"], nid)
        self.assertEqual(task["band"], "SEV1", "decide defaults to SEV1 per DEFAULT_BAND_BY_CATEGORY")

        code, out, err = self.run_cli("need", "amend", nid, "--band", "SEV0", "--note", "urgent now")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "OK")
        self.assertEqual(self.needs_json()["tasks"][0]["band"], "SEV0")
        self.assertEqual(self.needs_json()["tasks"][0]["note"], "urgent now")

        code, out, err = self.run_cli("need", "amend", nid, "--band", "SEV3")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "WARN")
        self.assertIn("de-escalate", err.lower())
        self.assertEqual(self.needs_json()["tasks"][0]["band"], "SEV0", "de-escalation must be refused")

        code, out, err = self.run_cli("need", "resolve", nid, "--answer", "used stdlib only")
        self.assertEqual(code, 0)
        needs = self.needs_json()
        self.assertEqual(needs["counts"]["open"], 0)
        self.assertEqual(needs["counts"]["resolved"], 1)
        self.assertEqual(needs["resolved_recent"][0]["answer"], "used stdlib only")

    def test_amend_unknown_id_fails(self):
        code, out, err = self.run_cli("need", "amend", "NH-999", "--band", "SEV0")
        self.assertEqual(code, 1)
        self.assertVerdict(out, "FAIL")

    def test_resolve_unknown_id_fails(self):
        code, out, err = self.run_cli("need", "resolve", "NH-999")
        self.assertEqual(code, 1)

    def test_amend_requires_band_or_note(self):
        _, opened, _ = self.run_cli("need", "open", "--title", "x", "--category", "review", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")
        nid = next(tok for tok in opened.split() if tok.startswith("NH-"))
        code, out, err = self.run_cli("need", "amend", nid)
        self.assertEqual(code, 2, "no --band and no --note is a usage error")

    def test_band_first_then_oldest_first_ordering(self):
        old_id = statectl._next_need_id()
        _lib.append_jsonl(
            self.root / ".claude" / "state" / "needs-human.jsonl",
            {"ts": "2020-01-01T00:00:00Z", "op": "open", "id": old_id,
             "title": "old low-sev", "category": "review", "band": "SEV2", "blocks": 0, "note": ""},
            durable=True,
        )
        self.run_cli("need", "open", "--title", "new critical", "--category", "system-blocker", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")
        self.run_cli("need", "open", "--title", "newer low-sev", "--category", "review", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")

        tasks = self.needs_json()["tasks"]
        bands = [t["band"] for t in tasks]
        self.assertEqual(bands, sorted(bands, key=lambda b: _lib.SEV_BANDS.index(b)))
        self.assertEqual(tasks[0]["category"], "system-blocker", "SEV0 must sort first")
        review_tasks = [t for t in tasks if t["category"] == "review"]
        self.assertEqual(review_tasks[0]["title"], "old low-sev", "within a band, oldest first")

    def test_age_days_computed_from_opened_ts(self):
        nid = statectl._next_need_id()
        _lib.append_jsonl(
            self.root / ".claude" / "state" / "needs-human.jsonl",
            {"ts": "2020-01-01T00:00:00Z", "op": "open", "id": nid,
             "title": "ancient", "category": "review", "band": "SEV2", "blocks": 0, "note": ""},
            durable=True,
        )
        self.run_cli("refresh")
        task = self.needs_json()["tasks"][0]
        self.assertGreater(task["age_days"], 365 * 5, "opened in 2020, should read as years old")

    def test_refused_deescalation_with_no_note_records_nothing(self):
        """A refusal that changes nothing (no --note either) must not leave a content-free
        {ts, op, id} line in the append-only ledger - there is nothing there to audit."""
        _, opened, _ = self.run_cli("need", "open", "--title", "x", "--category", "decide", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")
        nid = next(tok for tok in opened.split() if tok.startswith("NH-"))
        ledger = self.root / ".claude" / "state" / "needs-human.jsonl"
        lines_before = len(ledger.read_text().splitlines())

        code, out, err = self.run_cli("need", "amend", nid, "--band", "SEV3")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "WARN")
        lines_after = len(ledger.read_text().splitlines())
        self.assertEqual(lines_after, lines_before, "a no-op refusal must not grow the ledger")
        self.assertEqual(self.needs_json()["tasks"][0]["band"], "SEV1")


# --------------------------------------------------------------------------- 5. write gating

class TestRefreshWriteGating(StatectlCase):
    def test_second_refresh_with_no_new_events_touches_nothing(self):
        self.run_cli("task", "T1", "--title", "a", "--status", "doing")
        self.run_cli("need", "open", "--title", "x", "--category", "review", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")

        session_path = self.root / ".claude" / "state" / "session.json"
        needs_path = self.root / ".claude" / "state" / "needs-human.json"
        handoff_path = self.root / ".claude" / "state" / "HANDOFF.md"

        session_before = session_path.read_text()
        needs_before = needs_path.read_text()
        handoff_before = handoff_path.read_text()
        mtimes_before = {p: p.stat().st_mtime_ns for p in (session_path, needs_path, handoff_path)}

        code, out, err = self.run_cli("refresh")
        self.assertEqual(code, 0)

        for p in (session_path, needs_path, handoff_path):
            self.assertEqual(
                p.stat().st_mtime_ns, mtimes_before[p],
                f"{p.name} was rewritten even though nothing changed",
            )

        strip = statectl._GEN_AT_LINE_RE.sub("", handoff_before)
        self.assertEqual(strip, statectl._GEN_AT_LINE_RE.sub("", handoff_path.read_text()))

        def stripped(text: str) -> dict:
            return {k: v for k, v in json.loads(text).items() if k != "generated_at"}

        self.assertEqual(stripped(session_before), stripped(session_path.read_text()))
        self.assertEqual(stripped(needs_before), stripped(needs_path.read_text()))

    def test_refresh_after_a_new_event_does_change_content(self):
        self.run_cli("task", "T1", "--title", "a", "--status", "doing")
        session_path = self.root / ".claude" / "state" / "session.json"
        before = session_path.read_text()
        self.run_cli("task", "T1", "--status", "done")
        after = session_path.read_text()
        self.assertNotEqual(before, after)


# --------------------------------------------------------------------------- 6. resume + open intents

class TestResumeMentionsUnmatchedIntent(StatectlCase):
    def test_unmatched_begin_is_a_loud_crash_signal(self):
        self.run_cli("intent", "begin", "--id", "I1", "--op", "risky migration")
        code, out, err = self.run_cli("resume")
        self.assertEqual(code, 0)
        self.assertIn("I1", out)
        self.assertIn("open intent", out.lower())
        m = VERDICT_RE.search(out)
        self.assertEqual(m.group(1), "WARN", "an unfinished intent must make resume's verdict loud")
        self.assertLessEqual(len(out.strip().splitlines()), 25, "resume output must stay a short block")

    def test_matched_intent_is_not_reported_as_open(self):
        self.run_cli("intent", "begin", "--id", "I1", "--op", "risky migration")
        self.run_cli("intent", "done", "--id", "I1")
        code, out, err = self.run_cli("resume")
        self.assertNotIn("I1", out)
        m = VERDICT_RE.search(out)
        self.assertEqual(m.group(1), "OK")


# --------------------------------------------------------------------------- heartbeat

class TestHeartbeatProjection(StatectlCase):
    """heartbeat.sh (see test_hooks.py) writes exactly {ts, note}; this is the reader side
    of that contract, and it feeds three separate outputs (session.json, HANDOFF.md, resume)."""

    def write_heartbeat(self, ts: str, note: str) -> None:
        _lib.atomic_write_json(self.root / ".claude" / "state" / "heartbeat.json", {"ts": ts, "note": note})

    def test_absent_heartbeat_is_null(self):
        self.run_cli("pointer", "x")
        self.assertIsNone(self.session_json()["heartbeat"])
        self.assertIn("_Last heartbeat: never_", self.handoff_text())

    def test_present_heartbeat_flows_into_all_three_outputs(self):
        self.write_heartbeat("2020-01-01T00:00:00Z", "turn ended")
        self.run_cli("pointer", "x")

        hb = self.session_json()["heartbeat"]
        self.assertEqual(hb, {"ts": "2020-01-01T00:00:00Z", "note": "turn ended"})

        handoff = self.handoff_text()
        self.assertIn("2020-01-01T00:00:00Z", handoff, "HANDOFF footer carries the absolute ts")
        self.assertIn("turn ended", handoff)

        code, out, err = self.run_cli("resume")
        self.assertRegex(out, r"Heartbeat: \d+[smhd] ago", "resume shows a human-relative age, not raw ISO")
        self.assertNotIn("2020-01-01T00:00:00Z", out, "resume must not print the raw timestamp")


# --------------------------------------------------------------------------- gate lifecycle

class TestGateLifecycle(StatectlCase):
    def test_answered_gate_leaves_open_gates(self):
        self.run_cli("gate", "--question", "ship it?", "--kind", "blocking")
        self.assertTrue(any(g["question"] == "ship it?" for g in self.session_json()["open_gates"]))

        self.run_cli("gate", "--question", "ship it?", "--answer", "yes")
        self.assertEqual(self.session_json()["open_gates"], [], "an answered gate must not stay open")

        self.run_cli("gate", "--question", "ship it?", "--kind", "checkpoint")
        self.assertTrue(
            any(g["question"] == "ship it?" for g in self.session_json()["open_gates"]),
            "re-asking the same question with no answer reopens it",
        )


# --------------------------------------------------------------------------- misc / CLI contract

class TestVerdictAndUsage(StatectlCase):
    def test_every_command_prints_exactly_one_verdict_token(self):
        self.run_cli("start", "--session", "s1")
        commands = [
            ("pointer", "next step"),
            ("task", "T1", "--title", "a", "--status", "todo"),
            ("milestone", "M1", "--title", "shipped"),
            ("decision", "did a thing", "--why", "reasons"),
            ("loop", "L1", "--text", "watch", "--status", "open"),
            ("note", "narration"),
            ("intent", "begin", "--id", "I1"),
            ("intent", "done", "--id", "I1"),
            ("gate", "--question", "q?", "--answer", "yes"),
            ("tooling", "--change-type", "add", "--what", "x"),
            ("need", "list"),  # empty branch: no needs opened yet
            ("need", "open", "--title", "check this", "--category", "review", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question"),
            ("need", "list"),  # non-empty branch
            ("refresh",),
            ("resume",),
            ("status",),
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                tokens = VERDICT_RE.findall(out)
                self.assertEqual(len(tokens), 1, f"{argv} printed {len(tokens)} verdict tokens:\n{out!r}")

    def test_need_list_prints_open_needs(self):
        _, opened, _ = self.run_cli("need", "open", "--title", "pick a library", "--category", "decide", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question")
        nid = next(tok for tok in opened.split() if tok.startswith("NH-"))
        code, out, err = self.run_cli("need", "list")
        self.assertEqual(code, 0)
        self.assertIn(nid, out)
        self.assertIn("SEV1", out, "decide defaults to SEV1")
        self.assertIn("pick a library", out)

    def test_need_list_with_nothing_open(self):
        code, out, err = self.run_cli("need", "list")
        self.assertEqual(code, 0)
        self.assertVerdict(out, "OK")

    def test_invalid_task_status_is_a_usage_error(self):
        code, out, err = self.run_cli("task", "T1", "--status", "not-a-status")
        self.assertEqual(code, 2)

    def test_handoff_header_says_derived_and_do_not_hand_edit(self):
        self.run_cli("pointer", "resume here")
        text = self.handoff_text()
        first_line = text.splitlines()[0]
        self.assertIn("AUTO-GENERATED", first_line)
        self.assertIn("do not hand-edit", first_line.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDurableWriteOnWindows(FixtureCase):
    """atomic_write_text(durable=True) fsyncs the parent directory - a POSIX-only idiom.
    On Windows, os.open() on a directory raises PermissionError, which bricked checkctl
    (and with it the whole ritual) on the first real Windows adoption. These tests try to
    BREAK the guard: simulate the Windows behavior and prove the durable write still lands,
    then prove POSIX still pays for the directory fsync it promises."""

    def test_fsync_dir_is_a_deliberate_noop_off_posix(self):
        # os.name is patched only around _fsync_dir itself: pathlib picks its Path flavour
        # from os.name, so a wider patch would break every Path() the code under test makes.
        import os as os_mod
        from unittest import mock
        target_dir = self.root / ".claude" / "state"
        target_dir.mkdir(parents=True, exist_ok=True)
        real_open = os_mod.open

        def windows_like_open(path, flags, *args, **kwargs):
            # On Windows, opening a DIRECTORY raises PermissionError. If the guard were
            # gone, this poison pill is what the durable path would swallow.
            if isinstance(path, str) and os_mod.path.isdir(path):
                raise PermissionError(13, "Permission denied", path)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(_lib.os, "name", "nt"), \
             mock.patch.object(_lib.os, "open", side_effect=windows_like_open):
            _lib._fsync_dir(target_dir)  # must not raise, and must not open the directory

    def test_posix_durable_write_still_fsyncs_the_directory(self):
        import os as os_mod
        from unittest import mock
        if os_mod.name != "posix":
            self.skipTest("directory fsync is a POSIX-only guarantee")
        synced = []
        real_fsync = os_mod.fsync

        def counting_fsync(fd):
            synced.append(fd)
            return real_fsync(fd)

        with mock.patch.object(_lib.os, "fsync", side_effect=counting_fsync):
            _lib.atomic_write_json(self.root / ".claude" / "state" / "p.json", {"x": 1}, durable=True)
        self.assertGreaterEqual(len(synced), 2,
                                "durable=True must fsync the file AND its directory on POSIX")
