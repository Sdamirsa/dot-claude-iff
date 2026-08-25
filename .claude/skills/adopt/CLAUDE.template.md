# {{PROJECT_NAME}} - developer and agent guide

{{MISSION}}

**One place to interact** (Claude Code) · **one place to control** (the console) · **one command
to evolve** (`/project-memory`) - on an always-on record, behind fail-closed gates.

> Start here: `.claude/STATUS.md` (where we are) · `.claude/tasks/` (active work) · the console
> (`http://127.0.0.1:7717/console.html`, meant for half your screen beside this terminal).
> This file is a map, not a dump.

## Session ritual

- **Start:** the SessionStart hook prints the resume block. Read `.claude/STATUS.md` and the
  active task file. Set a pointer before anything long or risky:
  `python3 .claude/tools/statectl.py pointer "<next concrete action>"` - a mid-turn cutoff never
  fires the Stop hook, so the pointer on disk is the only thing that actually survives.
- **During:** follow `.claude/protocols/` - `handshake.md` (agent to agent), `human-gates.md`
  (wait vs proceed), `honesty.md` (how to report), `evolution.md` (how this folder changes).
- **End:** run `/project-memory`.

## Working agreement

1. **Faithful reporting.** Failures are reported with their output. Skipped steps are named.
   "Done" means verified-done.
2. **Mistakes get logged, both sides.** A `mistake` entry in `Project-log.jsonl` plus a row in
   `LESSONS.jsonl` whose prevention rule is mechanical and checkable.
3. **Recurring mistakes get one respectful reminder,** citing the lesson row.
4. **Pushback, then align.** State the concern once with evidence and an alternative. If the
   user confirms their path, align fully and log it with tag `user-confirmed-over-pushback`.
5. **No volatile numbers in docs.** Point at the source data file instead.

## Three laws

1. **Anti-rot.** Every generator is registered in `.claude/config/memory.json` and runs only
   through `/project-memory`. An unregistered generator rots.
2. **Gates fail closed, telemetry fails open.** A broken validator blocks the write; a broken
   capture hook loses an event and nothing else.
3. **The record is radioactive.** Verbatim content never enters git. It lives in `RECORD_ROOT`,
   a sibling folder outside the repo; only allowlisted metadata and the anchor are committed.

## Stack and commands

{{STACK_SUMMARY}}

```
{{DEV_COMMANDS}}
```

System commands:

```
python3 .claude/tools/statectl.py  pointer|task|milestone|decision|loop|note|need|refresh|resume
python3 .claude/tools/checkctl.py  run --phase check|polish|publish · probe · generators
python3 .claude/tools/obsctl.py    ingest|seal|rollup|anchor|report|story|size|analyze
python3 .claude/tools/mapctl.py    scan|lint|compile|show
python3 .claude/tools/consolectl.py build|payload|serve
python3 .claude/tools/tests/run_tests.py
```

Skills: `/project-memory` (check · polish · publish · evolve; `--hard` for a maturation
session) · `/plan-task` · `/adopt`.
Agents: `anatomist` (anatomy, cards, placement, pruning) · `retro-analyst` (propose-only
evolution) · `verifier` (adversarial claim checking).

## Invariants

- Hooks and the six core tools are **bash + python3 stdlib only**. Project steps may use uv.
- `.claude/hooks/`, `config/`, `agents/`, `protocols/`, `settings.json` are the **protected
  tree**: main session only. Sub-agents propose changes there, they do not make them.
- Derived files (`session.json`, `HANDOFF.md`, `needs-human.json`, `map.json`, `console.html`)
  are never hand-edited. Change the source, rerun the generator.
- Append-only stores are written through `statectl.py`, not by hand.
- Flow layers are earned through evolution, never guessed at adoption. Unplaced is honest.

{{INVARIANTS}}

## Domain notes

{{DOMAIN_NOTES}}
