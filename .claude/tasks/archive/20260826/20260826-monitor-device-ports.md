# Task: system monitor + device identity + derived console ports

_Created 2026-08-26 · Status: done_

## Goal

Ship the two maintainer-approved features: (A) console ports derived from the repo folder
name with `<folder>.localhost` URLs, ending per-project port decisions; (B) an optional live
system-monitor strip (CPU/RAM/GPU/VRAM) on the console's NOW tab plus a device-identity
record that loudly flags a machine change at session start.

**Definition of done:** full test suite passes; live console serves `/live/system.json` and
renders the strip between needs-human and journal; `statectl device` names this machine;
kits ship `port: "auto"` and `monitor.enabled: false`; both NH decisions resolved.

## Context

- Approved via NH-20260826113413-f456 and NH-20260826113413-083e (queue), design settled in
  session 2026-08-26: stdlib probes only, stats live-only (never in the built payload or the
  demo), no username stored (12-hex fingerprint + user-named alias), posix stays case-exact.
- This repo's decided port 7146 EQUALS the derived value (7100 + sha256('dot-claude-iff') %
  800), so switching config to "auto" changes nothing user-visible here.
- distctl already normalizes shipped console.json (port); extend, don't duplicate.

## Plan

- [x] A1 `_lib.console_port()` + `console_hostname()`, done: PortDerivationTests pass
      (explicit wins, auto/absent derive 7100-7899, deterministic).
- [x] A2 console.py bind_server walk-on-auto + `*.localhost` Host accepted, done:
      PortCollisionTests still pass; walk + Host-header tests pass.
- [x] A3 hook prints folder URL; distctl ships "auto" + monitor-off; START-HERE reworded,
      done: test_dist auto-port/monitor test passes.
- [x] B1 sysmon.py, done: SysmonTests pass; live sample on this machine shows CPU/RAM/GPU.
- [x] B2 /live/system.json + NOW strip, done: HTTP 200 with metrics on port 7146; strip
      rendered in browser between Needs-human and Journal ("CPU 12% · RAM 26.1/31.7 GB ·
      GPU 12% · VRAM 3.1/8.0 GB"). Poll pauses while the tab is hidden, by design.
- [x] B3 machine identity, done: TestDeviceIdentity passes; this machine named
      'sdami-win11' (fp c363ed8aade7); machine_check() returns None here now.
- [x] Registry cards added (console.port updated, console.monitor.enabled new); CHANGELOG
      Unreleased updated; suite 225 passed / 0 failed / 1 skipped; both NH items resolved.

## Checkpoint

- **Last completed:** everything; live-verified end to end. Mistake logged: stray
  console.pid/log briefly written into .claude-iff (wrong dir helper), removed, journaled.
- **Next action:** archive this task in the next /project-memory (2e).
- **State files:** `.claude/state/machine.json`, `.claude/config/console.json`,
  `.claude/config/registry.json`.
- **Updated:** 2026-08-26

**Outcome:** shipped. Console at http://dot-claude-iff.localhost:7146/console.html (derived
port; the session-start block prints it), System strip live, device identity armed.
