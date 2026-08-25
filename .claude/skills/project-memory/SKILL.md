---
name: project-memory
description: The one ritual - check, polish, publish, evolve. Invoke at the end of a working session, after a milestone, or when the user says "update memory", "log this session", "update the log", "curate memory", "update status", "wrap up", or "run the ritual". Add --hard for a dedicated maturation session that deeply refines the .claude system itself. NOT for planning or resuming work (use /plan-task), and NOT for a cold "where were we" (read .claude/STATUS.md).
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion
---

# /project-memory

Four phases, in order: **CHECK, POLISH, PUBLISH, EVOLVE.**

Run this in the MAIN session, never as a sub-agent. You are the only agent who saw the whole
conversation, and curation is exactly the part that cannot be reconstructed from disk.

Two gears. **Soft** is the default: evolve incrementally as part of wrapping up. **Hard**
(`/project-memory --hard`) is a dedicated maturation session, described at the end.

Every generator in this system runs here and only here. That is not a stylistic preference: in
the system this one was distilled from, the single regenerator nobody wired into the ritual sat
frozen for two and a half months while every downstream surface quietly served stale data.

---

## Phase 1 - CHECK

```
python3 .claude/tools/checkctl.py run --phase check
```

Read the output properly. It reports one line per step:

- **FAIL** stops the ritual. Fix what is fixable, then rerun. If it cannot be fixed now, say so
  plainly and stop before PUBLISH: reporting a clean wrap-up over a failed check is the exact
  false "done" that `.claude/protocols/honesty.md` forbids.
- **WARN** informs. A stale generator at CHECK time is normal (POLISH is about to rebuild it).
  An empty price table, an unregistered tunable, a checkpoint that disagrees with disk: mention
  them in the final report.

Then verify the things a script cannot: for each active task in `.claude/tasks/`, does the
Checkpoint still describe reality? A checkpoint is a claim, not a fact. Where they disagree,
reality wins: correct the checkpoint first.

If CHECK failed and you are stopping, still tell the user what you learned. A failed ritual that
reports honestly is worth more than a green one that skipped a step.

---

## Phase 2 - POLISH

Curation first (this is you, not a script), then the generators (a script, all of them).

**2a. Reconstruct the session.** Decisions and their reasons, deliverables, analyses, mistakes
on both sides, open questions, next steps. Favor signal; omit asides.

**2b. Append to `.claude/Project-log.jsonl`** - one condensed entry per real thing:

```json
{"date":"YYYY-MM-DD","type":"decision|deliverable|milestone|analysis|note|mistake|tooling",
 "title":"grep-skimmable","summary":"1-2 sentences: what and why",
 "artifacts":["relative/path"],"tags":["..."],"source":"session|artifacts|git"}
```

`source` is an epistemic tag, not decoration: `session` means you witnessed it, `artifacts` or
`git` means you inferred it from files afterwards. Append only; never edit or reorder past
lines. No volatile numbers, point at the source data instead.

**2c. Curate `.claude/LESSONS.jsonl`.** One row per real mistake, where real means it cost
something (rework, a wrong output, lost time, a misleading report):

```json
{"id":"L-<n>","date":"YYYY-MM-DD","who":"agent|developer|both","what":"...",
 "root_cause":"...","prevention":"mechanical and checkable","active":true}
```

The prevention rule must be something a future session can execute ("run X before Y", "grep for
Z first"). "Be more careful" prevents nothing. Rotate `active`: keep one to three rows active,
retire what has been internalized. Active rows are what the console's WORK tab shows.

**2d. Rewrite `.claude/STATUS.md`** from scratch, not patched. Five sections, 15 to 25 lines
total: Current focus, Active tasks, Next steps (concrete enough to start from cold), Blockers
and open decisions, Watch-outs (mirrored from the active lessons).

**2e. Close finished tasks.** Verify each Plan item's done-evidence rather than assuming it,
fill Outcome, flip the status line, archive to `.claude/tasks/archive/YYYYMMDD/`. Surface any
unanswered `## NEEDS-HUMAN` row to the user NOW, and make sure it exists in the queue:

```
python3 .claude/tools/statectl.py need open --title "..." --category decide --band SEV1 \
  --context "<what is going on, why it matters, what it blocks - written for a human reading cold>" \
  --action "<the one concrete thing to do>"
```

**2f. Set the resume pointer** for whatever comes next:

```
python3 .claude/tools/statectl.py pointer "<the very next concrete action>"
```

**2g. Run every generator:**

```
python3 .claude/tools/checkctl.py run --phase polish
```

This rebuilds the cards, the map, the story feed and the console, and stamps each generator's
content hash into `.claude/state/generators.json`. A generator that is skipped here is a
generator that will rot.

**2h. Update `.claude/CLAUDE.md` only if locations or conventions changed** this session. It is
a map, not a dump.

---

## Phase 3 - PUBLISH

```
python3 .claude/tools/checkctl.py run --phase publish
```

This refuses to run unless POLISH completed in the SAME run id, so a half-built set of derived
surfaces can never be committed as if it were whole. If it refuses, rerun POLISH; do not work
around it.

It ingests token metadata from the transcripts, seals the raw record through the allowlist,
writes the daily rollup and updates the anchor. Nothing verbatim crosses into the repo.

**Then git.** Stage, and commit with a conventional message:

```
git add -A && git commit -m "<type>: <what changed>"
```

**Push per `push` in `.claude/config/memory.json`:**

- `ask` (the default): ask once, with AskUserQuestion, naming the branch and remote.
- `always`: push without asking. Invoking the ritual is the standing instruction.
- `never`: commit only.

Never `--force`, never `--no-verify`, never push when CHECK failed.

---

## Phase 4 - EVOLVE

**4a. Build a session digest**, 15 to 30 lines: goals and outcome; instructions you needed more
than once (with counts); failures and their root causes; human corrections; gates asked and
their answers; friction moments; which skills and agents were used and whether they fit.

**4b. Dispatch the retro-analyst** (Sonnet, propose-only by tool restriction) with the digest
plus an inventory of `.claude/**/*.md`, telling it to read `.claude/protocols/evolution.md` and
apply its bar. It returns at most three proposals in the Evolution proposal format, or the
single line `NO-CHANGES: <reason>`.

**4c. Filter.** The analyst saw only a lossy digest; you saw the session. Drop proposals whose
evidence does not actually hold. Expect to drop some. `NO-CHANGES` is a good outcome, not a
failure to produce.

**4d. Confirm - a BLOCKING gate.** Present the survivors with AskUserQuestion (multi-select, one
option per proposal, plus "None of these"). Never modify `.claude/` without confirmation.

**4e. Implement.** For anything touching components, dispatch the **anatomist**; it implements
with the smallest primitive that holds the fix and updates the card in the same pass. Log each
change:

```
python3 .claude/tools/statectl.py tooling --change-type <kind> --what "..." --evidence "..."
```

**4f. Reconcile the map** if any component changed this session (`git status --short` tells
you). Dispatch the anatomist; if nothing changed, say so explicitly rather than silently
skipping.

**4g. Close the ritual:**

```
python3 .claude/tools/checkctl.py complete --note "<one line>"
```

---

## The report

One condensed block, in this order: what CHECK said (including warnings), what was logged and
what changed in STATUS, generators rebuilt, what PUBLISH did (commit, and whether it pushed),
retro outcome (proposals confirmed, dropped, or NO-CHANGES), map reconciled or skipped and why,
and open questions for the maintainer.

Report skipped and failed steps faithfully. "Done" means verified-done.

---

## Hard gear - `/project-memory --hard`

A dedicated maturation session for deep refinement of the `.claude` system or the project's
work-package pipeline. Run the four phases as usual, and additionally:

1. **Full anatomist audit** - not just reconciliation: folder health, drift review, every
   component's card checked against its source, unclassifiable components surfaced.
2. **Pruning sweep** - usage evidence from the record (`obsctl.py report --by session`, journal
   and log greps) against every skill, agent, tool and rule. Unused machinery becomes a demotion
   or archival proposal. Growth and shrinkage run on the same evidence.
3. **Task cards** for everything the audit surfaced, each with description, to-do, satisfaction
   criteria and a pass-test, written into `.claude/tasks/`.
4. **A decision list** for the maintainer: every judgment call the audit could not make alone,
   with a recommendation.
5. Each implemented card lands with a probe in `checkctl.py probe`, so "it is built" stays a
   mechanical claim rather than a rhetorical one.
