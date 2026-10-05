# Task: T7 - visibility setting and public export

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: #10 · Shared contracts: `030a-00-milestone.md`

## Goal

An adopter decides whether `.claude/` is public, the system acts on it, and publishing a clean copy to a public repo is one deliberate command.

**Definition of done:** `memory.json` `visibility` is `tracked` or `ignored` with a registry card; `/adopt` asks once, defaults to `ignored` when the remote is public (via `gh repo view --json visibility` when available, otherwise it asks), and writes the matching `.gitignore` block on every install path including the adopt kit; `gitignore_shadowing` is quiet under `ignored`; `distctl export --to <dir> [--dry-run]` copies git-tracked project files plus only the `.claude/` subset allowed by `.claude/config/publish.json`, always excluding state, logs, lessons, status, tasks, research, the committed record surface, local settings and env files, writes a manifest, drops files it exported previously that are no longer in the set, runs the secrets check on the output and refuses on FAIL, and never touches the target's `.git` or runs git writes; `reference/public-private.md` recommends a private working repo with a public release repo.

**Test:** `python3 .claude/tools/tests/run_tests.py test_export test_dist -q`

## Context

- Depends on T6 (`secrets_placement` callable on an arbitrary root) and T2 (distctl changes merged).
- Refuse when the target is inside the source, equals the source, or is not an existing directory. `--dry-run` prints the add/update/drop plan.
- The always-exclude list is hard-coded and tested; `publish.json` can only narrow, never widen past it.
- Default `publish.json` for adopters: publish nothing from `.claude/` (include list empty). This repo's own value may differ and must be normalised on the way into the kits (distribution-boundary mechanism 3).

## Plan

- [ ] `visibility` knob + adopt question + gitignore writer for all install paths, done when: tests for both values and for the kit path
- [ ] Shadowing check respects the knob, done when: test passes
- [ ] `distctl export` with manifest, mirror-drop, dry-run, refusals, done when: one test each
- [ ] Hard exclude list test (try to include `state/` via publish.json -> still excluded), done when: passes
- [ ] Secrets gate on output, done when: planted key in a publishable file makes export refuse
- [ ] Doc + cards + registry + probe, done when: `test_contracts` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T7.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

