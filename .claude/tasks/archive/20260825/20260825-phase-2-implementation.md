# Task: Phase 2 - build the system to self-hosting

_Created 2026-08-25 · Status: done_

## Goal

Implement the design in `.claude/research/2026-08-24-system-design.md` (v3) until this repo runs
on its own system: every component present, the full test suite green, and `/project-memory`
runnable end to end. Done when `checkctl.py probe` is all-green and a full ritual (check, polish,
publish) completes on this repo.

## Context

Phase 1 settled eight decisions on 2026-08-25. Build order is §9 of the design doc: foundations
first, then gates, state, observability, console, cards, and the polish pass last.

## Plan

- [x] Step 0: foundation (`_lib.py`), configs, shared test fixture (done when: `_lib.py --paths-json`
      resolves the sibling record root and every config parses)
- [x] Step 0: hooks-fire smoke test (done when: `test_hooks.py` is green, including fail-closed
      policy denials and fail-open capture)
- [x] Step 2: policy gate + post-write-validate (done when: a sub-agent is denied the protected
      tree, the main session is not, and malformed JSON under `.claude/` is blocked)
- [x] Step 3: state and continuity - `statectl.py` (done when: `test_statectl.py` green, every
      journal action projected)
- [x] Step 4: observability - `obsctl.py` (done when: `test_obsctl.py` green, seal drops
      non-allowlisted keys, nested subagent transcripts are counted)
- [x] Ritual runner - `checkctl.py` (done when: `run --phase check` reports, publish refuses
      without polish in the same run id)
- [x] Step 5: console - `consolectl.py` + template + server (done when: `test_console.py` green,
      payload renders on an empty project, server rejects a bad Host header)
- [x] Step 6: cards + `mapctl.py` (done when: `mapctl lint` has zero ERRORs and `map.json`
      compiles) - 47 cards, 95 edges, 0 errors, 2 deliberate unplaced warnings
- [x] Step 7: remaining docs - plan-task, adopt, retro-analyst, verifier, glossary, CLAUDE
      template (done when: `checkctl.py probe` is all-green) - 37/37
- [x] Step 8: full ritual on this repo + final code review (done when: check, polish and publish
      all pass and the review's findings are triaged) - 3 critical and 7 important defects found
      and fixed, each with a regression test

## Checkpoint

- **Last completed:** the adversarial review is triaged and every finding fixed. 140 tests
  green with zero skips; probe 37/37; the full ritual runs as one transaction.
- **Next action:** use the system for a real session and run /project-memory at the end.
- **State files:** .claude/state/memory-run.json, .claude/state/generators.json, .claude/system-map/map.json
- **Updated:** 2026-08-25

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|----------------|--------|
| - | - | none open | - | - | - |

## Outcome

Built and self-hosting. 37 components, 140 tests, 47 map cards with 95 curated relations, one
console, one ritual. Twelve real defects were found before first use: six by running the system
against itself during integration, six more by an adversarial review that verified each one
empirically. Three were security-critical (a gate a large payload could switch off, a protected
tree that omitted the directory the gate executes from, and a record that silently discarded
concurrent events). Every fix landed with a regression test, and the three lessons worth keeping
are recorded as L-3, L-4 and L-5.

The most useful thing learned: the tests were originally written to confirm the design's
narrative rather than to attack it, and none of the review's ten findings was caught by the 125
tests that were green at the time.
