# Credits

dot-claude-iff was built with, and borrowed from, the projects below. This file is the lineage
and the thanks; `NOTICE` stays the legal file and carries the licence lines a copy must keep.
CREDITS.md travels inside `.claude/` of both kits, beside `LICENSE` and `NOTICE`.

Every entry is one `##` section with three lines: **URL**, **Licence** (what the project's own
LICENSE or README said when it was read; "no licence stated" when none could be read, never a
guess) and **What we took**. An entry marked planned names something a proposal will bring in;
nothing from it ships yet. `tests/test_dist.py` checks that every entry is complete.

## Claude Code and the Anthropic docs

- **URL:** https://github.com/anthropics/claude-code (docs: https://docs.claude.com/en/docs/claude-code)
- **Licence:** proprietary. Claude Code's `LICENSE.md` reads "© Anthropic PBC. All rights
  reserved. Use is subject to Anthropic's Commercial Terms of Service." The docs site's own
  terms were not read.
- **What we took:** the host. Every hook, setting, permission rule, skill, subagent and
  worktree here runs on Claude Code, and its documentation is where their contracts were
  learned. Nothing of Claude Code is copied into this repo.

## ADHD (the /adhd skill)

- **URL:** https://github.com/UditAkhourii/adhd
- **Licence:** MIT, Copyright (c) 2026 ADHD contributors. The upstream licence text ships at
  `.claude/skills/adhd/LICENSE`; `NOTICE` carries the legal line.
- **What we took:** the brainstorm method of the `/adhd` skill: isolated parallel branches
  under different cognitive frames, a separate critic pass, then deepening the shortlist. What
  was kept, changed and removed is recorded in `.claude/skills/adhd/UPSTREAM.md`.

## Cview

- **URL:** https://github.com/Sdamirsa/Cview
- **Licence:** MIT, Copyright (c) 2026 Seyed Amir Ahmad Safavi-Naini (the same author as this
  system).
- **What we took:** the worker and review model the 0.3 backbone was shaped for: a worker per
  linked worktree driven by TASK.md and ending in DONE.md or BLOCKED.md, review rounds decided
  by a verdict, and the human as the only one who merges and pushes. No Cview code is vendored.

## GitDiagram

- **URL:** https://github.com/ahmedkhaleel2004/gitdiagram
- **Licence:** MIT.
- **What we took:** design only. Its pipeline shape (from the file tree and README, one model
  step returns a strict graph of groups, nodes and edges, every path is validated against the
  real repository, and a deterministic compiler renders the diagram with click-through links)
  informed the architecture view that shipped in 0.3.0-beta.1 (PR-15). The design is in
  `.claude/research/2026-11-04-code-graph-console.md`. No GitDiagram code is vendored.

## Graphify

- **URL:** https://github.com/Graphify-Labs/graphify
- **Licence:** Apache-2.0 (the repository's `LICENSE`).
- **What we took:** the reference for the code-graph design, and the optional extractor
  (shipped in 0.3.0-beta.1, PR-15): the human runs `graphify extract --code-only` in their own
  terminal through uvx (`graphctl.py build` prints the version-pinned command), and
  `graphctl.py` only normalises its output, falling back to a stdlib walker when there is
  none. Never installed into this repo or a kit: `graphify install` would add a third-party
  hook to the protected tree.

## tree-sitter

- **URL:** https://github.com/tree-sitter/tree-sitter
- **Licence:** MIT.
- **What we took:** nothing directly. It is the parser Graphify uses to read source code; this
  system reaches it only through Graphify's output, when the human runs that extraction, and
  never imports it (the stdlib walker reads Python through `ast`).

## actions/checkout

- **URL:** https://github.com/actions/checkout
- **Licence:** MIT.
- **What we took:** the only GitHub Action the CI workflows use, to check the repository out
  before the suite runs.

## Zenodo

- **URL:** https://zenodo.org (source: https://github.com/zenodo/zenodo)
- **Licence:** GPL, as the source repositories state: `zenodo/zenodo` reads GPL-2.0 and
  `zenodo/zenodo-rdm` reads GPL-3.0. The hosted service's terms of use were not read.
- **What we took:** archival of the releases through the GitHub integration and
  `CITATION.cff`: the concept DOI 10.5281/zenodo.24089281 is live, and v0.3.0-beta.1 has the
  version DOI 10.5281/zenodo.24089282. Nothing of Zenodo ships in the kits.

## Source system one: an adaptive .claude template (the memory spine)

- **URL:** https://github.com/Sdamirsa/dot-claude-iff/blob/main/.claude/research/2026-08-24-system-design.md
  (the design contract that names both source systems by role; the systems themselves are
  private and unpublished)
- **Licence:** no licence stated (a private system, never published).
- **What we took:** the memory spine, and the idea of a `.claude/` template that adapts to the
  project it lands in. The design contract records what was kept and what was rebuilt.

## Source system two: a long-run multi-agent crawler (the runtime spine)

- **URL:** https://github.com/Sdamirsa/dot-claude-iff/blob/main/.claude/research/2026-08-24-system-design.md
  (the same design contract; the system itself is private and unpublished)
- **Licence:** no licence stated (a private system, never published).
- **What we took:** the runtime spine, after its own maturation: the opt-in decorator lane of
  the record, the pattern behind the map's cards, the maturation-format task cards
  (description, to-do, satisfaction, pass-test) and one `checkctl` probe per component.
