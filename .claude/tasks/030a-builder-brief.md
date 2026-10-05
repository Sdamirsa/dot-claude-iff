# Builder brief - M-0.3.0-alpha (read fully before touching anything)

You are an Opus builder for one task of this milestone. The lead (main session) designed the
task, will review your diff, merge it and is accountable for it. Your job is to make the
task's **Definition of done** true and its **Test** command green, nothing more.

## Read first, in this order

1. `.claude/CLAUDE.md` (the map and the invariants).
2. `.claude/tasks/030a-00-milestone.md` (shared contracts: names other tasks build against).
3. Your task file (named in your prompt). Its Goal, Definition of done, Test, Context, Plan.
4. The source files your task touches, before designing anything.

## Where you are

- Your shell starts in the MAIN checkout. You do not work there. The lead created a git
  worktree of the `dev` branch for you at `.claude/worktrees/<wt>/` (the name is in your
  prompt). EVERY file you read, create or edit lives under that folder: the repo's
  `.claude/tools/x.py` is, for you, `.claude/worktrees/<wt>/.claude/tools/x.py`. Editing
  anything outside your worktree is a failure of the task, even if a tool allows it.
- Shell commands: start each Bash call with a relative `cd .claude/worktrees/<wt> && ...`
  (relative, never the absolute path: a guard trips on absolute paths here). The working
  directory does not persist between calls. Tools run from inside the worktree treat the
  worktree as the project root.
- You may edit any file in your worktree, including hooks, config, agents and protocols:
  nothing there reaches `dev` until the lead reviews and merges it.
- Do not use git at all: sub-agents are denied most git commands and the denial wastes a
  turn. To see what you changed, keep your own list as you go. Code under test may call git
  through subprocess; that is fine.
- Platform is Windows with Git Bash; `python3` works. Code must also run on Linux (CI).

## Rules

- CI runs the suite on Ubuntu and on Windows under Git Bash. Tests must not assume `bash` on PATH
  is Git Bash outside that, must not depend on the developer's home paths, and must pass on a
  clean checkout (no local state files, no heartbeat).

- Hooks and tools are bash + python3 standard library only. No new dependencies.
- Paths: build with `as_posix()`, compare with `os.path.normcase` (lesson L-9). A gate that
  enumerates tool names needs one test per shell lane (L-10).
- Gates fail closed; telemetry fails open.
- Every new journal action goes into `_lib.JOURNAL_ACTIONS` and the projector in the same
  change. Every new store joins `mapctl.KNOWN_STORES` and gets a card under
  `.claude/system-map/cards/`. Every new knob gets a card in `.claude/config/registry.json`.
  Every new component gets a `checkctl probe` entry. Existing tests enforce most of this.
- Tests live in `.claude/tools/tests/test_<name>.py` (auto-discovered; use `FixtureCase`
  from `_fixture.py`). Write the tests the task file names. Tests must be real: each must be
  able to fail. Never weaken or delete an existing test to get green; if an existing test
  encodes behaviour your task deliberately changes, change it and say so in DEVIATIONS.
- Stay inside your task. Do not edit `CHANGELOG.md`, `system_version`, `.claude/dist/*.zip`
  (the lead rebuilds zips at merge), `STATUS.md`, the journal, or other tasks' files. If you
  find a bug outside your scope, report it, do not fix it.
- When several tasks touch one file (`checkctl.py`, `registry.json`, `memory.json`,
  `test_contracts.py`), keep your hunks small and local; do not reformat or reorder.
- No volatile numbers in docs. Match the surrounding code's style and comment density.
- If the task file is wrong or a contract cannot be met as written, do not improvise a
  different design silently: implement the closest sound thing and state it in DEVIATIONS.

## Before you finish

1. Run your task's Test command, then the whole suite:
   `python3 .claude/tools/tests/run_tests.py -q` (about a minute). Both must be green.
2. Write the handoff envelope to `.claude/state/handshakes/<TASK_ID>.json` (TASK_ID like
   `T6`), valid JSON with exactly these keys:

```json
{
  "task_id": "T6",
  "agent_id": "builder-T6",
  "status": "ok | partial | blocked (same value as STATUS; the current validator hook wants both)",
  "agent": "builder",
  "model": "opus",
  "STATUS": "ok | partial | blocked",
  "RESULT": "two or three sentences: what now exists",
  "files_changed": ["relative/path", "..."],
  "tests": [{"command": "…", "exit_code": 0, "summary": "last line of output"}],
  "EVIDENCE": ["command -> what it showed", "..."],
  "DEVIATIONS": ["where you departed from the task file and why"],
  "UNCERTAINTIES": ["what you did not verify"],
  "needs_main": ["things only the lead can do, e.g. git index changes, zip rebuild"],
  "SUGGESTIONS": ["out-of-scope findings"]
}
```

3. Final message to the lead: STATUS, the test summary lines, DEVIATIONS, UNCERTAINTIES and
   needs_main, in under 250 words. Report failures with their output. Do not claim anything
   you did not run.
