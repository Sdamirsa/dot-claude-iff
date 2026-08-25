# Evolution: how .claude changes

## Principle

`.claude` is a living system: the project teaches it what it needs. But it changes ONLY on
evidence, and only with user confirmation. Structure rots in both directions: missing tooling
wastes session after session on the same manual work, while speculative tooling rots into noise
every future agent must read past. The resolving rule: **only necessary changes**. Necessary
means the friction already happened, repeatedly or expensively, and the change mechanically
prevents it from happening again.

## Signals to candidate change

Map observed friction (never imagined friction) to the smallest structure that kills it:

| Signal | Candidate change |
|--------|------------------|
| Same manual instruction given 2 to 3 times | **skill** (`.claude/skills/<name>/`) |
| A skill that needs fresh/large context, isolation, or a different model | **subagent** (`.claude/agents/<name>.md`) |
| A deterministic command sequence repeated 2+ times | **script** (`.claude/tools/`) |
| A recurring error class, or a trust-but-verify need | **verifier** step, or an instantiation of `.claude/agents/verifier.md` |
| Recurring human correction of the same kind | **rule** (`.claude/rules/`) or a CLAUDE.md line plus a LESSONS entry |
| Repeatedly waiting on the human at the same point | **gate redesign** per `.claude/protocols/human-gates.md` |
| A durable cross-session fact | **memory or log entry**: Project log, STATUS.md, or a research doc |
| Zero or low use of an existing component across sessions | **prune**: demote or archive (see Pruning below) |

## Choosing the primitive

| Choose | When |
|--------|------|
| **script** | The steps are deterministic, no judgment inside. Cheaper, faster, testable; prefer it whenever it suffices. |
| **skill** | The procedure needs judgment or live conversation context, and you keep re-explaining it. |
| **subagent** | The work needs a clean or large context window, isolation from the conversation, or a different model, not merely a saved procedure. |
| **rule** | The problem is a convention repeatedly violated in a specific part of the tree; a path-scoped rule corrects it passively, with no procedure to run. |

Prefer the smallest intervention: a CLAUDE.md line beats a rule beats a skill beats a subagent.
The same ordering runs backwards for pruning: demote a subagent to a skill before archiving it
outright, if a smaller form still covers the need.

## Two gears

**Soft (default):** runs inside every ritual's EVOLVE phase. Session digest to
`.claude/agents/retro-analyst.md` (propose-only) to a main-agent filter against the bar below to
a BLOCKING user gate to implementation to `.claude/agents/anatomist.md` reconciling
`.claude/system-map/cards/`.

**Hard (`/project-memory --hard`):** a dedicated maturation session, for deep refinement of
`.claude` itself or the project's pipeline. Procedure: full anatomist audit (folder health, map
reconciliation, drift review) plus a usage-evidence pruning sweep plus plan/spec-style task
cards, each with `description · to-do · satisfaction · pass-test`, plus a decision list for the
maintainer. Approved cards are implemented one at a time; each lands with a `checkctl` probe.
Run hard gear when "a session focused on improving the system" is the actual goal, not a
side-effect of shipping project work.

## Proposal format

From the retro-analyst at session end, or from the human directly. Either the single line
`NO-CHANGES: <one-line reason>`, or 1 to 3 proposals:

```
### P<n>: <short title>
- change_type: skill | subagent | script | rule | verifier | gate | memory | doc | demote | archive
- what: <the change in one sentence>
- evidence: <2+ occurrences this/recent sessions, or 1 concrete failure, cited from the digest>
- benefit_vs_cost: <time saved / errors prevented vs maintenance weight>
- implementation: <exact files to create/edit/move + sketch>
- risk_if_skipped: <what keeps hurting>
```

## The bar

- Max 3 proposals per session.
- Each needs 2+ occurrences or 1 concrete failure as evidence.
- `NO-CHANGES` is a *good* outcome, not a failure to produce.
- Proposals without evidence are dropped by the main agent before the user ever sees them: the
  analyst only saw a digest; the main agent saw the session and filters accordingly.

An unnecessary addition is negative value: it adds context weight to every future session, and
it rots the moment the project moves on. When in doubt, wait for the second occurrence.

## Pruning

Evolution removes as well as adds. The record (journal, obs capture, handshake envelopes) knows
what actually ran: which skills were invoked, which agents dispatched, which tools executed.
Components with zero or low use across sessions become pruning candidates:

- **Demote** down the ladder: subagent to skill, skill to rule, rule to a CLAUDE.md line.
- **Archive**, never delete: move to a sibling `archive/YYYYMMDD/` with a README line
  (what · archived · why · replacement).

Always user-gated through the same proposal format and BLOCKING gate as an addition. Logged as
a `tooling` entry the same way. The hard-gear pruning sweep is the systematic pass; the soft
gear can still surface an obvious single-component prune when the evidence is already in hand.

## The anatomist

`.claude/agents/anatomist.md` implements approved changes with the primitive chosen above, and
keeps `.claude/system-map/cards/` truthful. It reconciles cards after every soft-gear
implementation and runs the full audit at the start of every hard-gear session. **A change that
adds or removes a component is not done until its card is**: a proposal that lands a new skill
or archives an old subagent without a matching card add, edit, or archive is incomplete, not
merely undocumented.

## Change lifecycle and audit trail

1. **Evidence** accumulates: in the session, in `.claude/LESSONS.jsonl`, in the Project log.
2. **Proposal** in the format above, filtered against the bar.
3. **User confirmation**: a BLOCKING gate per `.claude/protocols/human-gates.md`. Never silently
   modify `.claude/`.
4. **Implement**, then log a `tooling` entry:
   `python3 .claude/tools/statectl.py tooling --change-type <type> --what "<sentence>" --evidence "<cite>"`.
   Every change to `.claude/` itself is traceable to a date, a reason, and the files it touched.
5. **Review after about 2 uses**: keep, amend, or archive. Superseded files follow the archive
   convention above; never delete silently.
