#!/usr/bin/env python3
"""test_obsctl.py - tests for obsctl.py, the OBSERVE spine CLI.

Every test drives obsctl through its public `main(argv)` entry point (in-process, stdout
captured) against the FixtureCase scratch project + record root, exactly like a real
ritual invocation would - never by poking at private module state directly.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

# This file's own directory must be on sys.path BEFORE `from _fixture import ...` - _fixture
# only adds .claude/tools (for _lib), not .claude/tools/tests itself. Matching the convention
# in test_hooks.py/test_statectl.py keeps this module importable standalone (e.g. `python3 -m
# unittest tests.test_obsctl` or `-p test_obsctl.py`), not only as a side effect of some other
# test module happening to run first in the same process during full-suite discovery.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import FixtureCase, TOOLS_DIR  # noqa: E402

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _lib  # noqa: E402
import obsctl  # noqa: E402


def run(argv: list) -> tuple[int, str]:
    """Call obsctl.main in-process, capturing stdout. Returns (exit_code, stdout_text)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = obsctl.main(argv)
    return rc, buf.getvalue()


def yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def two_days_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")


class SealAllowlistTest(FixtureCase):
    """1. Seal must drop any key not in seal_allowlist - a secret-bearing field must never
    survive into the (committed-adjacent, widely-read) segment, only into raw."""

    def test_drops_non_allowlisted_keys_but_keeps_them_in_raw(self):
        date = yesterday()
        secret = "sk-live-totally-real-secret-abc123"
        self.spool_event(
            session="s1",
            _obs_ts=f"{date}T12:00:00Z",
            hook_event_name="PostToolUse",
            tool_name="Bash",
            tool_response={"secret": secret},
            prompt="do the thing with the secret " + secret,
        )
        rc, out = run(["seal"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_OK", out)

        seg_path = self.record / "segments" / f"{date}.jsonl"
        self.assertTrue(seg_path.exists())
        seg_text = seg_path.read_text(encoding="utf-8")
        self.assertNotIn("tool_response", seg_text)
        self.assertNotIn("prompt", seg_text)
        self.assertNotIn(secret, seg_text)
        events = _lib.read_jsonl(seg_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("tool_name"), "Bash")  # allowlisted key survives

        # raw (sealed-raw) is gzipped by default (compact_sealed_raw: true in the shipped
        # config) and MUST still carry the full original event, secret included.
        raw_path = self.record / "sealed-raw" / f"{date}.jsonl.gz"
        self.assertTrue(raw_path.exists())
        with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
            raw_text = fh.read()
        self.assertIn(secret, raw_text)
        self.assertIn("tool_response", raw_text)


class SealIdempotentTest(FixtureCase):
    """2. Sealing the same event twice must not duplicate it in the segment."""

    def test_seal_twice_same_count(self):
        date = yesterday()
        ev = {"_obs_ts": f"{date}T08:00:00Z", "_obs_source": "hook",
              "hook_event_name": "SessionStart", "session_id": "dup-session"}
        self.spool_event(session="s1", **ev)
        rc, _ = run(["seal"])
        self.assertEqual(rc, 0)
        seg_path = self.record / "segments" / f"{date}.jsonl"
        first_count = len(_lib.read_jsonl(seg_path))
        self.assertEqual(first_count, 1)

        rc, _ = run(["seal"])  # nothing new in spool: must be a true no-op
        self.assertEqual(rc, 0)
        self.assertEqual(len(_lib.read_jsonl(seg_path)), first_count)

        # Re-append the SAME identity into spool for the same already-sealed date (as if a
        # duplicate capture occurred) and seal again - identity-based dedupe must still
        # catch it, exercising the actual dedupe logic rather than "nothing left to do".
        self.spool_event(session="s2", **ev)
        rc, _ = run(["seal"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(_lib.read_jsonl(seg_path)), first_count)


class RawPreservedRetentionTest(FixtureCase):
    """3. Raw is preserved verbatim in sealed-raw, and retention_days: 0 never deletes."""

    def test_raw_preserved_and_zero_retention_never_deletes(self):
        old_date = two_days_ago()
        marker = "raw-preserved-marker-xyz"
        self.spool_event(
            session="s1", _obs_ts=f"{old_date}T00:00:00Z",
            hook_event_name="SessionStart", session_id="keepme",
            some_unallowlisted_field=marker,
        )
        cfg = _lib.load_config("observe")
        self.assertEqual(cfg.get("retention_days", 0), 0)  # shipped default

        run(["seal"])
        raw_path = self.record / "sealed-raw" / f"{old_date}.jsonl.gz"
        self.assertTrue(raw_path.exists())
        with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
            self.assertIn(marker, fh.read())

        # seal again (retention runs every seal) - the old raw must still be there
        run(["seal"])
        self.assertTrue(raw_path.exists())
        with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
            self.assertIn(marker, fh.read())


class CostTest(FixtureCase):
    """4. Cost formula: unknown model -> known:false/usd null; a model priced for only ONE
    of its two used classes -> that class prices, the other lands in unpriced_classes, and
    nothing is silently zeroed into a false-looking total."""

    def _seal_one_model_event(self, date: str, model: str, input_tokens: int, output_tokens: int):
        self.spool_event(
            session="s1", _obs_ts=f"{date}T00:00:00Z",
            hook_event_name="llm.usage", session_id="cost-session",
            **{
                "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": input_tokens,
                "gen_ai.usage.output_tokens": output_tokens,
            },
        )

    def test_unknown_model_reports_unknown(self):
        date = yesterday()
        self._seal_one_model_event(date, "mystery-model", 1000, 500)
        prices = _lib.load_config("model-prices")
        self.assertEqual(prices.get("per_million_tokens"), {})  # shipped empty

        run(["seal"])
        rc, _ = run(["rollup", "--date", date])
        self.assertEqual(rc, 0)
        rollup = _lib.read_json(self.root / ".claude-iff" / "obs" / "rollups" / f"{date}.json")
        cost = rollup["cost"]
        self.assertFalse(cost["known"])
        self.assertIsNone(cost["usd"])
        self.assertIn("mystery-model", cost["unknown_models"])

    def test_partially_priced_model_prices_one_class_and_flags_the_other(self):
        date = yesterday()
        self._seal_one_model_event(date, "priced-model", 1_000_000, 1_000_000)
        # price ONLY the input class for this model - output is deliberately left unpriced
        self.write_config("model-prices", {
            "version": 1, "currency": "USD",
            "per_million_tokens": {"priced-model": {"input": 3.0}},
        })

        run(["seal"])
        rc, _ = run(["rollup", "--date", date])
        self.assertEqual(rc, 0)
        rollup = _lib.read_json(self.root / ".claude-iff" / "obs" / "rollups" / f"{date}.json")
        cost = rollup["cost"]
        self.assertTrue(cost["known"])  # the model itself IS in the price table
        self.assertEqual(cost["unknown_models"], [])
        self.assertIn("priced-model:output", cost["unpriced_classes"])
        self.assertNotIn("priced-model:input", cost["unpriced_classes"])
        # 1,000,000 input tokens * $3.0 / 1e6 == $3.00 exactly; output contributes nothing
        # (not zero-guessed silently - it's recorded above in unpriced_classes)
        self.assertEqual(cost["usd"], 3.0)


class IngestTest(FixtureCase):
    """5. Transcript ingest: rglob (not top-level glob) finds nested subagent transcripts,
    the cursor is incremental across runs, and a shrunk file resets the cursor."""

    def setUp(self):
        super().setUp()
        self.projects_root = Path(self._tmp.name) / "fake-claude-projects"
        self.slug = obsctl._project_slug(self.root)
        self.proj_dir = self.projects_root / self.slug
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.write_config("observe", {
            **_lib.load_config("observe"),
            "ingest": {
                "roots": [str(self.projects_root)],
                "copy_verbatim": True,
                "max_files_per_run": 0,
            },
        })

    @staticmethod
    def _usage_line(message_id: str, input_tokens: int, output_tokens: int, session: str = "sess-1") -> str:
        rec = {
            "type": "assistant",
            "sessionId": session,
            "timestamp": "2026-08-20T00:00:00Z",
            "isSidechain": False,
            "message": {
                "id": message_id,
                "model": "claude-x",
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
        }
        return json.dumps(rec)

    def test_nested_subagent_transcript_is_counted(self):
        session_dir = self.proj_dir / "session-1"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "main.jsonl").write_text(self._usage_line("msg-main-1", 100, 10) + "\n")

        subagent_dir = session_dir / "subagents"
        subagent_dir.mkdir(parents=True, exist_ok=True)
        (subagent_dir / "agent-x.jsonl").write_text(
            self._usage_line("msg-sub-1", 200, 20, session="sess-1") + "\n")

        rc, out = run(["ingest"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_OK", out)
        self.assertIn("files_scanned=2", out)
        self.assertIn("new_events=2", out)
        self.assertIn("tokens_input=300", out)   # 100 + 200: nested file WAS counted
        self.assertIn("tokens_output=30", out)   # 10 + 20

        spool_events = _lib.read_jsonl(self.record / "spool" / "ingest.jsonl")
        message_ids = {e.get("message_id") for e in spool_events}
        self.assertEqual(message_ids, {"msg-main-1", "msg-sub-1"})

        mirrored_sub = self.record / "raw" / "transcripts" / self.slug / "session-1" / "subagents" / "agent-x.jsonl"
        self.assertTrue(mirrored_sub.exists())

    def test_second_run_adds_nothing(self):
        session_dir = self.proj_dir / "session-2"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "main.jsonl").write_text(self._usage_line("msg-a", 5, 5) + "\n")

        rc, out1 = run(["ingest"])
        self.assertEqual(rc, 0)
        self.assertIn("new_events=1", out1)

        rc, out2 = run(["ingest"])
        self.assertEqual(rc, 0)
        self.assertIn("new_events=0", out2)
        self.assertIn("files_scanned=1", out2)  # the file is still scanned, just yields nothing new

    def test_shrunk_file_resets_cursor(self):
        session_dir = self.proj_dir / "session-3"
        session_dir.mkdir(parents=True, exist_ok=True)
        f = session_dir / "main.jsonl"
        f.write_text(
            self._usage_line("msg-1", 1, 1) + "\n" +
            self._usage_line("msg-2", 2, 2) + "\n" +
            self._usage_line("msg-3", 3, 3) + "\n"
        )
        rc, out = run(["ingest"])
        self.assertIn("new_events=3", out)

        # simulate rotation: the file is replaced by a smaller one with fresh content
        f.write_text(self._usage_line("msg-rotated", 9, 9) + "\n")
        rc, out = run(["ingest"])
        self.assertEqual(rc, 0)
        self.assertIn("new_events=1", out)  # cursor reset to 0, re-read from the start
        spool_events = _lib.read_jsonl(self.record / "spool" / "ingest.jsonl")
        message_ids = [e.get("message_id") for e in spool_events]
        self.assertIn("msg-rotated", message_ids)


class StoryContractTest(FixtureCase):
    """6. story output validates against STORY_CONTRACT; t_tokens_cum is monotonically
    non-decreasing across points."""

    def test_story_matches_contract_and_clock_is_monotonic(self):
        d1 = two_days_ago()
        d2 = yesterday()
        for date, out_tokens in ((d1, 100), (d2, 250)):
            self.spool_event(
                session="s1", _obs_ts=f"{date}T00:00:00Z",
                hook_event_name="llm.usage", session_id="story-session",
                **{"gen_ai.request.model": "claude-x", "gen_ai.usage.output_tokens": out_tokens},
            )
        run(["seal"])
        for date in (d1, d2):
            rc, _ = run(["rollup", "--date", date])
            self.assertEqual(rc, 0)

        self.journal("milestone", id="m1", title="shipped obsctl")

        rc, out = run(["story"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_OK", out)

        feed = _lib.read_json(self.root / ".claude" / "state" / "story-feed.json")
        self.assertTrue(obsctl.validate_story_contract(feed))
        self.assertTrue(obsctl.validate_story_contract(feed, obsctl.STORY_CONTRACT))

        cum = [p["t_tokens_cum"] for p in feed["points"]]
        self.assertEqual(cum, sorted(cum))  # monotonically non-decreasing
        self.assertEqual(len(feed["points"]), 2)
        self.assertEqual(feed["points"][0]["t_tokens_cum"], 100)
        self.assertEqual(feed["points"][1]["t_tokens_cum"], 350)
        self.assertEqual(feed["clock"]["tokens_max"], 350)
        self.assertTrue(any(m["kind"] == "milestone" for m in feed["markers"]))

    def test_contract_rejects_a_broken_feed(self):
        broken = {"v": 1, "generated_at": "x", "clock": {"wall_min": "", "wall_max": ""},
                  "points": [], "markers": []}  # clock.tokens_max missing
        self.assertFalse(obsctl.validate_story_contract(broken))


class AnchorAndSelftestTest(FixtureCase):
    def test_anchor_hashes_match_segments(self):
        date = yesterday()
        self.spool_event(session="s1", _obs_ts=f"{date}T00:00:00Z",
                          hook_event_name="SessionStart", session_id="anchor-session")
        run(["seal"])
        rc, out = run(["anchor"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_OK", out)
        anchor = _lib.read_json(self.root / ".claude-iff" / "obs" / "anchor.json")
        seg_path = self.record / "segments" / f"{date}.jsonl"
        entry = next(c for c in anchor["chain"] if c["date"] == date)
        self.assertEqual(entry["sha256"], _lib.sha256_file(seg_path))
        self.assertEqual(anchor["last_sealed_date"], date)

    def test_selftest_does_not_touch_live_record(self):
        marker_dir = self.record / "spool"
        marker_dir.mkdir(parents=True, exist_ok=True)
        sentinel = marker_dir / "sentinel.jsonl"
        _lib.append_jsonl(sentinel, {"_obs_ts": "2020-01-01T00:00:00Z", "keep": True})

        rc, out = run(["selftest"])
        self.assertIn(rc, (0, 1))
        self.assertIn("OBS_", out)
        # the live record's spool sentinel must be untouched by an isolated selftest
        self.assertTrue(sentinel.exists())
        self.assertEqual(len(_lib.read_jsonl(sentinel)), 1)
        # env must be restored to the live fixture values after selftest returns
        import os
        self.assertEqual(os.environ.get("CLAUDE_PROJECT_DIR"), str(self.root))
        self.assertEqual(os.environ.get("CLAUDE_IFF_RECORD_ROOT"), str(self.record))


class AnalyzeNoopTest(FixtureCase):
    def test_default_provider_none_is_a_clean_noop(self):
        cfg = _lib.load_config("observe")
        self.assertEqual(cfg["analyze"]["provider"], "none")  # the shipped default
        rc, out = run(["analyze"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_OK", out)
        # pin the ACTUAL reason for the no-op (unconfigured provider), not just "no-op"
        # somewhere in the message - that phrase also appears on the "no events" branch,
        # which would let this test pass for the wrong reason.
        self.assertIn("not configured", out)
        self.assertIn("ANALYZE_API_KEY", out, "the no-op must guide the user to the key setup")
        self.assertFalse((self.record / "analysis").exists() and
                          any((self.record / "analysis").iterdir()))

    def test_dry_run_withholds_raw_content(self):
        secret = "super-secret-payload-should-not-print"
        self.spool_event(session="s1", tool_response={"x": secret})
        rc, out = run(["analyze", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN", out)
        self.assertNotIn(secret, out)

    def test_missing_api_key_degrades_to_noop(self):
        self.write_config("observe", {
            **_lib.load_config("observe"),
            "analyze": {**_lib.load_config("observe")["analyze"], "provider": "openrouter"},
        })
        import os
        os.environ.pop("OPENROUTER_API_KEY", None)
        rc, out = run(["analyze"])
        self.assertEqual(rc, 0)
        self.assertIn("not configured", out, "a remote endpoint with no key is unconfigured, and says so with guidance")


class SizeReportTest(FixtureCase):
    def test_size_never_deletes_and_reports_bytes(self):
        self.spool_event(session="s1", hook_event_name="SessionStart")
        before = _lib.read_jsonl(self.record / "spool" / "s1.jsonl")
        rc, out = run(["size"])
        self.assertEqual(rc, 0)
        self.assertIn("OBS_", out)
        after = _lib.read_jsonl(self.record / "spool" / "s1.jsonl")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
