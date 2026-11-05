# Task: T15 - generator freshness sees hand-edited outputs; build exit runs each suite once

_Created 2026-11-04 · Status: done_

Milestone: M-0.3.0-beta.1 · Closes: PR-9, PR-11 · Shared contracts: `030b-00-milestone.md`

## Goal

Two `checkctl.py` fixes. (1) A derived file edited by hand after its generator ran is
reported stale, because the ledger's `output_hash` is compared, not only `inputs_hash`.
(2) `phase-exit --from build` runs each distinct Test command once and skips a command that
a full-suite command already run covers, so thirteen tasks sharing the suite no longer rerun
it a dozen times.

**Definition of done:** `generator_freshness_report` returns WARN "output changed since the
last run (hand-edited or rebuilt outside the ritual)" when the output's hash differs from the
ledger's `output_hash` while the inputs still hash the same; a ledger entry without
`output_hash` (written before this change) is not flagged for that reason. `exit_build`
treats `python3 .claude/tools/tests/run_tests.py <modules...> [-q]` as covered when the
same runner with no module arguments is among the milestone's Test commands: the full run
executes once, the covered tasks get its status with a message saying "covered by the full
suite", and full-suite commands run first. Commands that are not the suite runner, or whose
runner path differs, are never treated as subsets.

**Test:** `python3 .claude/tools/tests/run_tests.py test_ritual test_phases -q`

## Context

- Proposals PR-9 and PR-11 (`statectl proposal list`). The ledger is written by
  `stamp_generator` in `.claude/tools/checkctl.py`, which already records `output_hash`; the
  report (`generator_freshness_report`) never reads it.
- `exit_build` already runs identical commands once (`ran` dict, test
  `test_a_shared_test_command_runs_once` in `tests/test_phases.py`). The subset rule must be
  a pure function of the list of commands, `shlex`-parsed, tested on its own: same runner
  path (compare with `os.path.normcase` after `as_posix()`), one side has no positional
  module arguments, flags like `-q`/`-v` ignored.
- The ritual's CHECK phase shows these findings; it must stay WARN, never FAIL: stale at
  CHECK time is normal (POLISH rebuilds). Do not touch `stamp_generator`'s format.
- Keep hunks local in `checkctl.py`: the lead merges four branches this wave.

## Plan

- [x] Output-hash comparison, done when: two new tests in `test_ritual.py` (hand edit flagged;
      legacy entry without output_hash not flagged for that reason) pass
- [x] Subset rule as a pure function with its own tests (covered; not covered for a different
      runner; not covered when both name modules), done when: `test_phases` green
- [x] `exit_build` uses it and reports "covered by the full suite", done when: a test with
      three tasks (full suite, a module subset, an unrelated command) shows exactly two runs
- [x] The `generators` subcommand's listing shows the new reason, done when: a test greps it

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T15.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

Done 2026-11-04: generator freshness compares the output hash too (a hand-edited or out-of-ritual rebuild is a WARN naming the cause); `exit_build` runs full-suite commands first and marks subset commands as covered. Closed PR-9 and PR-11.
