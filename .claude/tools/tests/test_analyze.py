#!/usr/bin/env python3
"""test_analyze.py - the structured-output analysis engine, exercised against a REAL fake
OpenAI-compatible server on loopback.

The engine's whole claim is "a tool call with a schema, nothing analyzed by Claude" - so these
tests attack the mechanical parts: the schema request and its fallback, the JSON repair, the
taxonomy validation that rejects off-contract replies, the parallel chunking, the env-only key
rule, and the plain-code aggregation that folds labels into the story feed. No test here ever
touches the real network.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import FixtureCase  # noqa: E402

import _lib  # noqa: E402
import obsctl  # noqa: E402


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    """A minimal OpenAI-compatible /chat/completions endpoint with switchable behavior."""

    def log_message(self, fmt, *args):  # noqa: N802
        pass

    def do_POST(self):  # noqa: N802
        cfg = self.server.behavior
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length))
        self.server.requests.append(request)

        if cfg["mode"] == "reject_schema" and "response_format" in request:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "response_format not supported"}')
            return

        # Every event line in the prompt is "\n[<i>] {...}" (the header line ends with a colon,
        # so event 0 also follows a newline): the count IS the batch length.
        batch_len = request["messages"][-1]["content"].count("\n[")
        labels = [{"i": i, "labels": cfg["labels"], "summary": f"event {i}"}
                  for i in range(min(batch_len, cfg.get("label_n", batch_len)))]
        content = json.dumps({"labels": labels, "batch_summary": "ok"})
        if cfg["mode"] == "fenced":
            content = "Here you go:\n```json\n" + content + "\n```"
        if cfg["mode"] == "garbage":
            content = "I cannot help with that."
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AnalyzeCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
        self.server.behavior = {"mode": "ok", "labels": ["error"]}
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def configure(self, **overrides):
        cfg = json.loads((self.root / ".claude/config/observe.json").read_text())
        cfg["analyze"].update({"base_url": self.base_url, "model": "fake-model",
                               "parallel": 3, "max_events_per_batch": 2, **overrides})
        self.write_config("observe", cfg)

    def seed_events(self, n=5):
        for i in range(n):
            self.spool_event(session="s1", hook_event_name="Stop", _obs_uid=f"uid-{i}")


class TestEngine(AnalyzeCase):
    def test_labels_flow_end_to_end(self):
        self.configure()
        self.seed_events(5)
        self.assertEqual(obsctl.main(["analyze"]), 0)
        products = list((self.record / "analysis").glob("*-labels.json"))
        self.assertEqual(len(products), 1)
        product = json.loads(products[0].read_text())
        self.assertEqual(product["engine"], "openai-compatible")
        self.assertEqual(product["n_events"], 5)
        self.assertEqual(product["n_failed"], 0)
        self.assertTrue(all(ev["labels"] == ["error"] for ev in product["events"]))
        # 5 events at batch size 2 = 3 chunks, all of which must have hit the server
        self.assertEqual(len(self.server.requests), 3)

    def test_schema_rejection_falls_back_once(self):
        """A server that rejects response_format gets one retry with the schema in-prompt."""
        self.server.behavior = {"mode": "reject_schema", "labels": ["decision"]}
        self.configure(max_events_per_batch=10)
        self.seed_events(3)
        self.assertEqual(obsctl.main(["analyze"]), 0)
        product = json.loads(next((self.record / "analysis").glob("*-labels.json")).read_text())
        self.assertEqual(product["n_failed"], 0)
        with_format = [r for r in self.server.requests if "response_format" in r]
        without = [r for r in self.server.requests if "response_format" not in r]
        self.assertTrue(with_format and without, "expected a schema attempt then a fallback")

    def test_fenced_json_is_repaired(self):
        self.server.behavior = {"mode": "fenced", "labels": ["error"]}
        self.configure(max_events_per_batch=10)
        self.seed_events(2)
        self.assertEqual(obsctl.main(["analyze"]), 0)
        product = json.loads(next((self.record / "analysis").glob("*-labels.json")).read_text())
        self.assertEqual(product["n_failed"], 0)

    def test_off_taxonomy_labels_invalidate_the_batch(self):
        """Validation is the guarantee, not the schema request: a label outside the taxonomy
        must fail the whole chunk rather than half-trusting it."""
        self.server.behavior = {"mode": "ok", "labels": ["not-in-taxonomy"]}
        self.configure(max_events_per_batch=10)
        self.seed_events(2)
        obsctl.main(["analyze"])
        product = json.loads(next((self.record / "analysis").glob("*-labels.json")).read_text())
        self.assertEqual(product["n_failed"], product["n_batches"])
        self.assertEqual(product["events"], [])

    def test_garbage_reply_counts_as_failed_not_crash(self):
        self.server.behavior = {"mode": "garbage", "labels": []}
        self.configure(max_events_per_batch=10)
        self.seed_events(2)
        obsctl.main(["analyze"])
        product = json.loads(next((self.record / "analysis").glob("*-labels.json")).read_text())
        self.assertEqual(product["n_failed"], 1)

    def test_unconfigured_is_a_guided_noop(self):
        self.seed_events(2)
        self.assertEqual(obsctl.main(["analyze"]), 0, "unconfigured must exit 0, never fail")
        self.assertEqual(list((self.record / "analysis").glob("*-labels.json")), [])

    def test_dry_run_sends_nothing(self):
        self.configure()
        self.seed_events(3)
        self.assertEqual(obsctl.main(["analyze", "--dry-run"]), 0)
        self.assertEqual(self.server.requests, [], "dry-run must not touch the network")

    def test_key_never_read_from_config(self):
        """The key comes from the environment ONLY - observe.json is committed to git, so a
        key placed there must simply be ignored (and localhost needs none anyway)."""
        self.configure(api_key="sk-SHOULD-BE-IGNORED")
        self.seed_events(1)
        self.assertEqual(obsctl.main(["analyze"]), 0)
        for request in self.server.requests:
            self.assertNotIn("SHOULD-BE-IGNORED", json.dumps(request))


class TestStatus(AnalyzeCase):
    def test_status_reports_configured_for_localhost_without_key(self):
        self.configure()
        status = obsctl.analyze_status()
        self.assertTrue(status["configured"], "localhost endpoints need no key")
        self.assertFalse(status["key_required"])

    def test_status_never_carries_key_material(self):
        import os
        os.environ["ANALYZE_API_KEY"] = "sk-super-secret-value"
        try:
            self.configure()
            status = obsctl.analyze_status()
            self.assertNotIn("sk-super-secret-value", json.dumps(status))
            self.assertEqual(status["key_env"], "ANALYZE_API_KEY")
        finally:
            del os.environ["ANALYZE_API_KEY"]

    def test_remote_without_key_is_not_configured(self):
        self.configure(base_url="https://openrouter.ai/api/v1")
        import os
        saved = {e: os.environ.pop(e, None) for e in obsctl.ANALYZE_KEY_ENVS}
        try:
            status = obsctl.analyze_status()
            self.assertFalse(status["configured"])
            self.assertTrue(status["key_required"])
        finally:
            for env, value in saved.items():
                if value is not None:
                    os.environ[env] = value


class TestStoryMerge(AnalyzeCase):
    def test_labels_reach_the_story_analysis_lane(self):
        """The programmatic-postprocessing half: model labels raw events, plain code folds
        them into per-date counts on the story feed. No model in this step."""
        self.configure(max_events_per_batch=10)
        self.seed_events(4)
        obsctl.main(["seal", "--date", _lib.today()])
        obsctl.main(["rollup", "--date", _lib.today()])
        self.assertEqual(obsctl.main(["analyze"]), 0)
        self.assertEqual(obsctl.main(["story"]), 0)
        feed = json.loads((_lib.state_dir() / "story-feed.json").read_text())
        today_point = next(p for p in feed["points"] if p["date"] == _lib.today())
        self.assertEqual(today_point["analysis"].get("error"), 4,
                         "4 labeled events must aggregate into the analysis lane")

    def test_story_without_analysis_is_unchanged(self):
        self.spool_event(session="s1", hook_event_name="Stop")
        obsctl.main(["seal", "--date", _lib.today()])
        obsctl.main(["rollup", "--date", _lib.today()])
        self.assertEqual(obsctl.main(["story"]), 0)
        feed = json.loads((_lib.state_dir() / "story-feed.json").read_text())
        self.assertTrue(all(p["analysis"] == {} for p in feed["points"]),
                        "no products means empty analysis dicts, never a crash or a missing key")


class TestAnalyzeEndpoint(FixtureCase):
    """The console's ONE action endpoint. Everything else stays command emission; this
    carve-out exists because analysis writes only labeled products into the out-of-repo
    record, never repo state."""

    def _server(self):
        import importlib.util
        console_py = Path(__file__).resolve().parents[2] / "console" / "console.py"
        spec = importlib.util.spec_from_file_location("console_server", console_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        server = mod.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, server.server_address[1]

    def test_post_analyze_unconfigured_returns_409_with_guidance(self):
        import urllib.request
        import urllib.error
        server, port = self._server()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/live/analyze", data=b"",
                                         method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 409)
            body = json.loads(ctx.exception.read().decode())
            self.assertIn("observe.json", body["how"])
        finally:
            server.shutdown()
            server.server_close()

    def test_post_anything_else_is_404(self):
        import urllib.request
        import urllib.error
        server, port = self._server()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/live/other", data=b"",
                                         method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_post_with_bad_host_is_403(self):
        import urllib.request
        import urllib.error
        server, port = self._server()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/live/analyze", data=b"",
                                         method="POST", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()


class TestNeedContext(FixtureCase):
    def test_open_requires_context_and_action(self):
        import statectl
        with self.assertRaises(SystemExit):
            statectl.main(["need", "open", "--title", "x", "--category", "decide"])

    def test_context_and_action_reach_the_console(self):
        import statectl
        import consolectl
        context = ("The deploy needs a decision about the cache layer before it can proceed, "
                   "and the wrong default would double our memory bill.")
        statectl.main(["need", "open", "--title", "Pick the cache layer", "--category", "decide",
                       "--context", context, "--action", "Reply with redis or in-process"])
        top = consolectl.payload()["now"]["needs_human"]["top"]
        self.assertEqual(top[0]["context"], context)
        self.assertEqual(top[0]["action"], "Reply with redis or in-process")
        template = top[0]["reply_template"]
        self.assertIn("Answering", template)
        self.assertIn(context, template)
        self.assertTrue(template.endswith("My answer: "),
                        "the paste template must end where the human types")

    def test_short_context_warns_but_does_not_block(self):
        import statectl
        code = statectl.main(["need", "open", "--title", "x", "--category", "review",
                              "--context", "too short", "--action", "look"])
        self.assertEqual(code, 0, "a short context warns; refusing outright would just teach "
                                  "agents to pad, and a parked question beats a lost one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
