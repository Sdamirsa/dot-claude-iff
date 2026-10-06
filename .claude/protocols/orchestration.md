# Orchestration protocol: the lead and its team

How work is split in the `fableous-orchestrated` mode: the top model plans, designs and stays
accountable; cheaper agents build and research; every handoff is a JSON envelope that is
checked by code, not by trust.

**When it applies.** Only in `fableous-orchestrated` (`statectl.py mode fableous`). In
`freestyle` and `guided-solo` none of this runs: no handoff guard, no stop check, no nudge.

## Roles

| Role | Who | Owns |
|---|---|---|
| **lead** | the main session, the session's model | plans, designs, writes task cards, dispatches, reviews, merges, reports to the human; owns the outcome |
| **builder** | `agents/builder.md`, opus | implements ONE task card in a worktree; never commits |
| **scout** | `agents/scout.md`, sonnet | research, codebase surveys, quick checks; read-only |
| **verifier** | `agents/verifier.md`, inherit | adversarial check of a finished task; never weaker than the work |
| **anatomist** | `agents/anatomist.md` | cards and placement, unchanged |
| **retro-analyst** | `agents/retro-analyst.md` | propose-only evolution, unchanged |

## Routing

| Work | Goes to | Why |
|---|---|---|
| A decision, a design, a task card, a human question | lead | judgment is what the top model is for; it cannot be handed back |
| A task card with a Definition of done and one Test | builder | the long, token-heavy part runs on its own context; the lead reads a diff |
| "Where is X", "what calls Y", a doc or web lookup | scout | a cheap model finds facts; the lead's context stays for decisions |
| "Is this finished task really done?" | verifier | independence: the checker is not the producer |
| A change of a few lines, or one the lead understands end to end | lead | a dispatch costs more than it saves |

## The task card

The task file IS the card: `.claude/tasks/<id>.md` from `_template.md` via `/plan-task`, with
a Goal, a `**Definition of done:**`, one backticked `**Test:**` command, Context and Plan.
Registered under the milestone: `statectl.py task <id> --milestone <mid>`. The envelope is
named for the same id: `.claude/state/handshakes/<id>.json`.

## Dispatch

`statectl.py dispatch` and `statectl.py accept` are the lead's: they run git, which the gate
denies to sub-agents.

1. **Task file** written, registered and committed, its Test runnable from the repo root. The
   worktree is cut from HEAD, so anything uncommitted (outside `.claude/state/`, the zips and
   the derived files) would be invisible to the builder; dispatch refuses until it is committed.
2. **One command**: `statectl.py dispatch <id>` cuts `.claude/worktrees/<id lowercased>` on a
   new branch `wt/<id lowercased>` from HEAD, writes `<id>.stub.json` with `dispatched_at`
   (the console shows it in flight) and prints `.claude/tasks/_builder-brief.md` filled with
   the task id, task file, worktree, branch and first-read list. It refuses with one line and
   changes nothing when no task file maps to the id, the worktree or branch exists, the tree
   is dirty, or this is not a git repo. `--agent scout` (any non-builder) writes the stub only.
   The harness's own worktree isolation branches from the remote default branch, not the
   working branch, so it is not used.
3. **Builder**: dispatch `builder` with the printed brief as its prompt. Builders of one wave
   are dispatched together.

By hand, the fallback: `git worktree add .claude/worktrees/<name> -b wt/<name> <branch>`, then
`statectl.py dispatch <id> --worktree .claude/worktrees/<name> --no-worktree` for the stub.

## Handoff

1. **Envelope**: the builder writes `<id>.json` in its worktree (`handshake.md`); a builder
   that stops without one is sent back once by the SubagentStop check.
2. **Review the diff** (`git -C .claude/worktrees/<name> status` and `diff`; new files show
   only in status): read it as the owner, not as a stamp. Protected-tree changes (hooks,
   tools, config, agents, protocols, skills) get a line-by-line read.
   `checkctl.py handoff <id> --run --root .claude/worktrees/<name>` shows the validation alone.
3. **Accept**: `statectl.py accept <id>` reruns that handoff check (schema, status done,
   `files_changed` exist, recorded tests rerun as recorded; `--no-run` skips the rerun) and
   refuses on any FAIL with nothing committed. It then restores `.claude/dist/` and the
   derived files (`policy.json` `derived_files`) to HEAD in the worktree, stages the rest, sets
   the exec bit on `.claude/hooks/*.sh` in the index, commits there (`--message`, default from
   the task title), merges into the working branch with `--no-ff`, and removes the worktree and
   its branch. On a conflict it stops: the merge stays in progress, the conflicted paths and
   the commands to finish or abort are printed, the worktree is kept. It never pushes and
   never marks the task done. Apply the rest of `needs_main` yourself.
4. **Suite**: `python3 .claude/tools/tests/run_tests.py -q` on the merged branch.
5. **Close**: `statectl.py task <id> --status done`. In this mode it refuses without a valid
   envelope whose tests passed; a task the lead did itself closes with
   `--no-envelope "<why>"`, logged.

By hand, the fallback: commit in the worktree, `git merge --no-ff wt/<name>`, then
`git worktree remove .claude/worktrees/<name>` and `git branch -d wt/<name>`.

- **Never resume a builder whose worktree no longer exists: re-dispatch.** Its brief, stub and
  context name a checkout that is gone; `accept` refuses such a task and says so.
- **Builders never stage or commit `.claude/dist/`** (nor rebuild the zips): where a project
  ships zips, its release step rebuilds and commits them, and `accept` discards any change
  there, so a merge on the working branch never conflicts on a zip.

## Waves

Plan waves by file overlap: tasks that touch disjoint files run in parallel; tasks that share
a file run in later waves, after the first is merged. Shared files that many tasks touch
(`checkctl.py`, `registry.json`, test contracts) take small local hunks, never reformatting.
Write the waves into the milestone file before the first dispatch.

## The lead never delegates

- Decisions and design: what to build, the contracts tasks build against.
- Human gates (`human-gates.md`): a builder or scout parks a question in its envelope; the
  lead asks the human.
- Merges and anything touching git history.
- Review of protected-tree changes: a sub-agent proposes, the lead decides.
- The report to the human, including the failures.

## When not to orchestrate

- A small task (a few lines, one file, already understood): the lead does it and closes it
  with `--no-envelope`. The delegation nudge is advisory for exactly this reason.
- `freestyle` and `guided-solo`: one agent, none of this applies.
- Work that is mostly deciding: a builder cannot decide for you.

## Progress: the human sees the run at a glance

A run can last hours; the maintainer must never have to ask how far it is. One progress model
(`tools/progress.py`): milestone bar, tasks done / in progress / waiting with checklist counts,
agents in flight, what needs the human, run time, last activity.

- **The console is the always-on view**: the Progress panel at the top of NOW, live.
- **The lead posts the block at each merge** (`statectl.py progress`), as is, in the chat.
- **The periodic report**: every `progress.report_minutes` the post-write hook hands the lead
  the same block with one instruction; post it to the user as is, then continue. Also in
  `guided-solo`; never for a sub-agent; never in `freestyle`.
- **Last activity stays true** mid-turn: the activity pulse refreshes the heartbeat from hooks
  that already run (`progress.pulse_seconds`).

## The mechanics behind it

| Mechanism | Where | Strength |
|---|---|---|
| Envelope schema | `_lib.validate_envelope`, used by `post-write-validate.sh`, `checkctl handoff`, the done guard | blocks a malformed envelope on write |
| Dispatch and accept | `statectl.py dispatch` / `accept` | refuse with one line, change nothing; a conflict stops the merge |
| Done guard | `statectl.py task <id> --status done` | hard: refuses without a valid envelope whose tests passed |
| Stop check | `hooks/handoff-guard.sh` on SubagentStop | best effort, once per stop, fails open |
| Delegation nudge | `post-write-validate.sh`, knob `orchestration.nudge_after` | advisory, never blocks |
| Progress report | `post-write-validate.sh`, knob `progress.report_minutes` | advisory, never blocks |
| Activity pulse | `policy_gate.py` after its decision, `obs-capture.sh` on sub-agent events | telemetry, fails open |
| Cost split | `obsctl.py report --by agent` | tokens per agent type; `lead` is the main session |
