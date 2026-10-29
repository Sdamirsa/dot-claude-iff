# Task: T11 - release v0.3.0-alpha.1 and answer the issues

_Created 2026-10-05 · Status: done_

Milestone: M-0.3.0-alpha · Closes: #7 to #14 · Shared contracts: `030a-00-milestone.md`

## Goal

The alpha is published as a pre-release from `dev` and every issue has an accurate reply.

**Definition of done:** `system_version` is `0.3.0-alpha.1`; CHANGELOG has the section; proposals are seeded (Jev-style gate with open-source variants, plugin packaging, per-write secret scan, analysis consent/redaction, generator output-hash check); a human test checklist exists at `docs/alpha-test-checklist.md`; after the maintainer's yes: tag pushed, workflow green on both OS, pre-release shows both zips, 'latest' is still v0.2.2, each issue has its reply and is closed or labelled per the maintainer.

**Test:** `python3 .claude/tools/tests/run_tests.py -q`

## Context

- HUMAN GATE: stop before the tag, before posting replies, before closing issues. Show the changelog section and the drafted replies first.
- Verifier runs the full suite and `checkctl probe` independently before the gate.
- Issue #12 reply: not added as a gate in 0.3; kept as a proposal; bar to plan it: an open-source variant that runs locally, or two logged incidents the plain checks missed.

## Plan

- [x] Version, changelog, proposals, checklist, done when: `changelog_parity` OK
- [x] Verifier verdict on the whole milestone, done when: envelope STATUS ok
- [x] Maintainer approval, done when: explicit yes in chat
- [x] Tag + watch workflow + verify release, done when: `gh release view` shows prerelease true and both assets
- [x] Issue replies, done when: posted and listed in Outcome

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T11.json`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

Shipped 2026-10-06: tag `v0.3.0-alpha.1` on `dev`, workflow run 37426017192 green on both
OS, GitHub pre-release with both zips, "latest" still v0.2.2. Replies posted on issues #7, #8,
#9, #10, #11, #12, #13, #14; all kept open until the stable release. Verifier envelope:
`.claude/state/handshakes/verify-M-030-alpha.json`. Phase moved to review with a logged
override (the build exit check exceeded ten minutes; proposal filed).
