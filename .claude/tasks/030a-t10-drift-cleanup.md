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
- Tool count wording ('six core tools') must match reality without a volatile number.

## Plan

- [ ] Adopt skill fixes, done when: text tests pass
- [ ] Dead knobs gone, done when: `test_contracts` green and grep finds no reference
- [ ] Brand file home-only, done when: `test_dist` asserts absence from both zips
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

