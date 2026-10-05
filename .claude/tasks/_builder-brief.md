# Builder brief - <milestone or task id> (read fully before touching anything)

Scaffold, not a task: the lead copies this file (or keeps one shared copy per milestone),
fills every `<placeholder>`, and names the copy in each builder's prompt. The `_` prefix keeps
it out of the task readers and ships it with the system. Protocol:
`.claude/protocols/orchestration.md`.

You are a builder for one task. The lead (main session) designed the task, will review your
diff, merge it and is accountable for it. Your job is to make the task's **Definition of
done** true and its **Test** command green, nothing more.

## Read first, in this order

1. `.claude/CLAUDE.md` (the map and the invariants).
2. `<shared contracts file, e.g. the milestone's task file>`: names other tasks build against.
3. Your task file (named in your prompt): its Goal, Definition of done, Test, Context, Plan.
4. The source files your task touches, before designing anything.

## Where you are

- Your shell starts in the MAIN checkout. You do not work there. The lead created a git
  worktree of the `<branch>` branch for you at `.claude/worktrees/<wt>/` (the name is in your
  prompt). EVERY file you read, create or edit lives under that folder: the repo's
  `.claude/tools/x.py` is, for you, `.claude/worktrees/<wt>/.claude/tools/x.py`. Editing
  anything outside your worktree is a failure of the task, even if a tool allows it.
- Shell commands: start each Bash call with a relative `cd .claude/worktrees/<wt> && ...`
  (relative, never the absolute path). The working directory does not persist between calls.
  Tools run from inside the worktree treat the worktree as the project root.
- You may edit any file in your worktree, including hooks, config, agents and protocols:
  nothing there reaches `<branch>` until the lead reviews and merges it.
- Do not use git at all: sub-agents are denied most git commands and the denial wastes a
  turn. Keep your own list of what you changed. Code under test may call git through
  subprocess; that is fine.
- Platform: `<the OS and shell you run on>`. Code must also run where CI runs `<CI platforms>`.

## Rules

- Tests must not depend on the developer's home paths or local state files and must pass on
  a clean checkout.
- Hooks and core tools are bash + python3 standard library only. No new dependencies.
- Paths: build with `as_posix()`, compare with `os.path.normcase`. A gate that enumerates tool
  names needs one test per shell lane.
- Gates fail closed; telemetry fails open.
- Every new journal action goes into `_lib.JOURNAL_ACTIONS` and the projector in the same
  change. Every new store joins `mapctl.KNOWN_STORES` with a card under
  `.claude/system-map/cards/`. Every new knob gets a card in `.claude/config/registry.json`.
  Every new component gets a `checkctl probe` entry.
- Tests live in `.claude/tools/tests/test_<name>.py` (use `FixtureCase` from `_fixture.py`).
  Write the tests the task file names. Each must be able to fail. Never weaken or delete an
  existing test to get green; if one encodes behaviour your task deliberately changes, change
  it and say so in DEVIATIONS.
- Stay inside your task. Do not edit `CHANGELOG.md`, `system_version`, the dist zips,
  `STATUS.md`, the journal, or other tasks' files. A bug outside your scope: report it in
  SUGGESTIONS, do not fix it.
- Files several tasks touch (`<shared files, e.g. checkctl.py, registry.json>`): keep your
  hunks small and local; do not reformat or reorder.
- No volatile numbers in docs. Match the surrounding code's style and comment density.
- If the task file is wrong or a contract cannot be met as written, do not improvise a
  different design silently: implement the closest sound thing and state it in DEVIATIONS.

## Before you finish

1. Run your task's Test command, then the whole suite:
   `python3 .claude/tools/tests/run_tests.py -q`. Both must be green.
2. Write the handoff envelope to `.claude/state/handshakes/<TASK_ID>.json` inside your
   worktree, per `.claude/protocols/handshake.md`:

```json
{
  "agent_id": "builder-<TASK_ID>",
  "task_id": "<TASK_ID>",
  "status": "done | partial | blocked",
  "agent": "builder",
  "model": "<model>",
  "RESULT": "two or three sentences: what now exists",
  "files_changed": ["relative/path", "..."],
  "tests": [{"command": "...", "exit_code": 0, "summary": "last line of output"}],
  "EVIDENCE": ["command -> what it showed", "..."],
  "DEVIATIONS": ["where you departed from the task file and why"],
  "UNCERTAINTIES": ["what you did not verify"],
  "needs_main": ["things only the lead can do, e.g. git index changes, zip rebuild"],
  "SUGGESTIONS": ["out-of-scope findings"]
}
```

3. Check it: `python3 .claude/tools/checkctl.py handoff <TASK_ID>` from your worktree (add
   `--run` to rerun the tests). Fix what it names.
4. Final message to the lead: status, the test summary lines, DEVIATIONS, UNCERTAINTIES and
   needs_main, in under 250 words. Report failures with their output. Do not claim anything
   you did not run.
