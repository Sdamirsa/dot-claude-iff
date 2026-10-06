# STATUS

_Rewritten by `/project-memory`. Read this first, every session. This copy was hand-updated at
the v0.3.0-alpha.1 close-out; the first ritual of 0.3 rewrites it properly._

## Current focus

v0.3.0-alpha.1 is published as a GitHub pre-release (2026-10-06) and, at the maintainer's
decision, merged to `main` (PR #15); the GitHub "latest" release stays v0.2.2 until the stable tag. The alpha adds modes, phases, the user-only
ritual with ROUTE, Fableous Orchestrated mode, the progress view, secrets check and doctor,
visibility and export, folder context, and `/adhd`. Issues #7 to #14 are answered and kept
open. None of the new hooks has run in a live session yet.

## Active tasks

- none. The milestone's task files are done and still in `.claude/tasks/030a-*.md`; the
  first ritual archives them.

## Next steps

1. **In a new session, type `/project-memory`.** It is item 1 of `docs/alpha-test-checklist.md`
   and the first live test of the ritual ticket. If it refuses, run
   `python3 .claude/tools/checkctl.py ticket --grant` in your own terminal.
2. Work through the rest of `docs/alpha-test-checklist.md` over the testing weeks; file
   findings as issues or with `statectl proposal add`.
3. When satisfied: merge `dev` -> `main` again, then the stable tag on `main`
   (`.claude/reference/release-flow.md`). Before that, the maturation pass (proposal PR-4).

## Blockers / open decisions

- none on the queue. Open proposals: `python3 .claude/tools/statectl.py proposal list`.

## Watch-outs

- Windows: a bare `bash` can be the WSL launcher; hooks and tests resolve Git Bash explicitly
  (`_lib.find_bash`). Hook files are pinned to LF and force UTF-8 for their Python children.
- The policy gate is a tripwire for honest mistakes, not a sandbox (README lists what it does
  not stop). Builders hold no grant in the protected tree; they work in lead-made worktrees.
- Harness worktree isolation branches from `origin/main`, not the working branch: use
  `statectl dispatch`, never resume a builder whose worktree is gone.
- The build exit check reruns every task's Test command; with a dozen tasks that exceeds ten
  minutes (proposal filed). The zips on `dev` may lag between releases; `main` and tags are exact.
