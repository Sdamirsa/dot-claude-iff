# Task: Milestone M-0.3.0-alpha - index and shared contracts

_Created 2026-10-05 · Status: done_

## Goal

Ship `v0.3.0-alpha.1` as a GitHub pre-release from the `dev` branch, closing issues #7 to #14,
with `main` and "latest" left on v0.2.2 until the maintainer signs off v0.3.1.

**Definition of done:** every task file `030a-t*.md` is done with its Test green on Windows
and (in CI) Ubuntu; the pre-release is visible with both zips; each issue has its reply.

**Test:** `python3 .claude/tools/tests/run_tests.py -q`

## Context

- Decisions were taken by the maintainer on 2026-10-05 after a planning review (journal
  `decision` entries of that date). Lifecycle for this and future work:
  plan -> build -> review (human tests) -> deploy.
- Branch flow: all work on `dev`; `main` = last stable. Alpha tags go on `dev` as
  pre-releases. Stable = one PR `dev` -> `main`, tag on `main`.
- Build method: Opus builders work in isolated git worktrees and never commit. The main
  session reviews each diff, merges into `dev`, runs the suite, then a verifier checks the
  task's Test. Nothing reaches `dev` except through the main session.
- Push `dev` and open PRs freely. STOP and ask before: the tag, issue replies, closing issues.

## Shared contracts (every task builds against these names)

**Three dials.** Phase = what work is allowed. Mode = how organised the work is. One payload.

| Mode | Meaning |
|---|---|
| `freestyle` | Default when unset. Record, gates and ritual only. No phase contract, no exit checks. |
| `guided-solo` | Phase contract printed at session start; leaving a phase runs its exit check. One agent. |
| `fableous-orchestrated` | `guided-solo` plus lead-and-team routing (`protocols/orchestration.md`), handoff validation, delegation nudge. |

- `statectl mode <freestyle|guided-solo|fableous-orchestrated>` -> journal action `mode{value}`.
- `statectl phase <plan|build|review|deploy> [--override "<why>"] [--signoff "<text>"]` ->
  journal action `phase{value,from,override,signoff}`. In `guided-solo`/`fableous-orchestrated` it runs
  `checkctl phase-exit --from <current>` first and refuses on FAIL unless `--override`.
- Short aliases are accepted on input (`guided`, `solo`, `fableous`, `orchestrated`); the stored and displayed
  values are the full names. Display labels: Freestyle · Guided Solo · Fableous Orchestrated.
- Both project into `session.json` (`session.mode`, `session.phase`), HANDOFF.md, the
  console badge and the SessionStart resume block.
- Phase exit checks (`checkctl phase-exit --from X`):
  plan: >=1 open task file, each with a non-placeholder `**Definition of done:**` and a
  `**Test:**` line holding one backticked command, each registered in the journal under a
  milestone. build: every task of the current milestone is `done` and its Test command exits 0
  when run. review: a `--signoff` text is supplied. deploy: `checkctl doctor` has no FAIL.
- `statectl task <id> --milestone <mid>` links tasks to milestones (replaces epics).
- Proposal box: `statectl proposal add "<text>" --source <issue#N|human|agent:<name>>
  [--kind feature|fix|evolve]`, `proposal list`, `proposal resolve <id> --as
  planned|rejected --note`. Store `.claude/state/proposals.jsonl`, ids `PR-<n>`. Shown on the
  console WORK tab. In deploy phase new feature ideas go here, not into code.
- Ritual ticket: `.claude/state/ritual-ticket.json`, written only by the prompt hook when the
  user types `/project-memory` or `/adopt`. `checkctl run` (opening a run) and
  `checkctl complete` refuse without a fresh one. One command to remember: `/project-memory`;
  it ends with a ROUTE step (AskUserQuestion: next phase, next mode, maturation pass).
- Handoff envelope: `.claude/state/handshakes/<task_id>.json`, validated by
  `checkctl handoff <task_id> [--run]`. In `fableous-orchestrated`, `statectl task <id> --status done`
  refuses without a valid envelope whose test passed.
- Invariants that still hold: hooks and core tools are bash + python3 stdlib only; every new
  journal action is added to `_lib.JOURNAL_ACTIONS` and the projector in the same change;
  every new store joins `mapctl.KNOWN_STORES` with a card; every new knob gets a registry
  card; every new component gets a `checkctl probe` entry; L-9 (as_posix + normcase) and L-10
  (one test per shell lane).

## Plan

- [x] T1 gate shell lanes, done when: `run_tests.py test_hooks` green with the new lane tests
- [x] T2 release engineering, done when: `run_tests.py test_dist test_contracts` green
- [x] T6 secrets + doctor, done when: `run_tests.py test_secrets` green
- [x] T8 communication + brainstorm skill, done when: `run_tests.py test_brainstorm` green
- [x] T9 folder context, done when: `run_tests.py test_context` green
- [x] T4 phases, modes, proposals, done when: `run_tests.py test_phases test_statectl` green
- [x] T7 visibility + export, done when: `run_tests.py test_export` green
- [x] T3 ritual ticket + ROUTE, done when: `run_tests.py test_ritual` green
- [x] T5 fableous mode, done when: `run_tests.py test_orchestration` green
- [x] T12 long-run progress, done when: `run_tests.py test_progress test_console` green
- [x] T13 dispatch + zip flow, done when: `run_tests.py test_orchestration test_dist` green
- [x] T10 drift and cleanup, done when: full suite green, `checkctl probe` all green
- [x] T11 release, done when: pre-release published, issues answered (human-gated)

Waves (by file overlap): 1 = T1 T2 T6 T8 T9 · 2 = T4 T7 · 3 = T3 T5 · 4 = T12 T13 · 5 = T10 · 6 = T11.

## Checkpoint

- **Last completed:** task files written, dev branch created
- **Next action:** dispatch wave 1 builders in worktrees
- **State files:** `.claude/tasks/030a-*.md`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

Published 2026-10-06 as pre-release v0.3.0-alpha.1 from `dev`; `main` stays at v0.2.2 until
the maintainer signs off the stable release after `docs/alpha-test-checklist.md`. Thirteen
tasks (T13 was added mid-build for one-command dispatch and conflict-free zips; T12 for the
long-run progress view). Deviations from the plan: the builder write grant was dropped (the
tools folder holds the gate's own library); harness worktree isolation branches from
origin/main, so the lead creates worktrees (`statectl dispatch`). Open proposals carry what
was deferred (PR-1 Jev-style gate, PR-4 maturation pass, PR-5 hard layer under the gate, and
the fixes found at close-out).
