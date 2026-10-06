#!/usr/bin/env python3
"""test_ritual.py - the ritual is the user's: the prompt hook's ticket, checkctl's ticket gate,
the nudges that ask instead of run, the session counter, and ROUTE.

The ticket proves the USER typed /project-memory (or /adopt). Only hooks/ritual-ticket.sh writes
it, `checkctl run` and `complete` refuse without a fresh one, `complete` consumes it, and every
read-only subcommand ignores it. These tests run the REAL hook scripts against a throwaway
project, and drive checkctl in-process with a ticket granted the way the hook writes it
(FixtureCase.grant_ticket): there is no flag or env var that skips the check, by design.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import distctl  # noqa: E402
import mapctl  # noqa: E402

HOOKS = CLAUDE_DIR / "hooks"
# A full path, never the bare name: on Windows a bare "bash" can resolve to the WSL launcher.
BASH = _lib.find_bash() or "bash"
ASK = "ask the user to type /project-memory; only the user can open the ritual"


def stamp(delta_seconds: float = 0.0) -> str:
    """A ticket/journal timestamp `delta_seconds` from now (negative = in the past)."""
    when = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class HookRunner(FixtureCase):
    """The repo's real hook scripts, run with the fixture project as CLAUDE_PROJECT_DIR."""

    def setUp(self) -> None:
        super().setUp()
        tools = self.root / ".claude" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLAUDE_DIR / "tools" / "_lib.py", tools / "_lib.py")

    def run_hook(self, name: str, stdin: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root),
                   CLAUDE_IFF_RECORD_ROOT=str(self.record))
        return subprocess.run([BASH, str(HOOKS / name)], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", timeout=60, env=env, check=False)

    def ticket_path(self) -> Path:
        return self.root / ".claude" / "state" / "ritual-ticket.json"

    def ticket(self):
        return _lib.read_json(self.ticket_path())


# --------------------------------------------------------------------------- the prompt hook

class TestPromptHook(HookRunner):
    def prompt(self, payload) -> dict | None:
        """Run the hook on one payload: it must exit 0 and print nothing, whatever it is."""
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        if self.ticket_path().exists():
            self.ticket_path().unlink()
        res = self.run_hook("ritual-ticket.sh", stdin)
        self.assertEqual(res.returncode, 0, f"the hook must never block a prompt: {res.stderr}")
        self.assertEqual(res.stdout, "", "stdout on a prompt event is injected into the context")
        return self.ticket()

    def test_user_prompt_submit_mints_a_ticket(self):
        ticket = self.prompt({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                              "user_input": "/project-memory", "user_input_type": "user"})
        self.assertEqual(ticket["skill"], "project-memory")
        self.assertEqual(ticket["session_id"], "s1")
        self.assertEqual(ticket["event"], "UserPromptSubmit")
        self.assertIsNotNone(_lib.parse_ts(ticket["ts"]))
        self.assertEqual(set(ticket), {"skill", "ts", "session_id", "event"})

    def test_the_older_prompt_field_and_arguments_and_leading_whitespace(self):
        for text, skill in (("  /adopt --upgrade", "adopt"), ("/project-memory\n", "project-memory"),
                            ("\t/project-memory wrap up the T3 work", "project-memory")):
            with self.subTest(text=text):
                ticket = self.prompt({"hook_event_name": "UserPromptSubmit", "session_id": "s2",
                                      "prompt": text})
                self.assertIsNotNone(ticket, text)
                self.assertEqual(ticket["skill"], skill)

    def test_user_prompt_expansion_mints_a_ticket(self):
        ticket = self.prompt({"hook_event_name": "UserPromptExpansion", "session_id": "s3",
                              "command_name": "project-memory", "user_input": "/project-memory",
                              "expanded_prompt": "# /project-memory\n\nFive phases..."})
        self.assertEqual((ticket["skill"], ticket["event"]), ("project-memory", "UserPromptExpansion"))
        # expansion_type absent, slash present, no user_input: the command name alone is enough
        ticket = self.prompt({"hook_event_name": "UserPromptExpansion", "session_id": "s3",
                              "command_name": "/adopt"})
        self.assertEqual(ticket["skill"], "adopt")
        ticket = self.prompt({"hook_event_name": "UserPromptExpansion", "commandName": "adopt",
                              "expansion_type": "slash_command"})
        self.assertEqual(ticket["skill"], "adopt")
        self.assertIsNone(ticket["session_id"])

    def test_no_ticket_for_any_other_prompt(self):
        payloads = {
            "mid-sentence": {"hook_event_name": "UserPromptSubmit",
                             "user_input": "please run /project-memory now"},
            "second line": {"hook_event_name": "UserPromptSubmit",
                            "user_input": "notes first\n/project-memory"},
            "longer command": {"hook_event_name": "UserPromptSubmit", "user_input": "/adopting x"},
            "suffix glued": {"hook_event_name": "UserPromptSubmit",
                             "prompt": "/project-memory-extra"},
            "no slash": {"hook_event_name": "UserPromptSubmit", "user_input": "project-memory"},
            "other command": {"hook_event_name": "UserPromptSubmit", "user_input": "/plan-task adopt"},
            "system turn": {"hook_event_name": "UserPromptSubmit", "user_input": "/project-memory",
                            "user_input_type": "system"},
            "other expansion": {"hook_event_name": "UserPromptExpansion", "command_name": "plan-task",
                                "user_input": "/plan-task",
                                "expanded_prompt": "/project-memory is how a task closes"},
            "submit has no command": {"hook_event_name": "UserPromptSubmit",
                                      "command_name": "project-memory", "user_input": "hello"},
            "another event": {"hook_event_name": "PreToolUse", "prompt": "/project-memory",
                              "command_name": "adopt"},
        }
        for name, payload in payloads.items():
            with self.subTest(case=name):
                self.assertIsNone(self.prompt(payload), f"{name} minted a ticket")

    def test_garbage_input_never_blocks_and_never_mints(self):
        for stdin in ("", "not json {{{ /project-memory", '["/project-memory"]',
                      '"/project-memory"', '{"user_input": ["/adopt"]}', "\x00\xff adopt"):
            with self.subTest(stdin=stdin[:20]):
                self.assertIsNone(self.prompt(stdin))

    def test_a_broken_install_still_lets_the_prompt_through(self):
        (self.root / ".claude" / "tools" / "_lib.py").unlink()
        self.assertIsNone(self.prompt({"hook_event_name": "UserPromptSubmit",
                                       "user_input": "/project-memory"}))

    def test_wired_on_both_prompt_events(self):
        settings = json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
        for event in ("UserPromptSubmit", "UserPromptExpansion"):
            with self.subTest(event=event):
                commands = [h.get("command", "") for g in settings["hooks"].get(event, [])
                            for h in g.get("hooks", []) if h.get("type") == "command"]
                self.assertIn('"$CLAUDE_PROJECT_DIR"/.claude/hooks/ritual-ticket.sh', commands)
        text = (HOOKS / "ritual-ticket.sh").read_bytes()
        self.assertTrue(text.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r\n", text, "CRLF breaks the hook under bash")
        self.assertIn("ritual-ticket.sh", checkctl.EXPECTED_HOOKS)
        self.assertIn(("hook.ritual-ticket", ".claude/hooks/ritual-ticket.sh"),
                      [(r.name, r.message) for r in checkctl.probe()])


# --------------------------------------------------------------------------- checkctl's gate

class GateCase(FixtureCase):
    def cc(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = checkctl.main(list(argv))
        return code, buf.getvalue()

    def snapshot(self) -> dict:
        return {p.relative_to(self.root.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.root.parent.rglob("*")) if p.is_file()}

    def assert_refused(self, code: int, out: str, *needles: str) -> None:
        self.assertEqual(code, 1, out)
        self.assertIn("refused:", out)
        self.assertIn(ASK, out.lower())
        self.assertIn("CHECK_FAIL", out)
        for needle in needles:
            self.assertIn(needle, out)


class TestTicketGate(GateCase):
    def test_run_refuses_without_a_ticket_and_writes_nothing(self):
        self.journal("pointer", text="keep going")
        before = self.snapshot()
        for argv in (("run", "--phase", "check"), ("run", "--phase", "check", "--new"),
                     ("run", "--phase", "polish"), ("run", "--phase", "publish", "--resume")):
            with self.subTest(argv=argv):
                code, out = self.cc(*argv)
                self.assert_refused(code, out, "opening a ritual run", "no ritual ticket")
        self.assertEqual(self.snapshot(), before, "a refused run wrote something")
        self.assertFalse((_lib.state_dir() / "memory-run.json").exists())

    def test_a_fresh_ticket_opens_the_run_and_the_record_says_who(self):
        ticket = self.grant_ticket(session_id="s-user")
        self.cc("run", "--phase", "publish", "--new")  # publish fails fast: nothing ran before it
        run = checkctl.load_run()
        self.assertTrue(run.get("run_id"))
        self.assertEqual(run["invoked_by"], "user-ticket")
        self.assertEqual(run["ticket_ts"], ticket["ts"])
        self.assertEqual(run["ticket_session"], "s-user")
        self.assertIn("publish", run["phases"])

    def test_check_opens_with_a_ticket(self):
        self.grant_ticket()
        code, out = self.cc("run", "--phase", "check", "--new")
        self.assertNotIn("refused:", out)
        self.assertIn("check", checkctl.load_run()["phases"])

    def test_an_expired_ticket_refuses_and_the_ttl_is_a_knob(self):
        self.grant_ticket(ts=stamp(-361 * 60))
        code, out = self.cc("run", "--phase", "publish", "--new")
        self.assert_refused(code, out, "expired", "ritual.ticket_ttl_minutes")
        cfg = _lib.load_config("memory")
        cfg["ritual"]["ticket_ttl_minutes"] = 600
        self.write_config("memory", cfg)
        code, out = self.cc("run", "--phase", "publish", "--new")
        self.assertNotIn("refused:", out)
        self.assertEqual(checkctl.load_run()["invoked_by"], "user-ticket")

    def test_the_ttl_knob_ships_at_its_default_and_bad_values_read_as_it(self):
        shipped = _lib.read_json(CLAUDE_DIR / "config" / "memory.json")
        self.assertEqual(shipped["ritual"]["ticket_ttl_minutes"], checkctl.DEFAULT_TICKET_TTL_MINUTES)
        for bad in ("abc", 0, -5, True, None, [10]):
            with self.subTest(value=bad):
                cfg = _lib.load_config("memory")
                cfg["ritual"]["ticket_ttl_minutes"] = bad
                self.write_config("memory", cfg)
                self.assertEqual(checkctl.ticket_ttl_minutes(), 360.0)

    def test_forged_shapes_are_refused(self):
        path = _lib.state_dir() / "ritual-ticket.json"
        cases = {
            "unparsable": "{not json",
            "no skill": json.dumps({"ts": _lib.utc_now()}),
            "wrong skill": json.dumps({"skill": "plan-task", "ts": _lib.utc_now()}),
            "no ts": json.dumps({"skill": "project-memory"}),
            "future": json.dumps({"skill": "project-memory", "ts": stamp(3 * 3600)}),
            "a list": json.dumps(["project-memory"]),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                code, out = self.cc("run", "--phase", "publish", "--new")
                self.assert_refused(code, out)

    def test_continuing_a_run_needs_the_ticket_to_still_be_fresh(self):
        self.grant_ticket()
        self.cc("run", "--phase", "check", "--new")
        opened = checkctl.load_run()["run_id"]
        self.cc("run", "--phase", "polish")
        self.assertEqual(checkctl.load_run()["run_id"], opened)
        self.assertIn("polish", checkctl.load_run()["phases"])
        self.grant_ticket(ts=stamp(-400 * 60))  # the same ritual, hours later
        before = (_lib.state_dir() / "memory-run.json").read_bytes()
        code, out = self.cc("run", "--phase", "publish")
        self.assert_refused(code, out, f"continuing ritual {opened}", "expired")
        self.assertEqual((_lib.state_dir() / "memory-run.json").read_bytes(), before)

    def test_complete_needs_the_ticket_and_consumes_it(self):
        ticket = self.grant_ticket()
        self.cc("run", "--phase", "publish", "--new")
        (_lib.state_dir() / "ritual-ticket.json").unlink()
        code, out = self.cc("complete", "--note", "x")
        self.assert_refused(code, out, "completing the ritual")
        self.assertIsNone(checkctl.load_run().get("last_completed"))

        ticket = self.grant_ticket()
        code, out = self.cc("complete", "--note", "x")
        self.assertEqual(code, 0, out)
        run = checkctl.load_run()
        self.assertEqual(run["status"], "done")
        self.assertTrue(run["last_completed"])
        self.assertEqual(run["ticket_consumed"], ticket["ts"])
        self.assertFalse((_lib.state_dir() / "ritual-ticket.json").exists(), "not consumed")

        code, out = self.cc("complete")
        self.assert_refused(code, out)
        code, out = self.cc("run", "--phase", "check", "--new")
        self.assert_refused(code, out, "opening a ritual run")

    def test_a_consumed_ticket_that_lingers_is_spent(self):
        """complete deletes the ticket; if the file survived (a failed unlink, a restore from a
        backup), its ts is on record as consumed and it opens nothing."""
        ticket = self.grant_ticket()
        self.cc("run", "--phase", "publish", "--new")
        self.assertEqual(self.cc("complete")[0], 0)
        self.grant_ticket(ts=ticket["ts"])
        code, out = self.cc("run", "--phase", "check", "--new")
        self.assert_refused(code, out, "consumed")
        self.grant_ticket(ts=stamp(-30))  # the user types /project-memory again: a new ts
        code, out = self.cc("run", "--phase", "publish", "--new")
        self.assertNotIn("refused:", out)

    def test_read_only_subcommands_need_no_ticket(self):
        commands = [("status",), ("generators",), ("probe",), ("doctor",),
                    ("phase-exit", "--from", "review", "--signoff", "tried it"),
                    ("phase-exit", "--from", "plan")]
        try:  # handoff arrives with another task of this milestone; check it when present
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                checkctl.main(["handoff", "--help"])
        except SystemExit as exc:
            if exc.code == 0:
                commands.append(("handoff", "T-none"))
        for argv in commands:
            with self.subTest(argv=argv):
                try:
                    _code, out = self.cc(*argv)
                except SystemExit:
                    out = ""
                self.assertNotIn("refused:", out)
                self.assertNotIn("ritual ticket -", out)
        self.assertFalse((_lib.state_dir() / "ritual-ticket.json").exists())

    def options(self, command: str) -> set:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            checkctl.main([command, "--help"])
        return set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", buf.getvalue())) - {"--help"}

    def test_there_is_no_bypass(self):
        """No flag and no environment variable skips the ticket: tests grant one instead."""
        self.assertEqual(self.options("run"), {"--phase", "--resume", "--new", "--json"},
                         "a new flag on `checkctl run`: none may skip the ticket")
        self.assertEqual(self.options("complete"), {"--note"})
        for fn in (checkctl.ticket_state, checkctl.ticket_ttl_minutes, checkctl.main):
            self.assertNotIn("environ", inspect.getsource(fn), fn.__name__)
        os.environ["CLAUDE_IFF_SKIP_TICKET"] = "1"
        try:
            code, out = self.cc("run", "--phase", "check", "--new")
            self.assert_refused(code, out)
        finally:
            os.environ.pop("CLAUDE_IFF_SKIP_TICKET", None)


class TestDoctorRow(GateCase):
    def wire_hook(self) -> None:
        shutil.copy(CLAUDE_DIR / "settings.json", self.root / ".claude" / "settings.json")
        (self.root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
        shutil.copy(HOOKS / "ritual-ticket.sh", self.root / ".claude" / "hooks" / "ritual-ticket.sh")

    def row(self):
        result, fix = checkctl._doctor_ritual_ticket()
        self.assertEqual(result.name, "ritual_ticket")
        return result, fix

    def test_no_hook_means_no_ritual_and_says_so(self):
        result, fix = self.row()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertIn("refuses", result.message)
        self.assertIn("settings.json", fix)

    def test_idle_fresh_expired_and_invalid(self):
        self.wire_hook()
        result, _ = self.row()
        self.assertEqual(result.status, checkctl.OK)
        self.assertIn("none open", result.message)
        self.grant_ticket()
        result, _ = self.row()
        self.assertEqual(result.status, checkctl.OK)
        self.assertIn("fresh", result.message)
        self.assertIn("more min", result.message)
        self.grant_ticket(ts=stamp(-2 * 86400))
        result, _ = self.row()
        self.assertEqual(result.status, checkctl.OK)
        self.assertIn("expired", result.message)
        (_lib.state_dir() / "ritual-ticket.json").write_text("{nope", encoding="utf-8")
        result, fix = self.row()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertIn("/project-memory", fix)


class TestHumanGrant(GateCase):
    """`checkctl ticket --grant`: the escape hatch the USER runs in their own terminal when the
    prompt hook never fires. The gate refuses it to every agent (test_hooks.TestRitualEscapes);
    here: what it writes, that it opens the ritual, and that the refusal and doctor name it."""

    def test_grant_writes_a_human_terminal_ticket_that_opens_the_ritual(self):
        code, out = self.cc("run", "--phase", "check", "--new")
        self.assert_refused(code, out, checkctl.GRANT_COMMAND, "own terminal")
        code, out = self.cc("ticket", "--grant")
        self.assertEqual(code, 0, out)
        ticket = _lib.read_json(_lib.state_dir() / "ritual-ticket.json")
        self.assertEqual(ticket["event"], "human-terminal")
        self.assertEqual(ticket["skill"], "project-memory")
        self.assertEqual(checkctl.ticket_state()["status"], "fresh")
        code, out = self.cc("run", "--phase", "check", "--new")
        self.assertNotIn("refused:", out)
        self.assertEqual(checkctl.load_run()["invoked_by"], "user-ticket")
        self.cc("ticket", "--grant", "--skill", "adopt")
        self.assertEqual(_lib.read_json(_lib.state_dir() / "ritual-ticket.json")["skill"], "adopt")

    def test_without_grant_it_only_reads(self):
        before = self.snapshot()
        code, out = self.cc("ticket")
        self.assertEqual(code, 0, out)
        self.assertIn("absent", out)
        self.assertEqual(self.snapshot(), before)

    def test_the_doctor_row_names_the_hatch(self):
        result, fix = checkctl._doctor_ritual_ticket()  # no hook wired here
        self.assertIn(checkctl.GRANT_COMMAND, fix)
        shutil.copy(CLAUDE_DIR / "settings.json", self.root / ".claude" / "settings.json")
        (self.root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
        shutil.copy(HOOKS / "ritual-ticket.sh", self.root / ".claude" / "hooks" / "ritual-ticket.sh")
        result, _ = checkctl._doctor_ritual_ticket()
        self.assertIn(checkctl.GRANT_COMMAND, result.message)

    def test_the_skill_and_reference_document_it(self):
        for path in (CLAUDE_DIR / "skills" / "project-memory" / "SKILL.md",
                     CLAUDE_DIR / "reference" / "glossary.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("ticket --grant", text)
                self.assertIn("own terminal", text)


# --------------------------------------------------------------------------- the nudge

class TestSessionNudge(HookRunner):
    def setUp(self) -> None:
        super().setUp()
        cfg = _lib.read_json(self.root / ".claude" / "config" / "console.json", {}) or {}
        cfg["autostart"] = False  # never spawn a server from the test suite
        _lib.atomic_write_json(self.root / ".claude" / "config" / "console.json", cfg)
        self.journal("pointer", text="x")  # a journal exists: this is a running project

    def ritual_done(self, ago_s: float, session: str = "s-ritual") -> str:
        last = stamp(-ago_s)
        _lib.atomic_write_json(_lib.state_dir() / "memory-run.json",
                               {"run_id": "r1", "status": "done", "last_completed": last,
                                "ticket_session": session})
        return last

    def started(self, session: str, ago_s: float, sealed: bool = False) -> None:
        event = {"_obs_ts": stamp(-ago_s), "hook_event_name": "SessionStart", "session_id": session,
                 "source": "startup"}
        if sealed:
            _lib.append_jsonl(self.record / "segments" / f"{event['_obs_ts'][:10]}.jsonl", event)
        else:
            self.spool_event(**event)

    def nudge(self, session: str = "s-now") -> str:
        res = self.run_hook("session-start.sh", json.dumps(
            {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}))
        self.assertEqual(res.returncode, 0)
        return next((ln for ln in res.stdout.splitlines() if ln.startswith("RITUAL:")), "")

    def test_counts_distinct_captured_sessions_since_the_ritual(self):
        self.ritual_done(3600)
        self.started("s-a", 1800)
        self.started("s-a", 900)               # the same session again (resume, compact)
        self.started("s-b", 600)
        self.started("s-c", 300, sealed=True)  # already sealed into a segment
        self.started("s-old", 7200)            # before the ritual
        self.started("s-ritual", 60)           # the ritual's own session, compacted later
        line = self.nudge("s-now")
        self.assertIn("4 session(s) ago", line, line)
        self.assertIn("Ask the user to run /project-memory", line)
        self.assertNotIn("Run it at", line)

    def test_quiet_below_both_thresholds(self):
        self.ritual_done(3600)
        self.started("s-a", 1800)
        self.assertEqual(self.nudge("s-now"), "", "2 sessions and an hour must not nudge")

    def test_journal_session_start_events_are_not_the_count(self):
        """No hook writes `session_start`; counting them made the nudge age-only in practice.
        Three of them now count for nothing: only captured sessions do."""
        self.ritual_done(3600)
        for i in range(3):
            self.journal("session_start", session=f"j{i}")
        self.assertEqual(self.nudge("s-now"), "")

    def test_capture_off_says_so_and_goes_by_age(self):
        cfg = _lib.load_config("observe")
        cfg["enabled"] = False
        self.write_config("observe", cfg)
        self.ritual_done(3600)
        for name in ("s-a", "s-b", "s-c"):
            self.started(name, 600)
        self.assertEqual(self.nudge(), "", "with capture off the sessions are unknown, not counted")
        self.ritual_done(4 * 86400)
        line = self.nudge()
        self.assertIn("sessions not counted", line)
        self.assertIn("Ask the user to run /project-memory", line)

    def test_age_alone_still_nudges_and_never_run_asks_too(self):
        self.ritual_done(5 * 86400)
        self.assertIn("Ask the user", self.nudge())
        (_lib.state_dir() / "memory-run.json").unlink()
        line = self.nudge()
        self.assertIn("has not run yet", line)
        self.assertIn("Ask the user to run /project-memory", line)


# --------------------------------------------------------------------------- wording and ROUTE

# Agent-facing text: what the agent reads as instructions. Human-facing READMEs and docs may
# keep telling the HUMAN to run the command.
def agent_facing_texts() -> dict:
    files = [CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md",
             CLAUDE_DIR / "tasks" / "_template.md"]
    for pattern in ("skills/*/SKILL.md", "protocols/*.md", "agents/*.md", "hooks/*.sh"):
        files += sorted(CLAUDE_DIR.glob(pattern))
    texts = {_lib.rel(p, REPO_ROOT): p.read_text(encoding="utf-8") for p in files if p.is_file()}
    texts["distctl.FRESH_STATUS"] = distctl.FRESH_STATUS
    return texts


# An imperative that has the agent open the ritual: "run /project-memory", "End: run
# `/project-memory`", "Run the first /project-memory", "invoke /adopt". Not flagged: the same
# verb addressed to the user ("ask the user to run", "the user runs", "ask them to run").
AGENT_RUNS_RITUAL = re.compile(
    r"(?<!user to )(?<!them to )(?<!user )(?<!users )"
    r"\b(?:run|runs|invoke|invokes|execute|executes|start|starts)\s+"
    r"(?:the\s+(?:first\s+)?)?`?/(?:project-memory|adopt)\b", re.IGNORECASE)


class TestNudgeWording(unittest.TestCase):
    def test_the_pattern_catches_the_old_forms_and_spares_the_new(self):
        for bad in ("- **End:** run `/project-memory`.", "3. Run the first /project-memory.",
                    "Run /project-memory at the next natural boundary", "then invoke /adopt"):
            with self.subTest(bad=bad):
                self.assertTrue(AGENT_RUNS_RITUAL.search(bad))
        for good in ("Ask the user to run /project-memory", "the user runs `/project-memory`",
                     "ask them to run /project-memory", "the user types `/project-memory`",
                     "Closing happens inside `/project-memory`"):
            with self.subTest(good=good):
                self.assertFalse(AGENT_RUNS_RITUAL.search(good))

    def test_no_agent_facing_text_tells_the_agent_to_run_the_ritual(self):
        offenders = []
        for name, text in agent_facing_texts().items():
            for lineno, line in enumerate(text.splitlines(), 1):
                if AGENT_RUNS_RITUAL.search(line):
                    offenders.append(f"{name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "only the user can open the ritual: tell the agent to ASK "
                                        "the user to type it")

    def test_the_guides_tell_the_agent_to_suggest_it(self):
        for path in (CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md"):
            with self.subTest(guide=path.name):
                text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
                self.assertIn("the user types `/project-memory`; you suggest it", text)


SKILL = CLAUDE_DIR / "skills" / "project-memory" / "SKILL.md"


class TestRouteStep(unittest.TestCase):
    def test_route_is_the_final_step_after_the_report(self):
        text = SKILL.read_text(encoding="utf-8")
        headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
        self.assertEqual(headings[-2:], ["The report", "Phase 5 - ROUTE"], headings)
        route = re.sub(r"\s+", " ", text.split("## Phase 5 - ROUTE", 1)[1])
        for marker in ("AskUserQuestion", "up to three questions", "statectl.py phase <next>",
                       "exit check", '--override "<why>"', '--signoff "<text>"',
                       "Skip it in Freestyle", "statectl.py mode", "Freestyle", "Guided Solo",
                       "Fableous Orchestrated", "Maturation pass now?", "anatomist audit",
                       "Pruning sweep", "task cards for a plan phase"):
            with self.subTest(marker=marker):
                self.assertIn(marker, route)

    def test_the_skill_says_it_is_the_users_and_why_the_ticket_is_enough(self):
        head = re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8").split("## Phase 1", 1)[0])
        for marker in ("only because the user typed `/project-memory`", "ritual-ticket.json",
                       "`checkctl run` refuses", "`checkctl complete` needs it and consumes it",
                       "tripwire, not cryptography"):
            with self.subTest(marker=marker):
                self.assertIn(marker, head)

    def test_the_unparsed_hard_flag_is_gone(self):
        files = [SKILL, CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md",
                 CLAUDE_DIR / "README.md", CLAUDE_DIR / "reference" / "glossary.md",
                 CLAUDE_DIR / "protocols" / "evolution.md",
                 REPO_ROOT / ".github" / "README.md", REPO_ROOT / "docs" / "done.html"]
        for path in files:
            if not path.exists():  # .github/ and docs/ live in the home repo only
                continue
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("--hard", text)
                self.assertNotIn("hard gear", text.lower())
        self.assertIn("maturation pass", (CLAUDE_DIR / "protocols" / "evolution.md")
                      .read_text(encoding="utf-8").lower())

    def test_the_adopt_skill_explains_why_its_runs_work(self):
        text = re.sub(r"\s+", " ", (CLAUDE_DIR / "skills" / "adopt" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("It works here because the user typed `/adopt`", text)
        self.assertIn("Never write, copy or forge a ticket", text)


# --------------------------------------------------------------------------- declared everywhere

class TestDeclared(FixtureCase):
    def test_store_card_registry_and_gitignore(self):
        store = next(s for s in mapctl.KNOWN_STORES if s["id"] == "store.ritual_ticket")
        self.assertEqual(store["path"], ".claude/state/ritual-ticket.json")
        card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "store.ritual_ticket.json")
        self.assertEqual(card["path"], store["path"])
        hook_card = _lib.read_json(CLAUDE_DIR / "system-map" / "cards" / "hook.ritual-ticket.json")
        self.assertIn("store.ritual_ticket", hook_card["writes"])
        keys = {e["key"]: e for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]}
        self.assertEqual(keys["memory.ritual.ticket_ttl_minutes"]["default"],
                         checkctl.DEFAULT_TICKET_TTL_MINUTES)
        self.assertIn(".claude/state/ritual-ticket.json",
                      distctl.render_gitignore_block("tracked").splitlines())
        self.assertTrue(checkctl._deliberately_ignored(".claude/state/ritual-ticket.json"))
        home_ignore = REPO_ROOT / ".gitignore"
        home = distctl.distribution_enabled(REPO_ROOT) and _lib.visibility(REPO_ROOT) == "tracked"
        if home and home_ignore.exists():  # the home repo keeps its own block current
            self.assertIn(distctl.render_gitignore_block("tracked"),
                          home_ignore.read_text(encoding="utf-8").replace("\r\n", "\n"),
                          "the repo's managed block is behind distctl's renderer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
