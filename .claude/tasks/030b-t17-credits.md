# Task: T17 - CREDITS.md: lineage and thanks, shipped with the kits

_Created 2026-11-04 · Status: todo_

Milestone: M-0.3.0-beta.1 · Closes: PR-16 · Shared contracts: `030b-00-milestone.md`

## Goal

A `CREDITS.md` at the repo root names every tool, project and source this system was built
with or borrowed from, each with a link, its licence and what was taken; it travels with
both kits next to LICENSE and NOTICE; a test keeps every entry complete.

**Definition of done:** `CREDITS.md` exists with one section per entry, each holding a URL,
a licence word (one of MIT, Apache, BSD, GPL, CC, proprietary, "no licence stated") and a
"what we took" line; `distctl.py` ships it inside `.claude/` in both zips the way
`LEGAL_FILES` ship (listed there or beside it); a test in `tests/test_dist.py` parses the
file and asserts every entry has a URL and a licence word, and that both kit manifests
contain `.claude/CREDITS.md`. NOTICE stays the legal file and is not edited.

**Test:** `python3 .claude/tools/tests/run_tests.py test_dist -q`

## Context

- Proposal PR-16 lists the known entries: Claude Code and the Anthropic docs (the host);
  UditAkhourii/adhd (MIT) for the /adhd skill, pointing at `.claude/skills/adhd/UPSTREAM.md`
  for what changed; sdamirsa/cview (the worker/review model); Graphify-Labs/graphify and
  tree-sitter (the code-graph extractor, "planned" until PR-15 lands); GitDiagram
  (ahmedkhaleel2004/gitdiagram, the pipeline shape, per the research note
  `.claude/research/2026-11-04-code-graph-console.md`); actions/checkout; Zenodo (planned,
  PR-13); the two private source systems of 2026-08 named only by role, never by path.
- "Jev" (issue #12): the maintainer's note reads as a reasoning-gate idea, not a borrowed
  artefact; add no entry, state in the envelope's UNCERTAINTIES that no source was found.
  Do not search the web for it.
- Licences: read each project's README or LICENSE on GitHub (WebFetch is allowed for this
  task) and record what you saw; an entry whose licence you could not read says "no licence
  stated" and is listed in UNCERTAINTIES. Never guess a licence.
- `distctl.py`: `LEGAL_FILES = ("LICENSE", "NOTICE")` and the `legal` list near the zip
  build; `tests/test_dist.py` has manifest tests to copy from. Do not rebuild or stage the
  zips (`.claude/dist/`): the test extracts what `distctl export` builds into a temp dir.
- No volatile numbers in the file (no version pins, no counts).

## Plan

- [ ] `CREDITS.md` written, done when: the new parser test passes on it
- [ ] Shipped in both kits, done when: the manifest test passes
- [ ] README mention: one line in the root README's licence paragraph, done when: grep

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T17.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
