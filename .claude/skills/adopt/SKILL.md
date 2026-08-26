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
| Does this project already run an agent operating system of its own (a continuity engine, policy hooks, self-tests, a console)? | Phase 1 sees files, not systems; kinship flips the whole install mode | If yes, use **Sibling mode** (end of Phase 3) instead of the copy |

Also compute and report the record root, and warn if it looks cloud-synced:

```
CLAUDE_PROJECT_DIR=<target> python3 <source>/.claude/tools/_lib.py --record-root
```

If the printed path contains `Dropbox`, `OneDrive`, `iCloud`, or `Google Drive`, say so explicitly
as a WARNING before the gate closes: a cloud-synced RECORD_ROOT defeats the point of an
out-of-repo, out-of-git record (it gets synced somewhere else instead), and the user may want to
override `policy.record_root` before the first capture ever writes.

Two Windows notes. The substring test above can miss: `Documents` is often OneDrive-redirected
while the visible path still reads `C:\Users\<name>\Documents` (Known Folder Move), so on
Windows also check the registry before declaring the record root cloud-free:
`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" /v Personal`.
And if `python3 --version` does not answer usefully (on many Windows boxes `python3` is the
Microsoft Store stub), use `python` for every command in this skill AND tell the user so
explicitly - the hooks' heredocs shell out to `python3`, so a stub there means the hooks
silently do nothing until the mismatch is resolved.

Do not proceed on silence. If the user answers "Other", capture their wording verbatim.

## Phase 3: install

Adopt from a CLEAN source. Check `git -C <source> status --short` first: if the source has
uncommitted changes, tell the user and prefer its last commit (or ask them to commit first). An
adoption snapshotted mid-edit can copy a file whose tests, producer or consumer moved on a
minute later, and the target inherits a mismatch nobody wrote on purpose.

Copy only files git TRACKS in `<source>` - take the manifest from
`git -C <source> ls-files -- .claude .claude-iff`, never from a directory walk. A clean
`git status` does not vouch for gitignored content: a private, gitignored tree under
`.claude/` (it happened with `reference/private/`) must never ride an adoption into someone
else's repo. Skip `reference/private/` even if a source tracks it.

Copy that manifest into `<target>/.claude/`, file by file, using this rule:

**Merge, never overwrite.** A path that already exists in `<target>/.claude/` is a conflict:
report it to the user (filename plus a one-line description of what each side has) and keep the
target's version unless told otherwise. A path `<source>` has that `<target>` lacks: copy it.
Skip `__pycache__/` directories and any `*.tmp` files if present in `<source>`: build artifacts,
not shipped assets.

This system ships **one profile: everything.** Unlike systems that hold back machine-specific
files, `hooks/`, `settings.json`, and `settings.local.json` ARE part of this install: they are
written portably against `$CLAUDE_PROJECT_DIR` (never a hardcoded path), so they carry cleanly
into any target. Copy them like every other file, subject to the same merge rule. An existing
`<target>/.claude/settings.json` is a conflict: report it and KEEP THE TARGET'S, like every
other collision. Never replace it wholesale - that single write would unbind all of the
target's hooks, bind this system's six, and silently drop the target's `permissions` and
`env` blocks. Hook-wiring changes are individual line merges the user approves one by one.

**Exclude per-project state and identity, even from `<source>`.** These paths hold that
project's own runtime history or its own voice, not shippable system content, and copying them
would hand the target another project's resume pointer, open loops, needs-human queue,
tamper-evidence anchor - or another project's live contract:

- `.claude/CLAUDE.md`: NEVER copy the source's own filled guide. Kits ship it in placeholder
  form, but a clone or a running project carries a filled one - the wrong project's mission
  and invariants, and it activates by existing (Claude Code auto-loads it the moment it
  lands, no hook or invocation needed). Phase 4 instantiates the target's own guide from
  `CLAUDE.template.md`.
- `.claude/state/`: create the directory (and `state/handshakes/`) empty in `<target>`. Copy no
  files from `<source>`'s `journal.jsonl`, `session.json`, `HANDOFF.md`, `heartbeat.json`,
  `needs-human.jsonl`, `generators.json`, `memory-run.json`, or any stub/envelope under
  `handshakes/`. The target's first `SessionStart` and first ritual populate these fresh.
- `.claude-iff/`: copy the top-level `.claude-iff/README.md` only (that is where the README
  lives; `obs/` holds no README). Create `obs/` with an empty `obs/rollups/` beneath it; do
  NOT copy `<source>`'s `obs/anchor.json` or any `obs/rollups/*.json`, those are the source
  project's own sealed totals and tamper-evidence head, meaningless (and actively misleading
  to retro-analyst's usage evidence, per its own agent file) if grafted onto a different
  project's record.

Also skip the derived files `.claude/console/console.html` and `.claude/system-map/map.json`
(both listed under `derived_files` in `policy.json`): Phase 5 regenerates both from scratch.

Also create, if `<target>` lacks it:
- A root `.gitignore` entry for `.claude/tools/__pycache__/` and any stray `*.tmp` atomic-write
  leftovers under `.claude/`, if the target doesn't already ignore them. While there, check the
  target's EXISTING patterns for over-broad shadows: a generic `dist/`, `build/` or `*.zip`
  matches at any depth and silently untracks `.claude/` paths (checkctl's gitignore_shadowing
  check warns about this from then on). RECORD_ROOT itself needs no gitignore entry: it is a
  sibling folder outside the repo, already unreachable by git.

Do not create `RECORD_ROOT` (the sibling `<target-parent>/<target-name>_claude_iff/` folder) by
hand: the first hook invocation creates it on demand, empty, and that is the correct starting
state.

**Sibling mode.** When the Phase 2 gate found the target already running an agent operating
system of its own (common when source and target share ancestry), do NOT run the copy above.
The merge rule's model - "a path the target lacks → copy it" - assumes the target lacks the
SYSTEM, not just the paths: on a sibling, functional counterparts exist under different names,
so almost nothing collides and almost everything copies, and the literal outcome is a second
operating system installed beside the first. Measured on a real sibling: 124 source files,
7 path collisions, 103 files would have landed, ONE genuinely additive - the rest a rival
journal beside the target's own, a second record root its deny rules did not cover, decoy
configs that look authoritative but are read by nothing, agents joining the roster on
existence alone, and skills whose triggers ("resume", "where were we") hijack the target's own
continuity. Instead, walk a **concept bridge** with the user: for each of this system's
mechanisms - the generator registry/ledger (law 1), the evolution protocol's pruning half, the
Task Brief, the honesty protocol, lessons rotation, the verifier role, the seal allowlist -
ask whether the target already has it under its own name. Bridge only what is missing,
implemented in the TARGET's own stores and idiom, never by copying this system's files. The
bridge report IS the install; Phases 4-6 then apply only to what was bridged.

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
   (Claude Code auto-loads `./CLAUDE.md` OR `./.claude/CLAUDE.md`, never both). A substantial
   fold will push the guide past the anatomist's ~80-line health threshold on day one; that is
   expected, not a defect - do not trim the user's content to satisfy the check. Note "condense
   in an early evolution pass" and move on; the anatomist's audit grants adoption-day folds
   that grace.
2. **STATUS.md**: rewrite to the project's true current state (its sections filled from the
   inventory and whatever the user says is in flight), not template text.
3. **Project-log.jsonl**: this step, step 4 and step 6 remove content that arrived FROM
   `<source>` in this install - NEVER the target's own past. If `<target>` already had a
   populated log, earned lessons, or live tasks before this install (entries predating the
   adoption are the tell), that is ITS history: keep it and append, do not replace. Otherwise:
   if `<source>` was a pristine checkout this file arrived empty; if `<source>` was an
   already-adopted project, Phase 3's merge just copied ITS populated log. Either way, REPLACE
   `<target>/.claude/Project-log.jsonl` with exactly one milestone line,
   today's date, none of `<source>`'s history:
   `{"date":"<today>","type":"milestone","title":"dot-claude-iff adopted","summary":"Installed the .claude operating system; CLAUDE.md and STATUS.md initialized.","artifacts":[".claude/"],"tags":["setup","tooling"],"source":"session"}`
   (on a target with its own pre-existing log: APPEND that milestone line instead)
   and record the same fact in the journal so the continuity feed reflects it too:
   `python3 .claude/tools/statectl.py milestone adopt-<today> --title "dot-claude-iff adopted"`.
4. **LESSONS.jsonl**: TRUNCATE to zero lines when its content came from `<source>` - lessons
   are earned, not inherited. But lessons the TARGET earned itself (in any prior format,
   `LESSONS.md` included) were paid for here: MIGRATE them into `LESSONS.jsonl` rows instead
   of truncating them away.
5. **`system-map/layers.json`**: keep the shipped `spines` list as is, and keep `flows: []`
   empty. Flow layers are the project's own pipeline stages; they get named through evolution as
   the project accumulates components, never guessed at adoption from folder names.
6. **Old-project state**: if `<source>` is an adopted project rather than a pristine checkout,
   its `.claude/tasks/`, `.claude/rules/`, `.claude/research/`, `.claude/reference/`, and
   `.claude/system-map/cards/` may hold ITS project-specific history, not the system's. Empty
   `<target>/.claude/tasks/` of everything that ARRIVED IN THIS INSTALL except `_template.md`,
   and remove an ARRIVED `tasks/archive/` entirely (the template is scaffold, not history; the
   archive is all history) - a live task or archive the target already had keeps running,
   untouched, per the step-3 rule. For
   `system-map/cards/`, drop ONLY cards whose `layer` names one of `<source>`'s own flow layers
   (a layer not in the five spines and not null). Keep spine cards AND keep `layer: null`
   cards: unplaced system components (`tool._lib`, `human.maintainer`) are core anatomy that is
   honestly unplaced, not project residue - deleting them removes real system parts from every
   downstream adoption. Review `rules/`, `research/`, `reference/` and keep only what genuinely
   generalizes to any project running this system; drop the rest now. This is the one exception
   to the archive-never-delete convention, because none of it is `<target>`'s history yet.
7. **Home-only generators stay off.** Kits ship `memory.json` with `distribution.enabled`
   already false; when `<source>` was a clone or a running project, its copy arrived with the
   knob TRUE - set it to `false` in `<target>/.claude/config/memory.json` now. It gates
   `demo_build` and `dist_build`, dot-claude-iff's own release machinery: in `<target>` they
   would zip its private `.claude/` into redistributable archives and render its real session
   state into `docs/`, which many repos publish. checkctl reports them as SKIP with the reason
   named; that is the correct steady state everywhere except the source repo.
8. **Console port, decided once.** `config/console.json` ships port 7717, and every adoption
   on one machine inherits it, so the second project's console loses the bind every session.
   Pick a free port ONCE, now - e.g.
   `python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()"`
   - and write it into `<target>/.claude/config/console.json` with a `_comment` naming why, so
   a future `/adopt --upgrade` reads it as intentional rather than unexplained drift. Decide it
   here and never again: the session-start hook reports a busy port instead of silently
   failing, but a collision reported every session is still a collision.

Do NOT preinstall speculative project-specific skills, rules, or agents, and tell the user so
explicitly: the evolution protocol (`.claude/protocols/evolution.md`) adds tooling when evidence
appears, not before. An unused skill is negative value.

## Phase 5: verify

Run the checklist against reality, not against your memory of what you did. Every command below
runs with `<target>` as the working directory (or `CLAUDE_PROJECT_DIR=<target>` set):

- [ ] Every file in the Phase 3 copy manifest (git-tracked under `<source>/.claude/`, minus the
      Phase 3 exclusions) exists at its matching path under `<target>/.claude/`, or was
      reported as a conflict and resolved with the user.
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
      no map, because it looks complete and isn't. On a same-session install the `anatomist`
      agent TYPE does not exist yet - Claude Code registers agent types at session start, so
      dispatch by type fails with "agent type not found". That is expected, not a reason to
      restart mid-procedure or report partial: dispatch a general-purpose agent with
      `<target>/.claude/agents/anatomist.md` as its contract (honor the frontmatter `model`
      pin), and accept its handshake envelope as the anatomist's.
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

1. Diff every git-tracked file under `<source>/.claude/` (same manifest rule as Phase 3, same
   `reference/private/` skip) against its counterpart in `<target>/.claude/`,
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
