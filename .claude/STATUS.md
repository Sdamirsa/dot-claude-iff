# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

The stable 0.3.0 round (M-0.3.0) is open on `dev`. First task shipped: the interactive
anatomy map on the docs landing page (`docs/anatomy.html`, generator `anatomy_build`).
v0.3.0-beta.1 stays GitHub's latest release while the maintainer uses it in real projects for
a week; those findings become the rest of the milestone.

## Active tasks

- None. 031-anatomy-map is done and archived; the milestone waits for the beta findings.

## Next steps

1. The maintainer reviews the live map on the Pages site (landing page and `anatomy.html`) at
   desktop and phone width, in light and dark; findings become fix tasks under M-0.3.0.
2. Collect the week's findings from the adopting repos as proposals (`statectl proposal add`).
3. Plan the rest of the round from the open proposals: PR-21 upgrade path, PR-23 fresh-kit
   test assumptions, PR-24 CI row in CHECK, PR-25 checklist wording, PR-26/27 exit-check
   noise, PR-29 demo-page redaction, PR-30 pre-release as latest, PR-4 maturation pass.
4. Stable tag v0.3.0 on `main` (own version DOI, moves Latest by itself). Raised, not filed:
   an adopter's own "release vX" has no stamp of its own.

## Blockers / open decisions

- None blocking. Open proposals: `python3 .claude/tools/statectl.py proposal list`.
- Untested on the map: Safari, Firefox, real touch. Untested elsewhere: the Claude Chrome
  extension half of checklist item 4; a native PowerShell tool for the denial box.

## Watch-outs

- Before any push that moves `main`, `distctl.py verify` must say DIST_OK (L-19).
- After touching a builder's worktree, rerun `checkctl handoff <id> --root ...` before
  `statectl accept` (L-20).
- A task card's status line is the bare word; grep it after any header edit (L-18).
