# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.1-beta.1 is cut from `dev` (2026-11-20): beta.2's system plus `/code-walk`, a user-invoked
skill that builds one offline HTML walk per version (a chapter per change, a diagram plus short
notes at `file:line`). The maintainer keeps using the beta in real projects; findings become
the stable round.

## Active tasks

- None. The code-walk skill is merged and released; no task card is open.

## Next steps

1. Confirm the release run landed green (`gh run list --workflow=release --limit 1`), the
   release is marked latest, and `main` sits on the release commit.
2. Write the first real walk: `python3 .claude/skills/code-walk/walk.py note v0.3.1-beta.1
   --base v0.3.0-beta.2`, then `/code-walk v0.3.1-beta.1`; open the page on a phone.
3. When Zenodo mints the new version DOI, add it to `CITATION.cff` identifiers.
4. Plan the stable round from the open proposals, PR-31 (graphctl's view into code-walk) among
   them: `python3 .claude/tools/statectl.py proposal list`.

## Blockers / open decisions

- None blocking. The stable's number is open: 0.3.0 never shipped stable and the betas are now
  at 0.3.1.
- A built walk page has not been opened in a browser from this repo; `page.html` is upstream's.

## Watch-outs

- Before any push that moves `main`, `distctl.py verify` must say DIST_OK (L-19).
- After touching a builder's worktree, rerun `checkctl handoff <id> --root ...` before
  `statectl accept` (L-20).
- Python with a backslash goes in a scratchpad file, never a bash heredoc; `py_compile` after
  a scripted edit (L-21).
