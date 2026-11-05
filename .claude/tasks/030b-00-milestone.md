# Task: Milestone M-0.3.0-beta.1 - index, waves and shared contracts

_Created 2026-11-04 · Status: doing_

Milestone: M-0.3.0-beta.1

## Goal

Close the fixes triaged on 2026-11-04 (PR-8, PR-9, PR-10, PR-11, PR-16), evaluate PR-5, trim
CLAUDE.md (PR-14) and land the code-graph console feature (PR-15), so `v0.3.0-beta.1` can be
cut from `dev` after a human review round.

**Definition of done:** every task file `030b-t*.md` is done with its Test green on Windows
and (in CI) Ubuntu; the suite is green; the proposals above are resolved as planned with a
note naming the task that closed them.

**Test:** `python3 .claude/tools/tests/run_tests.py -q`

## Context

- Triage and the three-session track: `.claude/STATUS.md` (2026-11-04) and the ritual commit
  b77f10f. Code-graph design: `.claude/research/2026-11-04-code-graph-console.md`.
- Mode `fableous-orchestrated`: builders (opus) work in worktrees cut by `statectl dispatch`,
  never commit; the lead reviews the diff, `statectl accept`, runs the suite, closes the task.
- Bugs first, then only-necessary features (the maintainer's instruction for this round).
- Deny rules (PR-5) research, 2026-11-04: Claude Code settings `permissions.deny` binds the
  main session and every subagent alike (no settings-level subagent-only deny); `Edit(...)`
  rules use gitignore syntax and cover Write; `Bash(...)` rules are prefix matches checked
  per subcommand and are documented as "not a security boundary"; a PreToolUse hook cannot
  override a deny; the sandbox is off by default and unsandboxed on native Windows.

## Waves (by file overlap)

| Wave | Task | Proposal | Files it owns |
|---|---|---|---|
| 1 | T14 | PR-10 | `tools/statectl.py` (`_accept_merge`), `tests/test_orchestration.py` |
| 1 | T15 | PR-9, PR-11 | `tools/checkctl.py` (`generator_freshness_report`, `exit_build`), `tests/test_ritual.py`, `tests/test_phases.py` |
| 1 | T16 | PR-8 | `tools/obsctl.py` (analyze), `config/observe.json`, `config/registry.json` (one card), `tests/test_analyze.py` |
| 1 | T17 | PR-16 | `CREDITS.md`, `tools/distctl.py` (`LEGAL_FILES` neighbourhood), `tests/test_dist.py` |
| 2 (lead) | - | PR-14, PR-5 | `.claude/CLAUDE.md`; `settings.json` deny rules (done 2026-11-04) |
| 3 | T18 | PR-15 tier 1 | `tools/mapctl.py` (`architecture` verb), `tools/checkctl.py` (one GENERATORS entry), `config/memory.json`, `config/policy.json`, `config/console.json` (knobs + allowlist), `config/registry.json` (cards), `docs/ARCHITECTURE.md`, `tests/test_architecture.py` |
| 3 | T19 | PR-15 tier 2 | new `tools/graphctl.py`, `tests/test_graph.py`, `.gitignore` (one line) |
| 3 | T20 | PR-15 viewer | `console/console.template.html`, `tools/consolectl.py`, `console/console.py` (if needed), `tests/test_console.py` |

Shared files nobody may reformat: `checkctl.py`, `registry.json`, `_lib.py`, `console.json`.
Wave 3 is disjoint by file; T18 and T19 meet only through the contract below.

## Shared contracts for wave 3 (PR-15; design: `.claude/research/2026-11-04-code-graph-console.md`)

**The architecture spec**, `.claude/system-map/architecture.json` (derived, committed, written
only by `mapctl architecture`):

```json
{"v": 1, "generated_at": "<utc>", "project": "<name>",
 "scopes": [{"id": "claude", "title": ".claude system", "root": ".claude",
             "source": "cards",
             "groups": [{"id": "govern", "title": "GOVERN", "blurb": "<one line>"}],
             "nodes":  [{"id": "tool.statectl", "group": "govern", "label": "statectl",
                         "path": ".claude/tools/statectl.py", "kind": "code|agent|data|human|folder|file"}],
             "edges":  [{"from": "tool.statectl", "to": "store.journal", "label": "writes",
                         "style": "solid|dashed", "weight": 3}],
             "stats": {"nodes": 0, "edges": 0, "folded": 0}}]}
```

- Every `path` is validated by stdlib code against the real tree (relative to the repo root,
  `as_posix()`); a node whose path is missing fails the build (gates fail closed).
- Scope `claude` is built from the cards by T18, deterministically: groups = the map's
  layers (`by_layer` names, `UNPLACED` last); nodes = cards, except that `data` cards under
  one folder fold into one `folder` node per folder (`.claude/state/`, `.claude/config/`,
  `.claude/system-map/`, `.claude-iff/`, the record root) and test files are excluded;
  edges = `reads|writes|invokes` aggregated between the resulting boxes (`weight` = count,
  self-edges dropped). A knob `architecture.max_nodes` (`console.json`, default 40) folds the
  lowest-degree nodes of a group into one "other" node per group until under the cap.
- Scopes other than `claude` come from `graphctl.architecture_scope(root)` (T19) when that
  module exists; T18 imports it inside a try and skips such scopes with a WARN line otherwise.
  Scope list knob: `architecture.scopes` in `console.json`, default `[{"id": "claude",
  "root": ".claude", "source": "cards"}, {"id": "tools", "root": ".claude/tools",
  "source": "code"}]`.
- Renderer (T18): `docs/ARCHITECTURE.md`, one Mermaid `flowchart LR` per scope, groups as
  `subgraph`, `click <id> "<url>"` to `https://github.com/<owner>/<repo>/blob/<branch>/<path>`
  when `git remote get-url origin` is a GitHub URL, else a relative link; regenerated by the
  generator `architecture_build` (tool `mapctl.py`, args `architecture`, inputs the cards dir
  and `console.json`, output `architecture.json`), named in `memory.json` `phases.polish`
  before `console_build`; both outputs join `policy.json` `derived_files`.
- Code graph (T19): `.claude/system-map/code-graph.json` (gitignored, never committed), slim
  schema `nodes[{id, kind: file|class|function|method, label, path, line, end_line}]`,
  `edges[{from, to, type: contains|imports|calls|inherits, confidence}]`, `stats`. Sources:
  graphify's `graphify-out/graph.json` under `RECORD_ROOT/graph/` when present (the human ran
  it), else the stdlib `ast` walker (Python) plus a `source:`-line scan of `.sh` files.
  `graphctl.architecture_scope(root)` collapses it to files and folders (max_nodes applies)
  in the architecture node/edge shape above, kind `file|folder`, edge labels `imports|calls`.
- Console (T20): the payload gains `architecture` (the file's content, or null); the server's
  `read_allowlist` gains `system-map/architecture.json` (T18 edits `console.json`; T20 may
  add the same line if it lands first, the merge keeps one). The MAP tab gets a scope switcher:
  `cards` (today's view, default) plus one entry per spec scope, rendered with the existing
  band-and-SVG-lines renderer (groups as bands); the node dialog shows path, the group blurb,
  the edge list, `pathLink` and, when live, an editor link `vscode://file/<abs path>` built
  from `project.root`. No library, no force layout.

## Plan

- [x] Wave 1 dispatched and accepted, done when: four `accept` merges and the suite green (2026-11-04, 818 tests and the kit self-test green)
- [x] Wave 2 (lead), done when: `mapctl context` and `context_health` accept CLAUDE.md; PR-5 decided (both committed 2026-11-04)
- [x] T18, T19, T20 accepted (2026-11-04), done when: `test_graph` green and the console MAP tab shows the `.claude` scope (suite 889 green; verifier envelope verify-wave3-030b)
- [x] Proposals resolved (2026-11-04), done when: `statectl proposal list` shows none of PR-8/9/10/11/14/15/16 open
- [ ] Review round (the human), done when: checklist items 4, 10 and 7 are run, findings are proposals or cards, and the review phase has a sign-off
- [ ] Release 0.3.0-beta.1 from dev, done when: the tag's release workflow is green

## Checkpoint

- **Last completed:** waves 1-3 accepted, verifier fixes merged, suite green on dev
- **Next action:** review round (STATUS step 4), then release 0.3.0-beta.1 from dev
- **State files:** `.claude/state/handshakes/verify-wave3-030b.json`
- **Updated:** 2026-11-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome
