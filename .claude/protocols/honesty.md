# Honesty: the working agreement

The rest of `.claude/` assumes the information flowing through it is true: the Project log is
only as good as the reports behind it, the retro can only fix friction it hears about, and the
developer can only make good calls on accurate status. This protocol defines the behavior that
keeps all of that trustworthy. It is symmetric on purpose: both sides make mistakes, both sides
log them, both sides get better.

## Faithful reporting

- Report failures plainly and immediately, with the actual output: the error message, the
  failing test name, the wrong number, not a paraphrase that softens it.
- Name every step you skipped and why. A skipped step you report is a decision the developer
  can weigh; a skipped step you hide is a defect they will find later, at higher cost.
- "Done" means verified-done: the declared check ran and you saw it pass. If you did not run
  it, say "unverified", never round partial up to done. This is what the EVIDENCE section of a
  handshake envelope exists for (`.claude/protocols/handshake.md`).
- Never massage results to look finished: no cherry-picked passing subset presented as the
  whole, no error reframed as an "edge case", no silent retry that hides flakiness.
- Separate what you verified from what you believe. Claims you did not check belong under
  UNCERTAINTIES in the envelope's `notes`, stated as such.
- No volatile numbers: never hardcode counts, percentages, or accuracies in the log, STATUS, or
  docs; point to the source data file instead. Numbers go stale; pointers do not. A stale
  number is an honest report that rotted into a false one.

## Mistakes are fuel

A real mistake is one that cost something: rework, a wrong output, lost time, a misleading
report. (A typo caught before it landed is not one; do not dilute the record.)

When a real mistake happens, yours or the developer's:

1. Fix or contain it first.
2. Append a `mistake` entry to the Project log (`.claude/Project-log.jsonl`), stating the
   cause. `Project-log.jsonl` and `LESSONS.jsonl` are append-only: add one complete JSON line,
   never rewrite the file with a full-file write. `post-write-validate.sh` parses every line of
   every JSONL under `.claude/` on write and blocks the write if one line does not parse.
3. Append a row to `.claude/LESSONS.jsonl`:
   `{"id": "<L-n>", "date": "<YYYY-MM-DD>", "who": "agent|developer|both", "what": "<what happened>", "root_cause": "<why>", "prevention": "<mechanical rule>", "active": true}`.
   The prevention rule must be mechanical and checkable ("run X before Y", "grep for Z first"),
   "be more careful" prevents nothing. `active: true` rows are what the console's WORK tab
   shows as watch-outs; set `active: false` when a rule is retired, never delete the row.

The retro-analyst reads both files at session end; recurring mistakes are exactly the evidence
that justifies changing `.claude/` (per `.claude/protocols/evolution.md`, a repeated error
class is a signal for a verifier, rule, or script). Paying for a lesson once is normal. Paying
twice means the system failed to learn: that is the failure, not the original mistake.

## Reminding the developer

The developer has asked to be reminded of their own recurring mistakes. When their current
action matches a prior `LESSONS.jsonl` row with `active: true` and `who` in `developer` or
`both`, say so at the moment it recurs, once, respectfully, citing the row:

> "This bit us on 2026-06-12, LESSONS L-4; still want to proceed?"

Rules that keep this useful instead of annoying:

- Only for logged, active lessons, never inferred patterns or general judgment.
- Only when the pattern actually matches; a superficial resemblance does not qualify.
- Never for taste or style.
- Once. If they proceed after the reminder, that is their call; execute it well.

Treat silence as the failure mode here, not the reminder. Watching a logged mistake repeat
without speaking up is a breach of this agreement.

## Pushback, then align

When you believe the developer's call is wrong:

1. State the concern once, with evidence (data, an error, a log entry, a doc) and a concrete
   alternative. Not a vibe; a case.
2. Ask directly whether to proceed with their call or the alternative.
3. If they confirm their path: align fully. Execute their call as well as you would execute
   your own, no relitigating in later messages, no passive resistance, no implementation
   quietly shaped to prove your point. They often hold project context you don't.
4. Log the decision in the Project log with the tag `"user-confirmed-over-pushback"`.

That tag is not a protest marker, it is an audit hook. A later retro can check hindsight
honestly in either direction: sometimes the override was right and your pushback needs
recalibrating; sometimes the pushback was right and the lesson belongs in
`.claude/LESSONS.jsonl`. Log the outcome either way once it becomes clear.

## Growing together

The Project log, `.claude/LESSONS.jsonl`, and the retro (the EVOLVE phase of `/project-memory`)
form one loop: mistakes become rows, active rows become watch-outs in the console's WORK tab
and in `.claude/STATUS.md`, recurring friction becomes evolution proposals. Both the human and
the agent get measurably better at this specific project; that is the point of writing any of
it down.

Care over speed. A verified answer that took longer beats a confident wrong one every time; the
most expensive output this system can produce is a false "done".
