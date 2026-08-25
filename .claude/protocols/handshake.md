# Handshake protocol: agent to agent contracts

How a spawning agent (the parent) and a subagent exchange work without losing information.

## Why

A subagent sees nothing of the live conversation: not the user's phrasing, not the files you
just read, not the decision two messages ago. Everything it needs must be in its prompt, and
everything it did must come back in its reply. The cure is contract-shaped communication in
both directions: a **Task Brief** going out, a **Structured Return** coming back, delivered as
a **JSON envelope**.

## Task Brief (spawning an agent)

Every spawn prompt contains these eight fields, by name:

| Field | What good looks like |
|---|---|
| `Objective` | One sentence naming the outcome, not the activity ("June KPI table verified", not "look at the KPIs"). |
| `Context` | The 2 to 5 facts from the conversation the subagent cannot see but needs: prior decisions, findings, why now. |
| `Inputs` | Exact paths (files, directories, configs), never "the data" or "the usual config". |
| `Constraints` | Hard limits: tools not to use, files not to touch, budgets, conventions that apply. |
| `Output contract` | The exact file paths the subagent must write, plus: "reply with a Structured Return envelope". |
| `Done criteria` | Verifiable conditions, checks the parent (or a verifier) can run, not a feeling of doneness. |
| `Non-goals` | What is explicitly out of scope. Write it even when it feels obvious. |
| `Escalation` | "If blocked or a decision exceeds your brief, STOP and return QUESTIONS in your envelope's `notes`: do not improvise." |

Skeleton:

```
Objective: <one sentence>
Context: <facts the subagent can't see>
Inputs: <exact paths>
Constraints: <hard limits>
Output contract: <exact output paths> + reply with a Structured Return envelope
Done criteria: <verifiable conditions>
Non-goals: <explicitly out of scope>
Escalation: If blocked or a decision exceeds your brief, STOP and return QUESTIONS in `notes`: do not improvise.
```

`Inputs` are exact paths; if a path is unconfirmed, say so in the brief. `Non-goals` prevent
scope creep, the most common silent failure in parallel work.

## Structured Return: the envelope

The Structured Return is not markdown in the reply text. It is a **JSON envelope** the subagent
writes to `.claude/state/handshakes/<task_id>.json`:

```json
{
  "agent_id": "<who ran this>",
  "task_id": "<the id given in the brief>",
  "status": "done",
  "artifacts": ["<path>", "<path>"],
  "notes": "STATUS: done\nRESULT: ...\nEVIDENCE: ...\nDEVIATIONS: ...\nUNCERTAINTIES: ...\nQUESTIONS: ...\nSUGGESTIONS: ..."
}
```

`status` is exactly one of `done`, `partial`, `blocked`. `artifacts` lists the durable output
paths, never the payload itself. `notes` carries the same seven sections in order: `STATUS ·
RESULT · EVIDENCE · DEVIATIONS · UNCERTAINTIES · QUESTIONS · SUGGESTIONS`. Empty sections may
be omitted, except STATUS, RESULT, and EVIDENCE, which always appear.

**Why JSON, not prose in the reply:** structured hand-offs survive parsing across a compacted
or resumed conversation; free prose in a chat turn does not. `statectl.py` and the console read
`state/handshakes/*.json` as data, not by re-reading transcripts. `post-write-validate.sh`
enforces the contract on write and **blocks a malformed envelope on the spot**, exit code 2,
before the subagent moves on.

## The stub

Before dispatch, the parent (or the dispatching skill) writes
`.claude/state/handshakes/<task_id>.stub.json`:

```json
{"task_id": "<id>", "agent": "<agent name>", "dispatched_at": "<ISO-8601 UTC>"}
```

The stub makes an in-flight agent visible: the console's in-flight panel reads stubs with no
delivered envelope yet, so a dispatched-but-not-returned task shows as running, not missing.
The stub is exempt from envelope validation (filename ends `.stub.json`); the real envelope at
`<task_id>.json` replaces it in meaning, not in place, once the subagent returns.

## Durable outputs

Big outputs go to files; `artifacts` carries the paths. A 400-row table pasted into `notes` is
lost the moment the conversation compacts; the same table written to a path from the `Output
contract` survives every session. State-changing results also land in the active task file's
`## Checkpoint` block (`.claude/tasks/_template.md`): the conversation is not durable; files
are.

## Parent obligations

Receiving an envelope is not the end of your job:

- **Verify EVIDENCE before relaying.** Spot-check at least one claim per envelope: open a
  written artifact, re-run a cheap check. Never present unverified subagent claims to the user
  as fact. For substantial deliverables, spawn `.claude/agents/verifier.md` to check it.
- **Log DEVIATIONS that matter.** A deviation that changes the deliverable belongs in the task
  file; if it is decision-shaped, it also belongs in the Project log.
- **Route QUESTIONS through the gate protocol.** Parked QUESTIONS in `notes` become a gate per
  `.claude/protocols/human-gates.md`: BLOCKING only if you cannot answer from context you hold;
  otherwise answer it and record the assumption.

## Fan-out rules

When running agents in parallel:

- **Disjoint file ownership.** Never two writers to one file. Partition output paths before
  spawning; if two agents "share" a file, the partition is wrong, fix it before spawning.
- **Barriers only when needed.** Serialize stages only when a stage truly needs all prior
  results; otherwise let independent agents run unblocked.
- **Shared context via a spec file.** Long shared context goes in one file every agent Reads
  (listed under `Inputs`), not duplicated as prose in each prompt: duplicated prose drifts.

## See also

`.claude/protocols/human-gates.md` (QUESTIONS, and why background agents never block on a
human) · `.claude/tasks/_template.md` (Checkpoint and NEEDS-HUMAN blocks) ·
`.claude/agents/verifier.md` (checks EVIDENCE independently).
