# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

The human review round for v0.3.0-beta.1 is signed off (items 4, 7, 10 run live) and its fix
round, task 030b-t21, is implemented with the suite green. What remains before the beta cut is
the verification pass on the fixes and the release itself, which the maintainer names.

## Active tasks

- `030b-t21-beta1-fix-round` (doing): every edit is in and the suite passes; open items are the
  zips rebuilt by this ritual and the human's verification pass.

## Next steps

1. **Verify the fixes** (the human, Place D and A): fresh adoption from the new zip plus the Cview
   paste, expecting eight PASS (E4 on intent); one new session here prints the console URL
   instead of the port-collision line. Then close T21 and run the build exit.
2. **Three live boxes still owed** from items 1, 5, 6: the plain-words ritual refusal, the
   deploy-phase proposal reflex, the three sub-agent denials (about fifteen minutes).
3. **Release 0.3.0-beta.1** from `dev` per the ritual's release steps, when the maintainer says so.
4. After it holds: PR-21 (documented 3-way upgrade path), PR-23 (fresh-kit test assumptions),
   PR-4 maturation pass, merge to `main`, stable tag, PR-13 Zenodo.

## Blockers / open decisions

- None blocking. The suite now takes about eleven minutes here; the CHECK step allows twenty.
- The Claude Chrome extension half of item 4 is still untested; the browser half passed.
- Open proposals: `python3 .claude/tools/statectl.py proposal list` (PR-1, 4, 7, 13, 21, 23).

## Watch-outs

- A reference URL is not a specification: show one sample of the intended output before
  filing a feature proposal (L-13).
- Test fixtures for an external format are cut from a real sample of that output, never
  typed from memory (L-15).
- A probe of something the code did not create (a pid, a port) gets a test where a different
  interpreter created it, and its Windows branch runs on Windows before the card closes (L-16).
