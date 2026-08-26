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
        # writes into.
        checkctl.main(["run", "--phase", "check", "--new"])
        opened = checkctl.load_run()["run_id"]
        checkctl.main(["run", "--phase", "polish"])
        self.assertEqual(checkctl.load_run()["run_id"], opened,
                         "POLISH started a new run instead of continuing the one CHECK opened")

    def test_new_forces_a_fresh_run(self):
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
        (self.root / ".gitignore").write_text("dist/\n", encoding="utf-8")
        result = checkctl.check_gitignore_shadowing()
        self.assertEqual(result.status, checkctl.WARN)
        self.assertTrue(any("dist" in d for d in result.details), result.details)

    def test_clean_ignores_pass(self):
        self.assertEqual(checkctl.check_gitignore_shadowing().status, checkctl.OK)

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


class TestChangelogParity(FixtureCase):
    """The release flow pins one CHANGELOG.md section per version; this check is the
    mechanical half of that promise, and it must stay silent outside the home repo."""

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
