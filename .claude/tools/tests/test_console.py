#!/usr/bin/env python3
"""test_console.py - tests for consolectl.py, console.py and console.template.html.

Run with: python3 -m unittest discover -s .claude/tools/tests -t .claude/tools -v
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import sys
import threading
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import FixtureCase, CLAUDE_DIR  # noqa: E402  (path must be set first)

import _lib  # noqa: E402
import consolectl  # noqa: E402

CONSOLE_DIR = CLAUDE_DIR / "console"
if str(CONSOLE_DIR) not in sys.path:
    sys.path.insert(0, str(CONSOLE_DIR))
import console  # noqa: E402  (path must be set first)

ZERO_TOKENS = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def _install_template(case: FixtureCase) -> None:
    """_fixture.py's scaffold makes a `console/` dir but does not seed it with the real
    template (it is not a config file) - tests that call build()/render() need it present,
    so copy the repo's actual template in rather than inventing a stand-in that could drift
    from what build() really ships."""
    src = CLAUDE_DIR / "console" / "console.template.html"
    shutil.copy(src, case.root / ".claude" / "console" / "console.template.html")


class PayloadEmptyProjectTests(FixtureCase):
    """1. payload() on a brand-new project with no state files at all."""

    def test_payload_does_not_raise_and_degrades_to_nulls(self):
        data = consolectl.payload()  # must not raise

        self.assertEqual(data["v"], 1)
        self.assertIsInstance(data["generated_at"], str)
        self.assertIsNone(data["server_ts"])
        self.assertEqual(data["mode"], "static")

        self.assertEqual(data["project"]["name"], self.root.name)
        # root is deliberately null: the payload is baked into a committed file, and a
        # machine's home path does not belong in a repo. file:// links derive the root
        # from the page's own location instead.
        self.assertIsNone(data["project"]["root"])

        now = data["now"]
        self.assertEqual(now["heartbeat"], {"ts": None, "note": None})
        self.assertIsNone(now["heartbeat_age_seconds"])
        self.assertIsNone(now["resume_pointer"])
        self.assertIsNone(now["phase"])
        self.assertIsNone(now["session_id"])
        self.assertEqual(now["open_loops"], [])
        self.assertEqual(now["in_flight"], [])
        self.assertEqual(now["needs_human"], {
            "open": 0,
            "by_band": {"SEV0": 0, "SEV1": 0, "SEV2": 0, "SEV3": 0},
            "top": [],
        })
        self.assertEqual(now["journal_tail"], [])

        tok = data["tokens"]
        self.assertIsNone(tok["as_of"])
        self.assertEqual(tok["today"], ZERO_TOKENS)
        self.assertEqual(tok["total"], ZERO_TOKENS)
        self.assertIsNone(tok["cost_usd"])
        self.assertFalse(tok["cost_known"])
        self.assertEqual(tok["unknown_models"], [])

        work = data["work"]
        self.assertEqual(work["tasks"], [])
        self.assertEqual(work["log_tail"], [])
        self.assertEqual(work["watch_outs"], [])
        self.assertEqual(work["research"], [])

        self.assertIsNone(data["map"])
        self.assertIsNone(data["story"])

        self.assertEqual(data["freshness"], {"live": ["now", "analysis"], "ritual": ["tokens", "work.log_tail", "map", "story"]})

        self.assertIn("no heartbeat yet", data["warnings"])
        # The shipped default is billing "subscription", under which an empty price table is
        # the correct state, not a warning. The api-mode warning is covered in test_dist.
        self.assertNotIn("price table empty - costs read unknown", data["warnings"])
        self.assertEqual(data["tokens"]["billing"], "subscription")
        self.assertIn("map not built", data["warnings"])
        self.assertIn("story not built", data["warnings"])


class PayloadPopulatedTests(FixtureCase):
    """2. payload() picks up session/heartbeat/needs-human/journal/handshakes when present."""

    def setUp(self) -> None:
        super().setUp()
        state = self.root / ".claude" / "state"

        _lib.atomic_write_json(state / "heartbeat.json", {"ts": _lib.utc_now(), "note": "mid-task"})

        # Shapes below mirror statectl.py's real projections exactly (verified against
        # _build_session_projection / _build_needs_projection), not a guessed schema -
        # session_id and phase live under a nested "session" object, and needs-human.json
        # carries pre-computed "counts" plus an already band-sorted "tasks" list.
        _lib.atomic_write_json(state / "session.json", {
            "resume_pointer": "run the console tests next",
            "session": {"id": "sess-abc123", "phase": "check", "started": _lib.utc_now()},
            "open_loops": [
                {"id": "L1", "text": "finish console tab wiring", "status": "open"},
                {"id": "L2", "text": "old closed loop", "status": "closed"},
            ],
        })

        _lib.atomic_write_json(state / "needs-human.json", {
            "counts": {"open": 2, "resolved": 1, "by_band": {"SEV0": 1, "SEV1": 1, "SEV2": 0, "SEV3": 0}},
            "tasks": [
                {"id": "NH2", "title": "env is wedged", "category": "system-blocker", "band": "SEV0",
                 "blocks": 0, "note": "", "opened": _lib.utc_now(), "age_days": 0, "status": "open"},
                {"id": "NH1", "title": "pick a color", "category": "decide", "band": "SEV1",
                 "blocks": 0, "note": "", "opened": _lib.utc_now(), "age_days": 0, "status": "open"},
            ],
            "resolved_recent": [{"id": "NH3", "title": "already handled", "answer": "", "resolved": _lib.utc_now()}],
        })

        self.journal("pointer", text="run the console tests next")
        self.journal("task", id="T1", title="Build console", status="doing")
        self.journal("milestone", id="M1", title="consolectl payload() lands")

        (state / "handshakes").mkdir(parents=True, exist_ok=True)
        _lib.atomic_write_json(state / "handshakes" / "HS1.stub.json", {
            "agent": "console-builder", "task_id": "HS1", "since": _lib.utc_now(),
        })

    def test_now_section_reflects_written_state(self):
        data = consolectl.payload()
        now = data["now"]

        self.assertIsNotNone(now["heartbeat"]["ts"])
        self.assertEqual(now["heartbeat"]["note"], "mid-task")
        self.assertIsInstance(now["heartbeat_age_seconds"], float)
        self.assertLess(now["heartbeat_age_seconds"], 30)

        self.assertEqual(now["resume_pointer"], "run the console tests next")
        self.assertEqual(now["phase"], "check")
        self.assertEqual(now["session_id"], "sess-abc123")

        self.assertEqual(now["open_loops"], [{"id": "L1", "text": "finish console tab wiring"}])

        nh = now["needs_human"]
        self.assertEqual(nh["open"], 2)
        self.assertEqual(nh["by_band"]["SEV0"], 1)
        self.assertEqual(nh["by_band"]["SEV1"], 1)
        self.assertEqual(nh["by_band"]["SEV2"], 0)
        ids = [item["id"] for item in nh["top"]]
        self.assertEqual(ids, ["NH2", "NH1"])  # projector's own band-first order, preserved
        self.assertNotIn("NH3", ids)  # only in resolved_recent, never read into needs_human
        self.assertEqual(nh["top"][0]["band"], "SEV0")

        summaries = [ev["summary"] for ev in now["journal_tail"]]
        self.assertIn("run the console tests next", summaries)
        self.assertIn("T1 · Build console · doing", summaries)
        self.assertIn("consolectl payload() lands", summaries)

        self.assertEqual(len(now["in_flight"]), 1)
        self.assertEqual(now["in_flight"][0]["agent"], "console-builder")
        self.assertEqual(now["in_flight"][0]["task_id"], "HS1")


class InFlightEnvelopeTests(FixtureCase):
    """3. in_flight excludes a stub that already has a delivered envelope."""

    def test_stub_with_envelope_is_excluded(self):
        hs_dir = self.root / ".claude" / "state" / "handshakes"
        hs_dir.mkdir(parents=True, exist_ok=True)
        _lib.atomic_write_json(hs_dir / "HS1.stub.json", {"agent": "a1", "task_id": "HS1", "since": _lib.utc_now()})
        _lib.atomic_write_json(hs_dir / "HS2.stub.json", {"agent": "a2", "task_id": "HS2", "since": _lib.utc_now()})
        _lib.atomic_write_json(hs_dir / "HS2.json", {"agent_id": "a2", "task_id": "HS2", "status": "done", "artifacts": [], "notes": ""})

        data = consolectl.payload()
        task_ids = {item["task_id"] for item in data["now"]["in_flight"]}
        self.assertIn("HS1", task_ids)
        self.assertNotIn("HS2", task_ids)


class BuildWriteGatingTests(FixtureCase):
    """4. build() write-gating: identical inputs produce identical bytes apart from
    generated_at - which build() achieves by mirroring statectl._write_gated's contract
    (skip the write entirely when nothing but generated_at would change), not merely by
    happening to serialize deterministically."""

    def setUp(self) -> None:
        super().setUp()
        _install_template(self)

    def test_second_build_with_no_source_change_is_byte_identical(self):
        with mock.patch.object(_lib, "utc_now", return_value="2030-01-01T00:00:00Z"):
            result1 = consolectl.build()
        self.assertTrue(result1["wrote"])
        first = result1["path"].read_text(encoding="utf-8")

        with mock.patch.object(_lib, "utc_now", return_value="2030-01-01T00:05:00Z"):
            result2 = consolectl.build()
        second = result2["path"].read_text(encoding="utf-8")

        self.assertFalse(result2["wrote"], "an unchanged payload must not rewrite console.html")
        self.assertEqual(first, second, "console.html changed even though nothing but the clock did")

    def test_a_real_source_change_does_rewrite(self):
        consolectl.build()
        state = self.root / ".claude" / "state"
        _lib.atomic_write_json(state / "heartbeat.json", {"ts": _lib.utc_now(), "note": "now working"})
        result = consolectl.build()
        self.assertTrue(result["wrote"], "a genuine source change must rewrite console.html")


class RenderedHtmlOfflineTests(FixtureCase):
    """5. no external resource references; exactly one __CONSOLE_DATA__ replacement."""

    def setUp(self) -> None:
        super().setUp()
        _install_template(self)

    def test_no_external_resource_references(self):
        result = consolectl.build()
        html = result["path"].read_text(encoding="utf-8")

        self.assertNotIn(consolectl.TEMPLATE_TOKEN, html)

        # The offline guarantee is about what the page FETCHES, not what it links to: script
        # src=, stylesheet link href=, css url(), and fetch() to a remote host would each make
        # the page depend on the network. A plain <a href> navigation anchor (the footer's
        # derived repo/tour/guide links) fetches nothing until a human clicks it, and is
        # allowed. The SVG namespace URI is a name, never a locator, and never fetched.
        fetchable = re.findall(
            r'(?:\bsrc\s*=\s*["\']https?://|<link[^>]{0,200}href\s*=\s*["\']https?://'
            r'|url\(\s*["\']?https?://|fetch\(\s*["\']https?://)',
            html,
        )
        self.assertEqual(fetchable, [], "the console must fetch nothing external")

        data = consolectl.payload()
        rendered = consolectl.render(data)
        self.assertEqual(rendered.count(consolectl.TEMPLATE_TOKEN), 0)
        marker = "const DATA = "
        idx = rendered.index(marker) + len(marker)
        parsed, _end = json.JSONDecoder().raw_decode(rendered[idx:])
        self.assertEqual(parsed, data)


class ServerTests(FixtureCase):
    """6. the hardened server: live payload, Host-header check, traversal defence."""

    def setUp(self) -> None:
        super().setUp()
        self.server = console.make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def _get(self, path: str, host_header: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {"Host": host_header} if host_header else {}
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, body
        finally:
            conn.close()

    def test_live_console_json(self):
        status, body = self._get("/live/console.json")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["mode"], "live")
        self.assertIsNotNone(data["server_ts"])

    def test_bad_host_header_is_rejected(self):
        status, _body = self._get("/live/console.json", host_header="evil.example")
        self.assertEqual(status, 403)

    def test_path_traversal_does_not_escape_console_dir(self):
        # policy.json is a real, shipped file, but it is not in read_allowlist and lives
        # outside console/ - a correct containment check must refuse it regardless of the
        # traversal sequence used to reach it.
        status, body = self._get("/../config/policy.json")
        self.assertNotEqual(status, 200)
        self.assertNotIn(b"fallback_protected", body)


class CLIVerdictTests(FixtureCase):
    """Every consolectl.py command prints exactly one <TAG>_OK|WARN|FAIL token (the same
    convention test_statectl.TestVerdictAndUsage enforces for statectl.py's commands),
    and `payload`'s stdout stays pure JSON - the token goes to stderr instead."""

    def setUp(self) -> None:
        super().setUp()
        _install_template(self)

    def _run(self, *argv):
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = consolectl.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _assert_exactly_one_token(self, combined: str):
        tokens = [t for t in ("CONSOLE_OK", "CONSOLE_WARN", "CONSOLE_FAIL") if t in combined]
        self.assertEqual(len(tokens), 1, f"expected exactly one verdict token, got {tokens} in: {combined!r}")

    def test_build_prints_one_verdict_token(self):
        code, out, err = self._run("build")
        self.assertEqual(code, 0)
        self._assert_exactly_one_token(out + err)

    def test_payload_stdout_is_pure_json_verdict_on_stderr(self):
        code, out, err = self._run("payload")
        self.assertEqual(code, 0)
        json.loads(out)  # must parse cleanly - no verdict token mixed into stdout
        self._assert_exactly_one_token(err)

    def test_open_prints_one_verdict_token(self):
        code, out, err = self._run("open")
        self.assertEqual(code, 0)
        self._assert_exactly_one_token(out + err)


class PortDerivationTests(FixtureCase):
    """Port "auto" derives from the folder name: stable, in range, and never one shared
    default for every adoption on a machine. An explicit port stays a decided-once value."""

    def test_explicit_integer_wins(self):
        self.assertEqual(_lib.console_port({"port": 7300}), 7300)
        self.assertEqual(_lib.console_port({"port": "7301"}), 7301)

    def test_auto_and_absent_derive_the_same_stable_port(self):
        a = _lib.console_port({"port": "auto"})
        b = _lib.console_port({})
        self.assertEqual(a, b)
        self.assertTrue(7100 <= a <= 7899, f"derived port {a} out of the documented range")

    def test_hostname_is_sanitized_folder_name(self):
        name = _lib.console_hostname()
        self.assertRegex(name, r"^[a-z0-9-]+\.localhost$")

    def test_auto_walks_past_a_collision(self):
        cfg = dict(_lib.load_config("console"), port="auto")
        self.write_config("console", cfg)
        base = _lib.console_port(cfg)
        try:
            blocker = console.make_server("127.0.0.1", base)
        except OSError:
            self.skipTest(f"derived port {base} already taken on this machine")
        try:
            server = console.bind_server("127.0.0.1", cfg, None)
            try:
                self.assertEqual(server.server_address[1], base + 1)
            finally:
                server.server_close()
        finally:
            blocker.server_close()

    def test_explicit_config_port_still_fails_loudly(self):
        blocker = console.make_server("127.0.0.1", 0)
        port = blocker.server_address[1]
        cfg = dict(_lib.load_config("console"), port=port)
        self.write_config("console", cfg)
        try:
            with self.assertRaises(OSError):
                console.bind_server("127.0.0.1", cfg, None)
        finally:
            blocker.server_close()

    def test_folder_localhost_host_header_is_allowed_and_others_stay_refused(self):
        server = console.make_server("127.0.0.1", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for host_header, expected in (
                    (f"anything.localhost:{port}", 200),
                    (f"evil.example.com:{port}", 403)):
                with self.subTest(host=host_header):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.request("GET", "/live/console.json", headers={"Host": host_header})
                    res = conn.getresponse()
                    res.read()
                    conn.close()
                    self.assertEqual(res.status, expected)
        finally:
            server.shutdown()
            server.server_close()


class SystemEndpointTests(FixtureCase):
    """/live/system.json is opt-in and live-only: 404 when the monitor is off (nothing to
    distinguish it from a route that does not exist), a fresh sample when on."""

    def _serve(self):
        server = console.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, server.server_address[1]

    def _get(self, port: int, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        res = conn.getresponse()
        body = res.read()
        conn.close()
        return res.status, body

    def test_disabled_monitor_is_a_404(self):
        cfg = dict(_lib.load_config("console"), monitor={"enabled": False})
        self.write_config("console", cfg)
        server, port = self._serve()
        try:
            status, _body = self._get(port, "/live/system.json")
            self.assertEqual(status, 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_enabled_monitor_serves_a_sample(self):
        cfg = dict(_lib.load_config("console"), monitor={"enabled": True})
        self.write_config("console", cfg)
        server, port = self._serve()
        try:
            status, body = self._get(port, "/live/system.json")
            self.assertEqual(status, 200)
            sample = json.loads(body)
            for key in ("os", "cpu_percent", "load1", "ram", "gpu"):
                self.assertIn(key, sample)
        finally:
            server.shutdown()
            server.server_close()


class SysmonTests(unittest.TestCase):
    def test_snapshot_never_raises_and_keeps_its_shape(self):
        import sysmon
        sample = sysmon.snapshot(interval=0.05)
        self.assertEqual(set(sample), {"os", "cpu_percent", "load1", "ram", "gpu"})
        if sample["ram"] is not None:
            self.assertIn("total", sample["ram"])
            self.assertIsInstance(sample["ram"]["total"], int)
        if sample["gpu"] is not None:
            self.assertIn("vram_total", sample["gpu"])


class PortCollisionTests(FixtureCase):
    """Port 7717 shipped as every project's default, so the second adoption on one machine
    lost the bind every session - silently, because the failure went only to a log nobody
    reads. A refused bind must be loud and name the one-line fix."""

    def test_bind_failure_exits_with_a_named_fix(self):
        import contextlib
        import io
        blocker = console.make_server("127.0.0.1", 0)
        port = blocker.server_address[1]
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = console.main(["--port", str(port)])
            self.assertEqual(code, 2)
            self.assertIn("CONSOLE_FAIL", err.getvalue())
            self.assertIn("console.json", err.getvalue(),
                          "the failure message must name where the port is decided")
        finally:
            blocker.server_close()



if __name__ == "__main__":
    unittest.main()
