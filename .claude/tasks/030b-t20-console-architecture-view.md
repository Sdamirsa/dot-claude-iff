# Task: T20 - the console MAP tab renders the architecture spec, with a scope switcher

_Created 2026-11-04 · Status: done_

Milestone: M-0.3.0-beta.1 · Closes: PR-15 (viewer) · Shared contracts: `030b-00-milestone.md`

## Goal

The console's MAP tab gains a scope switcher: `cards` (today's view, default) plus one entry
per scope in `architecture.json`; a spec scope renders with the existing band-and-SVG-lines
renderer (groups as bands, nodes as boxes, labelled edges), and the node dialog shows the
path, the group blurb, the edge list, the `pathLink` and, when live, an editor link.

**Definition of done:** `consolectl.payload()` carries `architecture` (the parsed
`.claude/system-map/architecture.json`, or null when absent or malformed, never raising);
`console.py` serves `/live/architecture.json` when `read_allowlist` names it (add the line
to `console.json` if T18 has not; one line, no other change there); the template renders a
scope switcher only when `architecture` has scopes, falls back to `cards` otherwise, and
draws a spec scope with no new library and no force layout; the dialog's editor link is
`vscode://file/<project.root>/<path>` and appears only when the page is live over http;
`test_console.py` covers: payload with and without the file, the switcher markup present
when scopes exist and absent otherwise (string checks on the built `console.html` from a
fixture spec), and the live endpoint; `consolectl build` on this repo still produces a
self-contained `console.html` (no `<script src`), and `test_console` and `test_contracts`
stay green.

**Test:** `python3 .claude/tools/tests/run_tests.py test_console test_contracts -q`

## Context

- Design: `.claude/research/2026-11-04-code-graph-console.md`, "What the console already
  is" (payload keys, `renderMap` ~1000, `drawEdges` ~1215, `openNodeDialog` ~1257,
  `pathLink` ~668 in `console.template.html`) and "Pivot" (the approved target: 20-40
  boxes grouped into subsystems, arrows, click to file).
- The spec schema is in the milestone's shared contracts. T18 builds the real file in
  parallel: in your worktree it does not exist, so build everything from a fixture spec
  written by the tests (two scopes, two groups each, a handful of nodes and edges) and keep
  a copy of that fixture in the test module. The payload snapshot embeds the whole spec: it
  is small by design (the cap is 40 nodes per scope).
- `consolectl.py` `payload()` ~451: readers degrade to null, never raise. `_read_map` is the
  pattern to copy. The console's self-contained rule: inline CSS and JS only.
- Node ids in the spec are card ids (`tool.statectl`): use them as `data-id` values, never
  as element ids without escaping.
- `project.root` is already in the payload; the editor link is built in JS only when
  `location.protocol` is `http:` or `https:`; over `file:` the existing `pathLink` applies.
- Do not change `.claude/config/console.json` beyond the one allowlist line, and do not touch
  `mapctl.py`, `checkctl.py` or `registry.json` (T18 owns them this wave).

## Plan

- [ ] Payload field + live endpoint, done when: payload tests and the endpoint test pass
- [ ] Scope switcher + spec renderer in the template, done when: string tests on the built
      HTML pass and a manual `consolectl build` shows the switcher with the fixture
- [ ] Node dialog additions, done when: the editor-link rule is tested by a string check on
      the JS (the function and the protocol guard exist)
- [ ] Self-containment, done when: the existing no-`<script src>` test still passes

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T20.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
