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

1. **Task file** written and registered, its Test runnable from the repo root.
2. **Worktree**, created by the lead from the working branch:
   `git worktree add .claude/worktrees/<name> -b wt/<name> <branch>`. The harness's own
   worktree isolation branches from the remote default branch, not the working branch, so
   it is not used.
3. **Brief**: a copy of `.claude/tasks/_builder-brief.md` with its placeholders filled (or one
   shared brief per milestone, as `030a-builder-brief.md` was).
4. **Stub**: `statectl.py dispatch <id> --agent builder --worktree .claude/worktrees/<name>`
   writes `<id>.stub.json` with `dispatched_at`; the console shows it in flight.
5. **Builder**: dispatch `builder` with a prompt naming the brief, the task file and the
   worktree name. Builders of one wave are dispatched together.

## Handoff

1. **Envelope**: the builder writes `<id>.json` in its worktree (`handshake.md`); a builder
   that stops without one is sent back once by the SubagentStop check.
2. **Validate**: `checkctl.py handoff <id> --run --root .claude/worktrees/<name>`: schema,
   status done, every `files_changed` path exists, every recorded test reruns as recorded.
3. **Review the diff** (`git -C .claude/worktrees/<name> diff`): read it as the owner, not as
   a stamp. Protected-tree changes (hooks, tools, config, agents, protocols, skills) get a
   line-by-line read.
4. **Merge** into the working branch; apply `needs_main` (index changes, zip rebuilds).
5. **Suite**: `python3 .claude/tools/tests/run_tests.py -q` on the merged branch.
6. **Close**: `statectl.py task <id> --status done`. In this mode it refuses without a valid
   envelope whose tests passed; a task the lead did itself closes with
   `--no-envelope "<why>"`, logged. Then remove the worktree.

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

## The mechanics behind it

| Mechanism | Where | Strength |
|---|---|---|
| Envelope schema | `_lib.validate_envelope`, used by `post-write-validate.sh`, `checkctl handoff`, the done guard | blocks a malformed envelope on write |
| Done guard | `statectl.py task <id> --status done` | hard: refuses without a valid envelope whose tests passed |
| Stop check | `hooks/handoff-guard.sh` on SubagentStop | best effort, once per stop, fails open |
| Delegation nudge | `post-write-validate.sh`, knob `orchestration.nudge_after` | advisory, never blocks |
| Cost split | `obsctl.py report --by agent` | tokens per agent type; `lead` is the main session |
