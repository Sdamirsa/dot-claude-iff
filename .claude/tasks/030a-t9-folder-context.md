# Task: T9 - folder context: discovery, resolution and lint

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: #14 · Shared contracts: `030a-00-milestone.md`

## Goal

Nested guides and path-scoped rules are first-class, mapped and linted, and anyone can ask what context loads for a given file.

**Definition of done:** `mapctl` discovers every `CLAUDE.md` / `CLAUDE.local.md` outside `.claude/` and every `.claude/rules/**/*.md`; `mapctl context <path>` prints the ordered chain of guides and rules that load for that path, with line counts; a `context_health` check reports: rule frontmatter that cannot be parsed, rule globs matching zero files, guides over the size target, missing or too-deep `@imports`, backticked paths that no longer exist, lines duplicated between a nested guide and an ancestor, and the always-on line budget (root guide + unscoped rules + imports); `mapctl context --suggest` proposes a folder guide only where the project's lessons and log show 2+ lessons or mistakes in a subtree with none; the console MAP tab lists the context files; adopt's rule becomes 'one root guide'.

**Test:** `python3 .claude/tools/tests/run_tests.py test_context test_mapctl -q`

## Context

- Claude Code facts (docs): ancestor CLAUDE.md files load at launch root-to-cwd; subdirectory ones load on demand when files there are read; `@path` imports resolve relative to the importing file, max depth 4; `.claude/rules/**/*.md` load at launch unless `paths:` frontmatter scopes them, then on Read/Write/Edit of a matching file; bad YAML makes a rule load unconditionally; size target under 200 lines per file.
- Stdlib only: write a small frontmatter reader for `paths:` (string, inline list, block list); anything else is reported as 'unparseable', never guessed. Glob matcher must support `**`, `*`, `?`, and `{a,b}`; test it directly.
- Discovery through `git ls-files` plus untracked-not-ignored, with a pruned walk fallback; never descend into `.git`, `.claude/worktrees`, `node_modules`, or the record folder. Use as_posix + normcase (L-9).
- Thresholds are registry knobs. 'Earned, not guessed': the system never creates a guide by itself; `--suggest` only proposes.

## Plan

- [ ] Discovery + component kinds `guide` and `rule` + `map_scan` inputs, done when: fixture with nested guides and rules is fully mapped
- [ ] Frontmatter reader + glob matcher, done when: unit tests incl. brace and `**` cases pass
- [ ] `mapctl context <path>` resolution, done when: chain order test passes for a 3-level fixture
- [ ] `context_health` check with all listed findings, done when: one test per finding
- [ ] `--suggest` from LESSONS/Project-log evidence, done when: fixture with two lessons in one subtree yields exactly one suggestion
- [ ] Console MAP listing + cards + probe, done when: `test_console` green incl. empty-project degrade

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T9.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

