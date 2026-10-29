#!/usr/bin/env python3
"""test_worktree.py - the worker rules: what this system does when a session starts INSIDE a
linked git worktree (.claude/worktrees/<task>/), which is where Cview starts its workers and
where the lead's `statectl dispatch` puts a builder.

A worktree is a full copy of the repo, .claude/ included, so the same hooks and tools run there
with the worktree as project root. The tracked state in it is main's, cut at the branch point.
These tests pin the eight points of the Cview-ready backbone (research/2026-10-29-cview-backbone-
issue.md): the detector tells a linked worktree from main, a submodule and a broken `.git` file;
the record root resolves against the MAIN checkout from a worktree (one record per project);
nothing in a worktree writes main's tracked state (the journal, the projections, needs-human,
proposals, the ritual); the hooks run from a REAL git worktree write only under the worktree or
the record root and leave both checkouts clean; SessionStart prints a WORKTREE SESSION block and
exits 0 with no gate in the way; the Stop hook's open-task line is advisory; no hook mutates git
and `git push` lives nowhere in the tools; the containers are gitignored; and the two CLAUDE.md
role sections exist in the home guide and in the adopt template.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402
from test_hooks import BASH, HOOKS, GateCase, HookCase  # noqa: E402

TOOLS_DIR = CLAUDE_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _lib  # noqa: E402
import checkctl  # noqa: E402
import distctl  # noqa: E402
import statectl  # noqa: E402

DERIVED_STATE = (".claude/state/session.json", ".claude/state/HANDOFF.md",
                 ".claude/state/needs-human.json")


def run_cli(module, *argv: str):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(list(argv))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class WorktreeCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        # The fixture pins the record root through the env override; these tests are about
        # what the DEFAULT and the committed relative value resolve to, so the override goes.
        os.environ.pop("CLAUDE_IFF_RECORD_ROOT", None)
        _lib.clear_config_cache()


# --------------------------------------------------------------------------- the detector

class TestDetector(WorktreeCase):
    def test_main_checkout_is_not_a_worktree(self):
        (self.root / ".git").mkdir()
        self.assertIsNone(_lib.worktree_info())
        self.assertEqual(_lib.main_checkout(), self.root.resolve())

    def test_no_git_at_all_is_not_a_worktree(self):
        self.assertIsNone(_lib.worktree_info())

    def test_linked_worktree_names_main_name_and_branch(self):
        wt = self.linked_worktree("todo-note")
        self.as_worktree(wt)
        info = _lib.worktree_info()
        self.assertIsNotNone(info)
        self.assertEqual(info["root"], wt.resolve())
        self.assertEqual(info["main"].resolve(), self.root.resolve())
        self.assertEqual(info["name"], "todo-note")
        self.assertEqual(info["branch"], "worktree-todo-note")
        self.assertEqual(_lib.main_checkout().resolve(), self.root.resolve())
        # project_root stays the worktree: its own .claude/ (config, hooks) is the one in use.
        self.assertEqual(_lib.project_root(), wt.resolve())

    def test_detached_head_reads_as_empty_branch(self):
        wt = self.linked_worktree("x")
        (self.root / ".git" / "worktrees" / "x" / "HEAD").write_text("0" * 40 + "\n")
        self.as_worktree(wt)
        self.assertEqual(_lib.worktree_info()["branch"], "")

    def test_a_submodule_is_not_a_worktree(self):
        sub = self.root / "vendor" / "lib"
        sub.mkdir(parents=True)
        (sub / ".git").write_text("gitdir: ../../.git/modules/vendor/lib\n")
        self.assertIsNone(_lib.worktree_info(sub))

    def test_a_broken_git_file_reads_as_main(self):
        """Fail toward main: a worker rule that switched on by accident in main would deny
        the lead its own state; a rule that stays off in a broken worktree is caught by the
        review of the diff."""
        for text in ("", "garbage", "gitdir:", "gitdir: relative/nowhere"):
            (self.root / ".git").write_text(text)
            self.assertIsNone(_lib.worktree_info(), text)
        (self.root / ".git").unlink()
        (self.root / ".git").mkdir()
        self.assertIsNone(_lib.worktree_info())

    def test_worker_task_reads_the_cview_files(self):
        self.assertEqual(_lib.worker_task(self.root), {"task": False, "report": None})
        (self.root / "TASK.md").write_text("# t\n")
        self.assertEqual(_lib.worker_task(self.root), {"task": True, "report": None})
        (self.root / "BLOCKED.md").write_text("why?\n")
        self.assertEqual(_lib.worker_task(self.root)["report"], "BLOCKED.md")
        (self.root / "DONE.md").write_text("# DONE\n")
        self.assertEqual(_lib.worker_task(self.root)["report"], "DONE.md")


# --------------------------------------------------------------------------- point 1: roots

class TestRecordRoot(WorktreeCase):
    def test_default_sibling_is_mains_from_a_worktree(self):
        self.write_config("policy", {**_lib.load_config("policy"), "record_root": None})
        wt = self.linked_worktree("t")
        self.as_worktree(wt)
        expect = self.root.resolve().parent / f"{self.root.name}_claude_iff"
        self.assertEqual(_lib.record_root(), expect.resolve())
        self.assertNotIn(".claude", _lib.record_root().parts[-2:])

    def test_relative_configured_root_anchors_at_main(self):
        self.write_config("policy", {**_lib.load_config("policy"), "record_root": "../proj-record"})
        in_main = _lib.record_root()
        self.assertEqual(in_main, (self.root.resolve().parent / "proj-record").resolve())
        wt = self.linked_worktree("t")
        self.as_worktree(wt)
        self.assertEqual(_lib.record_root(), in_main, "one record per project, main or worktree")

    def test_env_override_still_wins_everywhere(self):
        wt = self.linked_worktree("t")
        self.as_worktree(wt)
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.record)
        _lib.clear_config_cache()
        self.assertEqual(_lib.record_root(), self.record.resolve())


# --------------------------------------------------------------------------- point 2: one writer

class TestNoStateWritesFromAWorktree(WorktreeCase):
    """The worker rule with teeth: inside a worktree every writer of main's tracked state
    refuses, by name, and leaves the files byte-identical."""

    def setUp(self) -> None:
        super().setUp()
        self.journal("session_start", session="main-s1")
        run_cli(statectl, "pointer", "main's pointer")
        self.wt = self.linked_worktree("todo-note")
        self.as_worktree(self.wt)
        self.state = self.wt / ".claude" / "state"
        self.before = self.snapshot()

    def snapshot(self) -> dict:
        return {p.relative_to(self.state).as_posix(): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file()}

    def unchanged(self) -> None:
        self.assertEqual(self.snapshot(), self.before, "a worker changed main's state copy")

    def test_journal_append_refuses(self):
        with self.assertRaises(_lib.LibError) as ctx:
            _lib.journal_append("pointer", text="from the worker")
        msg = str(ctx.exception)
        self.assertIn("worktree", msg)
        self.assertIn("todo-note", msg)
        self.assertIn("BLOCKED.md", msg)
        self.unchanged()

    def test_statectl_mutations_refuse_with_one_line(self):
        for argv in (["pointer", "x"], ["note", "x"], ["decision", "x", "--why", "y"],
                     ["need", "open", "--title", "x", "--category", "decide",
                      "--context", "c" * 70, "--action", "a"],
                     ["proposal", "add", "x", "--source", "human"],
                     ["task", "t1", "--title", "x", "--status", "todo"],
                     ["milestone", "m", "--title", "m"], ["refresh"],
                     ["start", "--session", "w1"]):
            code, out, err = run_cli(statectl, *argv)
            self.assertNotEqual(code, 0, argv)
            self.assertIn("worktree", (out + err).lower(), argv)
            self.assertEqual(err.count("refused:"), 1, argv)
            self.assertNotIn("Traceback", out + err, argv)
            self.unchanged()

    def test_resume_and_status_read_without_writing(self):
        for argv in (["resume"], ["status"]):
            code, out, err = run_cli(statectl, *argv)
            self.assertEqual(code, 0, out + err)
            self.unchanged()
        code, out, _ = run_cli(statectl, "resume")
        self.assertIn("main's pointer", out)

    def test_the_ritual_is_refused_in_one_line(self):
        self.grant_ticket()  # even with a fresh ticket: the ritual is main's
        self.before = self.snapshot()
        for argv in (["run", "--phase", "check"], ["run", "--phase", "polish"], ["complete"]):
            code, out, err = run_cli(checkctl, *argv)
            self.assertNotEqual(code, 0, argv)
            self.assertIn("refused:", out, argv)
            self.assertIn("todo-note", out, argv)
            self.assertIn("DONE.md", out, argv)
            self.assertEqual(out.count("refused:"), 1, argv)
            self.unchanged()
        self.assertFalse((self.state / "memory-run.json").exists(), "a ritual run was opened")

    def test_main_is_untouched_by_the_rule(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        _lib.clear_config_cache()
        code, out, _ = run_cli(statectl, "pointer", "main still writes")
        self.assertEqual(code, 0, out)
        self.assertIn("main still writes",
                      (self.root / ".claude/state/journal.jsonl").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the gate's worker

class TestGateWorker(GateCase):
    """A session whose project root is a linked worktree carries no agent identity, so the
    gate used to read it as the main session in every respect. It is now a 'worker': main's
    trust for git and for its COPY of the protected tree (the main session reviews the diff
    before any merge, as for a builder's), but the lead's two commands are refused and main's
    tracked state in the copy is a ring. Main itself is untouched by the rule."""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.linked_worktree("todo-note")
        (self.wt / "TASK.md").write_text("# todo-note\n", encoding="utf-8")
        self.as_worktree(self.wt)
        self.gate_root = self.wt

    def write(self, rel: str, who: str | None = None, full: bool = False):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.wt / rel)}}
        if who:
            payload["agent_type"] = who
        return self.judge(payload, full=full)

    def test_mains_state_in_the_copy_is_denied_with_a_worker_reason(self):
        for rel in (".claude/state/journal.jsonl", ".claude/state/session.json",
                    ".claude/state/HANDOFF.md", ".claude/state/needs-human.jsonl",
                    ".claude/state/proposals.jsonl", ".claude/state/memory-run.json"):
            with self.subTest(rel=rel):
                out = self.write(rel, full=True)
                self.assertEqual(out["permissionDecision"], "deny")
                reason = out["permissionDecisionReason"]
                self.assertIn("worktree", reason)
                self.assertIn("DONE.md", reason)

    def test_shell_writes_into_mains_state_are_denied_too(self):
        for cmd in ("echo x >> .claude/state/journal.jsonl",
                    "cp a.json .claude/state/session.json",
                    "python3 - <<'EOF'\nopen('.claude/state/HANDOFF.md','w').write('x')\nEOF"):
            with self.subTest(cmd=cmd.splitlines()[0]):
                self.assertEqual(self.sh(cmd), "deny")

    def test_the_task_files_handshakes_and_the_rest_are_writable(self):
        for rel in ("TODO.md", "DONE.md", "BLOCKED.md", "src/x.py", ".claude/tasks/x.md",
                    ".claude/state/handshakes/T1.json", ".claude/hooks/session-start.sh",
                    ".claude/tools/_lib.py", ".claude/config/policy.json"):
            with self.subTest(rel=rel):
                self.assertIsNone(self.write(rel))

    def test_git_is_the_workers_to_use(self):
        """It commits on its branch; the refusal of a push is Cview's (a dead push URL on the
        worktree) and the human's, not this gate's. Sub-agents' git denylist does not apply."""
        for cmd in ("git add TODO.md", "git commit -am 'one line'", "git status --short",
                    "git log --oneline -3"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.sh(cmd))

    def test_the_leads_commands_are_refused(self):
        for cmd in ("python3 .claude/tools/statectl.py dispatch T1",
                    "python3 .claude/tools/statectl.py accept T1"):
            with self.subTest(cmd=cmd):
                out = self.judge({"tool_name": "Bash", "tool_input": {"command": cmd}}, full=True)
                self.assertEqual(out["permissionDecision"], "deny")
                self.assertIn("worktree", out["permissionDecisionReason"])

    def test_main_checkout_keeps_its_trust(self):
        (self.root / ".git").mkdir(exist_ok=True)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        _lib.clear_config_cache()
        self.gate_root = self.root
        for rel in (".claude/hooks/x.sh", ".claude/state/journal.jsonl", ".claude/state/session.json"):
            payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / rel)}}
            self.assertIsNone(self.judge(payload), rel)
        self.assertIsNone(self.sh("python3 .claude/tools/statectl.py dispatch T1"))

    def test_from_main_a_worktrees_state_copy_is_denied_to_every_identity(self):
        """The lead's builders: dispatched with main as the gate's root, their worktree's
        .claude/state/ is the same ring (handshakes/ carved out, where the envelope goes)."""
        (self.root / ".git").mkdir(exist_ok=True)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        _lib.clear_config_cache()
        self.gate_root = self.root
        for who in (None, "builder", "verifier"):
            for rel, expect in ((".claude/state/journal.jsonl", "deny"),
                                (".claude/state/session.json", "deny"),
                                (".claude/state/handshakes/T1.json", None),
                                ("src/x.py", None)):
                with self.subTest(who=who, rel=rel):
                    payload = {"tool_name": "Write",
                               "tool_input": {"file_path": str(self.wt / rel)}}
                    if who:
                        payload["agent_type"] = who
                    self.assertEqual(self.judge(payload), expect)

    def test_a_named_sub_agent_in_a_worktree_is_still_a_sub_agent(self):
        """Identity is never upgraded: a builder keeps its grants (none in the protected tree
        of the root it is judged against) and its git denylist."""
        self.assertEqual(self.write(".claude/hooks/x.sh", who="builder"), "deny")
        self.assertEqual(self.sh("git commit -am x", who="builder"), "deny")


# --------------------------------------------------------------------------- the hooks, for real

class TestHooksFromARealWorktree(HookCase):
    """`git worktree add` for real, then the four hooks a session fires (SessionStart, Stop,
    PostToolUse, the capture lane) run with the worktree as CLAUDE_PROJECT_DIR. Every path
    they write is under the worktree or the project's record root, both checkouts stay clean
    for git, and SessionStart prints the worker block and exits 0 with nothing in the way."""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("CLAUDE_IFF_RECORD_ROOT", None)  # the default sibling, resolved for real
        cfg = json.loads((self.root / ".claude/config/console.json").read_text())
        cfg["autostart"] = False  # never spawn a server from the test suite
        (self.root / ".claude/config/console.json").write_text(json.dumps(cfg))
        obs = json.loads((self.root / ".claude/config/observe.json").read_text())
        obs["capture_all_events"] = True
        (self.root / ".claude/config/observe.json").write_text(json.dumps(obs))
        (self.root / ".gitignore").write_text(distctl.render_gitignore_block("tracked"),
                                              encoding="utf-8")
        self.journal("session_start", session="main-s1")
        run_cli(statectl, "pointer", "main's own pointer")
        _lib.atomic_write_json(_lib.state_dir() / "memory-run.json",
                               {"last_completed": "2020-01-01T00:00:00Z"})
        self.wt = self.git_worktree("todo-note")
        self.record = self.root.parent / f"{self.root.name}_claude_iff"
        self.as_worktree(self.wt)

    def run_in_wt(self, name: str, payload: dict):
        env = dict(os.environ)
        env.pop("CLAUDE_IFF_RECORD_ROOT", None)
        env["CLAUDE_PROJECT_DIR"] = str(self.wt)
        return subprocess.run([BASH, str(HOOKS / name)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, env=env, check=False,
                              cwd=str(self.wt))

    def git(self, *args, cwd=None) -> str:
        res = subprocess.run(["git", *args], cwd=str(cwd or self.root), capture_output=True,
                             text=True, timeout=60, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    @staticmethod
    def files(base: Path, skip=(".git",)) -> dict:
        out = {}
        for p in base.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:  # python's own cache, gitignored
                continue
            rel = p.relative_to(base).as_posix()
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            out[rel] = p.read_bytes()
        return out

    def test_every_write_lands_under_the_worktree_or_the_record_root(self):
        main_before = self.files(self.root, skip=(".git", ".claude/worktrees"))
        wt_before = self.files(self.wt)
        record_before = self.files(self.record) if self.record.exists() else {}
        (self.wt / "TASK.md").write_text("# todo-note\n", encoding="utf-8")
        written = self.wt / "src" / "x.py"
        written.parent.mkdir()
        written.write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.wt)  # the worker's own commit, as Cview expects
        self.git("-c", "user.name=w", "-c", "user.email=w@x", "commit", "-q", "-m", "task",
                 cwd=self.wt)
        wt_before = self.files(self.wt)
        results = {
            "session-start.sh": self.run_in_wt("session-start.sh",
                                               {"hook_event_name": "SessionStart", "session_id": "w1"}),
            "post-write-validate.sh": self.run_in_wt("post-write-validate.sh", {
                "hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.wt),
                "tool_input": {"file_path": str(written)}}),
            "obs-capture.sh": self.run_in_wt("obs-capture.sh",
                                             {"hook_event_name": "SessionStart", "session_id": "w1"}),
            "heartbeat.sh": self.run_in_wt("heartbeat.sh", {"hook_event_name": "Stop", "session_id": "w1"}),
        }
        for name, res in results.items():
            self.assertEqual(res.returncode, 0, f"{name}: {res.stderr}")
        # (1) the main checkout: not one byte
        self.assertEqual(self.files(self.root, skip=(".git", ".claude/worktrees")), main_before,
                         "a hook run from the worktree wrote into the main checkout")
        # (2) the worktree: only its own gitignored runtime (the heartbeat), nothing tracked
        wt_after = self.files(self.wt)
        changed = {k for k in set(wt_after) | set(wt_before) if wt_after.get(k) != wt_before.get(k)}
        self.assertLessEqual(changed, {".claude/state/heartbeat.json"}, changed)
        self.assertEqual(self.git("status", "--short", cwd=self.wt), "", "the worktree is dirty")
        self.assertEqual(self.git("status", "--short"), "", "the main checkout is dirty")
        # (3) the capture went to the PROJECT's record root, the sibling of main
        record_after = self.files(self.record)
        new = {k for k in record_after if record_after[k] != record_before.get(k)}
        self.assertTrue(any(k.startswith("spool/") for k in new), new)
        self.assertFalse((self.wt.parent / f"{self.wt.name}_claude_iff").exists(),
                         "a record root was created beside the worktree")
        # (4) SessionStart: the worker block, nothing of main's, no decision JSON, exit 0
        out = results["session-start.sh"].stdout
        self.assertIn("WORKTREE SESSION", out)
        self.assertIn("todo-note", out)
        self.assertIn("worktree-todo-note", out)
        self.assertIn("TASK.md at the worktree root", out)
        self.assertIn("DONE.md", out)
        self.assertIn("BLOCKED.md", out)
        for mains in ("SESSION CONTINUITY", "main's own pointer", "RITUAL", "CONSOLE", "MODE:",
                      "statectl.py start", "permissionDecision", '"decision"'):
            self.assertNotIn(mains, out, mains)
        # (5) the Stop hook: one advisory line while the task is open, and never a block
        stop = json.loads(results["heartbeat.sh"].stdout.strip())
        self.assertIn("DONE.md", stop["systemMessage"])
        self.assertNotIn("decision", stop)

    def test_stop_hook_is_quiet_once_reported_and_in_main(self):
        (self.wt / "TASK.md").write_text("# todo-note\n", encoding="utf-8")
        (self.wt / "DONE.md").write_text("# DONE\nsummary: x\ntests: none\n", encoding="utf-8")
        self.assertEqual(self.run_in_wt("heartbeat.sh", {"hook_event_name": "Stop"}).stdout.strip(), "")
        (self.wt / "DONE.md").unlink()
        (self.wt / "TASK.md").unlink()
        self.assertEqual(self.run_in_wt("heartbeat.sh", {"hook_event_name": "Stop"}).stdout.strip(), "")
        (self.root / "TASK.md").write_text("# not a worker\n", encoding="utf-8")
        res = self.run_hook("heartbeat.sh", {"hook_event_name": "Stop"})
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "", "the advisory fired in the main checkout")

    def test_session_start_in_main_is_unchanged(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        _lib.clear_config_cache()
        res = self.run_hook("session-start.sh", {"hook_event_name": "SessionStart"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("WORKTREE SESSION", res.stdout)
        self.assertIn("MODE:", res.stdout)  # main's orientation, as before

    def test_statectl_run_from_inside_the_worktree_refuses_by_its_own_location(self):
        """A builder (or anyone) running `python3 .claude/tools/statectl.py` from the
        worktree's own copy, with no CLAUDE_PROJECT_DIR in the environment: the tool finds
        its root from its own location and refuses. This is what used to rebuild main's
        projections in a builder's worktree by mistake (handshake T4)."""
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("CLAUDE_IFF_RECORD_ROOT", None)
        res = subprocess.run([sys.executable, ".claude/tools/statectl.py", "pointer", "from a builder"],
                             cwd=str(self.wt), capture_output=True, text=True, timeout=60, env=env,
                             check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("refused:", res.stderr)
        self.assertIn("worktree", res.stderr)
        self.assertEqual(self.git("status", "--short", cwd=self.wt), "")

    def test_the_containers_are_ignored_by_name(self):
        for rel in (".claude/worktrees/x", *DERIVED_STATE, ".claude/state/heartbeat.json"):
            with self.subTest(rel=rel):
                res = subprocess.run(["git", "check-ignore", "-v", rel], cwd=str(self.root),
                                     capture_output=True, text=True, timeout=60, check=False)
                self.assertEqual(res.returncode, 0, f"{rel} is not ignored")
                self.assertIn(".gitignore", res.stdout)


# --------------------------------------------------------------------------- point 3: no git

MUTATING_GIT = ("push", "reset", "stash", "checkout", "worktree", "merge", "rebase", "commit",
                "add", "rm", "branch", "tag", "pull", "fetch", "am", "cherry-pick", "revert")
# The read-only spellings of two nouns that also mutate (`git tag -l`, `git branch --show-current`).
READ_ONLY_SECOND = {"tag": ("-l", "--list", "--points-at", "--contains", "--merged"),
                    "branch": ("--show-current", "--list", "-l", "-a", "-r", "--contains")}
MUTATION_RE = re.compile(r"\bgit\s+(push|reset|stash|checkout|worktree)\b")

# The one place git history moves: the lead's user-invoked commands and their helpers.
LEAD_GIT_FUNCTIONS = {"cmd_dispatch", "cmd_accept", "_accept_merge", "_restore_derived",
                      "_hook_exec_bits", "_dispatch_preflight"}


def _code_lines(text: str):
    """Lines that are code: full-line comments dropped (a hook's header, a docstring quoting
    the rule) so prose that NAMES a command is not read as running it."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        yield line


class TestHooksNeverMutateGit(FixtureCase):
    def test_no_hook_runs_a_mutating_git_command(self):
        for path in sorted(HOOKS.iterdir()):
            if not path.is_file() or path.suffix not in ("", ".sh", ".py"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".py":
                tree = ast.parse(text)
                for node in ast.walk(tree):  # drop docstrings: prose, not code
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) and \
                            ast.get_docstring(node):
                        node.body = node.body[1:]
                text = ast.unparse(tree)
            code = "\n".join(_code_lines(text))
            hits = [m.group(0) for m in MUTATION_RE.finditer(code)]
            self.assertEqual(hits, [], f"{path.name} names a mutating git command in code")

    def _git_calls(self, path: Path) -> dict:
        """{enclosing function: [subcommand]} for every list literal whose first element is a
        mutating git subcommand - the shape `_git(["worktree", "add", ...], cwd)` takes."""
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        found: dict = {}
        for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(func):
                if not (isinstance(node, ast.List) and node.elts
                        and isinstance(node.elts[0], ast.Constant)
                        and node.elts[0].value in MUTATING_GIT):
                    continue
                sub = node.elts[0].value
                second = node.elts[1].value if len(node.elts) > 1 and \
                    isinstance(node.elts[1], ast.Constant) else None
                if sub in READ_ONLY_SECOND and isinstance(second, str) and \
                        second.startswith(READ_ONLY_SECOND[sub]):
                    continue
                found.setdefault(func.name, []).append(sub)
        return found

    def test_git_history_moves_only_in_the_leads_commands(self):
        calls = self._git_calls(TOOLS_DIR / "statectl.py")
        self.assertTrue(calls, "the probe found no git call at all: the shape it looks for changed")
        self.assertLessEqual(set(calls), LEAD_GIT_FUNCTIONS, calls)
        for name in ("_lib.py", "checkctl.py", "obsctl.py", "mapctl.py", "consolectl.py", "distctl.py"):
            with self.subTest(tool=name):
                self.assertEqual(self._git_calls(TOOLS_DIR / name), {}, name)

    def test_git_push_lives_nowhere_in_the_tools_or_hooks(self):
        for path in sorted([*TOOLS_DIR.glob("*.py"), *HOOKS.iterdir()]):
            if not path.is_file():
                continue
            calls = self._git_calls(path) if path.suffix == ".py" else {}
            self.assertNotIn("push", [s for subs in calls.values() for s in subs], path.name)
            code = "\n".join(_code_lines(path.read_text(encoding="utf-8", errors="replace")))
            self.assertIsNone(re.search(r"\bgit\s+push\b", code), f"{path.name} spells `git push`")


# --------------------------------------------------------------------------- points 4 to 6

class TestContainersAndSections(FixtureCase):
    def test_the_managed_block_ignores_the_containers_and_the_projections(self):
        lines = distctl.render_gitignore_block("tracked").splitlines()
        for rule in (".claude/worktrees/", *DERIVED_STATE):
            with self.subTest(rule=rule):
                self.assertIn(rule, lines)
        self.assertNotIn(".claude/state/journal.jsonl", lines, "the journal stays tracked")
        for rule in DERIVED_STATE:
            self.assertTrue(checkctl._deliberately_ignored(rule), rule)
        self.assertFalse(checkctl._deliberately_ignored(".claude/state/journal.jsonl"))

    def test_the_home_guide_and_the_adopt_template_carry_both_role_sections(self):
        for guide in (CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md"):
            text = guide.read_text(encoding="utf-8")
            with self.subTest(guide=guide.name):
                for needle in ("TASK.md", "DONE.md", "BLOCKED.md", "never push", "Owns",
                               ".cview/", "/code-review main...worktree-", "cview verdict",
                               "--reasons"):
                    self.assertIn(needle, text, needle)


if __name__ == "__main__":
    import unittest

    unittest.main()
