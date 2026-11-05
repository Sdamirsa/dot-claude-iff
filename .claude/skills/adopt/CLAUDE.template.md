# {{PROJECT_NAME}} - developer and agent guide

{{MISSION}}

**One place to interact** (Claude Code) · **one place to control** (the console) · **one command
to evolve** (`/project-memory`) - on an always-on record, behind fail-closed gates.

> Start here: `.claude/STATUS.md` (where we are) · `.claude/tasks/` (active work) · the console
> (its port derives from the folder name; every session start prints the URL that bound).
> This file is a map, not a dump: the protocols hold the rules, `.claude/README.md` the manual.

## Session ritual

- **Start:** the SessionStart hook prints the resume block. Read `.claude/STATUS.md` and the
  active task file. Before anything long or risky, set a pointer:
  `python3 .claude/tools/statectl.py pointer "<next concrete action>"` - a mid-turn cutoff
  never fires the Stop hook; the pointer on disk is what survives.
- **During:** follow `.claude/protocols/`: handshake, human-gates, honesty, evolution,
  orchestration. Honesty is the working agreement (faithful reporting, mistakes logged on both
  sides, pushback then align, no volatile numbers in docs) and the communication rules.
- **End:** the user types `/project-memory`; you suggest it at a natural boundary. The ritual
  is the user's command: `checkctl` refuses a run without the ticket their prompt mints.

## In a worktree (a worker)

Applies when the SessionStart hook printed a WORKTREE SESSION block or TASK.md sits at the
checkout root. The task is TASK.md: edit only the files its Owns section lists; commit often
on this branch, never push; never edit TASK.md. Done: write DONE.md (`# DONE`, `summary:`,
`tests:`); stuck: BLOCKED.md with the question; then stop. The state folder and the ritual are
the main checkout's (`statectl`, the gate and `checkctl` refuse them here); it reviews the diff.

## Reviewing under Cview (the main session)

Applies when .cview/ exists at the repo root. On `REVIEW <task> round <n>`: run
`/code-review main...worktree-<task>`, decide with `cview verdict <task> pass|reject
--reasons "..."`; never edit a worktree or merge: the human merges from Intake and pushes.

## Communication

- Answer or verdict first. At most four options, exactly one marked as the pick with a
  one-line reason; rejected options listed apart, one line each.
- An ask with "quick" or "just" gets three lines or fewer.
- "Not enough evidence, missing X" is a valid pick.
- Brevity never trims failure output or the list of skipped steps: `honesty.md` wins.

## Three laws

1. **Anti-rot.** Every generator is registered in `.claude/config/memory.json` and runs only
   through `/project-memory`. An unregistered generator rots.
2. **Gates fail closed, telemetry fails open.** A broken validator blocks the write; a broken
   capture hook loses an event and nothing else.
3. **The record is radioactive.** Verbatim content never enters git. It lives in `RECORD_ROOT`,
   a sibling folder outside the repo; only allowlisted metadata and the anchor are committed.

## Stack

{{STACK_SUMMARY}}

```
{{DEV_COMMANDS}}
```

## Commands (each takes `--help`; `.claude/README.md` walks through them)

```
python3 .claude/tools/statectl.py   start|pointer|task|milestone|decision|loop|note|intent|gate
                                    need|tooling|device|refresh|resume|status · mode|phase|proposal
                                    dispatch|accept|progress (dispatch, accept: the lead only)
python3 .claude/tools/checkctl.py   run --phase check|polish|publish (inside the ritual) · status
                                    probe · generators · doctor · phase-exit · handoff <task_id>
                                    ticket --grant (the HUMAN, in their own terminal; never agents)
python3 .claude/tools/distctl.py    export|gitignore|verify
python3 .claude/tools/mapctl.py     scan|lint|compile|show|context|architecture
python3 .claude/tools/graphctl.py   build|update|status (code graph; extraction is the human's)
python3 .claude/tools/obsctl.py     ingest|seal|rollup|anchor|report|story|size|analyze
python3 .claude/tools/consolectl.py build|payload|serve · sysmon.py · tests/run_tests.py
```

- **Three dials**: mode (`statectl mode`: freestyle, guided-solo, fableous-orchestrated),
  phase (`statectl phase`: plan → build → review → deploy, each exit run by `phase-exit`), and
  the ritual, user-typed only, which ends with ROUTE (next phase, next mode, maturation pass).
- **Proposals** (`statectl proposal add`): parked ideas; in deploy, new features go here.
- **Orchestration** (`protocols/orchestration.md`): lead-only dispatch/accept, handoff, progress.
- **Health** `checkctl doctor` · **export** `distctl export` · **secrets** `reference/secrets.md`
  · **folder context** `mapctl context` (which guides and rules load where).

Skills: `/project-memory` (the user's) · `/plan-task` · `/adopt` · `/adhd` (a user-invoked
brainstorm of about ten agent calls: suggest it in one line for an open question, never run it).
Agents: `anatomist` (anatomy, cards, placement, pruning) · `retro-analyst` (propose-only
evolution) · `verifier` (adversarial claim checking) · `builder` (one task card in a worktree)
· `scout` (read-only research); the last two per `protocols/orchestration.md`.

## Layout

| Path | Role |
|------|------|
| `.claude/STATUS.md` · `Project-log.jsonl` · `LESSONS.jsonl` | the memory spine |
| `.claude/protocols/` · `.claude/reference/` | how agents work · glossary and manuals |
| `.claude/system-map/` · .claude/state/ | the map's cards · the journal and its projections (created on first use) |
| `.claude/config/` | every knob, each with a card in `registry.json` |
| `.claude-iff/` | committed record surface: anchor + redacted rollups, write-denied |
| RECORD_ROOT | the sibling folder outside the repo: raw capture, transcripts, analysis |

## Invariants

- Hooks and every tool in `.claude/tools/` are **bash + python3 stdlib only**. Project steps and
  optional features may use uv.
- The **protected tree** (`policy.json` `protected`: hooks, tools, config, agents, protocols,
  skills, console, settings) is the main session's on every write lane; sub-agents propose.
- Derived files (`policy.json` `derived_files`) are never hand-edited: change the source, rerun
  the generator. Append-only stores are written through `statectl.py`; a new journal action
  joins `_lib.JOURNAL_ACTIONS` and the projector in the same change.
- Flow layers are earned through evolution, never guessed at adoption. Unplaced is honest.

{{INVARIANTS}}
