# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.0-beta.1 is published (2026-11-09) and marked GitHub's latest release (2026-11-10), with
a Zenodo concept DOI. `main` carries the same tree as `dev`. The maintainer is using the beta
in real projects for a week; what breaks there becomes the stable 0.3.0 round.

## Active tasks

- None. Milestone M-0.3.0-beta.1 is complete and archived.

## Next steps

1. Confirm `main` and `dev` are green after this ritual's zip rebuild
   (`gh run list --limit 4`); the 2026-11-09 docs commit left main red on stale zips.
2. Collect the week's findings from the adopting repos as proposals (`statectl proposal add`).
3. Plan the stable round from the open proposals: PR-21 upgrade path, PR-23 fresh-kit test
   assumptions, PR-24 CI row in CHECK, PR-25 checklist wording, PR-26/27 exit-check noise,
   PR-29 demo-page redaction, PR-4 maturation pass. Stable tag v0.3.0 on `main` gets its own
   version DOI automatically.
4. Raised, not filed: an adopter's own "release vX" has no stamp of its own.

## Blockers / open decisions

- None blocking. Open proposals: `python3 .claude/tools/statectl.py proposal list`.
- Untested: the Claude Chrome extension half of checklist item 4; a native PowerShell tool for
  the sub-agent denial box.

## Watch-outs

- Before any push that moves `main`, `distctl.py verify` must say DIST_OK (L-19).
- Before PUBLISH commits, read the branch's CI verdicts; a red upstream run is a FAIL (L-17).
- A task card's status line is the bare word; grep it after any header edit (L-18).
