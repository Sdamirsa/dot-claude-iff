<!-- last-reviewed: YYYY-MM-DD -->
# <title>

Copy this file to `.claude/research/YYYY-MM-DD-<slug>.md`. A research doc is a durable
deep-dive, not a running log; keep the `last-reviewed` line current whenever you revisit and
confirm the content still holds.

## Question

<what were you trying to find out, and why did it matter right now: the decision or task it
was blocking>

## Findings

Each finding sourced, not asserted:

- <finding>, source: <file path, URL, command output, or "measured: `<command>`">
- <finding>, source: <...>

## Gotchas

Exact error messages and surprising behavior, quoted verbatim, not paraphrased:

- `<exact error text or exact command output>`: <what it meant, what it cost to figure out>

## Decisions taken from this

<what this research caused to change: a config value, a design choice, a proposal filed via
`.claude/protocols/evolution.md`. Cite the Project log entry or tooling change if one resulted.>

## Open threads

<what is still unresolved, and what would resolve it>

---

**Write one of these only when both hold:** the research took more than about 15 minutes of
digging, AND a future session will need the conclusion (not just this one). If only one holds,
a Project-log entry is enough; do not create a file for it.
