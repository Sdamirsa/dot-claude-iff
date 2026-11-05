# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

The build round for v0.3.0-beta.1 is merged into `dev`: waves 1-3 (T14-T20) closed with
their envelopes, the wave-3 verifier's two findings fixed, suite green. What remains before
the beta cut is the human review round; the release itself is the maintainer's call.

## Active tasks

- `030b-00-milestone.md` (M-0.3.0-beta.1): two plan items left, the review round and the release.

## Next steps

1. **Review round** (phase review, guided-solo): run `docs/alpha-test-checklist.md` items 4,
   10 and 7 in a fresh session; `/adhd` with the PR-12 question (sonnet vs inherit for the
   branch model); every finding becomes a proposal or a task card; sign off the phase.
2. **Tune the diagram**: open the console MAP tab, switch to the `claude` scope, and set
   `architecture.max_nodes` in `console.json` if it is denser than the approved sample.
3. **Release 0.3.0-beta.1** from `dev` per the ritual's release steps (maintainer names it).
4. After it holds: PR-4 maturation pass, merge to `main`, stable tag, PR-13 Zenodo.

## Blockers / open decisions

- graphify extraction stays a human-terminal command; `graphctl build` only normalises
  what exists under the record root and prints the exact command otherwise.
- PR-5 scope: the deny layer names only universal denies (deny rules bind the main session too).
- Open proposals: `python3 .claude/tools/statectl.py proposal list` (PR-1, 4, 7, 12, 13).

## Watch-outs

- A reference URL is not a specification: show one sample of the intended output before
  filing a feature proposal (L-13).
- Test fixtures for an external format are cut from a real sample of that output, never
  typed from memory (L-15).
- A gitignored file under .claude/ needs three entries: the managed gitignore block, the kit
  exclude list and checkctl's deliberate-ignore list; CHECK warns when one is missing.
