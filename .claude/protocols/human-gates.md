# Human gates: when to wait, when to proceed

You constantly face the choice: ask the human, or keep moving. Ask too little and you burn
work on wrong guesses; ask too much and every workflow stalls on a person who is not at the
keyboard. This protocol makes the choice mechanical, and guarantees autonomous work never
deadlocks waiting for an answer.

## Gate taxonomy

Three gates, exactly. Classify every decision point as one of them.

| Gate | Definition | When required | Mechanic |
|------|------------|---------------|----------|
| **BLOCKING** | Stop and ask; wrong guess is unsafe, expensive, irreversible, or wastes the work. | Before destructive, irreversible, outward-facing, or costly actions; before committing to one reading of a genuinely ambiguous request. | `AskUserQuestion` with an options list, your recommended option first (the user always has "Other"). Do not proceed until answered. Log it: `python3 .claude/tools/statectl.py gate --question "<q>" --kind blocking --answer "<a>"`. |
| **CHECKPOINT** | Present result plus stated assumption, proceed by default, user can redirect. | Direction-setting choices that have a defensible default. | State the assumption explicitly ("Assuming X because Y, proceeding"), do the work, flag the assumption for review in your report. |
| **FYI** | Report, never wait. | Anything else worth the user knowing. | One line in the report. No pause, no question mark. |

## Choosing the gate

| Situation | Gate |
|-----------|------|
| Destructive or irreversible: deleting data, overwriting outputs without backup, rewriting git history | BLOCKING |
| Outward-facing: anything a third party will see (emails, publishing, pushing to shared branches) | BLOCKING |
| Costly if wrong: meaningful API spend, or more than 30 min of work wasted on a bad guess | BLOCKING |
| Direction-setting with a defensible default: naming, file layout, a library choice within the existing stack | CHECKPOINT |
| Everything else | FYI |

When both readings of an ambiguous request lead to materially different work, BLOCKING;
otherwise pick the careful-colleague reading and CHECKPOINT.

**Default down, not up.** An unnecessary BLOCKING gate costs the user's attention and stalls
the work. Reserve BLOCKING for decisions that meet the table's criteria, and let CHECKPOINT
carry everything that has a defensible default.

## The async rule (workflows must not break)

Background agents, subagents, and parallel workflow stages NEVER block on a human. A subagent
cannot see the conversation; a question raised inside one is a deadlock, not a gate. When work
running in the background hits a BLOCKING-grade decision:

1. Open it on the **needs-human queue**:
   `python3 .claude/tools/statectl.py need open --title "<question>" --category <cat> --context "<the situation>" --action "<what to do>" [--band SEVn] --note "<options + recommendation>"`.
   It prints an id. Categories are `provide-input · decide · review · unblock-env ·
   approve-release · system-blocker`. Band defaults from category if omitted
   (`system-blocker`→SEV0, `approve-release`/`decide`→SEV1, `review`/`provide-input`/
   `unblock-env`→SEV2); pass `--band` to raise it.

   **`--context` and `--action` are required, and they are for a human reading cold.** Context
   answers three things in plain, warm language: what is going on, why it matters, and what it
   blocks. Action names the one concrete thing to do next. Write them the way you would brief a
   colleague who just walked in, not as a telegram: "The May export has three duplicate days;
   deleting raw rows is irreversible, so I stopped. This blocks the May reingest only" beats
   "dup rows May". The tool warns below ~60 characters of context because a queue of truncated
   one-liners just teaches the human to ignore the queue. The console renders context and
   action under each item, with a copy button that turns them into a paste-ready reply, so the
   quality of what you write here is exactly the quality of the conversation the human can
   have back with you.
2. Add a row to the active task file's `## NEEDS-HUMAN` table with the **same id**, so the
   queue and the task file cross-reference each other (columns: `Id | Date | Question |
   Options | Recommendation | Blocks`, per `.claude/tasks/_template.md`).
3. Finish all independent work: everything not gated by the answer.
4. Mark the dependent items as deferred in your envelope's `notes` (see
   `.claude/protocols/handshake.md`), pointing at the `NH-<n>` id.

The MAIN session surfaces open queue items to the user at the next natural boundary (a phase
end, or the CHECK step of `/project-memory`), not mid-stream. Skills that script
`AskUserQuestion` gates place them at phase boundaries in the main session only, never inside
fan-out or background stages.

The effect: a human who steps away for an hour comes back to finished independent work plus a
short, ranked list of parked questions, not a stalled pipeline.

## The queue itself

- **Band-first, then oldest-first.** SEV0 items sort before SEV1 before SEV2 before SEV3;
  within a band, oldest open item first. `python3 .claude/tools/statectl.py need list` prints
  it in that order.
- **Every row carries an id and a date.** The id (`NH-<n>`) and the open timestamp are set by
  `need open` and never hand-edited.
- **Bands:** SEV0 halts everything and surfaces out of band (do not wait for a ritual
  boundary); SEV1 gates governance (release, irreversible decisions); SEV2 gates content or
  verification; SEV3 is discretionary, safe to batch.
- **Escalation is allowed, de-escalation is refused.** Raise a band with
  `need amend NH-<n> --band SEV1` any time new evidence justifies it. `need amend` silently
  keeps the higher band and prints a warning if you try to lower one, no bypass exists.
- **Resolve on answer:** `python3 .claude/tools/statectl.py need resolve NH-<n> --answer "<a>"`.

## Approval scope

Approval in one context does not transfer. A new session, a new target (different file,
dataset, environment), or a new risk level means you re-ask; "yes to X yesterday" is not "yes
to X-like things forever".

The exception: a standing "don't ask about X" from the user IS durable. Record it as a rule or
memory entry via `.claude/protocols/evolution.md` so future sessions inherit it instead of
re-asking, or worse, re-guessing.

## Resume safety

An unanswered gate must survive the session that raised it. The needs-human queue is
append-only and independent of any one session; the task file's NEEDS-HUMAN row carries the
same id, question, options, recommendation, and what it blocks. Whether the answer arrives in
five minutes or three sessions later, anyone must be able to execute it from `need list` and
the task file alone; reality wins over any stale memory of what was asked (see
`.claude/tasks/_template.md`).

## Worked micro-example

A subagent backfilling last quarter's sales data finds the May raw export contains three
duplicate days. Deleting rows from `data/raw/` is irreversible, so BLOCKING, but it is a
subagent, so it runs:
`statectl need open --title "Drop duplicate May days?" --category decide --context "The May raw export in data/raw/ contains three duplicate days. Deleting rows from a raw export is irreversible, so I stopped rather than guessing. Only the May reingest is blocked; the other months are done." --action "Tell me which way to go: dedupe in-pipeline (my recommendation, raw stays untouched), edit the raw file, or skip May." --note "options: in-pipeline (recommended) / edit raw / skip May"`
It gets back `NH-7`, adds that id to the task file's NEEDS-HUMAN table, completes the other
months, and returns `status: partial` with May deferred, pointing at `NH-7` in `notes`. The
main session raises `NH-7` at the next phase boundary. Nothing deadlocked; nothing irreversible
happened on a guess.
