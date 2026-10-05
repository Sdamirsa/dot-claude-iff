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

import base64
import contextlib
import importlib.util
import io
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

sys.path.insert(0, str(CLAUDE_DIR / "tools"))
import _lib as _repo_lib  # noqa: E402

# A full path, never the bare name: on Windows a bare "bash" can resolve to the WSL launcher.
BASH = _repo_lib.find_bash() or "bash"


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
            [BASH, str(HOOKS / name)],
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


class GateCase(HookCase):
    """Judges payloads with the real policy_gate.py loaded in-process. The lane matrices below
    run hundreds of cases and one bash + python spawn per case would cost minutes; every
    mutator family still runs once per lane through the real wrapper (TestShellLanesEndToEnd)."""

    _module = None

    @classmethod
    def gate(cls):
        if GateCase._module is None:
            spec = importlib.util.spec_from_file_location("policy_gate_under_test",
                                                          HOOKS / "policy_gate.py")
            module = importlib.util.module_from_spec(spec)
            saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # keep hooks/ clean
            try:
                spec.loader.exec_module(module)
            finally:
                sys.dont_write_bytecode = saved
            GateCase._module = module
        return GateCase._module

    def judge(self, payload: dict):
        import _lib
        _lib.clear_config_cache()  # a fresh process reads policy.json fresh too
        data = dict(payload)
        data.setdefault("cwd", str(self.root))
        data["_project_root"] = str(self.root)  # what the wrapper adds
        path = Path(self._tmp.name) / "payload.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        out, saved, code = io.StringIO(), list(sys.path), 0
        try:
            with contextlib.redirect_stdout(out):
                try:
                    self.gate().main([str(path)])
                except SystemExit as exc:
                    code = exc.code or 0
        finally:
            sys.path[:] = saved
        self.assertEqual(code, 0, f"the gate crashed on {payload!r}; the wrapper would fall back")
        text = out.getvalue().strip()
        return json.loads(text)["hookSpecificOutput"]["permissionDecision"] if text else None

    def sh(self, command: str, who: str | None = None, lane: str = "Bash"):
        payload = {"tool_name": lane, "tool_input": {"command": command}}
        if who:
            payload["agent_type"] = who
        return self.judge(payload)

    def write(self, path: str, who: str | None = None):
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}}
        if who:
            payload["agent_type"] = who
        return self.judge(payload)


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
            [BASH, str(HOOKS / "obs-capture.sh")],
            input="not json at all {{{",
            capture_output=True, text=True, timeout=30, env=env, check=False,
        )
        self.assertEqual(res.returncode, 0, "telemetry must never fail the tool call")

    def test_fails_open_when_record_root_is_unwritable(self):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["CLAUDE_IFF_RECORD_ROOT"] = "/proc/definitely-not-writable/record"
        res = subprocess.run(
            [BASH, str(HOOKS / "obs-capture.sh")],
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

    def test_powershell_is_a_shell_lane_too(self):
        """On Windows the PowerShell tool mutates files exactly like Bash does; an unmatched
        tool name meant every ring was open to it. Same judgment, same command string."""
        cases = [
            ("Remove-Item -Recurse -Force .claude-iff", "deny"),
            (f"Set-Content {self.record}/segments/x.jsonl 'gone'", "deny"),
            ("Get-Content .claude-iff/obs/anchor.json", None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                payload = {"tool_name": "PowerShell", "tool_input": {"command": command},
                           "cwd": str(self.root)}
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), expected)

    def test_powershell_denied_commands_match_subagents(self):
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "git push"},
                   "agent_type": "worker", "cwd": str(self.root)}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    @unittest.skipUnless(os.name == "nt", "case-insensitive filesystem semantics")
    def test_case_spoofed_protected_path_is_still_protected(self):
        """Windows filesystems are case-insensitive: writing .ClAuDe/config/x lands in the
        real .claude/config, so a case-exact prefix match was a spelling away from open."""
        res = self.run_hook("policy-gate.sh",
                            self.write_payload(".ClAuDe/Config/policy.json", "worker"))
        self.assertEqual(self.decision(res), "deny")

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


def _protected_targets(policy: dict) -> list:
    """One write target per protected entry: a file inside each directory, the file itself."""
    return [p + "x.py" if p.endswith("/") else p for p in policy["protected"]]


# One mutator family per row, spelled natively for each shell lane (L-10). {t} is the target.
FAMILIES = {
    "redirect": ("echo x > {t}", "'x' > {t}"),
    "append": ("echo x >> {t}", "'x' >> {t}"),
    "copy": ("cp README.md {t}", "cp README.md {t}"),
    "move": ("mv README.md {t}", "Move-Item README.md {t}"),
    "delete": ("rm -f {t}", "Remove-Item -Force {t}"),
    "tee": ("echo x | tee {t}", "'x' | Tee-Object -FilePath {t}"),
    "in-place edit": ("sed -i 's/a/b/' {t}", "(Get-Content {t}) -replace 'a','b' | Set-Content {t}"),
    "python one-liner": ("python3 -c \"open('{t}','w').write('x')\"",
                         "python -c \"open('{t}','w').write('x')\""),
    "Set-Content": ("pwsh -c \"Set-Content {t} x\"", "Set-Content -Path {t} -Value x"),
    "Out-File": ("pwsh -c \"'x' | Out-File {t}\"", "'x' | Out-File -FilePath {t}"),
    "Copy-Item": ("pwsh -c \"Copy-Item README.md {t}\"",
                  "Copy-Item -Path README.md -Destination {t}"),
}
LANES = ("Bash", "PowerShell")


class TestShellLanes(GateCase):
    """The protected tree holds on the shell lanes exactly as on Write/Edit: for every protected
    prefix and both lanes, a sub-agent's mutation is denied, a grant allows it, the main session
    is unrestricted, and reading still passes."""

    def setUp(self) -> None:
        super().setUp()
        self.policy = json.loads((self.root / ".claude/config/policy.json").read_text())
        (self.root / "README.md").write_text("x")

    def cases(self, prefixes=None):
        for target in prefixes or _protected_targets(self.policy):
            for family, spellings in FAMILIES.items():
                for lane, template in zip(LANES, spellings):
                    yield target, family, lane, template.format(t=target)

    def test_subagent_mutation_of_every_protected_prefix_is_denied_on_both_lanes(self):
        for target, family, lane, command in self.cases():
            with self.subTest(target=target, family=family, lane=lane):
                self.assertEqual(self.sh(command, "worker", lane), "deny", command)

    def test_main_session_is_unrestricted_in_the_protected_tree(self):
        outside_record = [t for t in _protected_targets(self.policy) if not t.startswith(".claude-iff")]
        for target, family, lane, command in self.cases(outside_record):
            with self.subTest(target=target, family=family, lane=lane):
                self.assertIsNone(self.sh(command, None, lane), command)

    def test_the_record_prefix_stays_denied_to_the_main_session(self):
        for target, family, lane, command in self.cases([".claude-iff/obs/x.json"]):
            with self.subTest(family=family, lane=lane):
                self.assertEqual(self.sh(command, None, lane), "deny", command)

    def test_granted_agents_may_mutate_their_grant_and_nothing_else(self):
        grants = {"builder": (), "anatomist": (".claude/skills/",)}
        for who, granted in grants.items():
            for target, family, lane, command in self.cases():
                expected = None if granted and target.startswith(granted) else "deny"
                with self.subTest(who=who, target=target, family=family, lane=lane):
                    self.assertEqual(self.sh(command, who, lane), expected, command)

    def test_builder_holds_no_protected_tree_grant(self):
        # .claude/tools/ holds _lib.py, which the gate imports: a grant there is a grant on the
        # gate. Builders work in worktrees and the main session merges.
        agents = self.policy["agents"]
        self.assertEqual(agents["builder"]["write_paths"], [])
        self.assertNotIn("write_paths", self.policy["default"],
                         "default.write_paths was never read by the gate; it must not come back "
                         "looking like a grant")

    def test_read_only_commands_still_pass(self):
        reads = {
            "Bash": ["cat .claude/tools/_lib.py", "ls -la .claude/hooks/",
                     'grep -rn "rm " .claude/hooks/', "grep -rn confirm .claude/config",
                     "head -5 .claude/config/policy.json", "wc -l .claude/tools/*.py",
                     "sed -n '1,5p' .claude/hooks/policy_gate.py",
                     "find .claude/tools -name '*.py' -newer README.md",
                     "diff .claude/tools/_lib.py README.md",
                     "cp .claude/tools/_lib.py /tmp/lib-copy.py",
                     "grep --include=*.py -rn x .claude/tools",
                     "python3 .claude/tools/checkctl.py probe 2>&1 | tail -3",
                     "python3 -c \"print(open('.claude/config/policy.json').read())\"",
                     'echo "rm -rf .claude/tools"',
                     "git log --oneline -- .claude/hooks/policy_gate.py"],
            "PowerShell": ["Get-Content .claude/tools/_lib.py", "Get-ChildItem .claude/hooks",
                           "Select-String -Path .claude/hooks/*.sh -Pattern 'rm '",
                           "Test-Path .claude/config/policy.json",
                           "Get-Content .claude/config/policy.json | ConvertFrom-Json",
                           "Write-Output 'Remove-Item .claude/tools'"],
        }
        for lane, commands in reads.items():
            for command in commands:
                with self.subTest(lane=lane, command=command):
                    self.assertIsNone(self.sh(command, "worker", lane))

    def test_unparseable_command_naming_the_tree_next_to_a_mutator_is_denied(self):
        self.assertEqual(self.sh("echo 'unclosed > .claude/tools/x.py", "worker"), "deny")
        self.assertEqual(self.sh("Set-Content .claude/hooks/x \"unclosed", "worker",
                                 "PowerShell"), "deny")
        # ...but an unparseable READ is not a write, and an unparseable write elsewhere is fine.
        self.assertIsNone(self.sh("cat '.claude/tools/x.py", "worker"))
        self.assertIsNone(self.sh("echo 'unclosed > notes.txt", "worker"))

    def test_a_write_the_gate_cannot_resolve_is_denied_when_the_command_names_the_tree(self):
        for lane, command in (
            ("Bash", "D=$(echo .claude/tools); echo x > $D/x.py"),
            ("Bash", "ls .claude/tools | xargs rm"),
            ("Bash", "cd \"$(dirname .claude/tools/x)\" && rm _lib.py"),
            ("Bash", "for f in .claude/hooks/*.sh; do rm \"$f\"; done"),
            ("Bash", "X=$(echo tools); echo x > .claude/$X/_lib.py"),
            ("PowerShell", "Get-ChildItem .claude/tools | ForEach-Object { Remove-Item $_ }"),
            ("PowerShell", "Get-ChildItem .claude/tools -Filter *.py | Remove-Item"),
        ):
            with self.subTest(lane=lane, command=command):
                self.assertEqual(self.sh(command, "worker", lane), "deny")
        for lane, command in (("Bash", 'echo x > "$OUT"'), ("Bash", "ls src | xargs rm"),
                              ("Bash", 'echo x > "./$NAME.bak"'),
                              ("PowerShell", "Get-ChildItem build | Remove-Item")):
            with self.subTest(lane=lane, command=command, expected="allow"):
                self.assertIsNone(self.sh(command, "worker", lane))

    def test_indirect_spellings_of_a_protected_write_are_denied(self):
        for command in (
            "D=.claude/tools; echo x > $D/x.py",
            "cd .claude/tools && rm _lib.py",
            "(cd /tmp); rm .claude/tools/_lib.py",
            "bash -c 'echo x > .claude/hooks/x'",
            'sh -c "rm .claude/config/policy.json"',
            "bash -o pipefail -c 'rm .claude/tools/_lib.py'",
            'eval "rm .claude/tools/_lib.py"',
            'eval "cd .claude/tools"; rm _lib.py',
            "echo $(rm .claude/tools/_lib.py)",
            "echo `rm .claude/tools/_lib.py`",
            "sudo -u nobody rm .claude/tools/_lib.py",
            "timeout 5 cp README.md .claude/hooks/",
            "rm .claude/{tools,x}/_lib.py",
            "rm .claude/t*ls/_lib.py",
            "echo x > .claude/to*/new.py",
            "find .claude/tools -name '*.pyc' -delete",
            "find . -name '*.py' -exec sed -i s/a/b/ {} +",
            "cp -r /tmp/stage/.claude .",
            "rsync -a /tmp/stage/ .claude/hooks/",
            "tar -xf payload.tar",
            "tar -xf payload.tar -C .claude",
            "dd if=README.md of=.claude/tools/_lib.py",
            "ln -sf /tmp/evil.py .claude/tools/_lib.py",
            "chmod -R 777 .claude",
            "python3 -c \"import os; open(os.path.join('.claude','tools','x.py'),'w')\"",
            "python3 - <<'EOF'\nopen('.claude/tools/_lib.py', 'w').write('x')\nEOF",
            "cat <<'EOF' | bash\nrm .claude/hooks/policy_gate.py\nEOF",
            "node -e \"require('fs').writeFileSync('.claude/console/x.js','')\"",
            "perl -pi -e 's/a/b/' .claude/tools/_lib.py",
            "cmd /c \"del .claude\\tools\\_lib.py\"",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.sh(command, "worker"), "deny")
        for command in ("[IO.File]::WriteAllText('.claude/tools/x.py', 'y')",
                        "(Get-Item .claude/tools/_lib.py).Delete()",
                        "Set-Location .claude/tools; Remove-Item _lib.py",
                        "$d = '.claude/hooks'; Set-Content \"$d/x.ps1\" 'y'"):
            with self.subTest(command=command, lane="PowerShell"):
                self.assertEqual(self.sh(command, "worker", "PowerShell"), "deny")

    def test_degraded_policy_still_guards_the_shell_lanes(self):
        (self.root / ".claude/config/policy.json").write_text("{ this is not json")
        self.assertEqual(self.sh("echo x > .claude/hooks/x", "worker"), "deny")
        self.assertEqual(self.sh("Set-Content .claude/tools/x 'y'", "worker", "PowerShell"),
                         "deny")
        self.assertEqual(self.write(".claude/settings.json", "worker"), "deny",
                         "settings.json was missing from the gate's built-in fallback list")
        self.assertIsNone(self.sh("echo x > src/main.py", "worker"))

    def test_fallback_list_in_policy_matches_the_gate(self):
        self.assertEqual(tuple(self.policy["fallback_protected"]),
                         self.gate().FALLBACK_PROTECTED)


class TestShellLanesEndToEnd(HookCase):
    """Each mutator family once per lane through the real wrapper and a real python process
    (L-10: a lane with no test is a lane with no gate)."""

    def test_each_family_is_denied_on_each_lane_through_the_wrapper(self):
        (self.root / "README.md").write_text("x")
        targets = [".claude/hooks/x.sh", ".claude/tools/x.py", ".claude/config/x.json",
                   ".claude/agents/x.md", ".claude/protocols/x.md", ".claude/skills/x/SKILL.md",
                   ".claude/console/x.js", ".claude/settings.json"]
        for k, (family, spellings) in enumerate(FAMILIES.items()):
            target = targets[k % len(targets)]
            for lane, template in zip(LANES, spellings):
                payload = {"tool_name": lane, "agent_type": "worker", "cwd": str(self.root),
                           "tool_input": {"command": template.format(t=target)}}
                with self.subTest(family=family, lane=lane):
                    self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)),
                                     "deny")

    def test_ritual_ticket_is_denied_to_the_main_session_on_every_lane(self):
        ticket = ".claude/state/ritual-ticket.json"
        for payload in ({"tool_name": "Write", "tool_input": {"file_path": ticket}},
                        {"tool_name": "Bash", "tool_input": {"command": f"echo '{{}}' > {ticket}"}},
                        {"tool_name": "PowerShell",
                         "tool_input": {"command": f"'{{}}' | Set-Content {ticket}"}}):
            with self.subTest(lane=payload["tool_name"]):
                payload["cwd"] = str(self.root)
                self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")


class TestRitualTicket(GateCase):
    """The ticket proves the USER typed /project-memory or /adopt. Only the prompt hook writes
    it, and a hook never passes through this gate; every identity that does is denied."""

    TICKET = ".claude/state/ritual-ticket.json"

    def test_every_identity_is_denied_on_every_lane(self):
        writes = {
            "Write": None,
            "Bash": [f"echo '{{}}' > {self.TICKET}", f"cp /tmp/ritual-ticket.json .claude/state/",
                     f"mv /tmp/forged.json {self.TICKET}", f"touch {self.TICKET}",
                     f"python3 -c \"open('{self.TICKET}','w').write('{{}}')\""],
            "PowerShell": [f"'{{}}' | Set-Content {self.TICKET}", f"New-Item -Path {self.TICKET}",
                           f"Copy-Item -Path forged.json -Destination {self.TICKET}"],
        }
        for who in (None, "worker", "builder", "anatomist", "verifier"):
            with self.subTest(who=who, lane="Write"):
                self.assertEqual(self.write(self.TICKET, who), "deny")
            for lane in ("Bash", "PowerShell"):
                for command in writes[lane]:
                    with self.subTest(who=who, lane=lane, command=command):
                        self.assertEqual(self.sh(command, who, lane), "deny")

    def test_reading_the_ticket_and_writing_beside_it_are_fine(self):
        self.assertIsNone(self.sh(f"cat {self.TICKET}"))
        self.assertIsNone(self.sh(f"Get-Content {self.TICKET}", lane="PowerShell"))
        self.assertIsNone(self.sh("echo x > .claude/state/handshakes/T1.json"))
        self.assertIsNone(self.write(".claude/state/session.json"))


class TestWorktrees(GateCase):
    """Agent worktrees live at .claude/worktrees/<name>/ and are COPIES: their .claude/tools is
    not the protected tree, which is what lets a builder work there. That is a decision, pinned
    here, and so is its limit: no path trick from a worktree reaches the real tree."""

    WT = ".claude/worktrees/t1"

    def setUp(self) -> None:
        super().setUp()
        for sub in ("tools", "hooks", "config"):
            (self.root / self.WT / ".claude" / sub).mkdir(parents=True, exist_ok=True)

    def test_a_worktree_copy_is_not_the_protected_tree(self):
        for who in ("worker", "builder"):
            with self.subTest(who=who, lane="Write"):
                self.assertIsNone(self.write(f"{self.WT}/.claude/hooks/policy_gate.py", who))
            for lane, command in (
                ("Bash", f"cd {self.WT} && echo x > .claude/tools/x.py"),
                ("Bash", f"echo x > {self.WT}/.claude/config/policy.json"),
                ("Bash", f"cd {self.WT} && sed -i s/a/b/ .claude/hooks/policy_gate.py"),
                ("PowerShell", f"Set-Location {self.WT}; Set-Content .claude/hooks/x.sh 'y'"),
                ("PowerShell", f"Copy-Item README.md {self.WT}/.claude/tools/"),
            ):
                with self.subTest(who=who, lane=lane, command=command):
                    self.assertIsNone(self.sh(command, who, lane))

    def test_path_tricks_from_a_worktree_cannot_reach_the_real_tree(self):
        self.assertEqual(self.write(f"{self.WT}/../../tools/_lib.py", "worker"), "deny")
        self.assertEqual(self.write(f"{self.WT}/.claude/../../../hooks/x.sh", "worker"), "deny")
        self.assertEqual(self.write(".claude\\tools\\_lib.py", "worker"), "deny")
        self.assertEqual(self.write(f"{self.WT}\\..\\..\\config/policy.json", "worker"), "deny")
        for lane, command in (
            ("Bash", f"cd {self.WT} && echo x > ../../tools/_lib.py"),
            ("Bash", f"echo x > {self.WT}/../../hooks/x.sh"),
            ("Bash", f"cd {self.WT}/.claude && cd ../../../.. && echo x > .claude/tools/x.py"),
            ("Bash", "echo x > '.claude\\tools\\_lib.py'"),
            ("Bash", f"cp README.md '{self.WT}\\..\\..\\config\\'"),
            ("PowerShell", "Set-Content .claude\\hooks\\x.sh 'y'"),
            ("PowerShell", f"Set-Location {self.WT}; Remove-Item ..\\..\\tools\\_lib.py"),
        ):
            with self.subTest(lane=lane, command=command):
                self.assertEqual(self.sh(command, "worker", lane), "deny")

    @unittest.skipUnless(os.name == "nt", "case-insensitive filesystem semantics")
    def test_case_and_trailing_dot_spellings_reach_the_real_tree_on_windows(self):
        """Windows folds case and drops a segment's trailing dot, so these all land in the
        real .claude/tools."""
        for spelling in (".CLAUDE/Tools/_lib.py", ".claude/tools./_lib.py",
                         ".claude/tools/_lib.py::$DATA", ".claude/tools::$INDEX_ALLOCATION/x.py",
                         f"{self.WT.upper()}/../../TOOLS/_lib.py"):
            with self.subTest(spelling=spelling, lane="Write"):
                self.assertEqual(self.write(spelling, "worker"), "deny")
            for lane, command in (("Bash", f"echo x > '{spelling}'"),
                                  ("PowerShell", f"Set-Content '{spelling}' 'y'")):
                with self.subTest(spelling=spelling, lane=lane):
                    self.assertEqual(self.sh(command, "worker", lane), "deny")

    def test_a_symlink_out_of_a_worktree_is_followed(self):
        link = self.root / self.WT / "link"
        try:
            os.symlink(self.root / ".claude" / "tools", link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            try:  # Windows without the symlink privilege: a junction needs none
                import _winapi
                _winapi.CreateJunction(str(self.root / ".claude" / "tools"), str(link))
            except Exception:
                self.skipTest(f"cannot create a symlink or junction here: {exc}")
        self.assertEqual(self.write(f"{self.WT}/link/_lib.py", "worker"), "deny")
        self.assertEqual(self.sh(f"echo x > {self.WT}/link/_lib.py", "worker"), "deny")


class TestRecordProse(GateCase):
    """A main-session command that MENTIONS the record in prose is not a write. The old shell
    check denied any line naming the record next to a mutator SUBSTRING, so "confirm" (rm),
    "address" (dd), "committee" (tee) and an arrow (>) in a note were all writes. One test per
    false-positive CLASS (L-6), each in the forms the main session actually uses."""

    def prose(self) -> dict:
        return {
            "word-inside-word": "Confirm .claude-iff/obs is sealed, address the committee.",
            "arrow": "Rollups flow capture -> .claude-iff/obs/rollups, never back.",
            "verb-as-prose": "Never rm, cp or remove anything in ../proj_claude_iff by hand.",
            "cmdlet-as-prose": "Remove-Item and Set-Content are the wrong tools for .claude-iff.",
            "absolute-path": f"Do not tee or dd into {self.record}/segments; obsctl seals it.",
        }

    def test_prose_heredoc_through_the_wrapper(self):
        """The reported case, end to end: denied before the fix (all five classes)."""
        for cls, line in self.prose().items():
            payload = {"tool_name": "Bash", "cwd": str(self.root), "tool_input": {
                "command": f"cat > .claude/tasks/notes.md <<'EOF'\n{line}\nEOF"}}
            with self.subTest(cls=cls):
                self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))

    def test_prose_in_every_main_session_form_is_not_a_mutation(self):
        for cls, line in self.prose().items():
            safe = line.replace('"', "'")
            forms = {
                "heredoc": ("Bash", f"cat > .claude/tasks/notes.md <<'EOF'\n{line}\nEOF"),
                "commit message": ("Bash", f"git commit -m \"$(cat <<'EOF'\nfix: {line}\nEOF\n)\""),
                "quoted argument": ("Bash", f'python3 .claude/tools/statectl.py note "{safe}"'),
                "script stdin": ("Bash", f"python3 .claude/tools/x.py <<'EOF'\n{line}\nEOF"),
                "echo": ("Bash", f'echo "{safe}" >> .claude/tasks/notes.md'),
                "powershell": ("PowerShell", f'Add-Content .claude/tasks/notes.md "{safe}"'),
            }
            for form, (lane, command) in forms.items():
                with self.subTest(cls=cls, form=form):
                    self.assertIsNone(self.sh(command, lane=lane))

    def test_real_record_mutations_in_less_obvious_forms_are_still_denied(self):
        encoded = base64.b64encode("Remove-Item .claude-iff -Recurse".encode("utf-16-le")).decode()
        for lane, command in (
            ("Bash", "bash <<'EOF'\nrm -rf .claude-iff\nEOF"),
            ("Bash", "cat <<'EOF' | sh\nrm -rf .claude-iff/obs\nEOF"),
            ("Bash", "cat <<'EOF' | bash -o pipefail\nrm -rf .claude-iff/obs\nEOF"),
            ("Bash", "python3 -c 'exec(input())' <<'EOF'\nimport shutil; shutil.rmtree('.claude-iff')\nEOF"),
            ("Bash", "python3 - <<'EOF'\nimport shutil\nshutil.rmtree('.claude-iff')\nEOF"),
            ("Bash", 'echo "<<EOF"\nrm -rf .claude-iff\nEOF'),  # a quoted "heredoc" is no heredoc
            ("Bash", 'D=.claude-iff; rm -rf "$D"'),
            ("Bash", "cd .claude-iff && rm -f obs/anchor.json"),
            ("Bash", "echo `rm -rf .claude-iff`"),
            ("Bash", 'eval "rm -rf .claude-iff"'),
            ("Bash", "find .claude-iff -name '*.json' -exec rm {} +"),
            ("Bash", "ls .claude-iff/obs/* | xargs rm"),
            ("Bash", "rm -rf .claude-{iff,x}"),
            ("Bash", "rm -rf .claude-i*"),
            ("Bash", "rm -rf .claude/../.claude-iff"),
            ("Bash", "rm -rf ../proj_claude_iff"),
            ("Bash", "rm -rf .."),
            ("Bash", "mv .claude-iff /tmp/elsewhere"),
            ("Bash", "cp -r /tmp/x/. .claude-iff/"),
            ("Bash", "tar -xf x.tar -C .claude-iff"),
            ("Bash", "dd if=/dev/zero of=.claude-iff/obs/anchor.json"),
            ("Bash", f"truncate -s0 {self.record}/segments/a.jsonl"),
            ("Bash", "echo x >| .claude-iff/obs/anchor.json"),
            ("Bash", "exec 3> .claude-iff/obs/x"),
            ("Bash", "printf x 1<>.claude-iff/obs/x"),
            ("Bash", 'pwsh -c "Remove-Item .claude-iff -Recurse"'),
            ("Bash", f"powershell -EncodedCommand {encoded}"),
            ("Bash", 'cmd /c "del .claude-iff\\obs\\anchor.json"'),
            ("Bash", "git rm -r --cached .claude-iff"),
            ("PowerShell", "[IO.File]::WriteAllText('.claude-iff/obs/anchor.json','x')"),
            ("PowerShell", "(Get-Item .claude-iff/obs/anchor.json).Delete()"),
            ("PowerShell", "Get-ChildItem .claude-iff | Remove-Item -Recurse"),
        ):
            with self.subTest(lane=lane, command=command):
                self.assertEqual(self.sh(command, lane=lane), "deny")

    def test_reading_and_committing_the_record_stay_allowed(self):
        for lane, command in (
            ("Bash", "ls ../proj_claude_iff/"),
            ("Bash", "du -sh ../proj_claude_iff"),
            ("Bash", "git add .claude-iff/obs/anchor.json && git commit -m anchor"),
            ("Bash", "python3 -c \"import json; print(json.load(open('../proj_claude_iff/x.json')))\""),
            ("Bash", "python3 -c \"x = open('.claude-iff/obs/anchor.json').read()\" > /tmp/a"),
            ("Bash", "find . -path ./.claude-iff -prune -o -name '*.md' -print"),
            ("Bash", "echo 'rm -rf .claude-iff'"),
            ("PowerShell", "Get-Content .claude-iff/obs/anchor.json 2>$null"),
        ):
            with self.subTest(lane=lane, command=command):
                self.assertIsNone(self.sh(command, lane=lane))


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

    BUILDER = {"agent_id": "builder-T1", "task_id": "T1", "status": "done", "agent": "builder",
               "model": "opus", "files_changed": ["src/a.py"], "needs_main": [],
               "tests": [{"command": "python3 -m x", "exit_code": 0, "summary": "ok"}]}

    def test_a_builder_envelope_meets_the_one_shared_contract(self):
        """The hook and checkctl handoff call the same validator: a builder envelope missing
        its tests is blocked on write, a complete one passes."""
        self.assertEqual(self.run_on(".claude/state/handshakes/T1.json",
                                     json.dumps(self.BUILDER)).returncode, 0)
        incomplete = {k: v for k, v in self.BUILDER.items() if k != "tests"}
        res = self.run_on(".claude/state/handshakes/T1.json", json.dumps(incomplete))
        self.assertEqual(res.returncode, 2)
        self.assertIn("tests must be a list", res.stderr)
        red = dict(self.BUILDER, tests=[{"command": "x", "exit_code": 1, "summary": "1 failed"}])
        res = self.run_on(".claude/state/handshakes/T1.json", json.dumps(red))
        self.assertEqual(res.returncode, 2, "done with a failing test is not done")

    def test_the_legacy_brief_shape_alone_is_blocked(self):
        legacy = {k: v for k, v in self.BUILDER.items() if k not in ("agent_id", "status")}
        legacy["STATUS"] = "ok"
        res = self.run_on(".claude/state/handshakes/T1.json", json.dumps(legacy))
        self.assertEqual(res.returncode, 2)
        self.assertIn("agent_id", res.stderr)

    def test_a_broken_validator_fails_closed_on_envelopes_only(self):
        (self.root / ".claude" / "tools" / "_lib.py").write_text("raise ImportError('broken')\n")
        res = self.run_on(".claude/state/handshakes/T1.json", json.dumps(self.BUILDER))
        self.assertEqual(res.returncode, 2)
        self.assertIn("validator could not run", res.stderr)
        self.assertEqual(self.run_on(".claude/config/x.json", '{"a": 1}').returncode, 0,
                         "plain JSON needs no validator and no advisory")


class TestHandoffGuard(HookCase):
    """handoff-guard.sh on SubagentStart/SubagentStop, end to end: a builder with no envelope is
    sent back once, the loop guard and every other agent type pass, and garbage fails open."""

    def setUp(self) -> None:
        super().setUp()
        import _lib
        self.lib = _lib
        _lib.journal_append("mode", value="fableous-orchestrated")
        _lib.atomic_write_json(_lib.stub_path("T1"), {"task_id": "T1", "agent": "builder",
                                                      "dispatched_at": "2020-01-01T00:00:00Z"})

    def guard(self, event: str, **fields) -> subprocess.CompletedProcess:
        return self.run_hook("handoff-guard.sh", dict({"hook_event_name": event,
                                                        "agent_id": "a1",
                                                        "agent_type": "builder"}, **fields))

    def test_a_builder_without_an_envelope_is_blocked_once(self):
        start = self.guard("SubagentStart")
        self.assertEqual((start.returncode, start.stdout.strip()), (0, ""))
        self.assertIn("a1", self.lib.orchestration_state()["agents"])
        stop = self.guard("SubagentStop", stop_hook_active=False)
        self.assertEqual(stop.returncode, 0)
        out = json.loads(stop.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("T1", out["reason"])
        again = self.guard("SubagentStop", stop_hook_active=True)
        self.assertEqual((again.returncode, again.stdout.strip()), (0, ""),
                         "stop_hook_active: the hook must never loop")

    def test_a_delivered_envelope_lets_the_builder_stop(self):
        self.guard("SubagentStart")
        self.lib.atomic_write_json(self.lib.envelope_path("T1"), dict(
            TestPostWriteValidate.BUILDER, files_changed=[]))
        self.assertEqual(self.guard("SubagentStop").stdout.strip(), "")

    def test_other_agent_types_and_modes_are_untouched(self):
        for kind in ("verifier", "scout", "general-purpose"):
            with self.subTest(agent_type=kind):
                self.guard("SubagentStart", agent_type=kind)
                self.assertEqual(self.guard("SubagentStop", agent_type=kind).stdout.strip(), "")
        self.guard("SubagentStart")
        self.lib.journal_append("mode", value="guided-solo")
        self.assertEqual(self.guard("SubagentStop").stdout.strip(), "")

    def test_fails_open_on_garbage(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root),
                   CLAUDE_IFF_RECORD_ROOT=str(self.record))
        for raw in ("not json {{{", "[]", "", '{"hook_event_name": "SubagentStop", "agent_type": 7}'):
            with self.subTest(raw=raw):
                res = subprocess.run([BASH, str(HOOKS / "handoff-guard.sh")], input=raw,
                                     capture_output=True, text=True, timeout=30, env=env,
                                     check=False)
                self.assertEqual((res.returncode, res.stdout.strip()), (0, ""))


class TestDelegationNudgeHook(HookCase):
    """The nudge rides post-write-validate.sh's exit-0 path as additionalContext: it never
    blocks, never unblocks, and a broken counter costs only the note."""

    def setUp(self) -> None:
        super().setUp()
        import _lib
        _lib.journal_append("mode", value="fableous-orchestrated")
        cfg = {"nudge_after": 1, "handoff_test_timeout": 60}
        (self.root / ".claude" / "config" / "orchestration.json").write_text(json.dumps(cfg))
        self.state = self.root / ".claude" / "state" / "orchestration.json"

    def write(self, relative: str, content: str, **extra) -> subprocess.CompletedProcess:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        payload = dict({"tool_name": "Write", "tool_input": {"file_path": str(path)},
                        "cwd": str(self.root)}, **extra)
        return self.run_hook("post-write-validate.sh", payload)

    def test_the_nudge_is_additional_context_on_a_passing_write(self):
        res = self.write("src/a.py", "x = 1\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("DELEGATION NUDGE", out["additionalContext"])

    def test_a_blocked_write_stays_blocked_when_a_nudge_is_due(self):
        res = self.write(".claude/config/x.json", '{"a": ')
        self.assertEqual(res.returncode, 2)
        self.assertIn("VALIDATE_FAIL", res.stderr)

    def test_a_broken_counter_never_breaks_the_hook(self):
        self.state.mkdir(parents=True)  # a directory where the state file belongs: unwritable
        res = self.write("src/a.py", "x = 1\n")
        self.assertEqual((res.returncode, res.stdout.strip()), (0, ""))
        self.assertEqual(self.write(".claude/config/y.json", "{ nope").returncode, 2)

    def test_sub_agents_and_other_modes_get_no_nudge(self):
        self.assertEqual(self.write("src/a.py", "x\n", agent_type="builder").stdout.strip(), "")
        import _lib
        _lib.journal_append("mode", value="freestyle")
        self.assertEqual(self.write("src/b.py", "x\n").stdout.strip(), "")


def _iso(offset: float) -> str:
    import time
    from datetime import datetime, timezone
    return datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestActivityPulseHook(HookCase):
    """The activity pulse (T12) rides the policy gate AFTER its decision and the capture hook on
    sub-agent events: a throttled heartbeat refresh that can never change, delay or break a
    decision. Law 2, telemetry half, with the gate half as the thing being protected."""

    # An allow and a deny, on the Write lane and both shell lanes (one real wrapper run each per
    # condition below: these are the slow tests, so the matrix is kept to what proves the point).
    PAYLOADS = (
        ("allow Write", {"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}}),
        ("deny Write", {"tool_name": "Write", "tool_input": {"file_path": ".claude/config/policy.json"},
                        "agent_type": "worker"}),
        ("allow Bash", {"tool_name": "Bash", "tool_input": {"command": "ls -la src"}}),
        ("deny PowerShell", {"tool_name": "PowerShell",
                             "tool_input": {"command": "Remove-Item .claude/hooks/x.sh"},
                             "agent_type": "worker"}),
    )

    def setUp(self) -> None:
        super().setUp()
        self.state = self.root / ".claude" / "state"
        self.beat = self.state / "heartbeat.json"

    def knobs(self, progress_value) -> None:
        cfg = json.loads((CLAUDE_DIR / "config" / "orchestration.json").read_text(encoding="utf-8"))
        cfg["progress"] = progress_value
        (self.root / ".claude" / "config" / "orchestration.json").write_text(json.dumps(cfg))

    def gate(self, payload: dict) -> tuple:
        res = self.run_hook("policy-gate.sh", dict(payload, cwd=str(self.root)))
        return res.returncode, res.stdout, res.stderr

    def write_beat(self, offset: float, note: str = "working") -> str:
        text = json.dumps({"ts": _iso(offset), "note": note, "via": "Bash"})
        self.state.mkdir(parents=True, exist_ok=True)
        self.beat.write_text(text, encoding="utf-8")
        return text

    def test_the_heartbeat_refreshes_after_the_window_not_before(self):
        self.knobs({"pulse_seconds": 60, "report_minutes": 30})
        fresh = self.write_beat(-10)
        self.gate(dict(self.PAYLOADS)["allow Bash"])
        self.assertEqual(self.beat.read_text(encoding="utf-8"), fresh, "inside the window: untouched")
        self.write_beat(-120)
        self.gate(dict(self.PAYLOADS)["allow Bash"])
        text = self.beat.read_text(encoding="utf-8")
        beat = json.loads(text)
        self.assertEqual((beat["note"], beat["via"]), ("working", "Bash"))
        import _lib
        self.assertLess(_lib.age_seconds(beat["ts"]), 30)
        self.assertNotIn("ls -la", text, "the tool's name, never its input")

    def test_a_turn_ended_beat_and_a_sub_agent_call_pulse_and_zero_turns_it_off(self):
        self.knobs({"pulse_seconds": 60, "report_minutes": 30})
        self.write_beat(-5, note="turn ended")
        self.gate(dict(self.PAYLOADS)["deny Write"])  # a sub-agent, and a denied call: still activity
        self.assertEqual(json.loads(self.beat.read_text(encoding="utf-8"))["note"], "working")
        self.beat.unlink()
        self.knobs({"pulse_seconds": 0, "report_minutes": 30})
        self.gate(dict(self.PAYLOADS)["allow Write"])
        self.assertFalse(self.beat.exists(), "pulse_seconds 0: no pulse")

    def test_a_broken_pulse_leaves_every_decision_byte_identical(self):
        self.knobs({"pulse_seconds": 0, "report_minutes": 30})
        baseline = {name: self.gate(p) for name, p in self.PAYLOADS}
        for name, (code, out, _err) in baseline.items():  # the baseline itself is right
            self.assertEqual(code, 0, name)
            self.assertEqual(self.decision(subprocess.CompletedProcess([], 0, out, "")),
                             "deny" if name.startswith("deny") else None, name)
        lib = self.root / ".claude" / "tools" / "_lib.py"
        shipped = lib.read_text(encoding="utf-8")

        def pulse_on():
            self.knobs({"pulse_seconds": 1, "report_minutes": 30})

        def beat_is_a_directory():
            pulse_on()
            self.beat.mkdir(parents=True)

        def corrupt_beat():
            pulse_on()
            self.beat.write_bytes(b"\xff{ not json")

        def state_is_a_file():
            pulse_on()
            shutil.rmtree(self.state)
            self.state.write_text("not a directory", encoding="utf-8")

        def pulse_raises():
            pulse_on()
            lib.write_text(shipped + "\n\ndef activity_pulse(via):\n    raise RuntimeError('broken')\n",
                           encoding="utf-8")

        def pulse_prints_and_exits():
            pulse_on()
            lib.write_text(shipped + "\n\ndef activity_pulse(via):\n    import sys\n"
                                     "    print('NOISE')\n    sys.stderr.write('NOISE')\n"
                                     "    sys.exit(7)\n", encoding="utf-8")

        def reset():
            lib.write_text(shipped, encoding="utf-8")
            if self.state.is_file():
                self.state.unlink()
            if self.beat.is_dir():
                shutil.rmtree(self.beat)
            self.beat.unlink(missing_ok=True)
            self.state.mkdir(parents=True, exist_ok=True)

        for condition in (pulse_on, beat_is_a_directory, corrupt_beat, state_is_a_file,
                          pulse_raises, pulse_prints_and_exits):
            condition()
            try:
                for name, payload in self.PAYLOADS:
                    with self.subTest(condition=condition.__name__, call=name):
                        self.assertEqual(self.gate(payload), baseline[name])
            finally:
                reset()

    def test_the_capture_hook_pulses_on_sub_agent_events_only(self):
        self.knobs({"pulse_seconds": 60, "report_minutes": 30})
        res = self.run_hook("obs-capture.sh", {"hook_event_name": "Stop", "session_id": "s1"})
        self.assertEqual(res.returncode, 0)
        self.assertFalse(self.beat.exists(), "Stop is the heartbeat hook's, not a pulse")
        for event in ("SubagentStart", "SubagentStop"):
            with self.subTest(event=event):
                self.beat.unlink(missing_ok=True)
                res = self.run_hook("obs-capture.sh", {"hook_event_name": event, "session_id": "s1",
                                                       "agent_type": "builder"})
                self.assertEqual((res.returncode, res.stdout.strip()), (0, ""))
                beat = json.loads(self.beat.read_text(encoding="utf-8"))
                self.assertEqual((beat["note"], beat["via"]), ("working", event))
        spool = (self.record / "spool" / "s1.jsonl").read_text(encoding="utf-8")
        self.assertIn("SubagentStop", spool, "capture still records the event")

    def test_the_capture_hook_still_captures_when_the_pulse_breaks(self):
        self.knobs({"pulse_seconds": 60, "report_minutes": 30})
        self.beat.mkdir(parents=True)  # unwritable heartbeat
        res = self.run_hook("obs-capture.sh", {"hook_event_name": "SubagentStart", "session_id": "s2"})
        self.assertEqual(res.returncode, 0)
        self.assertIn("SubagentStart", (self.record / "spool" / "s2.jsonl").read_text(encoding="utf-8"))


class TestProgressReportHook(HookCase):
    """The periodic progress report rides post-write-validate.sh's advisory channel: additional
    context on an exit-0 run, for the lead only, in the two organised modes, never blocking."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("progress.py", "statectl.py", "checkctl.py"):
            shutil.copy(CLAUDE_DIR / "tools" / name, self.root / ".claude" / "tools" / name)
        import _lib
        self.lib = _lib
        _lib.journal_append("mode", value="guided-solo")
        _lib.journal_append("milestone", id="M1", title="the long run")
        _lib.journal_append("task", id="T1", title="build it", status="doing", milestone="M1")

    def write(self, relative: str, content: str, **extra) -> subprocess.CompletedProcess:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        payload = dict({"tool_name": "Write", "tool_input": {"file_path": str(path)},
                        "cwd": str(self.root)}, **extra)
        return self.run_hook("post-write-validate.sh", payload)

    def minutes(self, value: int) -> None:
        cfg = json.loads((CLAUDE_DIR / "config" / "orchestration.json").read_text(encoding="utf-8"))
        cfg["progress"] = {"pulse_seconds": 60, "report_minutes": value}
        (self.root / ".claude" / "config" / "orchestration.json").write_text(json.dumps(cfg))

    def test_due_then_not_due(self):
        res = self.write("src/a.py", "x = 1\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("post this progress block to the user as is, then continue", out["additionalContext"])
        self.assertIn("PROGRESS M1", out["additionalContext"])
        self.assertIn("report", self.lib.orchestration_state())
        again = self.write("src/b.py", "x = 2\n")
        self.assertEqual((again.returncode, again.stdout.strip()), (0, ""), "not due inside the window")

    def test_knob_zero_freestyle_and_a_sub_agent_get_nothing(self):
        self.minutes(0)
        self.assertEqual(self.write("src/a.py", "x\n").stdout.strip(), "")
        self.minutes(30)
        self.assertEqual(self.write("src/a.py", "x\n", agent_type="builder").stdout.strip(), "")
        self.lib.journal_append("mode", value="freestyle")
        self.assertEqual(self.write("src/a.py", "x\n").stdout.strip(), "")
        self.assertFalse(self.lib.orchestration_state_path().exists())

    def test_a_blocked_write_stays_blocked_when_a_report_is_due(self):
        res = self.write(".claude/config/x.json", '{"a": ')
        self.assertEqual(res.returncode, 2)
        self.assertIn("VALIDATE_FAIL", res.stderr)
        self.assertEqual(res.stdout.strip(), "")

    def test_a_broken_model_never_breaks_the_hook(self):
        (self.root / ".claude" / "tools" / "progress.py").write_text("raise ImportError('broken')\n")
        res = self.write("src/a.py", "x\n")
        self.assertEqual((res.returncode, res.stdout.strip()), (0, ""))


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


class TestSymbolicRefIsNotReadOnly(HookCase):
    """`git symbolic-ref NAME REF` WRITES the ref, so it must not ride the read-only
    carve-out - neither via policy.json's allowlist nor via the fallback regex in
    policy_gate.py (the two must stay in step)."""

    def test_subagent_symbolic_ref_is_denied(self):
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "git symbolic-ref refs/heads/main refs/heads/evil"},
                   "agent_type": "worker"}
        self.assertEqual(self.decision(self.run_hook("policy-gate.sh", payload)), "deny")

    def test_read_only_alternative_still_allowed(self):
        payload = {"tool_name": "Bash",
                   "tool_input": {"command": "git rev-parse --symbolic-full-name HEAD"},
                   "agent_type": "worker"}
        self.assertIsNone(self.decision(self.run_hook("policy-gate.sh", payload)))


class TestConsolePortCollision(HookCase):
    """autostart used to swallow a lost bind entirely: the hook printed the URL of whatever
    process held the port (usually another project's console) and nothing ever said so."""

    def test_busy_port_is_reported_not_silently_swallowed(self):
        import socket
        import _lib
        blocker = socket.socket()
        try:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]
            (self.root / ".claude" / "console").mkdir(parents=True, exist_ok=True)
            (self.root / ".claude" / "console" / "console.py").write_text("# stub\n", encoding="utf-8")
            cfg = _lib.read_json(self.root / ".claude" / "config" / "console.json", {}) or {}
            cfg.update({"autostart": True, "host": "127.0.0.1", "port": port})
            _lib.atomic_write_json(self.root / ".claude" / "config" / "console.json", cfg)
            res = self.run_hook("session-start.sh", {"hook_event_name": "SessionStart"})
            self.assertEqual(res.returncode, 0)
            self.assertIn("already in use", res.stdout)
            self.assertNotIn("console.html (open it", res.stdout,
                             "a busy port must not print a URL that points at another project")
        finally:
            blocker.close()



if __name__ == "__main__":
    unittest.main(verbosity=2)
