# Task: T2 - pre-releases, CI on push, fresh zips, dev/main flow

_Created 2026-10-05 · Status: done_

Milestone: M-0.3.0-alpha · Closes: debt · P1 P2 P7 · Shared contracts: `030a-00-milestone.md`

## Goal

A pre-release tag publishes as a GitHub pre-release without moving 'latest'; tests run on every push and PR; committed zips can never be stale; heartbeat is untracked but always present.

**Definition of done:** `system_version` accepts `X.Y.Z` and `X.Y.Z-alpha.N|beta.N|rc.N`; changelog parity understands those headings; `release.yml` passes `--prerelease` for such tags and the notes regex cannot match a longer version; a `ci.yml` runs the suite on push and pull_request for ubuntu and windows; a test rebuilds both zips and fails when the committed copies differ; `heartbeat.json` is gitignored and a fixture test shows the Stop hook creates it on a fresh install; clone adoptions skip `.claude/dist/`; `.claude/worktrees/` is gitignored and never enters a zip.

**Test:** `python3 .claude/tools/tests/run_tests.py test_dist test_contracts -q`

## Context

- Zips STAY committed at `.claude/dist/` (maintainer decision). The staleness came from the payload filtering on the git index while freshness hashes the working tree: untracked new files are skipped. Fix the cause (decide and document one rule) and add the rebuild-equality test.
- Zips are deterministic already (fixed date, sorted). The equality test must pass on Windows and Linux (line endings: check `.gitattributes`).
- Add `.claude/reference/release-flow.md`: dev/main flow, alpha on dev, stable by PR, never force-push a tag. Home-only via an existing distribution-boundary mechanism.
- Do not bump `system_version` or edit CHANGELOG (T11 does). The main session removes heartbeat.json from the git index at merge (builders cannot run git writes).

## Plan

- [x] Version/tag parsing helper in `_lib` + changelog parity, done when: tests for alpha/beta/rc and plain versions pass
- [x] `release.yml` prerelease flag + exact-match notes regex, done when: a test reads the workflow text for the flag logic and a regex unit test passes
- [x] `ci.yml` on push/PR, done when: file exists and a structural test passes
- [x] Zip freshness test + cause fixed, done when: `test_dist` green after a rebuild and red when a payload file is edited without rebuild
- [x] Heartbeat ignored + created on first Stop, done when: fixture test passes
- [x] Suite added to home `project_steps.check`, done when: a fixture run of the check phase lists it

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T2.json`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

