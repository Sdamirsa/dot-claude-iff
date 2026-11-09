# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.0-beta.1 is cut from `dev` (2026-11-09): the alpha checklist ran live end to end, the fix
round (T21) is verified by the maintainer, and the release workflow publishes the pre-release.
The next work is letting the beta hold and preparing the stable 0.3.0.

## Active tasks

- None. Milestone M-0.3.0-beta.1 is complete (nine tasks done, all archived).

## Next steps

1. Confirm the release workflow landed green on both OS (`gh run list --workflow=release`), and
   that the GitHub pre-release shows the v0.3.0-beta.1 changelog section as its notes.
2. Let the beta hold: adopt it into one real project from the GitHub link and run a session or
   two there; findings become proposals.
3. Plan the stable round from the open proposals: PR-21 (documented 3-way upgrade path), PR-23
   (fresh-kit test assumptions), PR-24 (CI row in CHECK), PR-26/27 (exit-check duplicates and
   stdout noise), PR-4 maturation pass; then merge to `main`, stable tag, PR-13 Zenodo.
4. The release steps are home-only; an adopter's "release vX" has no stamp of its own (raised,
   not filed).

## Blockers / open decisions

- None blocking. Open proposals: `python3 .claude/tools/statectl.py proposal list`.
- The Claude Chrome extension half of checklist item 4 and a native PowerShell tool for the
  sub-agent denial box are untested; both are noted in the checklist.

## Watch-outs

- Before PUBLISH commits, read the branch's CI verdicts; a red upstream run is a FAIL (L-17).
- A task card's status line is the bare word; grep it after any header edit (L-18).
- A probe of something the code did not create gets a test where a different interpreter
  created it, and its Windows branch runs on Windows (L-16).
