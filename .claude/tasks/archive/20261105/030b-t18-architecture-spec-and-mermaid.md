# Task: T18 - architecture spec from the cards, and the Mermaid renderer

_Created 2026-11-04 · Status: done_

Milestone: M-0.3.0-beta.1 · Closes: PR-15 (tier 1) · Shared contracts: `030b-00-milestone.md`

## Goal

`mapctl architecture` writes `.claude/system-map/architecture.json` (the spec in the
milestone's shared contracts) with the `claude` scope built deterministically from the cards,
and renders `docs/ARCHITECTURE.md`: one Mermaid flowchart per scope, groups as subgraphs,
every box clickable to its file on GitHub. Registered as the generator `architecture_build`.

**Definition of done:** `python3 .claude/tools/mapctl.py architecture` exits 0 on this repo
and writes both files; the `claude` scope has between 15 and 40 nodes and every node's
`path` exists; `docs/ARCHITECTURE.md` renders (a test parses the Mermaid block: every edge
names two declared node ids, every `click` line names a declared node and a URL under the
repo's GitHub blob URL); a node with a missing path makes the build exit 1 with the path
named; the generator is in `checkctl.GENERATORS`, in `memory.json` `phases.polish` before
`console_build`, both outputs are in `policy.json` `derived_files`, the knobs
`architecture.scopes` and `architecture.max_nodes` exist in `console.json` with registry
cards, and `console.json` `read_allowlist` names `system-map/architecture.json`; `checkctl
probe`, `test_contracts`, `test_mapctl` and `test_ritual` stay green.

**Test:** `python3 .claude/tools/tests/run_tests.py test_architecture test_mapctl test_contracts -q`

## Context

- The design and the maintainer's pivot to a GitDiagram-style diagram:
  `.claude/research/2026-11-04-code-graph-console.md`, sections "Pivot" and "Decisions".
  The sample the maintainer approved had about 26 boxes and 32 arrows for `.claude`.
- The cards: `.claude/system-map/cards/*.json` (`id`, `kind`, `layer`, `path`, `reads`,
  `writes`, `invokes`); the compiled map: `.claude/system-map/map.json` (`layers`, `nodes`,
  `edges` with `type` in reads|writes|invokes, `stats.by_layer`). `mapctl.py` has `compile`;
  put `architecture` beside it and reuse its card loader. Folding rules and the cap are in
  the milestone contract; make the rules pure functions with their own tests.
- Scopes with `source: code` call `graphctl.architecture_scope(root)`, built in parallel by
  T19 and absent in your worktree: import inside a try, print one WARN line per skipped
  scope, exit 0. Write one test with a fake `graphctl` module injected into `sys.modules`
  that returns a small scope, so the wiring is proven before T19 lands.
- GitHub URL: `git remote get-url origin` through subprocess (code under test may call git),
  parse `github.com/<owner>/<repo>`, strip `.git`; branch from `git rev-parse --abbrev-ref
  HEAD`; a non-GitHub or missing remote means relative links (`../<path>`). Never raise on a
  missing git.
- Generators: `checkctl.GENERATORS` (tool, args, inputs, output); the ledger hashes inputs
  and outputs; `test_ritual.py` and `test_contracts.py` assert generator/memory.json parity.
  The output must not be inside its own input set.
- Registry cards: copy the shape of a `console.*` card in `.claude/config/registry.json`.
- Mermaid ids must be safe (`[A-Za-z0-9_]`): map card ids like `tool.statectl` to
  `tool_statectl` and keep a table in the file's comment block or a test.
- No volatile numbers in `docs/ARCHITECTURE.md` prose (the diagram itself is data).

## Plan

- [x] Folding rules as pure functions, done when: tests for folder-fold, test exclusion,
      cap-to-"other", self-edge drop pass
- [x] `mapctl architecture` writes the spec and validates paths, done when: a test with a
      bogus path sees exit 1 and the path in the message
- [x] Mermaid renderer, done when: the parser test passes on this repo's output
- [x] Generator + knobs + cards + allowlist + derived files, done when: probe, contracts,
      ritual tests green
- [x] Fake-graphctl wiring test, done when: it passes and a run without graphctl warns and exits 0

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T18.json`
- **Updated:** 2026-11-04

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

Done 2026-11-04: `mapctl architecture` writes the spec and docs/ARCHITECTURE.md (Mermaid, GitHub click links); registered generator `architecture_build`, knobs and cards in place, missing paths fail the build. The verifier pass found no defect here. Closed PR-15 tier 1.
