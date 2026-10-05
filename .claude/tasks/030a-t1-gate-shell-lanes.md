# Task: T1 - protected tree on the Bash and PowerShell lanes

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: debt (security) · Shared contracts: `030a-00-milestone.md`

## Goal

A sub-agent cannot write into the protected tree through any shell lane, the same as through Write/Edit.

**Definition of done:** for every protected prefix and for both Bash and PowerShell payloads, a sub-agent mutation (redirect, copy, move, delete, tee, in-place sed, a python one-liner that opens a file for writing, Set-Content, Out-File, Copy-Item) is denied, a granted agent is allowed, the main session is allowed, and read-only commands still pass.

**Test:** `python3 .claude/tools/tests/run_tests.py test_hooks -q`

## Context

- Today `hooks/policy_gate.py` checks the protected tree in the Write/Edit lane only; the shell lane enforces `deny_bash` and the record ring. The record ring already has shell-mutator detection to reuse.
- `policy.json` `default.write_paths` is never read; either make the gate read it or delete it (delete preferred; record which).
- Add an every-identity deny (like the record ring) for `.claude/state/ritual-ticket.json` in both lanes; T3 relies on it.
- Add a `builder` grant: `.claude/tools/`, `.claude/console/`, `.claude/skills/` (not hooks, config, agents, protocols, settings).
- Fail closed: a shell command that cannot be parsed and mentions a protected prefix next to a mutator is denied for sub-agents.
- Known false positive to fix while here: the record-ring shell check fires on a main-session heredoc that merely mentions the record folder name in prose next to words like copy or remove. Narrow it to real path arguments without opening a hole, with a test for each direction.

## Plan

- [ ] Shell-lane protected-tree check with per-agent grants, done when: new tests pass
- [ ] One test per lane per mutator family (L-10), incl. Windows path forms (backslash, drive letter, mixed case) (L-9), done when: tests pass on Windows
- [ ] Ticket-file deny for all identities, done when: test asserts main session is denied too
- [ ] Record-ring false positive narrowed, done when: prose-mention test passes and every existing record-ring deny test still passes
- [ ] `policy.json` comment and CLAUDE.md protected-tree list corrected, done when: grep shows tools/, skills/, console/ listed

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T1.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

