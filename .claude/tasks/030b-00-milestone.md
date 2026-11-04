# Task: Milestone M-0.3.0-beta.1 - index, waves and shared contracts

_Created 2026-11-04 · Status: doing_

Milestone: M-0.3.0-beta.1

## Goal

Close the fixes triaged on 2026-11-04 (PR-8, PR-9, PR-10, PR-11, PR-16), evaluate PR-5, trim
CLAUDE.md (PR-14) and land the code-graph console feature (PR-15), so `v0.3.0-beta.1` can be
cut from `dev` after a human review round.

**Definition of done:** every task file `030b-t*.md` is done with its Test green on Windows
and (in CI) Ubuntu; the suite is green; the proposals above are resolved as planned with a
note naming the task that closed them.

**Test:** `python3 .claude/tools/tests/run_tests.py -q`

## Context

- Triage and the three-session track: `.claude/STATUS.md` (2026-11-04) and the ritual commit
  b77f10f. Code-graph design: `.claude/research/2026-11-04-code-graph-console.md`.
- Mode `fableous-orchestrated`: builders (opus) work in worktrees cut by `statectl dispatch`,
  never commit; the lead reviews the diff, `statectl accept`, runs the suite, closes the task.
- Bugs first, then only-necessary features (the maintainer's instruction for this round).
- Deny rules (PR-5) research, 2026-11-04: Claude Code settings `permissions.deny` binds the
  main session and every subagent alike (no settings-level subagent-only deny); `Edit(...)`
  rules use gitignore syntax and cover Write; `Bash(...)` rules are prefix matches checked
  per subcommand and are documented as "not a security boundary"; a PreToolUse hook cannot
  override a deny; the sandbox is off by default and unsandboxed on native Windows.

## Waves (by file overlap)

| Wave | Task | Proposal | Files it owns |
|---|---|---|---|
| 1 | T14 | PR-10 | `tools/statectl.py` (`_accept_merge`), `tests/test_orchestration.py` |
| 1 | T15 | PR-9, PR-11 | `tools/checkctl.py` (`generator_freshness_report`, `exit_build`), `tests/test_ritual.py`, `tests/test_phases.py` |
| 1 | T16 | PR-8 | `tools/obsctl.py` (analyze), `config/observe.json`, `config/registry.json` (one card), `tests/test_analyze.py` |
| 1 | T17 | PR-16 | `CREDITS.md`, `tools/distctl.py` (`LEGAL_FILES` neighbourhood), `tests/test_dist.py` |
| 2 (lead) | - | PR-14, PR-5 | `.claude/CLAUDE.md`; `settings.json` deny rules if the evaluation says yes |
| 3 | T18 | PR-15 | code graph: new `tools/graphctl.py`, console MAP tab, `docs/ARCHITECTURE.md` |

Shared files nobody in wave 1 may reformat: `checkctl.py`, `registry.json`, `_lib.py`.

## Plan

- [ ] Wave 1 dispatched and accepted, done when: four `accept` merges and the suite green
- [ ] Wave 2 (lead), done when: `mapctl context` and `context_health` accept CLAUDE.md; PR-5 decided
- [ ] T18 accepted, done when: `test_graph` green and the console MAP tab shows the `.claude` scope
- [ ] Proposals resolved, done when: `statectl proposal list` shows none of PR-8/9/10/11/14/15/16 open

## Checkpoint

- **Last completed:** cards written
- **Next action:** register tasks, commit, dispatch wave 1
- **State files:** `.claude/state/handshakes/T14.json` .. `T17.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
