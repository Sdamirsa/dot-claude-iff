#!/usr/bin/env python3
"""test_secrets.py - the secrets_placement check and the read-only `checkctl doctor`.

Every key-shaped string below is ASSEMBLED AT RUNTIME by concatenation. A literal one in this
file would trip outside scanners (GitHub push protection) and would teach the wrong habit.
The scanner skips this folder anyway; the fixtures it scans are written into temp projects.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, REPO_ROOT, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402

_MIXED = "aB3dE5gH7jK9mN2pQ4sT6vW8yZ1cF0"


def _body(n: int, alphabet: str = _MIXED) -> str:
    return (alphabet * (n // len(alphabet) + 1))[:n]


def fakes() -> dict:
    """pattern name -> (fake, the part that must never be printed). Built here, never stored."""
    return {
        "anthropic_key": ("sk-" + "ant-" + "api03-" + _body(48), _body(48)),
        "openai_key": ("sk-" + "proj-" + _body(44), _body(44)),
        "openrouter_key": ("sk-" + "or-" + "v1-" + _body(64, "0123456789abcdef"), _body(64, "0123456789abcdef")),
        "github_token": ("gh" + "p_" + _body(36), _body(36)),
        "github_pat": ("github" + "_pat_" + _body(40), _body(40)),
        "aws_access_key": ("AK" + "IA" + _body(16, "Q7W2E9R4T6Y1U3I8"), _body(16, "Q7W2E9R4T6Y1U3I8")),
        "slack_token": ("xo" + "xb-" + "2041" + "-" + _body(24), _body(24)),
        "google_api_key": ("AI" + "za" + _body(35), _body(35)),
        "private_key": ("-----" + "BEGIN RSA " + "PRIVATE" + " KEY-----", ""),
    }


def legacy_openai() -> str:
    return "sk-" + _body(48)


class SecretsCase(FixtureCase):
    def plant(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def assert_no_leak(self, text: str, secret: str, width: int = 8) -> None:
        for i in range(0, max(0, len(secret) - width + 1)):
            self.assertNotIn(secret[i:i + width], text,
                             "a slice of a secret value reached the output")

    def names_at(self, findings, rel):
        return {(f["line"], f["pattern"], f["severity"]) for f in findings if f["path"] == rel}

    def run_main(self, argv) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = checkctl.main(argv)
        return code, buf.getvalue()


class GitCase(SecretsCase):
    def setUp(self):
        super().setUp()
        if not shutil.which("git"):
            self.skipTest("git not available")
        res = self.git("init", "-q")
        if res.returncode != 0:
            self.skipTest(f"git init failed: {res.stderr}")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True,
                              text=True, check=False)


# --------------------------------------------------------------------------- patterns

class TestPatternFamilies(SecretsCase):
    """Walk mode (the fixture is not a git repo): every family, the pragma, the skips."""

    def test_every_vendor_family_fails_with_path_line_and_name(self):
        for name, (fake, _secret) in fakes().items():
            self.plant(f"src/{name}.txt", f"first line\nvalue = {fake}\n")
        self.plant("src/legacy.txt", "x\ny\nOPENAI = '" + legacy_openai() + "'\n")
        findings = checkctl.scan_secrets(self.root)
        for name in fakes():
            with self.subTest(pattern=name):
                self.assertIn((2, name, checkctl.FAIL), self.names_at(findings, f"src/{name}.txt"))
        self.assertIn((3, "openai_key", checkctl.FAIL), self.names_at(findings, "src/legacy.txt"))

    def test_sk_families_do_not_double_report(self):
        fake, _ = fakes()["anthropic_key"]
        self.plant("a.txt", fake + "\n")
        self.assertEqual(self.names_at(checkctl.scan_secrets(self.root), "a.txt"),
                         {(1, "anthropic_key", checkctl.FAIL)})

    def test_generic_assignment_warns(self):
        value = "q8Zr" + "Lm4tWp9x" + "Vb2nKc7d"
        self.plant("conf/app.py", f'import os\nDB_PASSWORD = "{value}"\n')
        self.plant("conf/app.yaml", f"service:\n  api_key: {value}\n")
        findings = checkctl.scan_secrets(self.root)
        self.assertIn((2, "generic_secret", checkctl.WARN), self.names_at(findings, "conf/app.py"))
        self.assertIn((2, "generic_secret", checkctl.WARN), self.names_at(findings, "conf/app.yaml"))

    def test_findings_never_carry_the_value(self):
        for name, (fake, _secret) in fakes().items():
            self.plant(f"src/{name}.txt", f"value = {fake}\n")
        dumped = json.dumps(checkctl.scan_secrets(self.root))
        result = checkctl.check_secrets_placement()
        text = dumped + result.message + "\n".join(result.details)
        for name, (_fake, secret) in fakes().items():
            with self.subTest(pattern=name):
                self.assert_no_leak(text, secret)
        self.assertEqual(result.status, checkctl.FAIL)

    def test_pragma_silences_one_line(self):
        fake, _ = fakes()["github_token"]
        self.plant("doc.md", f"see {fake}  <!-- iff:allow-secret -->\nand {fake}\n")
        self.assertEqual(self.names_at(checkctl.scan_secrets(self.root), "doc.md"),
                         {(2, "github_token", checkctl.FAIL)})

    def test_tests_folder_is_not_scanned(self):
        fake, _ = fakes()["openai_key"]
        self.plant(".claude/tools/tests/test_x.py", f"K = '{fake}'\n")
        self.assertEqual(checkctl.scan_secrets(self.root), [])

    def test_placeholders_and_glued_prefixes_pass(self):
        self.plant("README.md", "\n".join([
            "aws example: " + "AK" + "IA" + "IOSFODNN7" + "EXAMPLE",
            "risk-" + _body(30),
            "task-" + _body(30),
            'api_key = "<your key goes here 2024>"',
            'api_key = "your-api-key-goes-here-1234"',
            'token = "OPENROUTER_API_KEY_NAME_2"',
            'max_tokens = "' + _body(24) + '"',
            "export ANALYZE_API_KEY=sk-...",
            "key prefixes: sk-ant-, ghp_, github_pat_, AKIA, xoxb-, AIza",
        ]) + "\n")
        self.assertEqual(checkctl.scan_secrets(self.root), [])

    def test_allow_paths_knob_and_argument(self):
        fake, _ = fakes()["slack_token"]
        self.plant("vendor/fixtures/hooks.json", f'{{"url": "{fake}"}}\n')
        self.assertTrue(checkctl.scan_secrets(self.root))
        policy = _lib.load_config("policy")
        policy["secrets"] = {"allow_paths": ["vendor/"]}
        self.write_config("policy", policy)
        self.assertEqual(checkctl.scan_secrets(self.root), [])
        self.assertEqual(checkctl.scan_secrets(self.root, allow_paths=["vendor/**"]), [])
        self.assertTrue(checkctl.scan_secrets(self.root, allow_paths=[]))

    def test_never_enters_worktrees_git_or_the_record(self):
        fake, _ = fakes()["google_api_key"]
        self.plant(".claude/worktrees/agent-1/src/k.txt", fake + "\n")
        self.plant(".git/config", fake + "\n")  # not a real repo: the scan falls back to a walk
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.root / "inner_record")
        self.plant("inner_record/spool/s1.jsonl", json.dumps({"prompt": fake}) + "\n")
        self.plant("src/real.txt", fake + "\n")
        findings = checkctl.scan_secrets(self.root)
        self.assertEqual({f["path"] for f in findings}, {"src/real.txt"},
                         "the scan entered a pruned folder (or did not walk at all)")

    def test_files_argument_limits_the_scope(self):
        fake, _ = fakes()["openai_key"]
        self.plant("a.txt", fake + "\n")
        self.plant("b.txt", fake + "\n")
        findings = checkctl.scan_secrets(self.root, files=["b.txt"])
        self.assertEqual({f["path"] for f in findings}, {"b.txt"})

    def test_per_user_secret_files_are_never_read(self):
        fake, secret = fakes()["anthropic_key"]
        self.plant(".env", f"ANTHROPIC_API_KEY={fake}\n")
        self.plant(".claude/settings.local.json", json.dumps({"env": {"K": fake}}) + "\n")
        strict = checkctl.scan_secrets(self.root)
        self.assertEqual({(f["path"], f["pattern"]) for f in strict},
                         {(".env", "local_secret_file"),
                          (".claude/settings.local.json", "local_secret_file")},
                         "a per-user file in a folder meant for publication must FAIL by presence, "
                         "and its values must never be read")
        self.assertTrue(all(f["line"] == 0 and f["severity"] == checkctl.FAIL for f in strict))
        self.assertEqual(checkctl.scan_secrets(self.root, allow_local=True), [])

    def test_binary_files_are_skipped(self):
        fake, _ = fakes()["openai_key"]
        (self.root / "blob.bin").write_bytes(b"\x00\x01" + fake.encode())
        self.assertEqual(checkctl.scan_secrets(self.root), [])


# --------------------------------------------------------------------------- structured files

class TestMcpAndSettings(SecretsCase):
    def mcp(self, servers: dict) -> str:
        text = json.dumps({"mcpServers": servers}, indent=2) + "\n"
        self.plant(".mcp.json", text)
        return text

    def line_of(self, text: str, needle: str) -> int:
        return next(i for i, line in enumerate(text.split("\n"), 1) if needle in line)

    def test_expansions_pass(self):
        self.mcp({"gh": {
            "command": "npx", "args": ["-y", "server", "--api-key", "${GH_KEY}", "--token=${T}"],
            "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}", "API_KEY": "${API_KEY:-}"},
        }, "remote": {
            "type": "http", "url": "https://mcp.example.com/mcp?api_key=${REMOTE_KEY}",
            "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"},
        }})
        self.assertEqual(checkctl.scan_secrets(self.root), [])

    def test_literal_under_a_keylike_name_warns_with_its_line(self):
        literal = "plain" + "Literal" + "Value77"
        text = self.mcp({"svc": {
            "command": "svc",
            "args": ["--port", "8080", "--client-secret", literal + "a"],
            "env": {"SERVICE_API_KEY": literal + "b", "DEBUG": "true"},
            "headers": {"x-api-key": literal + "c"},
        }, "web": {"type": "http", "url": "https://mcp.example.com/m?token=" + literal + "d"}})
        findings = checkctl.scan_secrets(self.root)
        got = self.names_at(findings, ".mcp.json")
        for suffix in "abcd":
            with self.subTest(value=suffix):
                line = self.line_of(text, literal + suffix)
                self.assertIn((line, "mcp_literal", checkctl.WARN), got)
        self.assert_no_leak(json.dumps(findings), literal)

    def test_vendor_value_fails_once_per_line(self):
        fake, _ = fakes()["github_token"]
        text = self.mcp({"gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": fake}}})
        findings = [f for f in checkctl.scan_secrets(self.root) if f["path"] == ".mcp.json"]
        self.assertEqual(findings, [{"path": ".mcp.json", "line": self.line_of(text, "GITHUB_TOKEN"),
                                     "pattern": "github_token", "severity": checkctl.FAIL}])

    def test_counts_and_flags_are_not_credentials(self):
        self.mcp({"svc": {"command": "svc", "args": ["--max-tokens", "4096"],
                          "env": {"MAX_TOKENS": "4096", "TOKENIZER": "bert-base-uncased-v2",
                                  "AUTH_ENABLED": "true", "PATH": "/usr/local/bin:/usr/bin"}}})
        self.assertEqual(checkctl.scan_secrets(self.root), [])

    def test_committed_settings_env(self):
        fake, _ = fakes()["openai_key"]
        literal = "settings" + "Literal" + "Value42"
        text = json.dumps({"env": {"OPENAI_API_KEY": fake, "MY_SERVICE_TOKEN": literal,
                                   "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000"}}, indent=2) + "\n"
        self.plant(".claude/settings.json", text)
        got = self.names_at(checkctl.scan_secrets(self.root), ".claude/settings.json")
        self.assertEqual(got, {(self.line_of(text, "OPENAI_API_KEY"), "openai_key", checkctl.FAIL),
                               (self.line_of(text, "MY_SERVICE_TOKEN"), "settings_env_literal",
                                checkctl.WARN)})

    def test_settings_local_values_are_not_scanned(self):
        fake, _ = fakes()["openai_key"]
        self.plant(".claude/settings.local.json", json.dumps({"env": {"OPENAI_API_KEY": fake}}))
        self.assertEqual(checkctl.scan_secrets(self.root, allow_local=True), [])
        self.assertEqual([f["pattern"] for f in checkctl.scan_secrets(self.root)],
                         ["local_secret_file"])


# --------------------------------------------------------------------------- git scope

class TestGitScope(GitCase):
    def test_planted_key_in_a_tracked_file_fails_without_leaking(self):
        fake, secret = fakes()["openai_key"]
        self.plant("src/app.py", f"import os\n\nCLIENT = make_client('{fake}')\n")
        self.git("add", "src/app.py")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("FAIL src/app.py:3 openai_key", result.details)

        self.grant_ticket()  # the user typed /project-memory
        code, out = self.run_main(["run", "--phase", "check", "--new"])
        self.assertEqual(code, 1)
        self.assertIn("secrets_placement", out)
        self.assertIn("src/app.py:3 openai_key", out)
        run_record = (_lib.state_dir() / "memory-run.json").read_text(encoding="utf-8")
        self.assertIn("src/app.py:3 openai_key", run_record)
        for text in (out, run_record, result.message, "\n".join(result.details)):
            self.assert_no_leak(text, secret)

    def test_ignored_files_are_out_of_scope(self):
        fake, _ = fakes()["openai_key"]
        self.plant(".gitignore", "private/\n")
        self.plant("private/notes.txt", fake + "\n")
        self.assertEqual(checkctl.scan_secrets(self.root), [])

    def test_untracked_unignored_files_are_in_scope(self):
        """The next `git add -A` would carry it, so the ritual must see it before PUBLISH."""
        fake, _ = fakes()["aws_access_key"]
        self.plant("scratch.txt", fake + "\n")
        self.assertEqual(self.names_at(checkctl.scan_secrets(self.root), "scratch.txt"),
                         {(1, "aws_access_key", checkctl.FAIL)})

    def test_dotenv_must_be_ignored(self):
        self.plant(".env", "CLAUDE_IFF_RECORD_ROOT=/elsewhere\n")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any(d.startswith("FAIL .env:") and "NOT gitignored" in d for d in result.details),
                        result.details)
        self.plant(".gitignore", ".env\n")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.OK, result.details)
        self.assertIn("gitignored", result.message)

    def test_a_nested_unignored_dotenv_is_caught_too(self):
        self.plant("frontend/.env", "VITE_KEY=abc\n")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any(d.startswith("FAIL frontend/.env:") for d in result.details), result.details)
        self.plant(".gitignore", ".env\n")  # a bare name matches at every depth
        self.assertEqual(checkctl.check_secrets_placement().status, checkctl.OK)

    def test_settings_local_must_be_ignored(self):
        self.plant(".claude/settings.local.json", '{"env": {}}\n')
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any(".claude/settings.local.json" in d and "NOT gitignored" in d
                            for d in result.details), result.details)
        self.plant(".gitignore", "settings.local.json\n")
        self.assertEqual(checkctl.check_secrets_placement().status, checkctl.OK)

    def test_tracked_dotenv_fails_even_under_an_ignore_rule(self):
        self.plant(".gitignore", ".env\n")
        self.plant(".env", "CLAUDE_IFF_RECORD_ROOT=/elsewhere\n")
        self.git("add", "-f", ".env")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("FAIL .env local_secret_file_tracked", result.details)

    def test_allow_paths_exempts_a_deliberately_committed_dotenv(self):
        self.plant(".env", "VITE_TITLE=demo\n")
        policy = _lib.load_config("policy")
        policy["secrets"] = {"allow_paths": [".env"]}
        self.write_config("policy", policy)
        self.assertEqual(checkctl.check_secrets_placement().status, checkctl.OK)


class TestOutsideGit(SecretsCase):
    def test_ignore_rows_skip_rather_than_fail(self):
        self.plant(".env", "CLAUDE_IFF_RECORD_ROOT=/elsewhere\n")
        result = checkctl.check_secrets_placement()
        self.assertEqual(result.status, checkctl.OK, result.details)
        self.assertTrue(any(d.startswith("SKIP .env:") for d in result.details), result.details)


# --------------------------------------------------------------------------- registration

class TestRegistration(SecretsCase):
    def test_check_is_registered_and_bound(self):
        self.assertIn("secrets_placement", checkctl.phase_steps("check"))
        self.assertIs(checkctl.CHECKS["secrets_placement"], checkctl.check_secrets_placement)

    def test_allow_paths_knob_has_a_registry_card(self):
        leaves = {f"policy.{dotted}" for dotted, _ in checkctl._walk_leaves(_lib.load_config("policy"))}
        self.assertIn("policy.secrets.allow_paths", leaves, "the knob is not in the shipped policy.json")
        keys = {e["key"] for e in _lib.load_config("registry")["entries"]}
        self.assertIn("policy.secrets.allow_paths", keys)
        # And the lint agrees, with the agent files present so no dead-card error truncates it.
        shutil.copytree(CLAUDE_DIR / "agents", self.root / ".claude" / "agents")
        result = checkctl.check_config_registry()
        self.assertNotEqual(result.status, checkctl.FAIL, result.details)
        self.assertFalse([d for d in result.details if "policy.secrets" in d], result.details)

    def test_reference_card_exists(self):
        card = json.loads((CLAUDE_DIR / "system-map" / "cards" / "reference.secrets.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(card["path"], ".claude/reference/secrets.md")

    def test_probe_is_green_on_the_shipped_tree(self):
        skip = shutil.ignore_patterns("worktrees", "state", "dist", "__pycache__", "private",
                                      "settings.local.json")
        shutil.rmtree(self.root / ".claude")
        shutil.copytree(CLAUDE_DIR, self.root / ".claude", ignore=skip)
        shutil.copy(REPO_ROOT / ".claude-iff" / "README.md", self.root / ".claude-iff" / "README.md")
        results = checkctl.probe()
        self.assertIn("reference.secrets", {r.name for r in results})
        self.assertEqual([r.name for r in results if r.status != checkctl.OK], [])


# --------------------------------------------------------------------------- doctor

ROW_RE = re.compile(r"^\[(OK  |WARN|FAIL|SKIP)\] (\S+)\s+(.*)$")
DOCTOR_NAMES = {"python", "python3", "bash", "git", "config", "hooks", "record_root",
                "record_cloud_sync", "console", "heartbeat", "secrets_placement", "mode",
                "phase", "ritual_ticket"}


class TestDoctor(SecretsCase):
    def table(self, out: str) -> dict:
        rows = {}
        for line in out.splitlines():
            m = ROW_RE.match(line)
            if m:
                rows[m.group(2)] = (m.group(1).strip(), m.group(3))
        return rows

    def snapshot(self) -> dict:
        snap = {}
        for path in sorted(self.root.parent.rglob("*")):
            if path.is_file():
                snap[path.relative_to(self.root.parent).as_posix()] = \
                    hashlib.sha256(path.read_bytes()).hexdigest()
        return snap

    def test_one_row_per_item(self):
        code, out = self.run_main(["doctor"])
        rows = self.table(out)
        self.assertEqual(set(rows), DOCTOR_NAMES, out)
        self.assertEqual(code, 1 if any(s == "FAIL" for s, _ in rows.values()) else 0)
        self.assertEqual(rows["python"][0], "OK")
        self.assertRegex(out, r"CHECK_(OK|WARN|FAIL)")

    def test_planted_key_fails_and_is_never_printed(self):
        fake, secret = fakes()["slack_token"]
        self.plant("deploy/notify.sh", f"curl -H 'Authorization: Bearer {fake}' https://x\n")
        code, out = self.run_main(["doctor"])
        rows = self.table(out)
        self.assertEqual(code, 1)
        self.assertEqual(rows["secrets_placement"][0], "FAIL")
        self.assertIn("deploy/notify.sh:1 slack_token", out)
        self.assertIn("fix:", out)
        self.assertIn("CHECK_FAIL", out)
        self.assert_no_leak(out, secret)

    def test_doctor_writes_nothing(self):
        self.plant("src/app.py", "print('hi')\n")
        before = self.snapshot()
        self.run_main(["doctor"])
        self.run_main(["doctor", "--json"])
        self.assertEqual(self.snapshot(), before, "doctor wrote or changed a file")
        self.assertFalse(self.record.exists(), "doctor created the record folder")

    def test_absent_features_skip_then_read_when_present(self):
        rows = {r.name: r.status for r, _fix in checkctl.doctor()}
        for name in ("mode", "phase"):
            self.assertEqual(rows[name], checkctl.SKIP, name)
        # The ticket row is no longer a SKIP: the fixture wires no prompt hook, so no ticket
        # can ever be minted and every ritual would refuse (test_ritual covers the row fully).
        self.assertEqual(rows["ritual_ticket"], checkctl.WARN)
        _lib.atomic_write_json(_lib.state_dir() / "session.json",
                               {"session": {"mode": "guided", "phase": "build"}})
        self.grant_ticket()
        rows = {r.name: (r.status, r.message) for r, _fix in checkctl.doctor()}
        self.assertEqual(rows["mode"], (checkctl.OK, "mode: guided"))
        self.assertEqual(rows["phase"], (checkctl.OK, "phase: build"))
        self.assertEqual(rows["ritual_ticket"][0], checkctl.OK)

    def test_json_rows_carry_a_fix(self):
        code, out = self.run_main(["doctor", "--json"])
        payload = json.loads(out[:out.rindex("]") + 1])
        self.assertEqual({row["name"] for row in payload}, DOCTOR_NAMES)
        for row in payload:
            with self.subTest(row=row["name"]):
                self.assertIn(row["status"], ("OK", "WARN", "FAIL", "SKIP"))
                self.assertIn("fix", row)
                if row["status"] in ("WARN", "FAIL"):
                    self.assertTrue(row["fix"], f"{row['name']} has no fix hint")

    def test_hooks_row(self):
        rows = {r.name: r.status for r, _fix in checkctl.doctor()}
        self.assertEqual(rows["hooks"], checkctl.FAIL, "no settings.json means no hook is wired")
        shutil.copy(CLAUDE_DIR / "settings.json", self.root / ".claude" / "settings.json")
        shutil.copytree(CLAUDE_DIR / "hooks", self.root / ".claude" / "hooks",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.assertEqual(checkctl._doctor_hooks()[0].status, checkctl.OK)
        (self.root / ".claude" / "hooks" / "heartbeat.sh").unlink()
        result = checkctl._doctor_hooks()[0]
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("heartbeat.sh", result.message)

    def test_record_root_inside_the_repo_fails(self):
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.root / "record_inside")
        result, fix = checkctl._doctor_record_root()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("INSIDE", result.message)
        self.assertTrue(fix)
        os.environ["CLAUDE_IFF_RECORD_ROOT"] = str(self.record)
        self.assertEqual(checkctl._doctor_record_root()[0].status, checkctl.OK)

    def test_cloud_sync_markers(self):
        self.assertIsNotNone(checkctl.cloud_sync_reason(Path("/home/u/Dropbox/p_claude_iff")))
        self.assertIsNotNone(checkctl.cloud_sync_reason(Path("C:/Users/u/OneDrive - Org/p_claude_iff")))
        self.assertIsNotNone(checkctl.cloud_sync_reason(
            Path("/Users/u/Library/Mobile Documents/com~apple~CloudDocs/p_claude_iff")))
        self.assertIsNone(checkctl.cloud_sync_reason(self.record))

    def test_console_must_bind_loopback(self):
        cfg = _lib.load_config("console")
        cfg["host"] = "0.0.0.0"
        self.write_config("console", cfg)
        self.assertEqual(checkctl._doctor_console()[0].status, checkctl.FAIL)


# --------------------------------------------------------------------------- docs

class TestDocs(SecretsCase):
    def test_reference_names_every_home(self):
        text = (CLAUDE_DIR / "reference" / "secrets.md").read_text(encoding="utf-8")
        for needle in ("settings.local.json", "claude mcp add --env", "${VAR}", "${VAR:-default}",
                       "does not load `.env`", "iff:allow-secret", "secrets.allow_paths", "rotate"):
            with self.subTest(needle=needle):
                self.assertIn(needle.lower(), text.lower())

    def test_understand_page_no_longer_keeps_keys_in_dotenv(self):
        text = (REPO_ROOT / "docs" / "understand.html").read_text(encoding="utf-8")
        self.assertNotIn("API keys live in your environment or a gitignored <code>.env</code>", text)
        self.assertNotIn("gitignored; machine paths, keys", text)
        self.assertIn("settings.local.json", text)

    def test_key_hints_name_settings_local_first(self):
        import obsctl
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            obsctl.main(["analyze"])
        out = buf.getvalue()
        self.assertIn("settings.local.json", out)
        self.assertLess(out.index("settings.local.json"), out.index("export ANALYZE_API_KEY"))
        self.assertNotIn("shell profile", out)
        console_py = (CLAUDE_DIR / "console" / "console.py").read_text(encoding="utf-8")
        template = (CLAUDE_DIR / "console" / "console.template.html").read_text(encoding="utf-8")
        for name, text in (("console.py", console_py), ("console.template.html", template)):
            with self.subTest(source=name):
                self.assertIn("settings.local.json", text)
                self.assertNotIn("ANALYZE_API_KEY in your shell. See", text)
        self.assertNotIn('copyBox("export ANALYZE_API_KEY', template)

    def test_the_system_trees_are_clean(self):
        """The repo runs this check on itself: its own sources, docs and fixtures must pass."""
        if not (REPO_ROOT / ".git").exists() or not shutil.which("git"):
            self.skipTest("not a git checkout")
        own = (".claude/", ".claude-iff/", "docs/")
        failing = [f for f in checkctl.scan_secrets(REPO_ROOT, allow_local=True)
                   if f["severity"] == checkctl.FAIL and f["path"].startswith(own)]
        self.assertEqual(failing, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
