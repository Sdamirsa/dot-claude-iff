# Task: T10 - drift, dead knobs, docs and the map

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: debt · P3 P4 · Shared contracts: `030a-00-milestone.md`

## Goal

What ships matches what the docs say, with no dead knobs and a truthful map.

**Definition of done:** adopt text no longer mentions a fixed port, the impossible settings.local.json copy, or a git-only manifest (the non-git kit path works); `memory.snapshot.*`, `phases.evolve`, `project_steps.generator` and the journal `config` action are gone with their cards and tests; the provider enum agrees between registry, observe.json and code; `reference/brand-identity.md` is home-only with a `test_dist` assertion; CLAUDE.md, the template, both READMEs, glossary and docs describe modes, phases, the ticket, proposals, export and secrets; the anatomist has reconciled every card; `checkctl probe` is all green.

**Test:** `python3 .claude/tools/tests/run_tests.py -q`

## Context

- Runs after every other build task is merged.
- `CLAUDE.md` still describes `temp-to-analyse/` as present; the folder is gone from this machine. Keep the gitignore line, reword the section.
- Gate false positives found by the verifier (envelope `.claude/state/handshakes/verify-T1-policy-gate.json`), both to fix with a test each and without loosening any existing deny test: (1) a sub-agent's interpreter heredoc that writes to an allowed path (e.g. its own envelope under `.claude/state/handshakes/`) is denied when its TEXT merely names a protected path; (2) a backslash Windows path through a folder named `GIT` trips the sub-agent git word match (the lookbehind excludes `/` and `.` but not a backslash).
- Ticket forging: any identity can still run `.claude/hooks/ritual-ticket.sh` by hand with a made-up payload and mint a ticket. The gate must deny executing that script through a shell lane for every identity (it is only ever launched by Claude Code as a prompt hook), with tests on both lanes.
- Human escape hatch for the ritual ticket: if the prompt hook never fires (older Claude Code, hooks not trusted yet), the user is locked out of their own ritual. Add `checkctl ticket --grant` that the HUMAN runs in their own terminal; the gate denies that subcommand on every shell lane for every identity, so no agent can run it. `doctor` and the refusal message name it. Tests on both lanes.
- `doctor` rows: hook scripts have LF endings, a shebang and (where the OS reports it) the exec bit; `settings.json` wires every hook file that exists; a note of the minimum Claude Code version the prompt-expansion hook needs if the docs state one.
- Kit self-test: a test (and so a CI step) that extracts the fresh zip into a temp folder, initialises git there, and runs the kit's own suite and `checkctl doctor` inside it. Whatever only passes in the home repo is a defect in what adopters receive.
- Orchestration protocol addendum: never resume a builder whose worktree no longer exists (check first, re-dispatch instead); builders never commit or stage the dist zips.
- `STATUS.md` still mentions `--hard`; it is rewritten by the ritual, so leave a note for the first `/project-memory`.
- `check_gitignore_shadowing` feeds git paths in text mode on Windows (trailing CR), so file-level patterns like `*.zip` never match: fix with a test.
- Tool count wording ('six core tools') must match reality without a volatile number.

## Plan

- [ ] Adopt skill fixes, done when: text tests pass
- [ ] Dead knobs gone, done when: `test_contracts` green and grep finds no reference
- [ ] Brand file home-only, done when: `test_dist` asserts absence from both zips
- [ ] Two gate false positives + shadowing CR bug, done when: one new test each and `test_hooks` green
- [ ] Ticket escape hatch, doctor rows, kit self-test, done when: new tests green and CI runs the kit self-test on both OS
- [ ] Docs pass, done when: a docs test greps each new command name in CLAUDE.md or README
- [ ] Anatomist reconciliation, done when: `mapctl lint` clean
- [ ] Zips rebuilt, done when: freshness test green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T10.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

