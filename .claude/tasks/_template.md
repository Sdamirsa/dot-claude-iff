# Task: <name>

Copy this file to `.claude/tasks/<slug>.md`. One file per task. This file, not the
conversation, is what survives a disconnect: a resumed session reads it before doing anything
else.

## Goal

<one or two sentences: the outcome, not the activity>

**Definition of done:** <the verifiable condition that closes this task, e.g. "checkctl.py
probe passes for the new component" or "the report file exists and the numbers in it trace to
a source">

## Context

<the 2 to 5 facts a resuming session needs and cannot derive from the repo alone: why this
task exists, prior decisions, links to the research doc or Project log entries that motivated
it>

## Plan

Checklist. Every item names its own done-evidence, not just a checkbox:

- [ ] <step>, done when: <the check that proves it, e.g. "`pytest tests/test_x.py` passes">
- [ ] <step>, done when: <...>
- [ ] <step>, done when: <...>

## Checkpoint

- **Last completed:** <the last plan item actually finished, with evidence>
- **Next action:** <the single next concrete step, in imperative form>
- **State files:** <paths this task has written or depends on, e.g.
  `.claude/state/handshakes/T-12.json`, `.claude/system-map/cards/foo.json`>
- **Updated:** <YYYY-MM-DD>

**Resume-proofing rules:**

- Update this block **before** any long-running or risky operation, not after. A mid-turn
  cutoff never fires a Stop hook; the pointer on disk is the only thing that actually survives.
  Mechanically: `python3 .claude/tools/statectl.py pointer "<next concrete action>"` and, if
  this task has an id in the journal, `python3 .claude/tools/statectl.py task <id> --status
  doing --note "<what's in flight>"`.
- On resume, **verify this Checkpoint against reality first**: do the State files exist, does
  `git log` / `git status` match "Last completed", is the thing this task depended on still
  true. `/project-memory`'s CHECK phase runs a `task_reality` pass automatically for exactly
  this reason. **Reality wins**: when the file and the world disagree, trust the world, fix the
  file, then proceed.

## NEEDS-HUMAN

Rows here mirror entries opened on the queue
(`python3 .claude/tools/statectl.py need open --title ... --category ... [--band SEVn]`); the
`Id` column is the id it printed (`NH-<n>`), not invented here. See
`.claude/protocols/human-gates.md` for bands, categories, and the async rule.

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|
| NH-<n> | YYYY-MM-DD | <question> | <option A / option B> | <which, and why> | <what step waits on this> |

## Outcome

<filled in when the task closes: what shipped, what changed from the original Plan and why,
pointers to the durable artifacts. Leave blank while the task is open.>
