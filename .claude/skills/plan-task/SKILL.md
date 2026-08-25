---
name: plan-task
description: Create and maintain resume-proof task files under .claude/tasks/. Invoke on "plan this", "let's plan", "break this down", or any task likely to span more than one session, AND on "resume", "where were we", "continue the task", "pick up <task>". NOT for trivial single-step asks that finish inside one exchange; those need no task file.
---

# plan-task: resume-proof task planning

The conversation is not durable; the task file is. This skill turns any multi-step piece of work
into a file under `.claude/tasks/` that a future session (yours, another agent's, or a session
after a crash) can pick up without reconstruction. If losing this conversation would lose
progress, the task needs a file.

## Creating a task

1. Copy `.claude/tasks/_template.md` to `.claude/tasks/YYYYMMDD-<slug>.md` (date = today, slug =
   short kebab-case, e.g. `20260720-backfill-q2.md`). One file per task.
2. Fill the top sections:
   - **Goal**: 1 to 2 sentences that include the definition of done. If you can't state what
     must exist or pass for the task to close, you aren't ready to plan it, ask.
   - **Context**: why now; link the Project log entries and `.claude/research/` files that
     motivated it, so a resumer doesn't re-derive the rationale.
   - **Plan**: a checklist of verifiable steps. Each step names its done-evidence: the file that
     will exist, the test that will pass, the output that will match. A step whose completion
     can't be checked from disk isn't a step, split or sharpen it.
3. Register the task in `.claude/STATUS.md` under `## Active tasks` (link the file).

A well-formed plan step versus a vague one:

```markdown
- [ ] Reingest June exports (done when: output/weekly/2026-06-*.parquet exist and
      `pytest tests/test_ingest.py -q` passes)         # verifiable
- [ ] Handle June data                                  # not a step, no evidence
```

## While working

After every meaningful step, update the task file's `## Checkpoint` block:

- **Last completed**: the step just finished, verbatim from the Plan.
- **Next action**: the exact command to run, or the exact file + edit to make. "Continue the
  migration" is not a next action; `uv run scripts/ingest.py --month 2026-06` is.
- **State files**: the paths that hold progress (outputs, caches, staging files), so a resumer
  can verify state instead of guessing it.
- **Updated**: today's date.

Update the checkpoint BEFORE long or risky operations, not only after, and in the same step set
the journal pointer to match:

```
python3 .claude/tools/statectl.py pointer "<next concrete action>"
```

Use the same text you just wrote as Next action. The task file is what a resumer reads once they
have found it; the pointer is what a COLD session sees first, printed by the SessionStart hook
before anyone has opened a task file at all. If the operation crashes the session, both must
already describe how to recover: a checkpoint update with no matching pointer call leaves the
cold-start orientation pointing at stale work even though the task file itself is fine.

Questions for the human do not block the work. Open them in the needs-human queue:

```
python3 .claude/tools/statectl.py need open --title "<question>" \
  --category <provide-input|decide|review|unblock-env|approve-release|system-blocker> \
  --context "<the situation in plain language: what is going on, why it matters, what it blocks>" \
  --action "<the one concrete thing the human should do>" \
  --band <SEV0|SEV1|SEV2|SEV3> --note "<options + your recommendation>"
```

It prints an id (band defaults from category if you omit `--band`). Record that same id, the
question, options, your recommendation, and what it blocks in the task file's `## NEEDS-HUMAN`
table, then finish all independent steps. Two places hold one fact on purpose: the queue is what
a cold session and the console surface without anyone opening this file; the table is what a
resumer sees in context once they do. The rules for when to ask versus proceed are in
`.claude/protocols/human-gates.md`.

The task file is the single source of truth for progress. Never let the conversation hold state
the file doesn't.

## Resuming

On "resume", "where were we", "continue the task", or at the start of any session with an active
task:

1. Read `.claude/STATUS.md` to identify the active task file(s).
2. Read the task file: Goal, Plan, Checkpoint, NEEDS-HUMAN.
3. **Verify the checkpoint against reality before trusting it.** Checkpoints can be stale: a
   crash mid-write, an operation that half-completed, a manual change since. Check: do the State
   files exist? Does the last completed step's done-evidence still hold (files present, tests
   pass, output matches)? If reality disagrees with the checkpoint, reality wins, correct the
   checkpoint first, then proceed.
4. If any `## NEEDS-HUMAN` item gates the next action, resolve it with the user now (you are in
   the main session, asking is allowed here), then close the matching queue entry:
   `python3 .claude/tools/statectl.py need resolve <id> --answer "<answer>"`. Items that don't
   gate anything can wait for the next natural boundary.
5. Continue from **Next action**.

## Closing

When every Plan box is checked and each step's done-evidence has been verified (not assumed):

1. Fill `## Outcome`: what shipped, and the Project log entry that records it.
2. Change the status line to done and move the file to `.claude/tasks/archive/YYYYMMDD/` (create
   the dated folder; add a README line there: what, archived, why, replacement).
3. Append a `deliverable` or `milestone` entry to `.claude/Project-log.jsonl` if one doesn't
   already exist for this work.
4. Update `.claude/STATUS.md`: remove the task from `## Active tasks`, refresh `## Next steps`.

Closing normally happens inside `/project-memory` at session end, but close immediately if the
task finishes mid-session: a done task lingering as active misleads the next resumer.

## Rules

- One file per task. A task that grows a second goal becomes two files.
- Every Plan step names its done-evidence; unverifiable steps get split or sharpened.
- Never delete a task file; archive it under `.claude/tasks/archive/YYYYMMDD/`.
- Checkpoint updates set the journal pointer in the same step (`statectl.py pointer ...`),
  BEFORE long or risky operations, not only after (crash-safety).
- Parked questions go to both places: the needs-human queue (`statectl.py need open`) and the
  task file's `## NEEDS-HUMAN` table, same id in both.
- On resume, verify the checkpoint against reality before acting on it.
