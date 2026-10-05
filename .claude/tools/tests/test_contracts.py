#!/usr/bin/env python3
"""test_contracts.py - the seams between tools.

Every tool here has its own passing unit tests. That is not enough: the failure this file
exists to prevent already happened during this system's build, when obsctl wrote a rollup with
`tokens`/`cost.known` and consolectl read `totals`/`cost_known`. Both sides were internally
correct, both test suites were green, and every token figure on the console silently read zero.
A number that is quietly wrong is worse than a crash, because nothing asks you to look.

So these tests run producers and consumers against EACH OTHER and assert the numbers survive
the trip, plus they check that the data-driven registries (memory.json's phase lists) only name
steps that actually exist.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import CLAUDE_DIR, FixtureCase  # noqa: E402

import _lib  # noqa: E402
import checkctl  # noqa: E402
import consolectl  # noqa: E402
import obsctl  # noqa: E402


class TestRollupToConsole(FixtureCase):
    """The producer/consumer round trip for tokens: obsctl writes, consolectl must read."""

    def _make_rollup(self, output_tokens: int = 4321) -> dict:
        date = _lib.today()
        self.spool_event(
            session="s1",
            hook_event_name="llm.usage",
            _obs_source="transcript",
            **{
                "gen_ai.request.model": "test-model",
                "gen_ai.usage.input_tokens": 11,
                "gen_ai.usage.output_tokens": output_tokens,
                "gen_ai.usage.cache_read_input_tokens": 7,
                "gen_ai.usage.cache_creation_input_tokens": 5,
            },
        )
        self.assertEqual(obsctl.main(["seal", "--date", date]), 0)
        self.assertEqual(obsctl.main(["rollup", "--date", date]), 0)
        path = _lib.iff_dir() / "obs" / "rollups" / f"{date}.json"
        self.assertTrue(path.exists(), "rollup was not written")
        return json.loads(path.read_text())

    def test_rollup_satisfies_its_own_contract(self):
        rollup = self._make_rollup()
        for dotted in obsctl.ROLLUP_CONTRACT:
            with self.subTest(path=dotted):
                self.assertTrue(
                    obsctl._contract_path_ok(rollup, dotted.split(".")),
                    f"rollup is missing {dotted}; ROLLUP_CONTRACT and cmd_rollup have drifted",
                )

    def test_console_surfaces_the_rollup_numbers(self):
        """The regression test for the real bug: the console must show what obsctl counted."""
        rollup = self._make_rollup(output_tokens=4321)
        payload = consolectl.payload()
        self.assertEqual(
            payload["tokens"]["total"]["output"], rollup["tokens"]["output"],
            "console total disagrees with the rollup it reads: the two sides have drifted",
        )
        self.assertEqual(payload["tokens"]["total"]["output"], 4321)
        self.assertEqual(payload["tokens"]["today"]["output"], 4321)
        self.assertEqual(payload["tokens"]["total"]["cache_read"], rollup["tokens"]["cache_read"])
        self.assertEqual(payload["tokens"]["as_of"], rollup["generated_at"])

    def test_unpriced_models_reach_the_console_as_unknown(self):
        self._make_rollup()
        payload = consolectl.payload()
        self.assertFalse(payload["tokens"]["cost_known"])
        self.assertIsNone(payload["tokens"]["cost_usd"])
        self.assertIn("test-model", payload["tokens"]["unknown_models"])

    def test_priced_model_produces_a_known_cost(self):
        self.write_config("model-prices", {
            "per_million_tokens": {
                "test-model": {"input": 1.0, "output": 2.0, "cache_read": 0.5, "cache_creation": 1.5}
            }
        })
        self._make_rollup(output_tokens=1_000_000)
        payload = consolectl.payload()
        self.assertTrue(payload["tokens"]["cost_known"], "a fully priced model must yield a known cost")
        self.assertGreater(payload["tokens"]["cost_usd"], 1.9)


class TestStoryToRenderer(FixtureCase):
    """The story feed's producer and the console template must agree on key names."""

    def test_story_feed_satisfies_its_contract(self):
        self.spool_event(session="s1", hook_event_name="Stop")
        obsctl.main(["seal", "--date", _lib.today()])
        obsctl.main(["rollup", "--date", _lib.today()])
        self.assertEqual(obsctl.main(["story"]), 0)
        feed = json.loads((_lib.state_dir() / "story-feed.json").read_text())
        self.assertTrue(
            obsctl.validate_story_contract(feed),
            "story-feed.json does not satisfy STORY_CONTRACT",
        )

    def test_renderer_references_every_contract_leaf(self):
        """The crawler this system learned from shipped a producer emitting `points` to a
        renderer reading `scenes`. This asserts our two sides name the same things."""
        import re as _re
        template = (CLAUDE_DIR / "console" / "console.template.html").read_text(encoding="utf-8")
        missing = []
        for dotted in obsctl.STORY_CONTRACT:
            leaf = dotted.replace("[]", "").split(".")[-1]
            # Look for a real property access, not a bare substring: `ref` used to "pass"
            # because it occurs inside `preferred-color-scheme` and `preserveAspectRatio`, so
            # the one test asserting producer/renderer agreement was asserting a coincidence.
            pattern = _re.compile(
                rf"(?:\.{_re.escape(leaf)}\b)|(?:\[\s*[\"']{_re.escape(leaf)}[\"']\s*\])"
                rf"|(?:[\"']{_re.escape(leaf)}[\"']\s*:)|(?:\b{_re.escape(leaf)}\s*:)"
            )
            if not pattern.search(template):
                missing.append(dotted)
        self.assertEqual(
            missing, [],
            f"the console template never mentions these story keys: {missing}. Either the "
            f"renderer is ignoring data the producer emits, or the two have drifted.",
        )

    def test_story_token_clock_never_goes_backwards(self):
        self.spool_event(session="s1", hook_event_name="Stop")
        obsctl.main(["seal", "--date", _lib.today()])
        obsctl.main(["rollup", "--date", _lib.today()])
        obsctl.main(["story"])
        feed = json.loads((_lib.state_dir() / "story-feed.json").read_text())
        clock = [p["t_tokens_cum"] for p in feed["points"]]
        self.assertEqual(clock, sorted(clock), "the cumulative token clock must be monotonic")


class TestRitualRegistry(FixtureCase):
    """memory.json is data that names code. A name with no binding fails at ritual time, which
    is the worst moment to discover it, so assert the bindings here instead."""

    def test_every_check_name_is_bound(self):
        for name in checkctl.phase_steps("check"):
            with self.subTest(step=name):
                self.assertIn(name, checkctl.CHECKS,
                              f"memory.json phases.check names '{name}', which checkctl.CHECKS lacks")

    def test_every_polish_name_is_a_registered_generator(self):
        for name in checkctl.phase_steps("polish"):
            with self.subTest(step=name):
                self.assertIn(name, checkctl.GENERATORS,
                              f"memory.json phases.polish names '{name}', which is not a generator")

    def test_every_publish_name_is_bound(self):
        for name in checkctl.phase_steps("publish"):
            with self.subTest(step=name):
                self.assertIn(name, checkctl.PUBLISH_STEPS)

    def test_every_generator_names_a_real_tool_and_output(self):
        for name, spec in checkctl.GENERATORS.items():
            with self.subTest(generator=name):
                self.assertTrue((CLAUDE_DIR / "tools" / spec["tool"]).exists(),
                                f"generator {name} names a tool that does not exist: {spec['tool']}")
                self.assertTrue(spec["inputs"], f"generator {name} declares no inputs to hash")
                self.assertTrue(spec["output"], f"generator {name} declares no output")

    def test_law_one_every_generator_is_registered_in_a_phase(self):
        """Law 1: a generator that no phase runs will rot. If you build one, register it."""
        registered = set(checkctl.phase_steps("polish"))
        orphans = [name for name in checkctl.GENERATORS if name not in registered]
        self.assertEqual(orphans, [],
                         f"these generators are not run by any ritual phase and will rot: {orphans}")

    def test_home_check_phase_runs_the_suite(self):
        """The home repo's CHECK runs the whole suite as a project step. The fixture carries
        the shipped memory.json, so a fixture CHECK lists it (here it fails fast: the fixture
        has no tests to run, which is what keeps this from recursing)."""
        if not checkctl.distribution_enabled():
            self.skipTest("home-repo-only: kits ship project_steps empty")
        steps = {s.get("name"): s for s in checkctl.project_steps("check")}
        self.assertIn("test_suite", steps)
        self.assertEqual(steps["test_suite"]["argv"][1:],
                         [".claude/tools/tests/run_tests.py", "-q"])
        results = checkctl.run_check(checkctl.start_run())
        self.assertIn("test_suite", [r.name for r in results])


class TestPublishTransaction(FixtureCase):
    """PUBLISH must refuse to run on a ritual whose POLISH did not complete."""

    def test_publish_refuses_without_check(self):
        run = checkctl.start_run()
        results = checkctl.run_publish(run)
        self.assertEqual(results[0].name, "polish_complete")
        self.assertEqual(results[0].status, checkctl.FAIL)
        self.assertIn("CHECK", results[0].message)

    def test_publish_refuses_without_polish(self):
        run = checkctl.start_run()
        checkctl.record_phase(run, "check", [], checkctl.OK)
        results = checkctl.run_publish(run)
        self.assertEqual(results[0].status, checkctl.FAIL)
        self.assertIn("POLISH", results[0].message)

    def test_publish_refuses_over_a_failed_check(self):
        """Continuing the ritual must not launder a failed CHECK into a publishable state."""
        run = checkctl.start_run()
        checkctl.record_phase(
            run, "check", [checkctl.Result("journal_parses", checkctl.FAIL, "broken")], checkctl.FAIL
        )
        checkctl.record_phase(run, "polish", [], checkctl.OK)
        ok, why = checkctl.polish_complete(run)
        self.assertFalse(ok)
        self.assertIn("journal_parses", why)

    def test_later_phases_continue_the_run_check_opened(self):
        """CHECK opens a ritual; POLISH and PUBLISH must continue it.

        If every phase minted its own run id, PUBLISH's same-run-id precondition could never be
        satisfied in normal use, and the first thing anyone would learn is how to bypass it.
        """
        # The exit code is beside the point here (a fixture project has configs but no agent
        # files, so its CHECK legitimately fails); what matters is which run id the next phase
        # writes into. The ticket stands in for the user typing /project-memory.
        self.grant_ticket()
        checkctl.main(["run", "--phase", "check", "--new"])
        opened = checkctl.load_run()["run_id"]
        checkctl.main(["run", "--phase", "polish"])
        self.assertEqual(checkctl.load_run()["run_id"], opened,
                         "POLISH started a new run instead of continuing the one CHECK opened")

    def test_new_forces_a_fresh_run(self):
        self.grant_ticket()
        checkctl.main(["run", "--phase", "check", "--new"])
        first = checkctl.load_run()["run_id"]
        checkctl.main(["run", "--phase", "check", "--new"])
        self.assertNotEqual(checkctl.load_run()["run_id"], first)

    def test_publish_refuses_when_a_generator_ran_in_a_different_run(self):
        """A generator stamped by an EARLIER ritual does not count for this one: that is the
        difference between 'the file exists' and 'this run rebuilt it'."""
        # The fixture ships configs but not tools, and polish_complete() skips generators whose
        # tool is absent. Stage a stand-in so this test actually exercises the precondition
        # instead of quietly skipping itself, which is what it did before.
        target = next(name for name in checkctl.phase_steps("polish") if name in checkctl.GENERATORS)
        stub = self.root / ".claude" / "tools" / checkctl.GENERATORS[target]["tool"]
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("# stand-in so tool_path().exists() is true\n", encoding="utf-8")

        old = checkctl.start_run()
        checkctl.stamp_generator(target, checkctl.GENERATORS[target], old["run_id"])

        new = checkctl.start_run()
        new["run_id"] = old["run_id"] + "-later"
        # Satisfy the earlier preconditions so this test isolates the one it is about: a
        # generator whose stamp belongs to a PREVIOUS ritual must not count for this one.
        checkctl.record_phase(new, "check", [], checkctl.OK)
        checkctl.record_phase(new, "polish", [], checkctl.OK)
        ok, why = checkctl.polish_complete(new)
        self.assertFalse(ok, "publish must not accept a generator stamped by a previous run")
        self.assertIn(target, why)


class TestJournalVocabulary(FixtureCase):
    """One vocabulary, shared by writers and the projector."""

    def test_unknown_action_is_refused_at_the_source(self):
        with self.assertRaises(_lib.LibError):
            _lib.journal_append("not_a_real_action", text="x")

    def test_statectl_projects_every_known_action(self):
        import statectl
        source = (CLAUDE_DIR / "tools" / "statectl.py").read_text(encoding="utf-8")
        for action in _lib.JOURNAL_ACTIONS:
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', source,
                              f"statectl never mentions the '{action}' action: a writer and the "
                              f"projector have drifted, which is how events go invisible")
        self.assertTrue(hasattr(statectl, "main"))


class TestRecordIntegrity(FixtureCase):
    """The record is the tier everything else trusts. These are the ways it silently lied."""

    def _seal(self):
        return obsctl.main(["seal", "--date", _lib.today()])

    def test_concurrent_events_in_one_second_all_survive(self):
        """Five sub-agents finishing in the same second is routine. Identity used to be
        (second-precision ts, session, event, tool, message_id), and hook events carry neither
        tool_name nor message_id, so all five collapsed into one sealed row and four were lost
        from the tier documented as kept forever."""
        for i in range(5):
            self.spool_event(session="s1", hook_event_name="SubagentStop",
                             subagent_type=f"w{i}", _obs_uid=f"1000000000{i}-42")
        self.assertEqual(self._seal(), 0)
        segment = _lib.read_jsonl(self.record / "segments" / f"{_lib.today()}.jsonl")
        self.assertEqual(len(segment), 5, "distinct events were collapsed by identity")

    def test_a_structured_field_does_not_wedge_the_pipeline(self):
        """A list in an identity field raised TypeError, seal died, the spool was never drained,
        and every later seal hit the same line: one event wedged PUBLISH permanently."""
        self.spool_event(session="s1", hook_event_name="Stop", message_id=["a", "b"],
                         tool_name={"nested": "dict"})
        self.assertEqual(self._seal(), 0, "seal must survive a structured identity field")
        self.assertEqual(self._seal(), 0, "and must stay survivable on the next run")

    def test_nested_values_under_allowlisted_names_do_not_reach_the_segment(self):
        """The allowlist filters key NAMES. An allowlisted name holding a nested structure used
        to pass through verbatim, so a secret nested under `reason` reached the redacted tier."""
        secret = "AKIA-LEAKED-VERBATIM"
        self.spool_event(
            session="s1", hook_event_name="Stop",
            reason={"secret": secret},
            model={"deep": {"prompt": secret}},
            permission_mode=[secret],
        )
        self.assertEqual(self._seal(), 0)
        segment_text = (self.record / "segments" / f"{_lib.today()}.jsonl").read_text()
        self.assertNotIn(secret, segment_text, "a nested secret reached the redacted tier")
        for name in ("reason", "model", "permission_mode"):
            self.assertIn(name, segment_text, "the allowlisted name should survive as a marker")
        raw = list((self.record / "sealed-raw").glob("*"))
        self.assertTrue(raw, "raw must still hold the verbatim original")

    def test_top_level_non_allowlisted_keys_still_dropped(self):
        secret = "TOOL-RESPONSE-SECRET"
        self.spool_event(session="s1", hook_event_name="Stop", tool_response=secret, prompt=secret)
        self.assertEqual(self._seal(), 0)
        text = (self.record / "segments" / f"{_lib.today()}.jsonl").read_text()
        self.assertNotIn(secret, text)
        self.assertNotIn("tool_response", text)


class TestToleranceIsReal(FixtureCase):
    """Docstrings claimed tolerance the code did not deliver."""

    def test_a_non_utf8_byte_does_not_kill_continuity(self):
        self.journal("pointer", text="keep going")
        with open(_lib.journal_path(), "ab") as fh:
            fh.write(b'{"ts":"2026-01-01T00:00:00Z","action":"note","text":"\xe9 bad"}\n')
        events = _lib.journal_read()
        self.assertTrue(events, "one bad byte must not empty the journal")
        self.assertTrue(any(e.get("action") == "pointer" for e in events))

    def test_need_open_survives_a_bad_byte(self):
        """One byte used to take out continuity AND the human-escalation path at once."""
        import statectl
        with open(_lib.journal_path(), "ab") as fh:
            fh.write(b"\xe9\n")
        self.assertEqual(statectl.main(["need", "open", "--title", "x", "--category", "decide", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question"]), 0)


class TestNeedsHumanIdsAreCollisionFree(FixtureCase):
    def test_concurrent_opens_do_not_lose_a_question(self):
        """Read-max-then-write handed out duplicate ids under concurrency and the projector
        overwrote one with the other, losing a question with no error."""
        import statectl
        for i in range(12):
            statectl.main(["need", "open", "--title", f"question {i}", "--category", "decide", "--context", "test context long enough to satisfy the sixty character floor for humans", "--action", "answer the question"])
        board = _lib.read_json(_lib.state_dir() / "needs-human.json", {})
        self.assertEqual(board["counts"]["open"], 12, "a question was lost to an id collision")
        ids = [t["id"] for t in board["tasks"]]
        self.assertEqual(len(set(ids)), 12, "duplicate ids handed out")


class TestPortability(FixtureCase):
    """Nothing committed may name this machine: paths come from the repo folder at runtime,
    and machine-specific overrides travel through env or the gitignored .env, never a
    committed file."""

    def test_home_path_in_a_committed_tree_fails_check(self):
        from pathlib import Path as P
        (self.root / ".claude" / "research").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "research" / "leak.md").write_text(
            f"see {P.home()}/somewhere\n", encoding="utf-8")
        result = checkctl.check_no_machine_paths()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any("leak.md" in d for d in result.details))

    def test_clean_tree_passes(self):
        self.assertEqual(checkctl.check_no_machine_paths().status, checkctl.OK)

    def test_tilde_display_form(self):
        from pathlib import Path as P
        self.assertTrue(_lib.tilde(P.home() / "x" / "y").startswith("~/"))
        self.assertEqual(_lib.tilde("/opt/thing"), "/opt/thing")

    def test_record_root_readable_from_dotenv(self):
        import os
        override = self.root.parent / "elsewhere_record"
        (self.root / ".env").write_text(
            f"# machine-local, gitignored\nCLAUDE_IFF_RECORD_ROOT={override}\n", encoding="utf-8")
        saved = os.environ.pop("CLAUDE_IFF_RECORD_ROOT", None)
        try:
            _lib.clear_config_cache()
            self.assertEqual(_lib.record_root(), override.resolve())
        finally:
            if saved is not None:
                os.environ["CLAUDE_IFF_RECORD_ROOT"] = saved

    def test_env_var_beats_dotenv(self):
        (self.root / ".env").write_text("CLAUDE_IFF_RECORD_ROOT=/tmp/should-lose\n", encoding="utf-8")
        self.assertEqual(_lib.record_root(), self.record.resolve(),
                         "the fixture's env var must outrank .env")


class TestThemeTokenParity(FixtureCase):
    """Evolution P1 (from the first real retro): the console template defines its dark palette
    in two blocks with different indentation, and a token landing in only one shipped a page
    that rendered wrong in exactly one theme state - twice in one day (L-7). Parity is now a
    ritual gate, not an eyeball check."""

    def _write_template(self, media_tokens, explicit_tokens):
        css = ":root { --bg: #fff; }\n"
        css += "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {\n"
        css += "".join(f"    {t}: #111;\n" for t in media_tokens) + "  }\n}\n"
        css += ':root[data-theme="dark"] {\n'
        css += "".join(f"  {t}: #111;\n" for t in explicit_tokens) + "}\n"
        path = self.root / ".claude" / "console" / "console.template.html"
        path.write_text(f"<style>{css}</style><script>const DATA = __CONSOLE_DATA__;</script>")

    def test_matching_blocks_pass(self):
        self._write_template(["--bg", "--ink", "--edge-write"], ["--bg", "--ink", "--edge-write"])
        self.assertEqual(checkctl.check_theme_token_parity().status, checkctl.OK)

    def test_a_token_in_one_block_only_fails(self):
        self._write_template(["--bg", "--ink", "--edge-write"], ["--bg", "--ink"])
        result = checkctl.check_theme_token_parity()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertTrue(any("--edge-write" in d for d in result.details))

    def test_the_real_template_passes_right_now(self):
        import shutil
        real = CLAUDE_DIR / "console" / "console.template.html"
        shutil.copy(real, self.root / ".claude" / "console" / "console.template.html")
        self.assertEqual(checkctl.check_theme_token_parity().status, checkctl.OK,
                         "the shipped template must satisfy its own parity gate")


class TestTaskReality(FixtureCase):
    """check_task_reality parses human markdown. The bold-label form from its own docstring
    ("- **State files:** `a.py`") used to leave a backtick glued to the first path, so the
    checker reported a file that exists as missing - a claim-checker emitting a false claim."""

    def _task(self, line: str) -> None:
        (self.root / ".claude" / "tasks" / "20260101-t.md").write_text(
            f"# Task: t\n\nStatus: active\n\n{line}\n", encoding="utf-8")

    def test_bold_label_with_backticked_paths_matches_disk(self):
        (self.root / "a.py").write_text("x\n", encoding="utf-8")
        (self.root / "b.py").write_text("x\n", encoding="utf-8")
        self._task("- **State files:** `a.py`, `b.py`")
        result = checkctl.check_task_reality()
        self.assertEqual(result.status, checkctl.OK, result.details)

    def test_plain_label_still_works(self):
        (self.root / "a.py").write_text("x\n", encoding="utf-8")
        self._task("- State files: a.py")
        self.assertEqual(checkctl.check_task_reality().status, checkctl.OK)

    def test_missing_file_is_reported_with_a_clean_name(self):
        self._task("- **State files:** `gone.py`")
        result = checkctl.check_task_reality()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertTrue(any("names gone.py," in d for d in result.details),
                        f"the reported name must carry no markdown residue: {result.details}")


class TestGitignoreShadowing(FixtureCase):
    """A generic dist/ or *.zip ignore matches at any depth and silently untracks shipped
    .claude/ paths (it hid the adoption kits in the field). The check asks git itself."""

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

    def test_generic_dist_pattern_is_caught(self):
        # The dist zips are probed by name only where distribution is on (the home repo).
        cfg = _lib.load_config("memory")
        cfg["distribution"] = {"enabled": True}
        self.write_config("memory", cfg)
        (self.root / ".gitignore").write_text("dist/\n", encoding="utf-8")
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertTrue(any("dist" in d for d in result.details), result.details)

    def test_clean_ignores_pass(self):
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.OK)

    def test_a_file_level_pattern_is_caught(self):
        """Paths reached git through text-mode stdin, which on Windows ends each line in CRLF:
        git kept the CR as part of the path, so `*.md` never matched `notes.md<CR>` and only
        directory patterns ever fired."""
        (self.root / ".claude" / "reference").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "reference" / "notes.md").write_text("x\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.md\n", encoding="utf-8")
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.WARN, result.message)
        self.assertTrue(any("notes.md" in d for d in result.details), result.details)
        self.assertFalse(any("\r" in d for d in result.details), result.details)

    def test_deliberate_private_ignore_is_not_a_shadow(self):
        private = self.root / ".claude" / "reference" / "private"
        private.mkdir(parents=True)
        (private / "x.md").write_text("secret\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".claude/reference/private/\n", encoding="utf-8")
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.OK)

    def test_outside_a_repo_skips(self):
        import shutil as _shutil
        _shutil.rmtree(self.root / ".git")
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.SKIP)

    def test_heartbeat_and_worktrees_are_deliberate_ignores(self):
        """Both are gitignored on purpose (the kits' .gitignore says so); a check that warned
        about them would cry wolf in every repo on every ritual."""
        (self.root / ".claude" / "state" / "heartbeat.json").write_text("{}\n", encoding="utf-8")
        wt = self.root / ".claude" / "worktrees" / "t1" / ".claude"
        wt.mkdir(parents=True)
        (wt / "CLAUDE.md").write_text("copy\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(
            ".claude/state/heartbeat.json\n.claude/worktrees/\n", encoding="utf-8")
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.OK, result.details)


class TestChangelogParity(FixtureCase):
    """The release flow pins one CHANGELOG.md section per version; this check is the
    mechanical half of that promise, and it must stay silent outside the home repo."""

    def setUp(self):
        # The fixture IS a home repo: set the knob rather than inherit whatever the repo running
        # the suite ships (a kit ships it false, and these tests must pass inside a kit too).
        super().setUp()
        cfg = _lib.load_config("memory")
        cfg["distribution"] = {"enabled": True}
        self.write_config("memory", cfg)

    def test_gated_off_outside_the_home_repo(self):
        cfg = _lib.load_config("memory")
        cfg["distribution"] = {"enabled": False}
        self.write_config("memory", cfg)
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.SKIP)

    def test_missing_changelog_fails_in_the_home_repo(self):
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.FAIL)

    def test_pinned_section_passes(self):
        version = _lib.system_version()
        (self.root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## v{version} - 2026-01-01\n\n- something\n", encoding="utf-8")
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.OK)

    def test_a_version_without_a_section_is_named(self):
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## v9.9.9 - 2026-01-01\n", encoding="utf-8")
        result = checkctl.check_changelog_parity()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn(f"missing '## v{_lib.system_version()}'", result.details)

    def test_a_longer_version_does_not_satisfy_a_prefix(self):
        version = _lib.system_version()
        (self.root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## v{version}0 - 2026-01-01\n", encoding="utf-8")
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.FAIL)

    def _stamp(self, version: str) -> None:
        cfg = _lib.load_config("registry")
        cfg["system_version"] = version
        self.write_config("registry", cfg)

    def _changelog(self, *headings: str) -> None:
        body = "".join(f"## {h} - 2026-01-01\n\n- x\n\n" for h in headings)
        (self.root / "CHANGELOG.md").write_text(f"# Changelog\n\n{body}", encoding="utf-8")

    def test_a_prerelease_stamp_is_pinned_by_its_own_heading(self):
        self._stamp("0.3.0-alpha.1")
        self._changelog("v0.3.0-alpha.1")
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.OK)

    def test_a_prerelease_heading_does_not_satisfy_the_stable_stamp(self):
        """'\\b' used to let '## v0.3.0-alpha.1' satisfy v0.3.0: the hyphen is a word boundary."""
        self._stamp("0.3.0")
        self._changelog("v0.3.0-alpha.1")
        result = checkctl.check_changelog_parity()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertIn("missing '## v0.3.0'", result.details)

    def test_a_stable_heading_does_not_satisfy_a_prerelease_stamp(self):
        self._stamp("0.3.0-rc.2")
        self._changelog("v0.3.0", "v0.3.0-rc.20")
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.FAIL)

    def test_a_stamp_outside_the_grammar_fails(self):
        for bad in ("0.3", "0.3.0-alpha", "0.3.0-alpha1", "0.3.0-gamma.1", "v0.3.0"):
            with self.subTest(stamp=bad):
                self._stamp(bad)
                self._changelog(f"v{bad}")
                result = checkctl.check_changelog_parity()
                self.assertEqual(result.status, checkctl.FAIL)
                self.assertIn("is not X.Y.Z", result.message)

    def test_prerelease_tags_need_a_section_and_junk_tags_are_ignored(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not available")

        def git(*args):
            return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True,
                                  text=True, check=False)
        self.assertEqual(git("init", "-q").returncode, 0)
        commit = git("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                     "commit", "-q", "--allow-empty", "-m", "x")
        self.assertEqual(commit.returncode, 0, commit.stderr)
        for tag in ("v0.3.0-beta.2", "vnext", "v0.3"):
            self.assertEqual(git("tag", tag).returncode, 0)
        self._stamp("0.3.0-alpha.1")
        self._changelog("v0.3.0-alpha.1")
        result = checkctl.check_changelog_parity()
        self.assertEqual(result.status, checkctl.FAIL)
        self.assertEqual(result.details, ["missing '## v0.3.0-beta.2'"],
                         "a grammar tag needs its section; a non-version tag is not a release")
        self._changelog("v0.3.0-beta.2", "v0.3.0-alpha.1")
        self.assertEqual(checkctl.check_changelog_parity().status, checkctl.OK)


class TestVersionGrammar(unittest.TestCase):
    """One parser for the stamp, the tags, changelog parity and release.yml."""

    def test_stable_and_prerelease_versions_parse(self):
        self.assertEqual(_lib.parse_version("0.2.2"),
                         {"major": 0, "minor": 2, "patch": 2, "pre": None, "pre_n": None,
                          "prerelease": False})
        for text, pre, n in (("0.3.0-alpha.1", "alpha", 1), ("1.0.0-beta.2", "beta", 2),
                             ("10.20.30-rc.11", "rc", 11), ("0.3.0-alpha.0", "alpha", 0)):
            with self.subTest(version=text):
                parsed = _lib.parse_version(text)
                self.assertEqual((parsed["pre"], parsed["pre_n"]), (pre, n))
                self.assertTrue(_lib.is_prerelease(text))
        self.assertFalse(_lib.is_prerelease("0.3.0"))

    def test_outside_the_grammar_is_none(self):
        for text in ("", "0.3", "0.3.0.1", "0.3.0-alpha", "0.3.0-alpha1", "0.3.0-Alpha.1",
                     "0.3.0-gamma.1", "0.3.0-alpha.1.2", "01.2.3", "0.3.0-alpha.01", "v0.3.0",
                     " 0.3.0", "0.3.0 ", None):
            with self.subTest(text=text):
                self.assertIsNone(_lib.parse_version(text))
                self.assertFalse(_lib.is_prerelease(text))

    def test_tags_carry_a_v(self):
        self.assertTrue(_lib.is_prerelease("v0.3.0-alpha.1", tag=True))
        self.assertIsNotNone(_lib.parse_version("v0.2.2", tag=True))
        self.assertIsNone(_lib.parse_version("0.2.2", tag=True))
        self.assertIsNone(_lib.parse_version("vv0.2.2", tag=True))

    def test_the_shipped_stamp_is_in_the_grammar(self):
        stamp = _lib.read_json(CLAUDE_DIR / "config" / "registry.json", {})["system_version"]
        self.assertIsNotNone(_lib.parse_version(stamp), f"system_version {stamp!r}")

    def test_release_kind_cli_for_the_workflow(self):
        import subprocess
        lib = str(CLAUDE_DIR / "tools" / "_lib.py")

        def kind(tag):
            res = subprocess.run([sys.executable, lib, "--release-kind", tag],
                                 capture_output=True, text=True, timeout=30, check=False)
            return res.returncode, res.stdout.strip()
        self.assertEqual(kind("v0.3.0-alpha.1"), (0, "prerelease"))
        self.assertEqual(kind("v0.3.0-rc.1"), (0, "prerelease"))
        self.assertEqual(kind("v0.3.1"), (0, "stable"))
        for bad in ("v0.3.0-alpha1", "0.3.0", "v0.3", "manual-20260101"):
            with self.subTest(tag=bad):
                self.assertEqual(kind(bad)[0], 1, "a malformed tag must fail the release job")

    def test_changelog_section_is_exact(self):
        log = ("# Changelog\n\n## Unreleased\n\n- next\n\n## v3.0.1 - 2026-02-01\n\n- a\n\n"
               "## v0.3.0-alpha.1 - 2026-01-02\n\n- alpha\n- one\n\n## v0.2.2\n\n- last\n")
        self.assertIsNone(_lib.changelog_section(log, "v3.0"))
        self.assertIsNone(_lib.changelog_section(log, "v0.3.0"))
        self.assertIsNone(_lib.changelog_section(log, "v0.3.0-alpha"))
        self.assertEqual(_lib.changelog_section(log, "v3.0.1"), "- a")
        self.assertEqual(_lib.changelog_section(log, "v0.3.0-alpha.1"), "- alpha\n- one")
        self.assertEqual(_lib.changelog_section(log, "v0.2.2"), "- last",
                         "a heading with no date suffix at end of file still matches")
        self.assertEqual(_lib.changelog_section("## v1.0.0 - x\n## v0.9.0\n", "v1.0.0"), "",
                         "an empty section is present (not None)")


class TestNoDeadKnobs(unittest.TestCase):
    """Knobs and actions nothing read: a knob that does nothing teaches the user that knobs
    do nothing. Each was confirmed unread before it went; this keeps them gone."""

    def test_the_removed_knobs_stay_removed(self):
        memory = _lib.read_json(CLAUDE_DIR / "config" / "memory.json")
        self.assertNotIn("snapshot", memory)
        self.assertEqual(set(memory["phases"]), {"check", "polish", "publish"},
                         "phases.<name> is read for check, polish and publish only")
        self.assertEqual({k for k in memory["project_steps"] if not k.startswith("_")},
                         {"check", "polish"}, "checkctl runs project steps of these kinds only")
        keys = {e.get("key") for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]}
        self.assertFalse({k for k in keys if k and k.startswith("memory.snapshot")})
        self.assertNotIn("config", _lib.JOURNAL_ACTIONS, "no writer ever emitted it")

    def test_the_analyze_provider_enum_agrees_everywhere(self):
        entry = next(e for e in _lib.read_json(CLAUDE_DIR / "config" / "registry.json")["entries"]
                     if e.get("key") == "observe.analyze.provider")
        observe = _lib.read_json(CLAUDE_DIR / "config" / "observe.json")["analyze"]
        code = {"none"} | set(obsctl.PROVIDER_BASE_URLS)
        self.assertEqual(set(entry["enum"]), code, "registry.json enum vs obsctl")
        self.assertEqual(set(observe["providers"]), code, "observe.json providers vs obsctl")
        self.assertIn(observe["provider"], code)


def _subcommands_named(text: str, tool: str) -> set:
    """Words a guide names as `tool`'s subcommands: inline (`statectl mode`, `statectl.py
    phase`) and in a commands block: the rest of the `tools/<tool>.py` line plus its indented
    continuation lines."""
    import re
    out = set(re.findall(rf"\b{tool}(?:\.py)?\s+([a-z][a-z-]*)", text))
    for m in re.finditer(rf"tools/{tool}\.py[ \t]+([^\n]*(?:\n[ \t]{{8,}}[^\n]*)*)", text):
        out |= set(re.findall(r"[a-z][a-z-]*", m.group(1)))
    return out


class TestGuidesNameEveryCommand(unittest.TestCase):
    """The guide and the manual are where a human or an agent learns a command exists: one
    that neither names is a command nobody runs."""

    COMMANDS = {
        "statectl": ("mode", "phase", "proposal", "dispatch", "accept", "progress"),
        "checkctl": ("doctor", "phase-exit", "handoff", "ticket"),
        "distctl": ("export", "gitignore", "verify"),
        "mapctl": ("context",),
    }

    def test_each_new_command_is_named(self):
        texts = [(CLAUDE_DIR / name).read_text(encoding="utf-8")
                 for name in ("CLAUDE.md", "README.md")]
        for tool, subs in self.COMMANDS.items():
            named = set().union(*(_subcommands_named(t, tool) for t in texts))
            for sub in subs:
                with self.subTest(tool=tool, sub=sub):
                    self.assertIn(sub, named, f"neither CLAUDE.md nor README.md names "
                                              f"`{tool} {sub}`")

    def test_the_matcher_can_fail(self):
        self.assertNotIn("ticket", _subcommands_named("python3 .claude/tools/checkctl.py doctor\n"
                                                      "the ticket is minted", "checkctl"))

    def test_the_guide_says_whose_the_ritual_is_and_that_adhd_is_suggested(self):
        import re
        for path in (CLAUDE_DIR / "CLAUDE.md", CLAUDE_DIR / "skills" / "adopt" / "CLAUDE.template.md"):
            with self.subTest(guide=path.name):
                text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
                self.assertIn("The ritual is the user's command", text)
                self.assertRegex(text, r"`/adhd` \([^)]*suggest it[^)]*never run it\)")
                self.assertNotIn("six core tools", text)
                self.assertNotIn("temp-to-analyse", text)

    def test_the_readmes_say_the_gate_is_a_tripwire(self):
        for path in (CLAUDE_DIR / "README.md", CLAUDE_DIR.parent / ".github" / "README.md"):
            if not path.exists():
                continue  # .github/ is the source repo's; a kit has none
            with self.subTest(readme=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("What is new in 0.3", text)
                self.assertIn("not a sandbox", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
