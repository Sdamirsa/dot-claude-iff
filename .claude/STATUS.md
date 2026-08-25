# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

The system is live and public: v0.1.1 released, tour and demo console on Pages, README as a
showcase funnel, Partner Guide as the human learning path. The build-and-polish arc is done;
what remains is real use.

## Active tasks

- none. The Phase 2 build task is archived; new work starts with `/plan-task`.

## Next steps

1. Use the system for real project work and run `/project-memory` at natural boundaries.
2. Adopt into a second, real repo with `/adopt` to exercise the kit beyond the smoke test.
3. Configure the analysis engine (STORY tab setup guide) and run a first retrospective pass.
4. Hard-gear session when ready: `/project-memory --hard`, with the two queued improvements
   (adopt from git HEAD instead of the working tree; a durable proposal drop-box for
   gate-denied sub-agent work).

## Blockers / open decisions

- none. The needs-human queue is empty.

## Watch-outs

- For every claimed invariant, write the test that tries to BREAK it before writing the
  sentence that asserts it (L-3).
- Any data shape crossing a tool boundary needs a named CONTRACT in the producer and a
  round-trip test; never re-specify a schema in two places (L-4).
- Theme tokens must land in BOTH dark blocks; patch them with a count==2 assertion, never a
  bare replace (L-7, which bit twice in one day).
