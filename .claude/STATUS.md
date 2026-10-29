# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.0-alpha.2 is published as a GitHub pre-release from `dev` (2026-10-29): the Cview-ready
backbone (a session inside a linked worktree is a worker; main's state is written only from
the main checkout; the journal's projections are gitignored; worker and reviewer sections in
`CLAUDE.md`), plus the license set (Apache-2.0, NOTICE, `CITATION.cff`) and the road-to-beta
checklist, both committed on `dev` after the tag. Design record:
`.claude/research/2026-10-29-cview-backbone-issue.md`. "Latest" on GitHub is still the
alpha.1 release; the stable 0.3.0 waits for the beta.

## Active tasks

- none. The 0.3.0-alpha milestone's task files are archived in `.claude/tasks/archive/20261029/`.
  The next work is manual testing, not building.

## Next steps

1. **Sync the clones first.** This session ran in `GIT/_sources/dot-claude-iff`, a second clone;
   `GIT/dot-claude-iff` is the canonical one (editor, record history). Push `dev` from
   `_sources`; in the canonical clone discard the two derived state files, stash the `.vscode`
   gitignore line, `git pull --ff-only`, pop; copy today's spool file into its record root;
   then delete the `_sources` clone. Work only in `GIT/dot-claude-iff` from then on.
2. Work through `docs/alpha-test-checklist.md`; each item names where it runs. Item 1 (the
   ritual) is now evidenced; start at item 4 (console in a browser), then 10 (worktree
   session), then 7 (`/adopt --upgrade` into `extrct_private`, then Cview's eight-point prompt).
3. When every box is ticked once: "release 0.3.0-beta.1" from `dev`. After the beta holds:
   merge `dev` -> `main`, the stable tag, the CHANGELOG move to `.github/`, the Zenodo switch
   (proposal PR-13). Before that, the maturation pass (proposal PR-4).

## Blockers / open decisions

- Issue replies for #10, #16, #17, #18 are drafted in the session report, not posted: the
  maintainer decides.
- Open proposals: `python3 .claude/tools/statectl.py proposal list`.

## Watch-outs

- Two clones of this repo exist on this machine until step 1 above is done: check
  `git rev-parse --show-toplevel` before committing (lesson L-11).
- Names of files the system reads but never ships (TASK.md, DONE.md, .cview/) go in prose,
  never backticks, in `CLAUDE.md` and the adopt template: the dead-path lint flags them (L-12).
- Hook matchers must be the documented union of write tools, not a hand list (L-10).
- The policy gate is a tripwire for honest mistakes, not a sandbox. Windows: a bare `bash` can
  be the WSL launcher; hooks resolve Git Bash explicitly. The zips on `dev` may lag between
  releases; `main` and every tag are exact.
