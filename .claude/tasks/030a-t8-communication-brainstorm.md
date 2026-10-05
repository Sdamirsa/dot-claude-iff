# Task: T8 - communication block and the brainstorm skill

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: #8 · Shared contracts: `030a-00-milestone.md`

## Goal

Replies follow a short, checkable communication discipline, and divergent brainstorming is available as one tunable skill.

**Definition of done:** `.claude/CLAUDE.md` and `skills/adopt/CLAUDE.template.md` carry a Communication block of at most 10 lines; `.claude/skills/adhd/` holds the upstream skill (MIT notice and source URL retained) adapted to read its branch count, frames and models from `.claude/config/brainstorm.json`; every knob has a registry card; the skill is user-invoked; a test asserts the block is present in both files, the licence notice exists, and every config key the skill names exists in the config and registry.

**Test:** `python3 .claude/tools/tests/run_tests.py test_brainstorm -q`

## Context

- Upstream: https://github.com/UditAkhourii/adhd (MIT). Fetch `skills/adhd/SKILL.md` and LICENSE with `gh api` or raw URLs. Keep its method (isolated parallel branches under different frames, then a separate critic pass that clusters, prunes traps, shortlists). Remove invented-precision score chips.
- Models: branches on `sonnet`, critic on `inherit`; both are knobs. Default branch count from upstream.
- The ritual's EVOLVE step may propose changes to `brainstorm.json` (frames, counts, models); say so in the skill and return the matching `protocols/evolution.md` edit.
- Communication block content: answer or verdict first; at most four options with exactly one marked as the pick and a one-line reason; rejected options listed apart, one line each; 'quick'/'just' gets three lines or fewer; 'not enough evidence, missing X' is a valid pick; brevity never trims failure output or the list of skipped steps (honesty protocol wins).

## Plan

- [ ] Communication block in both guides, done when: test passes and CLAUDE.md stays under its line target
- [ ] Vendored + adapted skill with licence, done when: test passes
- [ ] `brainstorm.json` + registry cards + system-map card + probe, done when: `test_contracts` still green
- [ ] evolution.md wording, done when: present in the diff

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T8.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

