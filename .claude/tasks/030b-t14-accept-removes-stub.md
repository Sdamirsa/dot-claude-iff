# Task: T14 - accept removes the task's stub after a successful merge

_Created 2026-11-04 · Status: done_

Milestone: M-0.3.0-beta.1 · Closes: PR-10 · Shared contracts: `030b-00-milestone.md`

## Goal

A task that `statectl accept` has merged no longer shows as in flight: its
`.claude/state/handshakes/<id>.stub.json` is removed in the same step that removes the
worktree and branch.

**Definition of done:** after `statectl.py accept <id>` reaches a successful merge, the main
checkout's `<id>.stub.json` is gone and the builder's `<id>.json` envelope (merged from the
worktree) is kept; a refused or conflicted accept leaves the stub untouched; a missing stub is
not an error; the progress model no longer counts the task as an agent in flight.

**Test:** `python3 .claude/tools/tests/run_tests.py test_orchestration test_progress -q`

## Context

- The verifier found T10, T12 and T13 stubs left behind after their accepts (proposal PR-10).
- `_accept_merge` in `.claude/tools/statectl.py` is the only place that knows the merge
  succeeded; `_lib.stub_path(task_id)` names the stub; `_lib.write_stub` writes it. The stub
  lives in the MAIN checkout's state, never in the worktree.
- Stubs feed "agents in flight" in `tools/progress.py` and the console: read how they are
  counted before changing anything, and do not change the counting rule itself.
- An existing test, `test_dispatch_cuts_the_worktree_writes_the_stub_and_prints_the_brief`,
  shows the fixture that drives a real dispatch; there is an accept test nearby that ends in a
  merge: extend that path rather than inventing a second git fixture.

## Plan

- [ ] Remove the stub after the merge, before the journal note, done when: a test accepts a
      dispatched task end to end and asserts the stub is gone and the envelope remains
- [ ] Refusal keeps the stub, done when: a test runs accept with a failing handoff and asserts
      the stub still exists
- [ ] Docstring of `cmd_accept` and the orchestration protocol's accept step (one clause) name
      the stub removal, done when: grep finds "stub" in both

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T14.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
