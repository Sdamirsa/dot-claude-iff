#!/usr/bin/env python3
"""test_progress.py - the long-run progress model and its two telemetry halves.

What is pinned here: the ONE model (tools/progress.py) computed from a fixture with a milestone,
mixed task states and checklists gives the expected numbers (percent, counts, groups, natural
order, the task-file match, agents in flight, needs-human, proposals, run, last activity); the
honest empty states; the compact text block (grouping, NEEDS YOU only when open, one agents
line, no ANSI, ASCII unless asked, about 15 lines for a dozen tasks); `statectl progress` and
`--json`, including a forced ASCII stream; read-only; the activity pulse's throttle (in process;
the hooks run end to end in test_hooks.py); the periodic report (due / not due / knob 0 /
freestyle / sub-agent / empty model); and the registrations (knobs, cards, probe, glossary,
protocol, skill, guide).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import mapctl  # noqa: E402
import progress  # noqa: E402
import statectl  # noqa: E402

ORCH = "fableous-orchestrated"
STATECTL = CLAUDE_DIR / "tools" / "statectl.py"


def iso(offset: float = 0.0) -> str:
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_file(title: str, checked: int, unchecked: int, status: str = "doing",
              extra_plan: str = "") -> str:
    boxes = ["- [x] done step, done when: x"] * checked + ["- [ ] open step, done when: y"] * unchecked
    return (f"# Task: {title}\n\n_Created 2026-10-05 · Status: {status}_\n\n## Goal\n\ng\n\n"
            f"- [x] a checkbox outside Plan never counts\n\n## Plan\n\n" + "\n".join(boxes)
            + extra_plan + "\n\n## Checkpoint\n\n- [ ] not a plan item either\n")


class ProgressCase(FixtureCase):
    def statectl(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = statectl.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_task(self, stem: str, text: str) -> Path:
        path = self.root / ".claude" / "tasks" / f"{stem}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def stub(self, task_id: str, agent: str = "builder", when: str | None = None,
             worktree: str | None = None) -> Path:
        stub = {"task_id": task_id, "agent": agent, "dispatched_at": when or iso(-600)}
        if worktree:
            stub["worktree"] = worktree
        path = _lib.stub_path(task_id)
        _lib.atomic_write_json(path, stub)
        return path

    @staticmethod
    def builder_envelope(task_id: str) -> dict:
        return {"agent_id": f"builder-{task_id}", "task_id": task_id, "status": "done",
                "agent": "builder", "model": "opus", "files_changed": [], "needs_main": [],
                "tests": [{"command": "x", "exit_code": 0, "summary": "ok"}]}

    def milestone_fixture(self) -> None:
        """M1 with six tasks in mixed states, one task under another milestone, one need open
        and one resolved, two proposals of which one resolved, a builder in flight on T3."""
        self.journal("mode", value=ORCH)
        self.journal("milestone", id="M0", title="an older milestone")
        self.journal("task", id="T9", title="old", status="doing", milestone="M0")
        self.journal("milestone", id="M1", title="the first body of work")
        self.journal("phase", value="build")
        for tid, status in (("T1", "done"), ("T2", "doing"), ("T3", "todo"), ("T4", "todo"),
                            ("T5", "done"), ("T10", "blocked")):
            self.journal("task", id=tid, title=f"journal title {tid}", status=status, milestone="M1")
        # T1 by the id leading its title, done with one of four ticked: counts 4/4.
        self.write_task("030x-t1-alpha", task_file("T1 - alpha work", 1, 3, "done"))
        # T2 by the id leading its title: 2/4.
        self.write_task("030x-t2-beta", task_file("T2 - beta work", 2, 2))
        # T3 by the file stem (no id in the title): 0/2.
        self.write_task("t3", task_file("gamma work", 0, 2, "todo"))
        # T4 and T5 have no task file: one item each, checked only when done.
        # T10 sorts after T5 (natural order) and has a sub-heading inside Plan: 1/3.
        self.write_task("030x-t10-delta", task_file("T10 - delta work", 1, 1, "blocked",
                                                    extra_plan="\n\n### more\n\n- [ ] nested"))
        self.write_task("_template", task_file("<name>", 0, 9))  # a scaffold, never a task
        self.stub("T3", worktree=".claude/worktrees/t3")
        self.statectl("need", "open", "--title", "pick a colour", "--category", "decide",
                      "--context", "the shed needs paint and two colours are on the table, it "
                                   "blocks T10", "--action", "say red or green")
        code, out, _ = self.statectl("need", "open", "--title", "old question",
                                     "--category", "review", "--context", "x" * 70,
                                     "--action", "look")
        old = next(tok for tok in out.split() if tok.startswith("NH-"))
        self.statectl("need", "resolve", old, "--answer", "done")
        self.statectl("proposal", "add", "idea one", "--source", "human")
        self.statectl("proposal", "add", "idea two", "--source", "human")
        self.statectl("proposal", "resolve", "PR-1", "--as", "rejected", "--note", "no")


# =========================================================================== the checklist parser

class TestPlanChecklist(unittest.TestCase):
    def test_counts_only_the_plan_section_with_its_sub_headings(self):
        text = task_file("T1 - x", 2, 3, extra_plan="\n\n### later\n\n- [X] upper-case counts\n* [ ] star")
        self.assertEqual(progress.plan_checklist(text), (3, 7))

    def test_no_plan_or_no_boxes_is_zero(self):
        self.assertEqual(progress.plan_checklist("# Task: x\n\n## Goal\n\n- [x] a\n"), (0, 0))
        self.assertEqual(progress.plan_checklist("## Plan\n\nprose only\n"), (0, 0))
        self.assertEqual(progress.plan_checklist(""), (0, 0))

    def test_the_shipped_template_reads_as_three_open_items(self):
        text = (CLAUDE_DIR / "tasks" / "_template.md").read_text(encoding="utf-8")
        self.assertEqual(progress.plan_checklist(text), (0, 3))

    def test_crlf_files_count_the_same(self):
        text = task_file("T1 - x", 1, 1)
        self.assertEqual(progress.plan_checklist(text.replace("\n", "\r\n")), (1, 2))


# =========================================================================== the model

class TestModel(ProgressCase):
    def setUp(self) -> None:
        super().setUp()
        self.milestone_fixture()
        self.model = progress.compute()
        self.tasks = {t["id"]: t for t in self.model["tasks"]}

    def test_the_numbers(self):
        totals = self.model["totals"]
        # Units: T1 4 (done: all) + T2 4 + T3 2 + T4 1 + T5 1 (done) + T10 3 = 15;
        # checked: 4 + 2 + 0 + 0 + 1 + 1 = 8 -> 53%.
        self.assertEqual((totals["checklist_done"], totals["checklist_total"]), (8, 15))
        self.assertEqual(totals["percent"], 53)
        self.assertEqual((totals["tasks_done"], totals["tasks_total"]), (2, 6))
        self.assertIn("plan items", totals["basis"])

    def test_milestone_dials_and_order(self):
        self.assertEqual(self.model["milestone"], {"id": "M1", "title": "the first body of work"})
        self.assertEqual((self.model["mode"], self.model["mode_label"], self.model["phase"]),
                         (ORCH, "Fableous Orchestrated", "build"))
        self.assertIsNone(self.model["empty"])
        self.assertEqual([t["id"] for t in self.model["tasks"]], ["T1", "T2", "T3", "T4", "T5", "T10"],
                         "the milestone's tasks only, in natural order (T10 after T5)")

    def test_per_task_counts_titles_and_files(self):
        t1, t2, t3, t4, t10 = (self.tasks[k] for k in ("T1", "T2", "T3", "T4", "T10"))
        self.assertEqual((t1["checklist_done"], t1["checklist_total"]), (1, 4), "the file's own ticks")
        self.assertEqual((t2["checklist_done"], t2["checklist_total"]), (2, 4))
        self.assertEqual((t3["checklist_done"], t3["checklist_total"]), (0, 2))
        self.assertEqual((t4["checklist_done"], t4["checklist_total"], t4["file"]), (0, 0, None))
        self.assertEqual((t10["checklist_done"], t10["checklist_total"]), (1, 3))
        self.assertEqual(t2["title"], "beta work", "the file title, its leading id stripped")
        self.assertEqual(t3["title"], "gamma work")
        self.assertEqual(t4["title"], "journal title T4", "no file: the journal title")
        self.assertEqual(t2["file"], ".claude/tasks/030x-t2-beta.md")
        self.assertEqual(t3["file"], ".claude/tasks/t3.md", "matched by the file stem")

    def test_the_task_file_match_is_checkctls(self):
        files = checkctl.task_files(include_archive=True)
        for tid, task in self.tasks.items():
            with self.subTest(task=tid):
                hit = next((f for f in files if tid.casefold() in [i.casefold() for i in f["ids"]]), None)
                self.assertEqual(task["file"], _lib.rel(hit["path"]) if hit else None)

    def test_groups(self):
        groups = {tid: t["group"] for tid, t in self.tasks.items()}
        self.assertEqual(groups, {"T1": "done", "T5": "done", "T2": "in progress",
                                  "T3": "in progress", "T4": "waiting", "T10": "waiting"},
                         "a todo task with an agent in flight is in progress")

    def test_agents_in_flight(self):
        flight = self.model["agents_in_flight"]
        self.assertEqual([(a["task"], a["agent"], a["worktree"]) for a in flight],
                         [("T3", "builder", ".claude/worktrees/t3")])
        self.assertTrue(590 <= flight[0]["elapsed"] <= 700, flight[0])
        t3 = self.tasks["T3"]
        self.assertEqual(t3["in_flight"]["agent"], "builder")
        self.assertFalse(t3["has_valid_envelope"])
        self.assertIsNone(self.tasks["T2"]["in_flight"])

    def test_needs_human_proposals_run_and_last_activity(self):
        needs = self.model["needs_human"]
        self.assertEqual([(n["title"], n["band"]) for n in needs], [("pick a colour", "SEV1")])
        self.assertIsInstance(needs[0]["age"], int)
        self.assertEqual(self.model["proposals_open"], 1)
        phase_ts = [e["ts"] for e in _lib.journal_read() if e["action"] == "phase"][-1]
        self.assertEqual(self.model["run"]["started"], phase_ts, "run started = the phase event")
        self.assertIsInstance(self.model["run"]["elapsed"], int)
        last = self.model["last_activity"]
        self.assertEqual(last["source"], "journal", "no heartbeat yet: the journal is the newest sign")

    def test_the_heartbeat_wins_when_newer_and_says_working(self):
        _lib.atomic_write_json(_lib.heartbeat_path(), {"ts": iso(+5), "note": "working", "via": "Bash"})
        last = progress.compute()["last_activity"]
        self.assertEqual((last["source"], last["note"]), ("heartbeat", "working (Bash)"))
        _lib.atomic_write_json(_lib.heartbeat_path(), {"ts": "2020-01-01T00:00:00Z", "note": "turn ended"})
        self.assertEqual(progress.compute()["last_activity"]["source"], "journal")

    def test_run_falls_back_to_the_milestone_event_when_no_phase_is_set(self):
        self.journal("mode", value="freestyle")
        events = [e for e in _lib.journal_read() if e["action"] != "phase"]
        _lib.journal_path().write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        model = progress.compute()
        m1 = [e["ts"] for e in events if e["action"] == "milestone" and e["id"] == "M1"][-1]
        self.assertIsNone(model["phase"])
        self.assertEqual(model["run"]["started"], m1)

    def test_the_model_is_json_and_ages_are_the_only_clock(self):
        json.dumps(self.model)  # must serialise
        later = progress._now
        try:
            progress._now = lambda: time.time() + 3600
            moved = progress.compute()
        finally:
            progress._now = later
        self.assertNotEqual(moved["run"]["elapsed"], self.model["run"]["elapsed"])
        self.assertEqual(progress.without_clock(moved), progress.without_clock(self.model))


class TestInFlight(ProgressCase):
    def setUp(self) -> None:
        super().setUp()
        self.journal("milestone", id="M1", title="m")
        self.journal("task", id="T1", title="t", status="doing", milestone="M1")

    def flight(self) -> list:
        return [a["task"] for a in progress.compute()["agents_in_flight"]]

    def test_a_valid_envelope_in_the_worktree_lands_the_agent(self):
        self.stub("T1", worktree=".claude/worktrees/t1")
        self.assertEqual(self.flight(), ["T1"])
        wt = self.root / ".claude" / "worktrees" / "t1"
        _lib.atomic_write_json(_lib.envelope_path("T1", wt), self.builder_envelope("T1"))
        model = progress.compute()
        self.assertEqual(model["agents_in_flight"], [])
        task = model["tasks"][0]
        self.assertTrue(task["has_valid_envelope"])
        self.assertEqual(task["group"], "in progress")
        self.assertIn("handoff ready", progress.render_text(model))

    def test_an_invalid_or_older_envelope_does_not(self):
        self.stub("T1")
        bad = dict(self.builder_envelope("T1"), tests=[])
        _lib.atomic_write_json(_lib.envelope_path("T1"), bad)
        self.assertEqual(self.flight(), ["T1"], "a builder envelope with no test is not a handoff")
        path = _lib.envelope_path("T1")
        _lib.atomic_write_json(path, self.builder_envelope("T1"))
        os.utime(path, (time.time() - 7200,) * 2)
        self.assertEqual(self.flight(), ["T1"], "an envelope from before this dispatch answers an older one")

    def test_a_done_task_is_not_in_flight_and_a_scout_answers_with_any_valid_envelope(self):
        self.stub("T1")
        self.journal("task", id="T1", status="done")
        self.assertEqual(self.flight(), [])
        self.stub("survey", agent="scout")
        self.assertEqual(self.flight(), ["survey"], "a stub outside the milestone is still an agent")
        _lib.atomic_write_json(_lib.envelope_path("survey"),
                               {"agent_id": "scout-1", "task_id": "survey", "status": "done"})
        self.assertEqual(self.flight(), [])


class TestEmpty(ProgressCase):
    def test_no_milestone(self):
        model = progress.compute()
        self.assertIsNone(model["milestone"])
        self.assertIn("no milestone yet", model["empty"])
        self.assertEqual((model["tasks"], model["totals"]["percent"]), ([], 0))
        text = progress.render_text(model)
        self.assertIn("PROGRESS: no milestone yet", text)
        self.assertLessEqual(len(text.splitlines()), 4)

    def test_a_milestone_with_no_tasks(self):
        self.journal("milestone", id="M2", title="two")
        self.journal("task", id="T1", status="todo")  # no milestone: not M2's
        model = progress.compute()
        self.assertEqual(model["milestone"], {"id": "M2", "title": "two"})
        self.assertIn("milestone M2 has no tasks yet", model["empty"])
        self.assertEqual(model["tasks"], [])

    def test_a_milestone_named_only_by_its_tasks(self):
        self.journal("task", id="T1", status="doing", milestone="M3")
        model = progress.compute()
        self.assertEqual(model["milestone"], {"id": "M3", "title": ""})
        self.assertIsNone(model["empty"])
        self.assertEqual(model["run"], {"started": None, "elapsed": None})

    def test_corrupt_sources_degrade(self):
        self.journal("milestone", id="M1", title="m")
        self.journal("task", id="T1", status="doing", milestone="M1")
        _lib.heartbeat_path().write_text("{ not json", encoding="utf-8")
        _lib.stub_path("T1").write_text("[1, 2", encoding="utf-8")
        (_lib.state_dir() / "needs-human.jsonl").write_bytes(b"\xff\xfe garbage\n")
        model = progress.compute()  # must not raise
        self.assertEqual(model["tasks"][0]["id"], "T1")
        self.assertEqual([a["task"] for a in model["agents_in_flight"]], ["T1"],
                         "an unreadable stub still names its task (from the file name)")


# =========================================================================== the text block

class TestText(ProgressCase):
    def dozen(self) -> dict:
        self.journal("mode", value=ORCH)
        self.journal("milestone", id="M1", title="a dozen tasks with a title long enough to be clipped "
                                                 "somewhere on the first line of the block")
        self.journal("phase", value="build")
        for i in range(1, 13):
            status = "done" if i <= 7 else ("doing" if i <= 9 else "todo")
            self.journal("task", id=f"T{i}", title=f"task {i}", status=status, milestone="M1")
            self.write_task(f"t{i}", task_file(f"T{i} - work item {i}", i % 3, 2))
        self.stub("T8")
        self.statectl("need", "open", "--title", "approve it", "--category", "approve-release",
                      "--context", "c" * 70, "--action", "approve")
        return progress.compute()

    def test_a_dozen_tasks_fit_a_chat_message(self):
        text = progress.render_text(self.dozen())
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 15, text)
        self.assertTrue(all(len(line) <= 110 for line in lines), text)
        self.assertNotIn("\x1b", text, "no ANSI colours")
        self.assertTrue(all(ord(c) < 128 for c in text), "ASCII unless asked")
        for label in ("done (7)", "in progress", "waiting (3)", "NEEDS YOU (1)", "agents",
                      "last activity", "% of plan items checked"):
            self.assertIn(label, text)
        self.assertEqual(sum(1 for line in lines if line.startswith("agents")), 1)
        self.assertRegex(text, r"\[#+-*\] \d+% of plan items checked \(\d+/\d+\)")
        self.assertIn("T8 [", text)
        self.assertIn("builder 10m", text)

    def test_unicode_variant_and_no_needs_section_when_none_open(self):
        model = self.dozen()
        text = progress.render_text(model, unicode=True)
        self.assertIn("█", text)
        self.assertIn(" · ", text)
        model["needs_human"] = []
        self.assertNotIn("NEEDS YOU", progress.render_text(model))

    def test_bars_and_durations(self):
        self.assertEqual(progress.bar(0, 0, 10), "[----------]")
        self.assertEqual(progress.bar(9, 10, 10), "[#########-]")
        self.assertEqual(progress.bar(99, 100, 10), "[#########-]", "full only when done")
        self.assertEqual(progress.bar(5, 5, 4, unicode=True), "[████]")
        for seconds, shown in ((None, "?"), (5, "5s"), (125, "2m"), (3 * 3600 + 300, "3h05m"),
                               (2 * 86400 + 3 * 3600, "2d03h")):
            self.assertEqual(progress.fmt_duration(seconds), shown)


# =========================================================================== the CLI

class TestCLI(ProgressCase):
    def setUp(self) -> None:
        super().setUp()
        self.milestone_fixture()
        # T4 has no task file, so its journal title is the one shown on its in-progress line.
        self.journal("task", id="T4", title="naïve — unicode title", status="doing")

    def test_text_and_json(self):
        code, out, _ = self.statectl("progress")
        self.assertEqual(code, 0)
        self.assertEqual(re.findall(r"^STATE_(?:OK|WARN|FAIL)$", out, re.M), ["STATE_OK"])
        self.assertIn("PROGRESS M1", out)
        code, out, err = self.statectl("progress", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)  # stdout is pure JSON
        self.assertIn("STATE_OK", err)
        for key in ("mode", "phase", "milestone", "tasks", "totals", "needs_human", "proposals_open",
                    "agents_in_flight", "run", "last_activity"):
            self.assertIn(key, data)
        self.assertEqual(data["totals"]["percent"], progress.compute()["totals"]["percent"])

    def test_a_forced_ascii_stream_never_crashes(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            code = statectl.main(["progress"])
        stream.flush()
        text = raw.getvalue().decode("ascii")  # every byte is ASCII
        self.assertEqual(code, 0)
        self.assertRegex(text, r"\[[#-]{20}\] \d+% of plan items", "the ASCII bars, not replaced blocks")
        self.assertIn("STATE_OK", text)

    def test_a_real_process_on_an_ascii_console(self):
        """What a Windows console on cp1252 does: the encoding cannot hold the block bars."""
        env = dict(os.environ, PYTHONIOENCODING="ascii", CLAUDE_PROJECT_DIR=str(self.root))
        res = subprocess.run([sys.executable, str(STATECTL), "progress"], capture_output=True,
                             env=env, timeout=60, check=False)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        text = res.stdout.decode("ascii")
        self.assertRegex(text, r"\[[#-]{20}\] \d+% of plan items checked")
        self.assertIn("na?ve ? unicode title", text.replace("\r", ""))

    def test_progress_is_read_only(self):
        def snapshot():
            out = {}
            for p in sorted(self.root.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    out[p.relative_to(self.root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
            return out
        before = snapshot()
        self.statectl("progress")
        self.statectl("progress", "--json")
        progress.compute()
        self.assertEqual(snapshot(), before)


# =========================================================================== the activity pulse

class TestActivityPulse(ProgressCase):
    def beat(self) -> dict:
        return _lib.read_json(_lib.heartbeat_path(), None)

    def set_knobs(self, **progress_knobs) -> None:
        cfg = _lib.load_config("orchestration", use_cache=False)
        cfg["progress"] = dict(cfg.get("progress") or {}, **progress_knobs)
        self.write_config("orchestration", cfg)

    def test_due_when_missing_then_throttled(self):
        self.assertTrue(_lib.activity_pulse("Bash"))
        beat = self.beat()
        self.assertEqual((beat["note"], beat["via"]), ("working", "Bash"))
        self.assertFalse(_lib.activity_pulse("Write"), "younger than pulse_seconds: not due")
        self.assertEqual(self.beat()["via"], "Bash")

    def test_due_after_the_window(self):
        _lib.atomic_write_json(_lib.heartbeat_path(), {"ts": iso(-61), "note": "working", "via": "Bash"})
        self.assertTrue(_lib.activity_pulse("Edit"))
        self.assertEqual(self.beat()["via"], "Edit")
        self.assertLess(_lib.age_seconds(self.beat()["ts"]), 5)
        _lib.atomic_write_json(_lib.heartbeat_path(), {"ts": iso(-59), "note": "working", "via": "Bash"})
        self.assertFalse(_lib.activity_pulse("Edit"))

    def test_a_turn_ended_beat_is_superseded_at_once(self):
        _lib.atomic_write_json(_lib.heartbeat_path(), {"ts": iso(-2), "note": "turn ended"})
        self.assertTrue(_lib.activity_pulse("Bash"))
        self.assertEqual(self.beat()["note"], "working")

    def test_a_corrupt_beat_is_repaired(self):
        _lib.heartbeat_path().write_text("{ not json", encoding="utf-8")
        self.assertTrue(_lib.activity_pulse("Bash"))
        self.assertEqual(self.beat()["note"], "working")

    def test_zero_turns_it_off_and_a_bad_knob_reads_as_the_default(self):
        self.set_knobs(pulse_seconds=0)
        self.assertFalse(_lib.activity_pulse("Bash"))
        self.assertFalse(_lib.heartbeat_path().exists())
        self.write_config("orchestration", {"nudge_after": 8, "progress": {"pulse_seconds": "soon"}})
        self.assertEqual(_lib.progress_knob("pulse_seconds"), 60)
        self.write_config("orchestration", {"nudge_after": 8, "progress": ["x"]})
        self.assertEqual(_lib.progress_knob("report_minutes"), 30)

    def test_via_is_a_name_never_an_input(self):
        _lib.activity_pulse("Bash; rm -rf / && echo $(secret)")
        self.assertRegex(self.beat()["via"], r"^[A-Za-z0-9._-]+$")


# =========================================================================== the periodic report

class TestPeriodicReport(ProgressCase):
    LEAD = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": "x"}}

    def setUp(self) -> None:
        super().setUp()
        self.journal("mode", value="guided-solo")
        self.journal("milestone", id="M1", title="m")
        self.journal("task", id="T1", title="t", status="doing", milestone="M1")

    def last_at(self):
        report = _lib.orchestration_state().get("report") or {}
        return report.get("last_at")

    def set_minutes(self, minutes: int) -> None:
        cfg = _lib.load_config("orchestration", use_cache=False)
        cfg["progress"] = dict(cfg.get("progress") or {}, report_minutes=minutes)
        self.write_config("orchestration", cfg)

    def test_due_hands_the_block_with_the_instruction(self):
        self.assertIsNone(self.last_at(), "no report yet counts as due")
        notes = _lib.lead_advisories(self.LEAD)  # the shared advisory channel
        self.assertEqual(len(notes), 1, notes)
        note = notes[0]
        self.assertIn("post this progress block to the user as is, then continue", note)
        self.assertIn("PROGRESS M1", note)
        self.assertIn("```", note)
        self.assertIsNotNone(self.last_at())
        self.assertIsNone(_lib.progress_report(self.LEAD), "just reported: not due again")

    def test_not_due_inside_the_window_due_after_it(self):
        _lib.atomic_write_json(_lib.orchestration_state_path(), {"report": {"last_at": iso(-5 * 60)}})
        self.assertIsNone(_lib.progress_report(self.LEAD))
        _lib.atomic_write_json(_lib.orchestration_state_path(),
                               {"report": {"last_at": iso(-31 * 60)}, "nudge": {"count": 2}})
        self.assertIsNotNone(_lib.progress_report(self.LEAD))
        self.assertEqual(_lib.orchestration_state()["nudge"], {"count": 2}, "other runtime keys survive")
        self.assertIsNone(_lib.progress_report(self.LEAD), "just reported: not due again")

    def test_knob_zero_freestyle_and_sub_agents_get_nothing(self):
        self.set_minutes(0)
        self.assertIsNone(_lib.progress_report(self.LEAD))
        self.set_minutes(30)
        self.journal("mode", value="freestyle")
        self.assertIsNone(_lib.progress_report(self.LEAD))
        self.journal("mode", value=ORCH)
        for key in ("agent_type", "agent_name"):
            self.assertIsNone(_lib.progress_report(dict(self.LEAD, **{key: "builder"})))
        self.assertFalse(_lib.orchestration_state_path().exists(), "nothing recorded for any of them")
        self.assertIsNotNone(_lib.progress_report(self.LEAD), "fableous-orchestrated reports too")

    def test_an_empty_model_is_not_reported(self):
        events = [e for e in _lib.journal_read() if e["action"] == "mode"]
        _lib.journal_path().write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        self.assertIsNone(_lib.progress_report(self.LEAD))
        self.assertFalse(_lib.orchestration_state_path().exists())

    def test_a_broken_model_costs_only_the_note(self):
        original = progress.compute
        progress.compute = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(_lib.lead_advisories(self.LEAD), [])
        finally:
            progress.compute = original


# =========================================================================== registrations

class TestRegistrations(unittest.TestCase):
    def test_knobs_ship_with_their_cards_and_defaults(self):
        cfg = _lib.read_json(CLAUDE_DIR / "config" / "orchestration.json")
        self.assertEqual(cfg["progress"], _lib.PROGRESS_DEFAULTS)
        cards = {e["key"]: e for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]}
        for knob in ("pulse_seconds", "report_minutes"):
            with self.subTest(knob=knob):
                card = cards[f"orchestration.progress.{knob}"]
                self.assertEqual(card["target"], {"kind": "config", "file": "orchestration",
                                                  "path": f"progress.{knob}"})
                self.assertEqual(card["default"], _lib.PROGRESS_DEFAULTS[knob])

    def test_component_card_probe_and_stores(self):
        card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "tool.progress.json")
        self.assertEqual(card["path"], ".claude/tools/progress.py")
        self.assertIn(("tool.progress", ".claude/tools/progress.py"),
                      [(r.name, r.message) for r in checkctl.probe()])
        stores = {s["id"]: s["description"] for s in mapctl.KNOWN_STORES}
        self.assertIn("progress report", stores["store.orchestration"])
        self.assertIn("activity pulse", stores["store.heartbeat"])
        for sid in ("store.orchestration", "store.heartbeat"):
            on_card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / f"{sid}.json")["description"]
            self.assertEqual(on_card, stores[sid])

    def test_docs_name_it(self):
        glossary = (CLAUDE_DIR / "reference" / "glossary.md").read_text(encoding="utf-8")
        self.assertRegex(glossary, r"(?m)^- \*\*progress model\*\*:")
        protocol = (CLAUDE_DIR / "protocols" / "orchestration.md").read_text(encoding="utf-8")
        for phrase in ("statectl.py progress", "progress.report_minutes", "post it to the user as is",
                       "always-on view", "at each merge"):
            self.assertIn(phrase, protocol)
        skill = (CLAUDE_DIR / "skills" / "project-memory" / "SKILL.md").read_text(encoding="utf-8")
        report = skill[skill.index("## The report"):skill.index("## Phase 5")]
        self.assertIn("statectl.py progress", report)
        guide = (CLAUDE_DIR / "CLAUDE.md").read_text(encoding="utf-8")
        commands = guide[guide.index("## Commands"):]
        commands = commands[:commands.index("python3 .claude/tools/checkctl.py")]
        self.assertRegex(commands, r"\|progress\b", "statectl's command list names progress")


if __name__ == "__main__":
    unittest.main(verbosity=2)
