---
name: adopt
description: Install the dot-claude-iff system into a target project, or upgrade an already-installed one. Invoke on "adopt this system into <path>", "set up my project like this", "install the claude system", "bootstrap .claude for <project>", or "/adopt --upgrade" to bring an installed project's system up to date. A rare, high-blast-radius operation: runs only when the user explicitly asks for it.
disable-model-invocation: true
---

# adopt: install and upgrade the dot-claude-iff system

You are installing this operating system into a real project, or upgrading one that already has
it. Install is a gated procedure: inventory first, one BLOCKING gate for the things only the
human knows, then install, adapt, verify, and hand off. Never guess at mission or invariants,
read the target's code, and ask.

Orient yourself before Phase 1:

- `<source>` = the root of a dot-claude-iff checkout, or any project already running the system.
  This system ships ONE profile, everything, so a running project's own `.claude/` tree is a
  complete, valid asset to copy from, not a subset.
- `<target>` = the root of the project being adopted or upgraded. If you are running inside it,
  `<target>` is the current repo. If no `<source>` was given and you are running inside the
  target, ask for one (a local clone, or a URL to clone first).
- On `/adopt --upgrade`, skip Phases 1 to 2 (the target is already adopted; re-inventorying and
  re-confirming the frame would just repeat questions already answered) and go straight to the
  Upgrade procedure at the end of this file.

## Phase 1: inventory the target

Read before writing anything:

1. `<target>` README and manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`,
   Makefile, whatever exists). Detect language, package manager, test runner, lint command.
2. An existing `<target>/CLAUDE.md` or `<target>/.claude/CLAUDE.md`, if any: its content must
   survive as source material, not be clobbered.
3. An existing `<target>/.claude/`, if any: list every file. Anything already there (skills,
   agents, rules, config) belongs to the target, not to you.
4. Top-level layout: where code, scripts, configs, and outputs live.

Summarize the inventory to the user in a few lines (stack, commands found, what already exists).
This feeds Phase 2 and Phase 4.

## Phase 2: confirm the frame (BLOCKING gate)

Some facts only the human holds. This is a BLOCKING gate: use AskUserQuestion, recommended option
first, before touching the target. Ask:

| Question | Why you can't infer it | Recommended default |
|---|---|---|
| Mission, 1 to 2 sentences: what is this project for? | Code shows *what*, not *why* | Draft one from the README and offer it for editing |
| What counts as an invariant here? | Load-bearing constraints live in the human's head | Offer candidates you spotted in Phase 1 (e.g. "single source-of-truth data file", "atomic writes") |
| Track `.claude/` in git? | Team/privacy call | Yes, the system is designed to be tracked |

Also compute and report the record root, and warn if it looks cloud-synced:

```
CLAUDE_PROJECT_DIR=<target> python3 <source>/.claude/tools/_lib.py --record-root
```

If the printed path contains `Dropbox`, `OneDrive`, `iCloud`, or `Google Drive`, say so explicitly
as a WARNING before the gate closes: a cloud-synced RECORD_ROOT defeats the point of an
out-of-repo, out-of-git record (it gets synced somewhere else instead), and the user may want to
override `policy.record_root` before the first capture ever writes.

Do not proceed on silence. If the user answers "Other", capture their wording verbatim.

## Phase 3: install

Adopt from a CLEAN source. Check `git -C <source> status --short` first: if the source has
uncommitted changes, tell the user and prefer its last commit (or ask them to commit first). An
adoption snapshotted mid-edit can copy a file whose tests, producer or consumer moved on a
minute later, and the target inherits a mismatch nobody wrote on purpose.

Copy the entire `<source>/.claude/` tree into `<target>/.claude/`, file by file, using this rule:

**Merge, never overwrite.** A path that already exists in `<target>/.claude/` is a conflict:
report it to the user (filename plus a one-line description of what each side has) and keep the
target's version unless told otherwise. A path `<source>` has that `<target>` lacks: copy it.
Skip `__pycache__/` directories and any `*.tmp` files if present in `<source>`: build artifacts,
not shipped assets.

This system ships **one profile: everything.** Unlike systems that hold back machine-specific
files, `hooks/`, `settings.json`, and `settings.local.json` ARE part of this install: they are
written portably against `$CLAUDE_PROJECT_DIR` (never a hardcoded path), so they carry cleanly
into any target. Copy them like every other file, subject to the same merge rule (an existing
`<target>/.claude/settings.json` is a conflict to report, not a file to skip).

**Exclude per-project state, even from `<source>`.** Two trees under `<source>/.claude/` hold
that project's own runtime history, not shippable system content, and copying them wholesale
would hand the target another project's resume pointer, open loops, needs-human queue, and
tamper-evidence anchor:

- `.claude/state/`: create the directory (and `state/handshakes/`) empty in `<target>`. Copy no
  files from `<source>`'s `journal.jsonl`, `session.json`, `HANDOFF.md`, `heartbeat.json`,
  `needs-human.jsonl`, `generators.json`, `memory-run.json`, or any stub/envelope under
  `handshakes/`. The target's first `SessionStart` and first ritual populate these fresh.
- `.claude-iff/obs/`: copy `README.md` only. Create `obs/rollups/` empty; do NOT copy
  `<source>`'s `anchor.json` or any `rollups/*.json`, those are the source project's own sealed
  totals and tamper-evidence head, meaningless (and actively misleading to retro-analyst's usage
  evidence, per its own agent file) if grafted onto a different project's record.

Also skip the derived files `.claude/console/console.html` and `.claude/system-map/map.json`
(both listed under `derived_files` in `policy.json`): Phase 5 regenerates both from scratch.

Also create, if `<target>` lacks them:
- The rest of `.claude-iff/` beyond `obs/` (structure only), following the exclusion above.
- A root `.gitignore` entry for `.claude/tools/__pycache__/` and any stray `*.tmp` atomic-write
  leftovers under `.claude/`, if the target doesn't already ignore them. RECORD_ROOT itself needs
  no gitignore entry: it is a sibling folder outside the repo, already unreachable by git.

Do not create `RECORD_ROOT` (the sibling `<target-parent>/<target-name>_claude_iff/` folder) by
hand: the first hook invocation creates it on demand, empty, and that is the correct starting
state.

## Phase 4: adapt

Turn the copied scaffold into this project's system:

1. **`.claude/CLAUDE.md`**: INSTANTIATE it from the template first, then fill it. Copy
   `<target>/.claude/skills/adopt/CLAUDE.template.md` (which Phase 3 just installed) to
   `<target>/.claude/CLAUDE.md`. Never copy `<source>`'s own filled `.claude/CLAUDE.md`: that
   is the wrong project's guide, and Phase 3's merge rule deliberately excludes it. Then fill
   every `{{PLACEHOLDER}}` the template declares (mission, stack summary, dev commands,
   invariants, domain notes) from the Phase 1 inventory and the Phase 2 answers, from repo
   reality, not guesses. If the target already had a root `CLAUDE.md` or a pre-existing
   `.claude/CLAUDE.md`, fold that content verbatim into the right sections (Domain notes is the
   usual home) and remove the redundant file with the user's OK: keep exactly ONE project guide
   (Claude Code auto-loads `./CLAUDE.md` OR `./.claude/CLAUDE.md`, never both).
2. **STATUS.md**: rewrite to the project's true current state (its sections filled from the
   inventory and whatever the user says is in flight), not template text.
3. **Project-log.jsonl**: if `<source>` was a pristine checkout this file arrived empty; if
   `<source>` was an already-adopted project, Phase 3's merge just copied ITS populated log.
   Either way, REPLACE `<target>/.claude/Project-log.jsonl` with exactly one milestone line,
   today's date, none of `<source>`'s history:
   `{"date":"<today>","type":"milestone","title":"dot-claude-iff adopted","summary":"Installed the .claude operating system; CLAUDE.md and STATUS.md initialized.","artifacts":[".claude/"],"tags":["setup","tooling"],"source":"session"}`
   and record the same fact in the journal so the continuity feed reflects it too:
   `python3 .claude/tools/statectl.py milestone adopt-<today> --title "dot-claude-iff adopted"`.
4. **LESSONS.jsonl**: TRUNCATE to zero lines, even if Phase 3's merge just copied `<source>`'s
   own earned lessons from an already-running project. Lessons are earned, not inherited.
5. **`system-map/layers.json`**: keep the shipped `spines` list as is, and keep `flows: []`
   empty. Flow layers are the project's own pipeline stages; they get named through evolution as
   the project accumulates components, never guessed at adoption from folder names.
6. **Old-project state**: if `<source>` is an adopted project rather than a pristine checkout,
   its `.claude/tasks/`, `.claude/rules/`, `.claude/research/`, `.claude/reference/`, and
   `.claude/system-map/cards/` may hold ITS project-specific history, not the system's. Empty
   `<target>/.claude/tasks/` of everything EXCEPT `_template.md` and remove `tasks/archive/`
   entirely (the template is scaffold, not history; the archive is all history). For
   `system-map/cards/`, drop ONLY cards whose `layer` names one of `<source>`'s own flow layers
   (a layer not in the five spines and not null). Keep spine cards AND keep `layer: null`
   cards: unplaced system components (`tool._lib`, `human.maintainer`) are core anatomy that is
   honestly unplaced, not project residue - deleting them removes real system parts from every
   downstream adoption. Review `rules/`, `research/`, `reference/` and keep only what genuinely
   generalizes to any project running this system; drop the rest now. This is the one exception
   to the archive-never-delete convention, because none of it is `<target>`'s history yet.

Do NOT preinstall speculative project-specific skills, rules, or agents, and tell the user so
explicitly: the evolution protocol (`.claude/protocols/evolution.md`) adds tooling when evidence
appears, not before. An unused skill is negative value.

## Phase 5: verify

Run the checklist against reality, not against your memory of what you did. Every command below
runs with `<target>` as the working directory (or `CLAUDE_PROJECT_DIR=<target>` set):

- [ ] Every file under `<source>/.claude/` exists at its matching path under `<target>/.claude/`,
      or was reported as a conflict and resolved with the user.
- [ ] `grep -rnE "\{\{[A-Z_]+\}\}" <target>/.claude --include="*.md" | grep -v "skills/adopt/" | grep -v _template`
      returns nothing. The pattern matches real placeholders only (`{{PROJECT_NAME}}`-shaped),
      scoped to markdown: a bare `grep "{{"` can NEVER return clean on a correct install,
      because Python f-strings in hooks, brace literals in test fixtures, and the adopt skill's
      own shipped template (kept placeholder-form on purpose, so the target can seed the next
      adoption) all contain `{{` forever. A checkbox that cannot pass teaches agents to report
      `partial` on every install, or worse, to stop reading the checklist.
- [ ] Exactly one project guide exists: `<target>/.claude/CLAUDE.md`, or a root `CLAUDE.md` if
      the user chose that, never both.
- [ ] Every JSON/JSONL file under `<target>/.claude/` parses (config, journal, Project-log,
      LESSONS, layers, registry).
- [ ] `python3 <target>/.claude/tools/checkctl.py run --phase check` runs and its verdicts are
      legible (fresh install, so most checks pass trivially; report anything that doesn't).
- [ ] `python3 <target>/.claude/tools/mapctl.py scan` runs, then dispatch the **anatomist agent
      unconditionally** (never skip this, even on a clean install) to read the newly placed
      components and fill in relations: a map that ships with cards but no edges is worse than
      no map, because it looks complete and isn't.
- [ ] `python3 <target>/.claude/tools/checkctl.py run --phase polish` runs clean. Run the
      generators through the RITUAL, not by invoking mapctl/consolectl directly: the ritual is
      what stamps `state/generators.json`, and generators run outside it leave the freshness
      ledger reading "has never run through the ritual" on every CHECK from day one. This one
      command compiles the map, builds the story feed and builds the console, in order.
- [ ] **HOOKS-FIRE PROBE**: trigger a real event through the real hook and look for its effect:
      `echo '{}' | bash <target>/.claude/hooks/heartbeat.sh`, then confirm
      `<target>/.claude/state/heartbeat.json` exists and is fresh, and ask the user to make one
      ordinary tool call so Claude Code itself fires a hook (project hooks require the user to
      trust them before Claude Code executes them at all, so "installed but silently not
      firing" is a real state). Running `test_hooks.py` is NOT this probe: it exercises the
      hook scripts against temp fixtures and passes on any correct file copy, telling you
      nothing about whether THIS project's hooks are trusted and firing.

Report to the user in Structured Return form: `STATUS` (done | partial | blocked), `RESULT`
(what was installed, what was merged, what was skipped and why), `EVIDENCE` (file listing, grep
output, the checkctl/mapctl/consolectl/test_hooks output), plus `DEVIATIONS` and `QUESTIONS` if
any. Any unchecked box means STATUS is partial: say so plainly.

## Phase 6: first ritual

Close by putting the system into motion:

1. Set a pointer to the project's actual first move:
   `python3 <target>/.claude/tools/statectl.py pointer "<first real next action>"`.
2. Show the console: `python3 <target>/.claude/tools/consolectl.py open` prints the console's
   `file://` path and the one-line command to start the live server (`consolectl.py serve`).
   Recommend the half-screen layout: console in one half of the screen, Claude Code in the
   other, so state is visible while you work, per `.claude/README.md`.
3. Offer to run `/plan-task` on the project's first real task, right now.
4. Remind the user of the session ritual: start by reading `.claude/STATUS.md`, the active task
   file, and Watch-outs; end every session with `/project-memory`.
5. Set expectations for the first retro: it will likely return `NO-CHANGES`, that is correct
   behavior, not a failure. The system grows from evidence the project hasn't generated yet.

## Upgrade (`/adopt --upgrade`)

For a `<target>` that already has `.claude/` installed:

1. Diff every file under `<source>/.claude/` against its counterpart in `<target>/.claude/`,
   with the same exclusions as Phase 3: never diff or touch `.claude/state/` (including
   `handshakes/`), `.claude-iff/obs/anchor.json`, `.claude-iff/obs/rollups/`, or the derived
   files `console/console.html` and `system-map/map.json`. All of these are per-project runtime
   state, not shipped system content, on the target side as much as on the source side.
   - Missing in `<target>`: a new asset in this version of the system, not conflicting with
     anything, copy it.
   - Byte-identical: already up to date, skip.
   - Different: drift. Report it to the user (path, and a short description of what changed on
     the source side) and do NOT overwrite; a file the target customized is the target's, this
     system never clobbers a customized file on upgrade.
2. Apply the non-conflicting updates (the "missing in target" set) the same way Phase 3 does:
   merge-never-overwrite, portable against `$CLAUDE_PROJECT_DIR`.
3. Once applied, update `system_version` in `<target>/.claude/config/registry.json` to match
   `<source>`'s value. `checkctl`'s registry lint warns loudly whenever `system_version` is
   behind what `<source>` ships, so an upgrade that skips this step leaves the warning firing
   even though the files are current.
4. Re-run the Phase 5 verify checklist; an upgrade that adds new tools or agents still needs the
   anatomist dispatched to place them and `consolectl.py build` to pick them up.
5. Report drift and what was applied in Structured Return form, same shape as Phase 5.
