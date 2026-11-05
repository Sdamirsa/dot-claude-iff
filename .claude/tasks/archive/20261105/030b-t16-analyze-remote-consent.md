# Task: T16 - explicit consent and redaction before record text goes to a remote endpoint

_Created 2026-11-04 · Status: done_

Milestone: M-0.3.0-beta.1 · Closes: PR-8 · Shared contracts: `030b-00-milestone.md`

## Goal

`obsctl analyze` never sends record text to a non-local endpoint unless the human has set a
consent knob, and what it sends has key-shaped strings redacted first.

**Definition of done:** a new knob `analyze.remote_consent` (bool, default false) in
`.claude/config/observe.json` with a card in `registry.json`; when the endpoint is remote
(the existing `local` rule in `analyze_status`) and the knob is false, `analyze` sends
nothing, prints a consent line naming the event count, the endpoint host, the model, the
knob to set and the fact that secrets are redacted, and exits 1 (a gate fails closed);
when the knob is true the same line is printed before sending; local endpoints never need
it. Every serialized batch passes through a redactor that replaces matches of
`checkctl.SECRET_PATTERNS` with `[REDACTED:<name>]` before the request is built, for local
and remote alike. `analyze_status()` carries `remote_consent` and `consent_required`
(booleans) and the console payload shows them unchanged. `--dry-run` reports consent state
too and still sends nothing.

**Test:** `python3 .claude/tools/tests/run_tests.py test_analyze -q`

## Context

- Proposal PR-8. The engine: `cmd_analyze`, `_analyze_one_batch`, `_serialize_batch`,
  `analyze_status` in `.claude/tools/obsctl.py`. `SECRET_PATTERNS` lives in
  `.claude/tools/checkctl.py` (a tuple of (name, compiled regex)); import it, do not copy it.
- Existing tests in `tests/test_analyze.py` fake the endpoint with a local HTTP server; the
  "remote" case must be faked by the base_url text, not by a real network call: patch
  `_http_post_json` or point at a non-local base_url and assert nothing was posted.
- The record is radioactive (CLAUDE.md law 3): the consent line names counts and hosts,
  never event content. Never write under the project root from `analyze`.
- Registry card shape: see `memory.distribution.enabled` in `.claude/config/registry.json`;
  a knob card names its config file and path. `checkctl probe` and the contracts test
  (`tests/test_contracts.py`) may assert registry/config parity: run them.

## Plan

- [x] Knob + card, done when: `test_contracts` and `checkctl probe` stay green
- [x] Redactor applied in `_serialize_batch` (or just after), done when: a test feeds an event
      holding an `sk-` key and asserts the posted body has `[REDACTED:` and not the key
- [x] Consent gate, done when: tests for remote+false (exit 1, nothing posted, line printed),
      remote+true (posted, line printed), local (no line required, posted) pass
- [x] `analyze_status` booleans and dry-run text, done when: tests assert both
- [x] `reference/secrets.md` or the observe.json `_comment` names the knob, done when: grep

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T16.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

Done 2026-11-04: `analyze.remote_consent` knob with a registry card; a remote endpoint without consent sends nothing and exits 1; every batch is redacted with checkctl's secret patterns; status carries the two booleans; the console returns 409 without consent. The lead tightened the local-host rule to the parsed hostname. Closed PR-8.
