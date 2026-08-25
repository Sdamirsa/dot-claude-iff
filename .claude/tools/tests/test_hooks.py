#!/usr/bin/env python3
"""test_hooks.py - the hooks-fire smoke test.

Hooks are the one part of this system that no unit test of the tools can vouch for: they are
shell scripts invoked by the harness with a JSON payload on stdin, and "silently not running"
is a real state (project hooks require the user to trust them first). So these tests run the
REAL scripts from the repo against a throwaway project and assert on their observable effects
and exit codes.

The split under test is law 2: gates fail closed, telemetry fails open.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

HOOKS = CLAUDE_DIR / "hooks"


class HookCase(FixtureCase):
    """Runs the repo's real hook scripts with the fixture project as CLAUDE_PROJECT_DIR."""

    def setUp(self) -> None:
        super().setUp()
        tools = self.root / ".claude" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLAUDE_DIR / "tools" / "_lib.py", tools / "_lib.py")

    def run_hook(self, name: str, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["CLAUDE_IFF_RECORD_ROOT"] = str(self.record)
        return subprocess.run(
            ["bash", str(HOOKS / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )

    @staticmethod
    def decision(result: subprocess.CompletedProcess):
        out = result.stdout.strip()
        if not out:
            return None
        try:
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        except Exception:
            return None


class TestCapture(HookCase):
    def test_captures_an_allowed_event(self):
        res = self.run_hook("obs-capture.sh", {"hook_event_name": "Stop", "session_id": "abc"})
        self.assertEqual(res.returncode, 0)
        spool = self.record / "spool" / "abc.jsonl"
        self.assertTrue(spool.exists(), "Stop is in the lean capture set and must be spooled")
        event = json.loads(spool.read_text().strip())
        self.assertEqual(event["hook_event_name"], "Stop")
        self.assertIn("_obs_ts", event)
        self.assertEqual(event["_obs_source"], "hook")

    def test_skips_events_outside_the_lean_set(self):
        res = self.run_hook("obs-capture.sh", {"hook_event_name": "PreToolUse", "session_id": "abc"})
        self.assertEqual(res.returncode, 0)
        self.assertFalse((self.record / "spool" / "abc.jsonl").exists())

    def test_capture_all_events_flag(self):
        cfg = json.loads((self.root / ".claude/config/observe.json").read_text())
        cfg["capture_all_events"] = True
        (self.root / ".claude/config/observe.json").write_text(json.dumps(cfg))
        self.run_hook("obs-capture.sh", {"hook_event_name": "PreToolUse", "session_id": "abc"})
        self.assertTrue((self.record / "spool" / "abc.jsonl").exists())

    def test_fails_open_on_garbage(self):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["CLAUDE_IFF_RECORD_ROOT"] = str(self.record)
        res = subprocess.run(
            ["bash", str(HOOKS / "obs-capture.sh")],
            input="not json at all {{{",
            capture_output=True, text=True, timeout=30, env=env, check=False,
        )
        self.assertEqual(res.returncode, 0, "telemetry must never fail the tool call")

    def test_fails_open_when_record_root_is_unwritable(self):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["CLAUDE_IFF_RECORD_ROOT"] = "/proc/definitely-not-writable/record"
        res = subprocess.run(
            ["bash", str(HOOKS / "obs-capture.sh")],
            input=json.dumps({"hook_event_name": "Stop", "session_id": "x"}),
            capture_output=True, text=True, timeout=30, env=env, check=False,
        )
        self.assertEqual(res.returncode, 0)

    def test_disabled_capture_writes_nothing(self):
        cfg = json.loads((self.root / ".claude/config/observe.json").read_text())
        cfg["enabled"] = False
        (self.root / ".claude/config/observe.json").write_text(json.dumps(cfg))
        self.run_hook("obs-capture.sh", {"hook_event_name": "Stop", "session_id": "abc"})
        self.assertFalse((self.record / "spool" / "abc.jsonl").exists())


class TestHeartbeat(HookCase):
    def test_writes_heartbeat(self):
        res = self.run_hook("heartbeat.sh", {"hook_event_name": "Stop"})
        self.assertEqual(res.returncode, 0)
        hb = json.loads((self.root / ".claude/state/heartbeat.json").read_text())
        self.assertIn("ts", hb)
        self.assertEqual(hb["note"], "turn ended")

    def test_overwrites_rather_than_appends(self):
        self.run_hook("heartbeat.sh", {})
        self.run_hook("heartbeat.sh", {})
        text = (self.root / ".claude/state/heartbeat.json").read_text()
        self.assertEqual(len(json.loads(text)), 2, "heartbeat is an O(1) overwrite, not a log")


class TestPolicyGate(HookCase):
    def write_payload(self, path: str, identity: str | None = None) -> dict:
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": str(self.root)}
        if identity:
            payload["agent_type"] = identity
        return payload

    def test_main_session_may_edit_protected_config(self):
        res = self.run_hook("policy-gate.sh", self.write_payload(".claude/config/policy.json"))
        self.assertIsNone(self.decision(res), "the main session owns the protected tree")

    def test_subagent_may_not_edit_the_gate_that_polices_it(self):
        res = self.run_hook("policy-gate.sh", self.write_payload(".claude/hooks/policy-gate.sh", "worker"))
        self.assertEqual(self.decision(res), "deny")

    def test_subagent_may_not_edit_protected_config(self):
        res = self.run_hook("policy-gate.sh", self.write_payload(".claude/config/policy.json", "worker"))
        self.assertEqual(self.decision(res), "deny")

    def test_subagent_may_write_its_granted_paths(self):
        res = self.run_hook(
            "policy-gate.sh", self.write_payload(".claude/state/handshakes/t1.json", "worker")
        )
        self.assertIsNone(self.decision(res))

    def test_granted_agent_may_write_its_tree(self):
        res = self.run_hook(
            "policy-gate.sh", self.write_payload(".claude/system-map/cards/x.json", "anatomist")
        )
        self.assertIsNone(self.decision(res))

    def test_record_is_denied_to_every_identity(self):
        for identity in (None, "worker", "anatomist"):
            with self.subTest(identity=identity):
                res = self.run_hook(
                    "policy-gate.sh", self.write_payload(str(self.record / "segments" / "x.jsonl"), identity)
                )
                self.assertEqual(self.decision(res), "deny")

    def test_iff_surface_is_denied_to_every_identity(self):
        res = self.run_hook("policy-gate.sh", self.write_payload(".claude-iff/obs/anchor.json"))
        self.assertEqual(self.decision(res), "deny")

    def test_subagent_git_is_denied(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "agent_type": "worker"}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_subagent_git_denied_after_a_separator(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls && git commit -m x"}, "agent_type": "worker"}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_subagent_may_read_git_history(self):
        """A verifier that cannot run `git log` cannot verify a claim about history."""
        for command in ("git log --oneline -5", "git diff HEAD~1", "git --no-pager show abc123",
                        "git status --short"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "verifier"}
                self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_subagent_still_denied_mutating_git(self):
        for command in ("git push origin main", "git commit -am x", "git reset --hard",
                        "git checkout -b feature"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "verifier"}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_similar_command_is_not_denied(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "gitleaks detect"}, "agent_type": "worker"}
        self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_main_session_may_run_git(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}}
        self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_mutating_the_record_via_shell_is_denied(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": f"rm -rf {self.record}/segments"}}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_reading_the_record_via_shell_is_allowed(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": f"ls -la {self.record}/segments"}}
        self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_ordinary_project_write_is_allowed(self):
        res = self.run_hook("policy-gate.sh", self.write_payload("src/main.py", "worker"))
        self.assertIsNone(self.decision(res))

    def test_large_payload_cannot_switch_the_gate_off(self):
        """A Write payload carries the file's whole content. Passing it through an environment
        variable capped the gate at ~128 KB: above that execve failed, no decision was emitted,
        and the harness read silence as ALLOW. Writing a big file must not disable the gate."""
        for size in (1_000, 200_000, 1_000_000):
            with self.subTest(size=size):
                payload = {
                    "tool_name": "Write",
                    "tool_input": {"file_path": ".claude/config/policy.json", "content": "A" * size},
                    "agent_type": "worker",
                }
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_the_tools_the_gate_executes_are_protected(self):
        """policy_gate.py imports _lib from .claude/tools on every call: a sub-agent that can
        write there owns the gate on the next tool call."""
        for target in (".claude/tools/_lib.py", ".claude/tools/checkctl.py",
                       ".claude/console/console.py", ".claude/skills/project-memory/SKILL.md"):
            with self.subTest(target=target):
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh",
                                 self.write_payload(target, "worker"))), "deny")

    def test_anatomist_keeps_its_granted_skill_path(self):
        res = self.run_hook("policy-gate.sh",
                            self.write_payload(".claude/skills/new-thing/SKILL.md", "anatomist"))
        self.assertIsNone(self.decision(res))

    def test_shell_wrappers_cannot_launder_a_denied_command(self):
        for command in ('bash -c "git push"', 'sh -c "git push"', 'eval "git push"',
                        '$(echo git) push', 'xargs -I{} git push', '"git" push',
                        'G=git; $G push', 'echo hi && git push'):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "worker"}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_mutating_git_nouns_are_denied(self):
        for command in ("git branch -D main", "git tag -d v1",
                        "git remote set-url origin http://evil", "git remote add evil http://evil"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "verifier"}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_relative_spellings_of_the_record_are_guarded(self):
        for command in ("rm -rf .claude-iff", "rm -rf ./.claude-iff",
                        "find .claude-iff -delete",
                        "python3 -c \"import shutil; shutil.rmtree('.claude-iff')\""):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_a_path_segment_named_git_is_not_an_invocation(self):
        """Repos conventionally live under ~/git/ or ~/Documents/GIT/ - including this
        machine's. A path SEGMENT spelled like a denied command is a mention, not a run;
        matching it made every ls/cp/python3 that named such a path read as running git.
        Found by the adoption dry-run, which worked under exactly such a path."""
        for command in ("ls /home/x/Documents/GIT/dot-claude-iff",
                        "cp -r /home/x/GIT/repo /tmp/t",
                        "python3 /home/x/git/tools/x.py",
                        "find . -path '*/GIT/*' -name '*.py'"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "worker"}
                self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_real_git_invocations_still_denied_after_path_fix(self):
        for command in ("git push", "cd /home/x/GIT/repo && git commit -am x",
                        'bash -c "git push"'):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "agent_type": "worker"}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_another_projects_record_is_not_this_gates_business(self):
        """`mkdir -p <target>/.claude-iff/obs` is the exact step /adopt instructs when
        installing into another repo; a bare substring match used to deny it."""
        for command in ("mkdir -p /tmp/some-target/.claude-iff/obs/rollups",
                        "cp README /tmp/other/.claude-iff/",
                        "mkdir -p /tmp/adopt-target_claude_iff/spool"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "cwd": str(self.root)}
                self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_this_projects_record_still_guarded_by_every_spelling(self):
        for command in ("rm -rf .claude-iff",
                        f"rm -rf {self.root}/.claude-iff",
                        f"find {self.record} -delete",
                        "python3 -c \"import shutil; shutil.rmtree('.claude-iff')\""):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "cwd": str(self.root)}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_dev_null_redirect_is_not_a_mutation(self):
        """`grep ... 2>/dev/null` on the record is a read; the `>` in a /dev/null redirect
        used to trip the mutator scan and deny the maintainer's own audits."""
        for command in ("grep -rl x .claude .claude-iff 2>/dev/null",
                        "ls .claude-iff/obs >/dev/null 2>/dev/null"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "cwd": str(self.root)}
                self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_real_redirect_into_the_record_still_denied(self):
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "echo x > .claude-iff/obs/anchor.json"},
                   "cwd": str(self.root)}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_unparseable_tool_input_is_denied(self):
        payload = {"tool_name": "Write", "tool_input": ["not", "an", "object"]}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_a_subagent_named_orchestrator_gets_no_privileges(self):
        """Identity is 'main' only when the harness sent no agent identity at all: a sub-agent
        must not inherit the main session by choosing its own name."""
        for key in ("agent_type", "agent_name"):
            for name in ("orchestrator", "main"):
                with self.subTest(key=key, name=name):
                    payload = {"tool_name": "Write",
                               "tool_input": {"file_path": ".claude/config/policy.json"},
                               key: name}
                    self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_fails_closed_on_broken_policy(self):
        (self.root / ".claude/config/policy.json").write_text("{ this is not json")
        res = self.run_hook("policy-gate.sh", self.write_payload(".claude/config/policy.json", "worker"))
        self.assertEqual(self.decision(res), "deny", "a broken policy must not open the protected tree")
        res2 = self.run_hook("policy-gate.sh", self.write_payload("src/main.py", "worker"))
        self.assertIsNone(self.decision(res2), "a broken policy must not brick the session")


class TestPostWriteValidate(HookCase):
    def run_on(self, relative: str, content: str) -> subprocess.CompletedProcess:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return self.run_hook(
            "post-write-validate.sh",
            {"tool_name": "Write", "tool_input": {"file_path": str(path)}, "cwd": str(self.root)},
        )

    def test_valid_json_passes(self):
        self.assertEqual(self.run_on(".claude/config/x.json", '{"a": 1}').returncode, 0)

    def test_broken_json_blocks(self):
        res = self.run_on(".claude/config/x.json", '{"a": ')
        self.assertEqual(res.returncode, 2)
        self.assertIn("VALIDATE_FAIL", res.stderr)

    def test_broken_jsonl_line_blocks(self):
        res = self.run_on(".claude/state/x.jsonl", '{"a":1}\nnot json\n')
        self.assertEqual(res.returncode, 2)
        self.assertIn("line 2", res.stderr)

    def test_valid_jsonl_passes(self):
        self.assertEqual(self.run_on(".claude/state/x.jsonl", '{"a":1}\n{"b":2}\n').returncode, 0)

    def test_handshake_envelope_requires_its_contract(self):
        res = self.run_on(".claude/state/handshakes/t1.json", '{"agent_id": "x"}')
        self.assertEqual(res.returncode, 2)
        self.assertIn("task_id", res.stderr)

    def test_handshake_rejects_bad_status(self):
        res = self.run_on(
            ".claude/state/handshakes/t1.json",
            '{"agent_id":"x","task_id":"t","status":"finished"}',
        )
        self.assertEqual(res.returncode, 2)

    def test_valid_handshake_passes(self):
        res = self.run_on(
            ".claude/state/handshakes/t1.json",
            '{"agent_id":"x","task_id":"t","status":"done","artifacts":[],"notes":"ok"}',
        )
        self.assertEqual(res.returncode, 0)

    def test_stub_is_exempt_from_the_envelope_contract(self):
        res = self.run_on(".claude/state/handshakes/t1.stub.json", '{"agent":"x"}')
        self.assertEqual(res.returncode, 0)

    def test_ignores_files_outside_claude(self):
        self.assertEqual(self.run_on("data/x.json", "{ broken").returncode, 0)

    def test_ignores_non_structured_files(self):
        self.assertEqual(self.run_on(".claude/notes.md", "# hi").returncode, 0)


class TestSessionStart(HookCase):
    def setUp(self) -> None:
        super().setUp()
        cfg = json.loads((self.root / ".claude/config/console.json").read_text())
        cfg["autostart"] = False  # never spawn a server from the test suite
        (self.root / ".claude/config/console.json").write_text(json.dumps(cfg))

    def test_guides_a_project_with_no_journal(self):
        res = self.run_hook("session-start.sh", {"hook_event_name": "SessionStart"})
        self.assertEqual(res.returncode, 0)
        self.assertIn("statectl.py start", res.stdout)

    def test_nudges_when_the_ritual_is_stale(self):
        import _lib
        _lib.journal_append("session_start", session="s1")
        _lib.atomic_write_json(
            _lib.state_dir() / "memory-run.json", {"last_completed": "2020-01-01T00:00:00Z"}
        )
        res = self.run_hook("session-start.sh", {"hook_event_name": "SessionStart"})
        self.assertIn("RITUAL", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
