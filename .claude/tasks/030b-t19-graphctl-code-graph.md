# Task: T19 - graphctl: the code graph, from graphify's output or a stdlib walker

_Created 2026-11-04 · Status: todo_

Milestone: M-0.3.0-beta.1 · Closes: PR-15 (tier 2) · Shared contracts: `030b-00-milestone.md`

## Goal

A new stdlib tool `.claude/tools/graphctl.py` with `build`, `update` and `status`: it
normalises graphify's `graphify-out/graph.json` (under the record root, when the human ran
the extraction) or, failing that, walks the repo with `ast` (Python) and a `source:` scan
(`.sh`), into `.claude/system-map/code-graph.json` (gitignored); and it exposes
`architecture_scope(root)`, which collapses that graph to files and folders in the
architecture node/edge shape, for `mapctl architecture` (T18) to call.

**Definition of done:** `python3 .claude/tools/graphctl.py build` on this repo exits 0,
writes `code-graph.json` with the slim schema in the milestone contract, and prints the
exact `uvx --from graphifyy==<pinned> graphify extract ...` command as a WARN when no
graphify output exists (it never runs uvx or graphify itself, and never `graphify install`);
`update` re-normalises an existing graphify output or reruns the walker; `status` prints
node and edge counts, the graph's age, whether graphify output was found and where;
`architecture_scope(".claude/tools")` returns groups (folders), nodes (files, kind `file`,
with `path`), edges (`imports`, `calls` with `weight`), respects `max_nodes` by folding the
lowest-degree files into one "other" node per folder, and every returned path exists;
`.gitignore` ignores `.claude/system-map/code-graph.json` inside the managed block or next to
it; a `checkctl probe` entry exists for the tool; the suite is green.

**Test:** `python3 .claude/tools/tests/run_tests.py test_graph -q`

## Context

- Design: `.claude/research/2026-11-04-code-graph-console.md` ("What graphify is",
  "Measured on this repo", "Decisions", "Pivot", "Resolution of the permission thread"). The
  maintainer decided: extraction is a human-terminal command; the tool only normalises what
  exists. Pin: knob `code_graph.graphify_version` in `console.json` with a registry card;
  pick the version named in the research note's "Measured" section.
- graphify's output: `graphify-out/graph.json` with nodes whose labels follow `name()`,
  `.name()`, bare, filename conventions, and edges with a relation; the note describes the
  shape as measured. Record root: `_lib.record_paths()`; look under `<record>/graph/`.
  Never write under the record root from this tool except that folder.
- The `ast` fallback: every `.py` under the scope root (skip `__pycache__`, `tests` stay in
  the graph but are excluded by `architecture_scope`), `ClassDef`, `FunctionDef`,
  `AsyncFunctionDef` with line spans, `Import`/`ImportFrom` resolved to in-repo files when
  they name one, `Call` edges only when the callee name resolves within the same file or
  an imported in-repo module (confidence 0.6), else dropped. `.sh` files: `source` lines to
  in-repo files become `imports`.
- `architecture_scope(root, max_nodes=40)` is a pure function over the loaded graph; test it
  on a fixture tree written into the temp project (`FixtureCase`), not on this repo.
- Every new tool needs a `checkctl probe` entry (`checkctl.py`, small local hunk) and a
  card under `.claude/system-map/cards/` (`mapctl scan` can draft it; run `mapctl lint`).
- Paths: `as_posix()` to build, `os.path.normcase` to compare (L-9).

## Plan

- [ ] Walker + normaliser + schema writer, done when: fixture tests cover a class, a
      function, an import, a call, a `source:` line
- [ ] graphify normaliser, done when: a test feeds a small fake `graphify-out/graph.json`
      under the fixture record root and the same schema comes out
- [ ] `build|update|status` CLI, done when: tests see the WARN command line, the counts, exit 0
- [ ] `architecture_scope`, done when: fold and cap tests pass and every path exists
- [ ] Probe entry, card, gitignore line, done when: `checkctl probe` and `mapctl lint` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T19.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
