# Glossary

The controlled vocabulary for this system. One definition per term, or, for the rare term that
genuinely carries two senses (marked `(sense)` in its heading, cross-referenced to the other),
one definition per sense. When a term is renamed or retired, its old name is not redefined
elsewhere: the retired alias, and what replaced it, lives ONLY here. When a session needs a new
project term, add it here rather than inventing or redefining it inside whatever file happens
to need it that day. The anatomist maintains this file (see `.claude/agents/anatomist.md`);
anyone may propose an addition.

## Anatomy

- **card**: The system-map's atomic unit, one JSON file per component in
  `.claude/system-map/cards/`. Schema: `id · kind[agent|code|data|human|external] · layer ·
  title · path · description · reads · writes · invokes · flows · glyphs ·
  auto{discovered,hash}`. `mapctl.py lint` fails CHECK on a missing card or a dangling edge.
- **component**: Any discrete piece of the system with its own card: an agent, a tool, a hook,
  a skill, a config file, a data store. The unit the map is built from.
- **flow layer**: A layer defined through the project via evolution, not shipped at adoption.
  Holds components once the anatomist has traced enough relations to place them meaningfully,
  the alternative to leaving them in the unplaced band forever.
- **layer**: A named grouping in `.claude/system-map/layers.json` that a card is placed into.
  Two kinds: the five shipped spines, and flow layers earned through the project's own history.
- **package**: A cohesive bundle of components, typically a skill plus the agents and tools it
  drives, that together deliver one capability and get discussed as a unit (for example, "the
  discovery-control package"). Not a schema field on a card: each component in a package still
  gets its own card, tracked individually by the anatomist.
- **spine**: One of the five layers shipped already populated at adoption: MEMORY, GOVERN,
  OBSERVE, CONSOLE, EVOLVE. Contrast with a flow layer, which starts empty and is earned.
- **unplaced band**: Where a project's own components start at adoption, visible on the map but
  not yet assigned to a layer. The honest default: a component with no traced relations yet
  looks unplaced, not guessed-at.

## The record

- **allowlist**: The mechanism sealing uses to copy fields from raw capture into a segment. An
  unknown field is dropped by default, so a new secret-bearing field can never leak by omission
  (`observe.json`'s `seal_allowlist`).
- **anchor**: The SHA-256 head of the last seal, committed to `.claude-iff/obs/anchor.json`.
  What makes the record tamper-evident: altering a sealed segment after the fact changes the
  hash chain.
- **capture**: The hook-driven write of raw event payloads to the spool, on every relevant
  tool or session event. Fails open (law 2): a broken capture hook loses an event, never the
  work.
- **RECORD_ROOT**: The sibling folder `<repo-parent>/<repo-name>_claude_iff/`, out of the repo
  and out of git, holding spool, sealed-raw, segments, transcripts, analysis, and vault.
  Write-denied to every agent identity; resolved once through `_lib.record_root()` so the
  policy gate and the tools can never disagree on where it is.
- **rollup**: A daily allowlisted-totals file, `.claude-iff/obs/rollups/YYYY-MM-DD.json`,
  committed in-repo. The only in-repo trace of a day's activity, never verbatim.
- **seal**: The PUBLISH-time step that copies spool events through the allowlist into a
  segment, sets it read-only, and gzip-compacts the matching raw file. Raw is kept forever by
  default; sealing does not delete it.
- **sealed-raw**: The gzip-compacted copy of a day's raw spool events, kept forever by default
  (`retention_days: 0`). The agent's ground truth; the only sanctioned pathway over it is
  `obsctl.py analyze`.
- **segment**: A day's allowlisted metadata file, `RECORD_ROOT/segments/YYYY-MM-DD.jsonl`,
  produced by sealing. Metadata only, never verbatim content.
- **spool**: `RECORD_ROOT/spool/<session>.jsonl`, the raw append-only capture target hooks
  write to, verbatim, before sealing.
- **tamper-evident**: The property the anchor gives the record: a sealed segment can be checked
  against the committed anchor hash, so tampering after the fact is detectable. Not
  tamper-proof: someone with filesystem access could still alter it undetected until the next
  anchor check.

## The ritual

- **check**: The first phase of `/project-memory`. Verifies before touching anything: journal
  parses, heartbeat is fresh, generators are current, cards lint clean, registry lints clean,
  the price table isn't silently empty, record size is reported, needs-human is synced, and
  task Checkpoints match reality.
- **evolve (ritual phase)**: The fourth phase of `/project-memory`: soft gear by default, hard
  gear on demand. See `.claude/protocols/evolution.md`. For the surface-level sense, see
  **evolve (surface)** under The three surfaces.
- **freshness**: Whether a generated file matches its current source, decided by content hash
  (never by mtime: git does not preserve mtimes, so an mtime rule fires randomly on every fresh
  clone or branch switch).
- **generator**: A script that produces a derived file (`map.json`, `console.html`,
  `STATUS.md`, and so on). Every generator is registered by name in `config/memory.json` and
  runs only through the ritual (anti-rot); an unregistered generator rots invisibly.
- **hard gear**: `/project-memory --hard`, a dedicated maturation session: full anatomist
  audit, a usage-evidence pruning sweep, plan/spec-style task cards, a decision list for the
  maintainer.
- **phase (ritual)**: One of the four steps of `/project-memory`: check, polish, publish, evolve
  (`checkctl.py run --phase ...`). For where the project is in its work, see
  **phase (lifecycle)** under Lifecycle.
- **polish**: The second phase of `/project-memory`. Writes: log entries, LESSONS curation,
  STATUS rewrite, tasks closed, gates surfaced, and every generator.
- **pruning**: Evolution's removal half. Zero- or low-use components become candidates to
  demote (subagent to skill to rule to CLAUDE.md line) or archive, always user-gated.
- **publish**: The third phase of `/project-memory`. Ingest, seal, rollup, anchor, optional
  vault snapshot, commit always, push per policy.
- **run id**: The identifier tying one `/project-memory` invocation's phases together in
  `state/memory-run.json`; PUBLISH refuses to run without a POLISH completion recorded under
  the same run id.
- **soft gear**: The default evolution mode, running inside every ritual's EVOLVE phase: digest
  to retro-analyst to main-agent filter to user gate to implementation to the anatomist
  reconciling cards.

## Lifecycle

- **exit check**: The check that must pass to leave a lifecycle phase, named per phase in
  `config/phases.json` and run by `checkctl.py phase-exit --from <phase>`: plan needs open task
  files with a Definition of done, one Test command and a journal entry under a milestone;
  build needs every task of the milestone done with its Test green; review needs the human's
  `--signoff`; deploy needs `checkctl.py doctor` without a FAIL. `--override "<why>"` passes a
  failed one on the record.
- **milestone**: One body of work, recorded with `statectl.py milestone <id>`; tasks join it
  with `statectl.py task <id> --milestone <mid>`. The build exit check runs the tasks of the
  current (latest) milestone.
- **mode**: How organised the work is, set with `statectl.py mode`: `freestyle` (the default
  when unset: record, gates and ritual only), `guided-solo` (the phase contract prints at
  session start and leaving a phase runs its exit check), `fableous-orchestrated`
  (guided-solo plus lead-and-team routing). Input aliases: guided, solo, fableous,
  orchestrated; the stored value is always the full name.
- **phase (lifecycle)**: What work is allowed right now, one of plan, build, review, deploy,
  set with `statectl.py phase <next>`. Each has a contract of at most five lines and an exit
  check in `config/phases.json`. For the steps of `/project-memory`, see **phase (ritual)**
  under The ritual.
- **proposal**: An idea that is out of scope right now, parked in the proposal box
  (`state/proposals.jsonl`, ids `PR-<n>`) with `statectl.py proposal add` and closed with
  `proposal resolve <id> --as planned|rejected --note`. In the deploy phase every new feature
  idea goes here, not into code. Shown on the console WORK tab.

## Orchestration

The `fableous-orchestrated` mode's vocabulary (`.claude/protocols/orchestration.md`).

- **builder**: The agent (`.claude/agents/builder.md`, opus) that implements one task card
  in a git worktree the lead created, never commits, and hands back a builder envelope.
- **handoff**: A builder returning its work: the envelope it writes, checked by
  `checkctl.py handoff <task_id> [--run] [--root <worktree>]`, then the lead's diff review,
  merge and suite run. In this mode `statectl.py task <id> --status done` refuses without a
  valid envelope whose tests passed, unless `--no-envelope "<why>"`.
- **lead**: The main session in the `fableous-orchestrated` mode: plans, designs, writes task
  cards, dispatches, reviews, merges, reports to the human, and owns the outcome. Labelled
  `lead` by `obsctl.py report --by agent`.
- **scout**: The read-only research agent (`.claude/agents/scout.md`, sonnet): surveys,
  lookups and quick checks for the lead, with paths and line numbers; it edits nothing.
- **task card**: The task file a builder implements, `.claude/tasks/<id>.md`: Goal,
  Definition of done, one Test command, Context, Plan. Not a separate format; `_`-prefixed
  files in `tasks/` (`_template.md`, `_builder-brief.md`) are scaffolds, never cards.

## Contracts

- **band**: One of SEV0 to SEV3, the severity/urgency tier on a needs-human queue item. SEV0
  halts everything and surfaces out of band; SEV1 gates governance; SEV2 gates content or
  verification; SEV3 is discretionary.
- **envelope**: The JSON object a subagent writes to `.claude/state/handshakes/<task_id>.json`
  on completion: `{agent_id, task_id, status, artifacts[], notes}`, plus `agent, model,
  files_changed[], tests[], needs_main[]` from a builder. The delivered half of a handshake;
  one contract, checked by `_lib.validate_envelope` (`.claude/protocols/handshake.md`).
- **gate**: A decision point classified BLOCKING, CHECKPOINT, or FYI.
- **needs-human**: The async queue (`state/needs-human.jsonl`, opened, amended, and resolved
  via `statectl.py need`) that a background or subagent BLOCKING decision is written to instead
  of stalling the work.
- **Structured Return**: The seven-section prose contract a subagent's reply always follows
  (STATUS, RESULT, EVIDENCE, DEVIATIONS, UNCERTAINTIES, QUESTIONS, SUGGESTIONS), carried inside
  an envelope's `notes` field or as its top-level keys.
- **stub**: `.claude/state/handshakes/<task_id>.stub.json`, `{task_id, agent, dispatched_at,
  worktree?}`, written before dispatch (`statectl.py dispatch`) so an in-flight agent is visible
  before its envelope arrives.
- **Task Brief**: The eight-field spawn-prompt contract: Objective, Context, Inputs,
  Constraints, Output contract, Done criteria, Non-goals, Escalation.

## The three surfaces

- **control**: The surface running system to human: the console, reporting what happened and
  what needs attention.
- **evolve (surface)**: The surface running system to itself: one command, `/project-memory`,
  letting the project teach `.claude` what it needs, evidence-gated and user-confirmed. Enacted
  by the EVOLVE ritual phase; see **evolve (ritual phase)** under The ritual.
- **interact**: The surface running human to system: Claude Code itself, where the user and an
  agent work.
