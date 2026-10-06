# Task: T13 - one-command dispatch and conflict-free zips

_Created 2026-10-05 · Status: done_

Milestone: M-0.3.0-alpha · Closes: maintainer request 2026-10-05 (proposals PR-2, PR-3) · Shared contracts: `030a-00-milestone.md`

## Goal

Dispatching a builder is one command, accepting its work is one command, and the committed
zips never cause a merge conflict again while still being guaranteed correct wherever people
download them.

**Definition of done:** `statectl dispatch <id>` creates the worktree from the current
branch (`.claude/worktrees/<id-lowercase>`, branch `wt/<id-lowercase>`), writes the stub and
prints the filled builder brief ready to paste as the agent prompt (task file path, worktree
name, brief path), refusing cleanly when the task file is missing, the worktree already
exists, or the tree has uncommitted changes the builder would not see; `statectl accept <id>`
validates the handoff inside the worktree (`checkctl handoff --run --root`), restores any
change the builder made to `.claude/dist/` and to derived state files, commits the worktree,
merges it into the current branch without fast-forward, removes the worktree and branch, and
stops with a clear message on a merge conflict leaving everything resolvable; the committed
zips are required to equal a rebuild only on `main`, on tags, on pull requests into `main`,
and in the release workflow, and are skipped with a one-line reason elsewhere, so a merge on
`dev` needs no rebuild; `distctl verify` stays strict everywhere; the release flow rebuilds
and commits the zips as its own step; the orchestration protocol, the builder brief template
and `release-flow.md` describe all of this.

**Test:** `python3 .claude/tools/tests/run_tests.py test_orchestration test_dist test_statectl -q`

## Context

- This milestone was built by hand-running, per task: `git worktree add`, write a brief,
  dispatch, then `git add`, commit in the worktree, merge, rebuild zips, run the suite,
  remove the worktree. Two merges conflicted on the zips because a builder had rebuilt them
  in its branch while `dev` had rebuilt them too.
- `statectl dispatch` exists (it writes the stub with `dispatched_at`); extend it, do not add
  a second command for the same job. `checkctl handoff` exists with `--run` and `--root`.
- Git writes by these commands are the main session's own tool calls; sub-agents cannot run
  them (the gate denies git to sub-agents). Use subprocess without a shell.
- Where the strict zip rule applies is decided from the environment in one helper: current
  branch `main`, `GITHUB_REF_TYPE=tag`, `GITHUB_BASE_REF=main`, or an explicit flag. The
  release workflow already rebuilds and fails on any difference.
- The ritual's POLISH rebuilds zips at home; that is fine: only the lead commits on the
  working branch, so it cannot conflict.
- Hooks' exec bit: `accept` must preserve file modes (on Windows git does not see the bit;
  new `.sh` files need `git update-index --chmod=+x`; do it for every `.sh` under
  `.claude/hooks/` the merge adds).

## Plan

- [x] `statectl dispatch` creates worktree + stub + prints brief, done when: fixture-repo tests for success and each refusal
- [x] `statectl accept` validates, restores dist/derived files, commits, merges, cleans up, done when: fixture-repo tests for success, invalid envelope, and a merge conflict
- [x] Zip equality scoped to main/tags/PRs-to-main + release step, done when: tests for each environment case and `distctl verify` still red on a stale zip
- [x] Protocol, brief template, release-flow doc, cards, done when: `test_contracts` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T13.json`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

