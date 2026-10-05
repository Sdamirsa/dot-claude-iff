# Task: T3 - the ritual is the user's: ticket, nudges, one command with ROUTE

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: #9 · P5 · Shared contracts: `030a-00-milestone.md`

## Goal

Only a user-typed `/project-memory` (or `/adopt`) can open or complete a ritual run, every nudge says so, and the one command ends by setting up the next session.

**Definition of done:** a prompt hook writes the ticket on a user-typed `/project-memory` or `/adopt` (handles both the UserPromptSubmit `prompt` shape and the UserPromptExpansion `command_name` shape); `checkctl run` opening a run and `checkctl complete` refuse without a fresh ticket and say what the user should type; phases of an already-open run continue while the ticket is fresh; `complete` consumes it; expiry is a knob; `probe`, `status`, `generators`, `doctor`, `phase-exit`, `handoff` need no ticket; every instruction that told the agent to run the ritual now tells it to ask the user; the SessionStart nudge counts sessions correctly; the skill ends with a ROUTE step (AskUserQuestion: next phase with its exit check, mode for next session, optional maturation pass) and the unparsed `--hard` flag is gone with its content folded into ROUTE/plan.

**Test:** `python3 .claude/tools/tests/run_tests.py test_ritual test_hooks -q`

## Context

- Depends on T1 (ticket file deny for every identity) and T4 (mode/phase commands).
- The hook is bash + python3 stdlib. If the hook breaks, no ticket is written and the ritual refuses (fail closed overall).
- The ticket is not cryptographic; the gate deny plus the record make forging visible. Say so in the skill.
- Ticket records `{skill, ts, session_id}`; `memory-run.json` gains `invoked_by: user-ticket`.
- CI and tests: fixtures write a ticket directly; `distctl build` is not gated.
- The exact UserPromptExpansion field names are unverified from docs; code defensively and list 'type /project-memory and confirm the ritual opens' in the human test checklist.
- Nudge texts to change: session-start.sh, CLAUDE.md, CLAUDE.template.md, both READMEs, adopt and plan-task skills, protocols, task template, distctl FRESH_STATUS.

## Plan

- [ ] Prompt hook + settings.json wiring, done when: hook tests feed both payload shapes and a non-matching prompt
- [ ] Ticket check in checkctl, done when: refuse/accept/expire/consume tests pass
- [ ] Reworded nudges + fixed session counter, done when: a grep test finds no agent-directed 'run /project-memory' and the counter test passes
- [ ] ROUTE step + `--hard` folded, done when: skill text test passes and glossary/evolution references are updated
- [ ] Cards, registry knob, probe, done when: `test_contracts` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T3.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

