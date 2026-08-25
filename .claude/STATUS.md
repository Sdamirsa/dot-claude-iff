# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

Phase 2 is complete: the system is built and runs on itself. All 37 components probe green, 140
tests pass, and the full ritual (check, polish, publish) executes on this repo as one
transaction. Next is real use, which is the only thing that will tell us what to evolve.

## Active tasks

- [20260825-phase-2-implementation](tasks/20260825-phase-2-implementation.md) - closing; the
  final code review is triaged and every finding is fixed with a regression test.

## Next steps

1. Use it for a real session and run `/project-memory` at the end. The first retro will likely
   be NO-CHANGES, and that is the correct outcome.
2. Fill `.claude/config/model-prices.json` with verified prices so costs stop reading "unknown"
   (queued as NH item, category provide-input).
3. Decide whether hooks should be trusted in this project (they are wired in
   `.claude/settings.json`; Claude Code asks once). Until then, capture and the resume block
   only run when invoked by hand.
4. Adopt into a second project with `/adopt` to test the claim that this generalizes.
5. Let flow layers emerge through evolution. Two components sit unplaced on purpose.

## Blockers / open decisions

- None. The eight design decisions were settled 2026-08-25 and are recorded in
  `.claude/research/2026-08-24-system-design.md` §1.

## Watch-outs

- For every claimed invariant, write the test that tries to BREAK it before writing the sentence
  that asserts it. If the attack is not in the suite, do not make the claim (L-3).
- Any data shape crossing a tool boundary needs a named CONTRACT in the producer and a
  round-trip test; never re-specify a schema in two places (L-4).
- Never pass a tool payload through the environment: above 128KB the process never starts, and
  a gate that emits no decision reads as allow (L-5).
