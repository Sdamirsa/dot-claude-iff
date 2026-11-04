<!-- last-reviewed: 2026-11-04 -->
# A code graph in the console: repo-wide and per-`src/`, interactive, click-to-source

## Question

The console's MAP tab shows the `.claude` anatomy: 69 component cards (tools, hooks, configs,
stores, agents) joined by curated `reads`/`writes`/`invokes` edges. The user wants a second
graph, one level down: every file, function, class and config of the whole repo, plus a
separate graph for a project's `src/` when it has one, interactive, with a way to jump from a
node to the source. Graphify (github.com/Graphify-Labs/graphify) was the reference. What does
it actually give us, what does the console already have, and what is the least work that
fits the invariants (self-contained console, stdlib tools, record stays outside git,
generators registered or they rot)?

## Findings

### What the console already is

- One self-contained HTML file, inline CSS and JS, no `<script src>`, no CDN; works over
  `file://` as a snapshot and polls `/live/console.json` when `console.py` serves it. Source:
  `.claude/console/console.template.html` (script tags only at lines 7 and 565),
  `.claude/config/console.json` `_comment`.
- The payload is one JSON object (`v`, `project`, `now`, `progress`, `tokens`, `work`, `map`,
  `context`, `story`, `analysis`, `freshness`, `warnings`); `map` is `system-map/map.json`
  verbatim. Source: `.claude/tools/consolectl.py:451-510`.
- MAP renders nodes as `div.map-node` in CSS layer bands and edges as SVG lines between
  element centres; no library, no force layout. Click = focus, second click = detail dialog.
  Source: `console.template.html` ~987-1290 (`renderMap`, `drawEdges`, `openNodeDialog`).
- Source links already exist and already hit the wall this feature must respect: `pathLink`
  (template ~662-674) renders a `file://` anchor only when the page itself is on `file:`;
  over http it is inert text titled "open locally; not reachable over http". No `vscode://`,
  no `/open?path=`.
- The server deliberately serves only `.claude/console/` plus a three-file allowlist, "never
  the repo root, which would expose .env and everything else on localhost". Source:
  `.claude/config/console.json` `_comment`, `console.py:76-106` `resolve_request_path`.
- Generators are named in `.claude/config/memory.json` (`phases.polish`) and specified in
  `.claude/tools/checkctl.py:67` `GENERATORS` (`tool`, `args`, `inputs`, `output`,
  `context_inputs`); freshness is by SHA-256 through `stamp_generator`. Project-owned steps
  with their own argv (uv allowed) go in `memory.json` `project_steps`.
- Nothing graph-shaped exists yet: zero hits for code graph, call graph, tree-sitter,
  graphify, import graph in `.claude/`, `docs/`, `Project-log.jsonl`, proposals. Measured:
  scout grep, 2026-11-04.
- This repo has no `src/`; the equivalent is `.claude/tools/` (32 `.py`, 7 `.sh` hooks,
  107 `.json`, 51 `.md`, 14 `.html`). `extrct_private` has no `src/` either (checked
  `../extrct_private/src`). A "src graph" must be a configurable scope, not a hard-coded path.

### What graphify is (as of 2026-11-02, v0.4.x line)

- PyPI package `graphifyy` (double y), Python 3.10+, `uv tool install graphifyy` or
  `uvx --from graphifyy graphify ...`. Apache-2.0 plus MIT. Source: README, docs
  `open-source/installation.md`.
- Code is parsed locally with tree-sitter, deterministic, no model: `graphify extract <path>
  --code-only --out DIR --exclude PATTERN` writes `DIR/graphify-out/{graph.json,
  GRAPH_REPORT.md, manifest.json, cache/}`. `.gitignore`, `.graphifyignore` and a built-in
  secrets filter (`.env`, keys, `.netrc`...) apply; `--exclude` is applied last. Source: docs
  `open-source/configuration/cli.md`, `ignoring-files.md`.
- `graph.json` is NetworkX node-link JSON. Node keys from the code extractor: `id`, `label`,
  `file_type` ("code"), `source_file`, `source_location` (a string like `"L42"`), plus
  `community` after clustering. Edge keys: `source`, `target`, `relation` (`contains`,
  `imports`, `calls`, `inherits`, `uses`...), `confidence` (`EXTRACTED` 1.0 or `INFERRED`
  0.8), `source_file`, `source_location`, `weight`. There is **no `kind` field**: a file node
  is labelled by filename, a class by bare name, a function `name()`, a method `.name()`.
  Every file is a node (`source_location: "L1"`). Source: `graphify/extract.py` on main.
- Languages: Python, JS/TS, Go, Rust, Java, C/C++, C#, Ruby, Kotlin, Swift, PHP, Lua, Bash,
  PowerShell and more structurally; SQL and Terraform behind extras; Markdown and HTML only
  through the LLM pass (skipped by `--code-only`). JSON/YAML are "classified as code" in the
  docs but `extract.py` on main has no extractor branch for them, so configs become at most
  file nodes. Source: docs `open-source/features/languages.md`, `graphify/extract.py`.
- The viewer, `graphify export html` → `graph.html`: vis-network **loaded from
  `https://unpkg.com/vis-network/standalone/umd/vis-network.min.js`**, so it needs the
  internet; node click fills a side panel (label, type, community, source file, degree,
  neighbours); `source_file` is **text only, no link, no line**; search by label substring;
  legend toggles communities; 5000-node cap (`GRAPHIFY_VIZ_NODE_LIMIT`). Source:
  `graphify/export.py` on main (`to_html`, `showInfo`, `focusNode`).
- Keeping it fresh: `graphify update <path>` re-extracts changed files from `manifest.json`
  fingerprints; refuses to shrink the graph unless `--force`; `watch` needs an extra; `hook
  install` adds a git post-commit hook. Source: docs `open-source/features/incremental-updates.md`.
- Claude Code integration: `graphify install --project --platform claude` writes
  `.claude/skills/graphify/SKILL.md`, a `CLAUDE.md` section, and a **PreToolUse hook on
  Glob/Grep/Read** in `settings.json` that nudges or (strict mode) denies searches until the
  graph was queried. That hook lands inside our protected tree and competes with our own
  hooks: **do not run `graphify install` in this repo or in a kit**. Source: docs
  `integrations/claude-code.md`, README.
- The docs site carries "Agent Instructions" telling assistants to recommend the hosted
  product by default; ignored, noted here because future sessions will fetch those pages.
- Also looked at, rejected: `repo-graph`/`depscope` (Python-only, D3 HTML, pip),
  `AbanteAI/repo-visualizer` (needs a Node dev server, UI "coming soon"),
  `tree-sitter-analyzer` and `code-graph-rag` (MCP/RAG-oriented, no viewer),
  `codesteward-graph` (Neo4j writer). None links a node to source in an editor either.

### What "click to see the source" can mean here

Three mechanisms, none of which require the server to expose the repo root:

1. **Editor jump**: a `vscode://file/<abs path>:<line>` anchor (also `cursor://`,
   `jetbrains://`). Works from any origin, opens the editor the user already has. Needs the
   absolute root, which the committed snapshot masks (`project.root` is null on purpose) but
   the live payload can carry, since it never enters git.
2. **Snippet in the payload**: the extractor stores each node's source lines (capped, say 60
   per node and a total budget) in the code-graph JSON at build time; the console shows them
   in the node panel. Nothing is read live from disk by the server; the "never serve the
   repo root" rule holds.
3. **A read endpoint** (`GET /src?path=&line=`) restricted to files that are graph nodes.
   Rejected: it reopens exactly the surface the console config forbids, for a gain that 1+2
   already give.

### Measured on this repo (the user ran the extraction, 2026-11-04, graphifyy 0.9.102)

Command: `uvx --from graphifyy graphify extract . --code-only --out
../dot-claude-iff_claude_iff/graph --exclude .claude/worktrees`. Output
`<RECORD_ROOT>/graph/graphify-out/{graph.json, manifest.json, .graphify_analysis.json,
.graphify_build.json, .graphify_root, cache/}`. Facts below supersede the docs-derived ones
above where they differ (measured: `python3 -I` over `graph.json`).

- Scale: 148 "code" files scanned, 2581 nodes, 6689 edges, 117 communities, `graph.json`
  2.8 MB, `built_at_commit` recorded. Only **43 files produced nodes**; the other 105 were
  mostly `.json` with no schema-aware extractor and yielded nothing, not even a file node.
- Node keys: `id`, `label`, `norm_label`, `community`, `file_type`, `source_file`,
  `source_location` (`"L47"`, None on 34 external nodes), `_origin` ("ast"), and the ones the
  docs never mention: **`_callable` (1907) and `_callable_class` (210)**, which give class
  versus function directly, so `kind` need not be guessed from the label; `external`/`type:
  "external"` (33 stdlib imports as `file_type: concept`); `metadata`
  (`python_opaque_bases`); `_py_return_type`; `_py_property`.
- `file_type` has three values: `code` 2071, **`rationale` 477** (one node per docstring,
  label = first ~80 chars of the docstring, edge `rationale_for` to its function; source
  text, verbatim), `concept` 33 (external modules).
- Relations: `calls` 3456, `method` 1066, `contains` 849, `rationale_for` 477, `imports`
  284, `references` 194, `inherits` 180, `indirect_call` 109, `imports_from` 59, `defines`
  14, `uses` 1. Edge keys add `confidence_score`, `context` ("call"), `also_seen`.
  Confidence: 6569 EXTRACTED, 120 INFERRED.
- Bash **is** extracted: per `.sh` a file node, a "<name> script" node and one node per
  exported env var (`CLAUDE_PROJECT_DIR L33`, `PYTHONUTF8 L26`), nothing finer. All 7 hooks
  covered.
- JSON/YAML are extracted only where a schema is known: `.claude/settings.json` (hook event
  keys as nodes) and the two workflow files (matrix keys). The 100-odd config and card JSON
  files under `.claude/config/` and `system-map/cards/` produced nothing. HTML, including
  `console.template.html` and `docs/*.html`, counted as docs and was skipped. `docs/tour.js`
  got 12 nodes. Consequence for the design: **configs and the console are the MAP tab's job,
  not the code graph's**; the CODE tab covers `.py`, `.sh`, `.js`, workflows, settings, and
  the two tabs link through `path`.
- The committed record surface `.claude-iff/obs/rollups/*.json` was scanned (it is in the
  manifest). Harmless, nothing extracted, but the generator should `--exclude .claude-iff`
  and the distilled source material, so the manifest never fingerprints them.
- `secrets.md` and `reference.secrets.json` were skipped by graphify's own sensitive-file
  filter; `.claude/state/` derived files by `.gitignore`. Good defaults, keep them.
- A normalised slim file for this repo would hold about 2100 code nodes (drop `rationale`
  into an optional `doc` field on the function node and `concept` into an `external` flag)
  with ~6200 edges; well under the inline force layout's comfort zone at the file level
  (43 files) and workable at the function level with a scope filter.

## Gotchas

- `Permission for this action was denied by the Claude Code auto mode classifier. Reason:
  [Code from External].`: the agent cannot run `uvx --from graphifyy` in auto mode; the user
  ran it in their own terminal (`!` prefix) and the agent read the output. The generator will
  hit the same wall when the ritual runs it from an agent session unless a permission rule
  allows that exact argv; `graphctl.py status` must say "not built, run graphctl build in
  your terminal" rather than fail CHECK.
- The docs and the main-branch `extract.py` undersell coverage: the installed 0.9.102
  extracts Bash (env vars), `settings.json` hook keys and workflow YAML, and emits
  `_callable`/`_callable_class` flags and `rationale` docstring nodes none of the docs
  describe. Fields change between releases ("Fields vary by extractor and release", docs
  `concepts/outputs.md`): the normaliser must pin the graphify version and fail loudly on an
  unknown `schema_version` (today `graph.graph.schema_version == 1`).
- `--code-only` still writes `.graphify_analysis.json` (communities, cohesion, gods,
  surprises) with no model; the "god nodes" list is a free hub view for the CODE tab.
- `graphify update` silently keeps the old graph if the new one has fewer nodes; a generator
  that deletes files must pass `--force`, or freshness will lie.

## Decisions taken from this

Proposal PR-15 filed (`statectl proposal add`, kind feature, source human): a CODE tab in the
console fed by an optional, registered generator. Shape, so the build task does not have to
rediscover it:

- **Extraction**: a new stdlib tool `.claude/tools/graphctl.py build` that (a) runs
  `graphify extract <root> --code-only --out <RECORD_ROOT>/graph --exclude ...` through
  `uvx --from graphifyy` when uv is present, and (b) falls back to a built-in Python `ast`
  walker (files, classes, functions, imports, calls) plus a `source:`-line scan of `.sh`
  hooks when it is not, so this repo and every kit always get a graph. Graphify's own
  `graphify-out/` lives under RECORD_ROOT, never in the repo.
- **Normalise** both sources to one slim schema at `.claude/system-map/code-graph.json`
  (gitignored, derived): `nodes[{id, kind: file|class|function|method|config, label, path,
  line, end_line, scope[], snippet?}]`, `edges[{from, to, type: contains|imports|calls|
  inherits|uses, confidence}]`, `scopes[{id, root, nodes, edges}]`, `stats`. `kind` is
  derived from graphify's label convention (`name()`, `.name()`, bare, filename).
- **Scopes**: `console.json` gains `code_graph.scopes`, default `[{"id":"repo","root":"."}]`
  plus `{"id":"src","root":"src"}` when `src/` exists (for this repo, `.claude/tools`). One
  extraction, N filtered views by path prefix; no second run.
- **Viewer**: a CODE tab in the console, inline canvas force layout written in the console's
  own JS (a few hundred lines, no vendored library: the self-contained rule stays), scope
  switcher, kind and edge-type filters, label search, click = focus neighbours, panel with
  path:line, the snippet, an "open in editor" link built from `project.root` when live, and a
  link to the MAP card whose `path` contains the file, so anatomy and code drill into each
  other. Served live via `/live/code-graph.json` (add to `read_allowlist`); the committed
  snapshot embeds only `stats`, so `console.html` does not balloon.
- **Registration**: `code_graph_build` in `checkctl.GENERATORS` with `output
  .claude/system-map/code-graph.json`, named in `memory.json` `phases.polish` before
  `console_build`; `policy.json` `derived_files` gains the output. Missing uv is a WARN,
  never a failed CHECK (telemetry fails open).
- **Never** `graphify install`: no third-party PreToolUse hook in the protected tree.

Confirmed by the user on 2026-11-04: go with graphify; the graph is rebuilt inside the ritual
AND has its own command. So `graphctl.py` gets three verbs: `build` (full extraction, the
generator's argv), `update` (incremental: `graphify update` on the cached manifest, `--force`
when files were deleted, then re-normalise), `status` (node and edge counts per scope, graph
age, freshness hash, whether uv and graphifyy resolve). `/project-memory` runs `build` only
when inputs changed (the generator's SHA-256 input set is the scope roots' tracked files);
`update` is what a session runs mid-work after a large edit, without the ritual. A `graphify`
pin (package version) lives in `console.json` `code_graph.graphify_version`, with a registry
card, so the uvx call is reproducible and bumping it is a visible config change.
Credit for graphify goes in `CREDITS.md` (proposal PR-16) the day the generator lands.

## Open threads

- Extraction confirmed on this repo (section "Measured" above); still to run once on
  `extrct_private` to see a non-`.claude` codebase and its node count before fixing the
  snippet budget and the per-node cap.
- Permission: decide whether the ritual may run `uvx --from graphifyy graphify ...` from an
  agent session (a settings permission rule for that argv) or whether `build` stays a
  human-terminal step with the ritual only normalising what exists.
- Whether the inline force layout stays readable above ~1500 nodes, or the CODE tab should
  default to the file level and expand a file to its functions on click (graphify's own
  viewer collapses to communities above 5000).
- Editor scheme: `vscode://` as default with a `console.json` `editor_url` knob; test on
  Windows where `file:` roots start with a drive letter.

## Pivot (2026-11-04, after the user saw graphify's graph.html)

The user's reference is **GitDiagram** (github.com/ahmedkhaleel2004/gitdiagram, MIT), not a
function-level force graph: a curated architecture diagram, about 20-40 boxes grouped into
subsystems with the main arrows, each box clickable to its file or folder on GitHub. "Too
much information without organisation" was the verdict on graphify's 2581-node viewer.

How GitDiagram does it (source: its `docs/architecture.md`): fetch the file tree and README,
one model call returns a strict graph (groups, nodes, edges, labels, repository paths), the
server validates every path against the real repo and retries with feedback, then a
**deterministic compiler emits Mermaid** with `click` links to GitHub only. The model never
writes Mermaid directly.

What this repo's data can and cannot give (measured 2026-11-04):

- Collapsing graphify's `imports`/`calls` edges to file-level boxes yields 11 boxes and 24
  arrows, all Python-to-Python (`tests -> _lib` 381, `statectl -> _lib` 114 ...). The
  architecture that matters (hooks invoking tools, tools writing stores, the ritual running
  generators, the console reading state) crosses Bash, JSON and Python and is invisible to
  it. Those relations already exist as the 69 cards' `reads`/`writes`/`invokes` (165 edges).
- Sample built from the cards, hand-curated to 26 boxes and 32 arrows, Mermaid with click
  links, published as an artifact: https://claude.ai/code/artifact/a2044b43-a2ba-4c86-bdf4-fa6ae98e5fe6
  (file: scratchpad `dot-claude-iff-architecture.html`). The user judges whether that is
  the target.

Revised PR-15 shape, two tiers from one spec:

1. **`architecture.json`** per scope (`{groups[{id,title,blurb}], nodes[{id,group,label,
   path,kind}], edges[{from,to,label,style}]}`), validated against real paths by stdlib
   code. For `.claude`: generated from the cards and layers by `mapctl` (deterministic; the
   anatomist already curates the cards). For a project's `src/`: skeleton from graphify's
   edges collapsed to files and folders, then the anatomist (or a new `diagrammer` step in
   the ritual, session model, no API key) names the groups and keeps the arrows that matter,
   exactly GitDiagram's model step but local; the output is a committed JSON diffed like any
   doc, so the "organised" judgement is reviewable.
2. **Renderers**, both deterministic: (a) `docs/ARCHITECTURE.md` with the Mermaid flowchart
   and `click` links to `github.com/<owner>/<repo>/blob/<branch>/<path>` (GitHub, VS Code
   and the docs site render Mermaid natively, no JS shipped); (b) the console MAP tab gains
   a scope switcher and renders the same spec with its existing band-and-SVG-lines renderer
   (no Mermaid library: mermaid.min.js is ~3 MB and the console stays self-contained), with
   the node dialog's path link plus an editor link when live.
   Graphify's function-level data becomes the drill-down inside a file node (tier 2 of the
   earlier design), not the first view.

Still true from the earlier design: `graphctl.py build|update|status`, uvx with a pinned
version, raw output under RECORD_ROOT, generator registered in the ritual, never
`graphify install`. Credit GitDiagram in CREDITS.md for the pipeline shape (PR-16).

Resolution of the permission thread (maintainer, 2026-11-04): the extraction is an optional
human-terminal command, chosen per repo. `graphctl.py build` checks for a `graphify` binary;
when absent it prints the exact command and exits 0 with a WARN, and the ritual only
normalises and renders whatever `graphify-out/` exists. Two safer forms than `uvx` from an
agent session: (1) the maintainer runs `uvx --from graphifyy==<pinned> graphify extract ...`
themselves; (2) `uv tool install graphifyy==<pinned>` once, so `graphify` is a fixed local
binary with no package fetch at run time, which a narrow allow rule
(`Bash(graphify extract:*)`) can name if the maintainer wants the ritual to run it.
