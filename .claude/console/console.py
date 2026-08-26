#!/usr/bin/env python3
"""console.py - hardened stdlib HTTP server for the console.

Serves the single-file console (console.html, built by consolectl.py's `build` command)
plus live data at /live/console.json. Binds loopback only and refuses anything else. Serves
ONLY the console/ directory plus the explicit read_allowlist in config/console.json - NEVER
the repo root. (The source system this was extracted from served its whole repo on
localhost and exposed .env; that mistake is not repeated here.)

Two defences worth reading before touching this file:
  - path containment: every requested path is resolved() (collapsing any `..`) and then
    checked for containment inside console/ or inside one of the allowlisted files'
    resolved paths. The check operates on canonical paths, so it does not matter what
    literal traversal sequence a request used to get there.
  - Host header check: rejects any request whose Host is not 127.0.0.1[:port] or
    localhost[:port], defeating DNS-rebinding attacks that would otherwise let a remote
    page's browser-side JS reach this loopback-only server through the victim's own
    resolver.

Stdlib only, forever - see _lib.py's module docstring.
"""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import _lib  # noqa: E402
import consolectl  # noqa: E402

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

LOOPBACK_HOSTNAMES = ("localhost",)


def _is_loopback_host(host: str) -> bool:
    if host in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_request_path(url_path: str) -> Path | None:
    """Map a request path to an allowed on-disk file, or None if disallowed or missing.

    Every candidate is resolve()'d (which collapses `..` segments) before the containment
    check runs, so the check is correct regardless of the literal traversal sequence used
    to build the request path.
    """
    project_root = _lib.project_root().resolve()
    console_dir = _lib.console_dir().resolve()

    rel = url_path.lstrip("/")
    if rel == "":
        rel = "console.html"

    candidate = (console_dir / rel).resolve()
    if _is_within(candidate, console_dir) and candidate.is_file():
        return candidate

    requested = (project_root / rel).resolve()
    if not _is_within(requested, project_root):
        return None

    cfg = _lib.load_config("console")
    for entry in cfg.get("read_allowlist") or []:
        allowed = (project_root / str(entry)).resolve()
        if not _is_within(allowed, project_root):
            continue  # a misconfigured allowlist entry escaping the project is ignored
        if requested == allowed and allowed.is_file():
            return allowed
    return None


class ConsoleServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # POSIX SO_REUSEADDR eases TIME_WAIT rebinds and still refuses an actively-held port.
    # On Windows the SAME flag lets a second bind silently steal a port that is in use,
    # which defeats the loud-collision contract in main() - so there it stays off and
    # server_bind() asks for exclusive use instead.
    allow_reuse_address = sys.platform != "win32"
    once = False  # set by make_server(once=True): shut down after the first request

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ConsoleHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ConsoleHTTP/1"

    def log_message(self, fmt: str, *args) -> None:  # quiet; main() prints one startup line
        pass

    # -- Host header (DNS-rebinding defence) -------------------------------------------

    def _host_is_allowed(self) -> bool:
        host_header = self.headers.get("Host", "")
        hostname = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
        hostname = hostname.strip("[]").lower()
        # *.localhost is loopback by construction: browsers resolve every such name locally
        # (RFC 6761), never through a resolver an attacker could poison - so accepting the
        # whole family keeps the DNS-rebinding defence intact while allowing the
        # folder-name URL (http://<project>.localhost:<port>/).
        return hostname in ("127.0.0.1", "localhost", "::1") or hostname.endswith(".localhost")

    # -- routing -------------------------------------------------------------------------

    def do_GET(self) -> None:
        try:
            if not self._host_is_allowed():
                self.send_error(403, "Forbidden: bad Host header")
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)

            if path == "/live/console.json":
                self._serve_payload()
                return

            if path == "/live/system.json":
                self._serve_system()
                return

            target = resolve_request_path(path)
            if target is None:
                self.send_error(404, "Not Found")
                return
            self._serve_file(target)
        finally:
            self._maybe_shutdown()

    def do_POST(self) -> None:
        """Exactly ONE action, deliberately.

        The console's contract is command emission, never state mutation - a control surface
        that mutates behind the user's back becomes a second source of truth. Analysis is the
        one carve-out: it reads the record and writes only labeled products into the
        out-of-repo RECORD_ROOT/analysis/, touching no repo state, so a button for it does not
        breach the contract. No parameters cross from the browser except a bounded limit; the
        endpoint refuses when the engine is unconfigured rather than half-running.
        """
        try:
            if not self._host_is_allowed():
                self.send_error(403, "Forbidden: bad Host header")
                return
            parsed = urllib.parse.urlsplit(self.path)
            if urllib.parse.unquote(parsed.path) != "/live/analyze":
                self.send_error(404, "Not Found")
                return

            import obsctl
            status = obsctl.analyze_status()
            if not status.get("configured"):
                self._send_json(409, {
                    "ok": False,
                    "reason": "analysis engine not configured",
                    "how": ("Set analyze.base_url and analyze.model in "
                            ".claude/config/observe.json, and for remote endpoints export "
                            "ANALYZE_API_KEY in your shell. See the STORY tab's setup guide."),
                })
                return
            if (status.get("run") or {}).get("state") == "running":
                self._send_json(409, {"ok": False, "reason": "an analysis run is already in progress"})
                return

            limit = 0
            query = urllib.parse.parse_qs(parsed.query)
            if "limit" in query:
                try:
                    limit = max(0, min(int(query["limit"][0]), 10000))
                except ValueError:
                    limit = 0

            argv = [sys.executable, str(TOOLS_DIR / "obsctl.py"), "analyze"]
            if limit:
                argv += ["--limit", str(limit)]
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(_lib.project_root()))
            self._send_json(202, {"ok": True, "state": "started",
                                  "poll": "analysis.run in /live/console.json"})
        finally:
            self._maybe_shutdown()

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_system(self) -> None:
        """Live system metrics, opt-in and live-only: the samples exist ONLY behind this
        endpoint - they never enter the built payload, so neither the committed console.html
        nor the published demo can carry a machine's stats, and the build write-gate never
        sees a volatile number."""
        mon = _lib.load_config("console").get("monitor") or {}
        if not mon.get("enabled", False):
            self.send_error(404, "Not Found")
            return
        self._send_json(200, _system_sample())

    def _serve_payload(self) -> None:
        data = consolectl.payload(live=True)
        data["server_ts"] = _lib.utc_now()
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "Not Found")
            return
        ctype = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _maybe_shutdown(self) -> None:
        if getattr(self.server, "once", False):
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def make_server(host: str, port: int, once: bool = False) -> ConsoleServer:
    if not _is_loopback_host(host):
        raise _lib.LibError(
            f"refusing to bind non-loopback host {host!r}; the console must stay on loopback"
        )
    server = ConsoleServer((host, port), ConsoleHandler)
    server.once = once
    return server


# Sampling costs real milliseconds (nvidia-smi, counter reads); polls reuse a short-lived
# sample instead of probing on every 5s tick.
_SYSTEM_CACHE: dict = {"ts": 0.0, "data": None}
_SYSTEM_TTL_SECONDS = 10.0


def _system_sample() -> dict:
    import time
    now = time.monotonic()
    if _SYSTEM_CACHE["data"] is None or now - _SYSTEM_CACHE["ts"] > _SYSTEM_TTL_SECONDS:
        import sysmon
        _SYSTEM_CACHE["data"] = sysmon.snapshot()
        _SYSTEM_CACHE["ts"] = now
    return _SYSTEM_CACHE["data"]


def _config_port_is_explicit(cfg: dict) -> bool:
    try:
        int(cfg.get("port", "auto"))
        return True
    except (TypeError, ValueError):
        return False


def bind_server(host: str, cfg: dict, cli_port: int | None, once: bool = False) -> ConsoleServer:
    """Bind the decided port. An explicit port (a --port flag, or an integer in
    console.json) is a decided-once value: a collision there fails loudly with the named
    fix, never silently elsewhere. The shipped "auto" derives the port from the folder name
    and, on the rare hash collision, walks a few slots forward - the startup line and the
    session-start hook print whatever actually bound."""
    explicit = cli_port is not None or _config_port_is_explicit(cfg)
    base = cli_port if cli_port is not None else _lib.console_port(cfg)
    candidates = [base] if explicit else [base + i for i in range(10)]
    last_exc: OSError | None = None
    for candidate in candidates:
        try:
            return make_server(host, candidate, once=once)
        except OSError as exc:
            last_exc = exc
    raise last_exc if last_exc else OSError(f"could not bind any of {candidates}")


def main(argv: list | None = None) -> int:
    cfg = _lib.load_config("console")
    parser = argparse.ArgumentParser(prog="console.py", description="Serve the dot-claude-iff console.")
    parser.add_argument("--host", default=cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=None,
                        help="explicit port (default: console.json, or derived from the folder name)")
    parser.add_argument("--pidfile", default=None, help="write the server pid here while running")
    parser.add_argument("--once", action="store_true", help="handle exactly one request, then exit (tests)")
    args = parser.parse_args(argv)

    try:
        server = bind_server(args.host, cfg, args.port, once=args.once)
    except _lib.LibError as exc:
        print(f"CONSOLE_FAIL: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # An explicit port that collides fails loudly with the named fix; "auto" only lands
        # here when the whole candidate walk was taken, which means something is squatting
        # a broad range. Either way the message names where the port is decided.
        wanted = args.port if args.port is not None else _lib.console_port(cfg)
        print(f"CONSOLE_FAIL: cannot bind http://{args.host}:{wanted}/ ({exc}). "
              f"The port is likely held by another project's console. Set a unique 'port' "
              f"in .claude/config/console.json - decided once per project - and restart, "
              f"or keep \"auto\" to derive one from the folder name.",
              file=sys.stderr)
        return 2

    pidfile = Path(args.pidfile) if args.pidfile else None
    if pidfile:
        pidfile.write_text(str(os.getpid()), encoding="utf-8")

    bound_host, bound_port = server.server_address[:2]
    print(f"CONSOLE serving http://{bound_host}:{bound_port}/ "
          f"(http://{_lib.console_hostname()}:{bound_port}/console.html)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if pidfile:
            try:
                pidfile.unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
