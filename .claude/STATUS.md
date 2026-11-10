# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.0-beta.2 is cut from `dev` (2026-11-10): beta.1's system plus the interactive anatomy map on
the docs site, retagged so the kits on the release page match the site. The maintainer uses it
in real projects for a week; findings become the rest of the stable round (M-0.3.0).

## Active tasks

- None. 031-anatomy-map is done and archived; M-0.3.0 waits for the beta findings.

## Next steps

1. Confirm the release run landed green (`gh run list --workflow=release --limit 1`), then
   mark the release latest: `gh release edit v0.3.0-beta.2 --prerelease=false --latest`; then
   fast-forward `main` to the release commit (zips are fresh on it) so the front page agrees.
2. When Zenodo mints the beta.2 version DOI, add it to `CITATION.cff` identifiers.
3. Collect the week's findings as proposals; plan the rest of M-0.3.0 from the open
   proposals (PR-21, 23, 24, 25, 26, 27, 29, 30, PR-4 maturation); stable tag v0.3.0 on `main`.
4. The maintainer reviews the live anatomy map at desktop and phone width, light and dark.

## Blockers / open decisions

- None blocking. Open proposals: `python3 .claude/tools/statectl.py proposal list`.
- Untested on the map: Safari, Firefox, real touch.

## Watch-outs

- Before any push that moves `main`, `distctl.py verify` must say DIST_OK (L-19).
- After touching a builder's worktree, rerun `checkctl handoff <id> --root ...` before
  `statectl accept` (L-20).
- A task card's status line is the bare word; grep it after any header edit (L-18).
