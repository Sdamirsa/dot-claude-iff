# Task: T5 - fableous mode: lead, builders, scouts, validated handoff

_Created 2026-10-05 · Status: done_

Milestone: M-0.3.0-alpha · Closes: #11 · Shared contracts: `030a-00-milestone.md`

## Goal

With mode `fableous-orchestrated`, the top model plans, designs and stays accountable while cheaper agents build and research, every handoff is a JSON envelope that is checked automatically, and the cost split is visible.

**Definition of done:** `protocols/orchestration.md` defines roles, routing (lead = session model; `builder` = opus; `scout` = sonnet; verifier = inherit), task-card format (the task file) and the dispatch/handoff steps; `agents/builder.md` and `agents/scout.md` exist with model and effort pins mirrored in the registry and a lint that compares the values; `checkctl handoff <task_id> [--run]` validates the envelope (required fields, STATUS enum, files_changed exist, test command + result present, `--run` re-executes the test) and the stub field name agrees between protocol, code and tests; in `fableous-orchestrated`, `statectl task <id> --status done` refuses without a valid envelope whose test passed; a SubagentStop check blocks a builder that stops with no valid envelope (guarded against loops, fail-open on malformed payload); a delegation nudge reminds the lead after N main-session code edits with no dispatch (knob, 0 = off, advisory only); `obsctl report --by agent` groups tokens by agent type.

**Test:** `python3 .claude/tools/tests/run_tests.py test_orchestration test_obsctl test_hooks -q`

## Context

- Depends on T1 (builder grant, shell lanes) and T4 (mode).
- Extend the existing handshake envelope; do not invent a second format. Describe the schema in `protocols/handshake.md` and validate with stdlib code (no jsonschema dependency).
- Envelope additions: `task_id`, `agent`, `model`, `files_changed[]`, `tests[] {command, exit_code, summary}`, `needs_main[]` (changes the builder could not make, as path + diff).
- Nothing of this is active in `freestyle` or `guided-solo`.
- Epics/Jira: not added; milestones + tasks cover it.

## Plan

- [x] Protocol + two agents + registry pins + value-comparing lint, done when: lint test fails on a deliberate mismatch
- [x] `checkctl handoff` validator, done when: valid/invalid fixture envelopes behave, `--run` executes
- [x] `statectl task --status done` guard in fableous-orchestrated, done when: test passes in all three modes
- [x] SubagentStop check, done when: hook tests cover block, loop guard, other agent types untouched
- [x] Delegation nudge, done when: counter test passes and it never blocks
- [x] `obsctl report --by agent`, done when: fixture record test passes
- [x] Adopt + ROUTE mention the mode, cards, probe, done when: `test_contracts` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T5.json`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

