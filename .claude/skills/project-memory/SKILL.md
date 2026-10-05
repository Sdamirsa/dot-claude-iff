---
name: project-memory
description: The one ritual - check, polish, publish, evolve, then route the next session (phase, mode, optional maturation pass). Only the user opens it, by typing /project-memory at the end of a working session or after a milestone. When the user says "update memory", "log this session", "update the log", "curate memory", "update status", "wrap up", or "run the ritual", ask them to type /project-memory. NOT for planning or resuming work (use /plan-task), and NOT for a cold "where were we" (read .claude/STATUS.md).
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion
---

# /project-memory

Five phases, in order: **CHECK, POLISH, PUBLISH, EVOLVE**, then the report, then **ROUTE**.

**This skill runs only because the user typed `/project-memory`.** `checkctl` enforces it: the
prompt hook (`.claude/hooks/ritual-ticket.sh`) writes a ticket, `.claude/state/ritual-ticket.json`,
when the user's own prompt starts with `/project-memory` (or `/adopt`); `checkctl run` refuses
to open or continue a run without a fresh one, and `checkctl complete` needs it and consumes
it. The policy gate denies the file to every agent identity. The ticket is a tripwire, not
cryptography: it makes an agent-opened ritual fail loudly and leaves the issue in the record;
it does not make forging impossible, and it does not have to. If `checkctl` refuses for want of
a ticket, stop and ask the user to type `/project-memory`. Never work around it.

Run this in the MAIN session, never as a sub-agent. You are the only agent who saw the whole
conversation, and curation is exactly the part that cannot be reconstructed from disk.

EVOLVE runs in the soft gear every time: evolve incrementally as part of wrapping up. The
deeper maturation pass is a choice the user makes at ROUTE, described there.

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

**Cutting a release - home repo only, and only when the maintainer names a version.** Releases
are the maintainer's call, never yours. When they say "release vX.Y.Z":

1. Set `system_version` to `X.Y.Z` in `.claude/config/registry.json` - the tag and the stamp
   move in step.
2. Pin the changelog: in `CHANGELOG.md` (repo root), retitle `## Unreleased` to
   `## vX.Y.Z - YYYY-MM-DD` and curate it from `Project-log.jsonl` since the previous tag -
   written for readers of the release, not a commit dump - then start a fresh `## Unreleased`
   above it. The `changelog_parity` check FAILS the ritual if any version tag or the stamped
   `system_version` lacks its section, so a release cannot quietly outrun its changelog.
3. Commit `release: vX.Y.Z - <one line>` and tag `git tag vX.Y.Z`. Push per the `push` knob;
   if this session's credentials cannot push tags (branch-scoped tokens cannot), hand the
   maintainer the exact commands and say so in the report.
4. **Do not create the GitHub release by hand.** Pushing the tag fires
   `.github/workflows/release.yml`, which is the ONE publisher: it runs the suite on every
   supported OS, rebuilds both zips with the same registered generator the ritual uses, and
   publishes with this version's CHANGELOG section as the notes. Running `gh release create`
   yourself races it and leaves a red run behind ("a release with the same tag name already
   exists") - that is exactly what happened on v0.2.2. Instead, watch it land:

   ```
   gh run watch $(gh run list --workflow=release --limit 1 --json databaseId -q '.[0].databaseId')
   ```

   Report the run's verdict. If the suite fails there, the release does not exist and the tag
   needs a fix-forward commit and a new tag - never a force-push over a published tag.

Why adopters never see any of this: `CHANGELOG.md` lives at the repo ROOT deliberately - the
zips package only the tracked `.claude/` tree plus files distctl authors, and the adopt skill
copies only `.claude/` paths, so the system's own history structurally cannot reach an
adopting repo and there is no filter to maintain. The `changelog_parity` check rides the same
knob as the home-only generators (`distribution.enabled`, absent reads false): in an adopting
project it reports SKIP, in this repo it enforces the pin.

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

**4g. Close the ritual** (it needs the ticket and consumes it, so this is the last `checkctl`
write of the ritual):

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

## Phase 5 - ROUTE

The last step: set up the next session. Read the dials first
(`python3 .claude/tools/statectl.py status` prints Mode and Phase), then ask ONE
AskUserQuestion with up to three questions. Skip a question that does not apply; when none
applies, say so in one line and stop.

**1. Phase for the next session.** Skip it in Freestyle: that mode has no phase contract.
Options: stay in the current phase, or advance to the next one (plan, build, review, deploy,
then plan again for the next milestone; with no phase set, offer to start in plan). Recommend
staying unless this session's work met the phase's exit. On advance, run:

```
python3 .claude/tools/statectl.py phase <next>
```

It runs the current phase's exit check first. If it refuses, show the failing rows as printed,
then ask whether to fix them first or leave anyway. Offer `--override "<why>"` only when the
user chooses to leave anyway, and use their reason, not yours. Leaving `review` needs the
human's sign-off: ask for it and pass their words verbatim as `--signoff "<text>"`. Never write
a sign-off yourself.

**2. Mode for the next session.** Keep the current mode, or switch with
`python3 .claude/tools/statectl.py mode <freestyle|guided-solo|fableous-orchestrated>`. One line
each on what it costs and what it gives:

- **Freestyle**: costs nothing extra; gives the record, the gates and this ritual, with no
  phase contract and no exit checks.
- **Guided Solo**: costs an exit check at every phase change and a contract printed at each
  session start; gives a lifecycle that cannot quietly skip a test run or a sign-off. One agent.
- **Fableous Orchestrated**: costs more agent calls and a validated handoff envelope per task;
  gives Guided Solo plus a lead routing work to a team (`protocols/orchestration.md`).

**3. Maturation pass now?** Default no. It suits a session whose goal is improving the system
itself, not a side effect of shipping project work. When the user says yes, run it now:

1. **Full anatomist audit**: folder health, drift review, every component's card checked
   against its source, unclassifiable components surfaced. Not just reconciliation.
2. **Pruning sweep**: usage evidence from the record (`obsctl.py report --by session`, journal
   and log greps) against every skill, agent, tool and rule. Unused machinery becomes a
   demotion or archival proposal. Growth and shrinkage run on the same evidence.
3. **Proposals become task cards for a plan phase**: each surviving proposal goes through the
   evolution bar and the user gate, then lands as a `/plan-task` file in `.claude/tasks/`
   (Definition of done, one Test command, registered under a milestone), so the next plan
   phase picks it up. Each card's Test includes its `checkctl.py probe` entry, so "it is built"
   stays a mechanical claim.
4. **A decision list** for the maintainer: every judgment call the audit could not make alone,
   with a recommendation.

Card and map changes the pass makes are rebuilt by the next ritual, which the user opens by
typing `/project-memory` again: this one is already complete.
