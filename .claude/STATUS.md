# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.2.1: the hardening release distilled from the first three real adoptions (issues #1-#3 -
Linux repo with published docs/, a divergent sibling system, Windows). All confirmed findings
fixed: Windows durable-write crash, the two distribution privacy leaks (now gated by
`memory.json distribution.enabled`, kits ship it false), adopt-skill surgery (tracked-only
copy manifest, CLAUDE.md exclusion, history guard, sibling mode), gitignore-shadow lint,
loud console-port collisions.

## Active tasks

- none. New work starts with `/plan-task`.

## Next steps

1. Merge the v0.2.1 PR; the three field-report issues close with it.
2. Re-run an `/adopt` against a real second repo to validate the new Phase 3/4 rules end to
   end (especially sibling mode and the decide-once console port).
3. Configure the analysis engine (STORY tab setup guide) and run a first retrospective pass.
4. Hard-gear session when ready: `/project-memory --hard`, with the queued improvements
   (a durable proposal drop-box for gate-denied sub-agent work; the deferred field-report
   items: verifier's scripted invoker, memory-spine stores in mapctl, a `command` kind for
   adopter `commands/*.md`).

## Blockers / open decisions

- none. The needs-human queue is empty.

## Watch-outs

- For every claimed invariant, write the test that tries to BREAK it before writing the
  sentence that asserts it (L-3).
- Any data shape crossing a tool boundary needs a named CONTRACT in the producer and a
  round-trip test; never re-specify a schema in two places (L-4).
- Theme tokens must land in BOTH dark blocks; patch them with a count==2 assertion, never a
  bare replace (L-7, which bit twice in one day).
