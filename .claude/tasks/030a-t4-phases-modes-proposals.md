# Task: T4 - phases, modes, proposal box, tasks under milestones

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: #13 · Shared contracts: `030a-00-milestone.md`

## Goal

The project always knows its phase and mode, prints the matching contract, checks phase exits, and has a durable box for ideas that are out of scope right now.

**Definition of done:** the `mode`, `phase` and proposal contracts in the milestone file are implemented exactly; `JOURNAL_ACTIONS` and the projector cover the new actions; `checkctl phase-exit` passes and fails on fixtures for all four phases; `freestyle` runs no exit check; SessionStart prints mode, phase and a contract of at most five lines (nothing extra in freestyle); the console shows a mode/phase badge and a proposals list on WORK; `/plan-task` registers each task in the journal under a milestone; the task template has the Status line and the `**Test:**` line.

**Test:** `python3 .claude/tools/tests/run_tests.py test_phases test_statectl test_console -q`

## Context

- Existing free-text `session_start.phase` is replaced by the `phase` action as the source of `session.phase`; keep reading old journals without error.
- Phase contracts live in one data file (`.claude/config/phases.json`: per phase `contract` lines, `exit` check name) so the hook, console and docs read the same text. Registry cards required.
- The build exit check runs each task's Test command (the single backticked command on the `**Test:**` line) with a timeout knob, from the repo root, without a shell where possible; report per-task PASS/FAIL with the tail of output.
- Deploy contract: setup (`checkctl doctor`), debug, serve; fixes only; new ideas -> `statectl proposal add`. A `deploy_drift` WARN in the ritual check lists files added outside tests/docs since the phase began.
- Proposals store is append-only JSONL like needs-human; add to `mapctl.KNOWN_STORES` with a card.

## Plan

- [ ] `statectl mode|phase|proposal` + `task --milestone` + projections, done when: statectl tests pass
- [ ] `phases.json` + `checkctl phase-exit` (4 checks) + `deploy_drift`, done when: pass/fail fixtures per phase
- [ ] SessionStart block, done when: hook tests cover freestyle/guided-solo/fableous-orchestrated output
- [ ] Console badge + proposals list, done when: console tests incl. empty project
- [ ] plan-task skill + template, done when: template parses under `check_task_reality` and the console reader
- [ ] Cards, registry, probe, glossary entries, done when: `test_contracts` and `test_mapctl` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T4.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

