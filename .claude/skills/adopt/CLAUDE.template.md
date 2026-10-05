# {{PROJECT_NAME}} - developer and agent guide

{{MISSION}}

**One place to interact** (Claude Code) · **one place to control** (the console) · **one command
to evolve** (`/project-memory`) - on an always-on record, behind fail-closed gates.

> Start here: `.claude/STATUS.md` (where we are) · `.claude/tasks/` (active work) · the console
> (half your screen beside this terminal; its port derives from the folder name and every
> session start prints the URL that actually bound). This file is a map, not a dump.

## Session ritual

- **Start:** the SessionStart hook prints the resume block. Read `.claude/STATUS.md` and the
  active task file. Set a pointer before anything long or risky:
  `python3 .claude/tools/statectl.py pointer "<next concrete action>"` - a mid-turn cutoff never
  fires the Stop hook, so the pointer on disk is the only thing that actually survives.
- **During:** follow `.claude/protocols/`: handshake, human-gates, honesty, evolution, orchestration.
- **End:** the user types `/project-memory`; you suggest it at a natural boundary. The ritual
  is the user's command: `checkctl` refuses a run without the ticket their prompt mints.

## Working agreement

1. **Faithful reporting.** Failures come with their output; skipped steps are named; "done"
   means verified-done.
2. **Mistakes get logged, both sides:** a `mistake` entry in `Project-log.jsonl` plus a
   `LESSONS.jsonl` row whose prevention rule is mechanical and checkable.
3. **Recurring mistakes get one respectful reminder,** citing the lesson row.
4. **Pushback, then align.** The concern once, with evidence and an alternative; if the user
   confirms their path, align fully and log it tagged `user-confirmed-over-pushback`.
5. **No volatile numbers in docs.** Point at the source data file instead.

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
python3 .claude/tools/mapctl.py     scan|lint|compile|show|context
python3 .claude/tools/obsctl.py     ingest|seal|rollup|anchor|report|story|size|analyze
python3 .claude/tools/consolectl.py build|payload|serve · sysmon.py · tests/run_tests.py
```

- **Modes** (`statectl mode`): freestyle (default) · guided-solo · fableous-orchestrated.
- **Phases** (`statectl phase`): plan → build → review → deploy, each exit run by `phase-exit`.
- **The ritual**: user-typed only; it ends with ROUTE (next phase, next mode, maturation pass).
- **Proposals** (`statectl proposal add`): parked ideas; in deploy, new features go here.
- **Orchestration** (`protocols/orchestration.md`): lead-only dispatch/accept, handoff, progress.
- **Health** `checkctl doctor` · **export** `distctl export` · **secrets** `reference/secrets.md`.
- **Folder context** (`mapctl context`): which guides and rules load where.

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

## Domain notes

{{DOMAIN_NOTES}}
