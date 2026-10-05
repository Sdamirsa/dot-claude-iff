# Changelog

One pinned section per released version, newest first. Curated from `.claude/Project-log.jsonl`
at release time - written for readers of the release, not a commit dump. The `changelog_parity`
check fails the ritual if a version tag (or the stamped `system_version`) ever lacks its
section here.

This file lives at the repo root on purpose: the distribution zips package only the tracked
`.claude/` tree plus files they author, and the adopt skill copies only `.claude/` paths - so
this project's own release history structurally never reaches an adopting repo.

## Unreleased

- Nothing yet.

## v0.3.0-alpha.1 - 2026-10-05

Pre-release for testing. The suite passes on Windows and Ubuntu, but none of the new hooks
has run in a live session yet: work through `docs/alpha-test-checklist.md` before relying on
it. "Latest" stays on v0.2.2 until v0.3.1.

- Modes: `statectl mode freestyle | guided-solo | fableous-orchestrated`. Freestyle (the
  default) is the record, the gates and the ritual with nothing enforced. Guided Solo adds
  phases. Fableous Orchestrated adds a lead that plans and stays accountable while builders
  and scouts do the work (issues #11, #13).
- Phases: `statectl phase plan | build | review | deploy`. Each prints a short contract at
  session start; leaving one runs an exit check (`checkctl phase-exit`): plan needs task files
  with a named test under a milestone, build reruns those tests, review needs a human
  sign-off, deploy needs a clean `checkctl doctor` (#13).
- Proposal box: `statectl proposal add | list | resolve`. Ideas that are out of scope right
  now, including everything raised during deploy, land here and feed the next plan.
- The ritual is the user's: `/project-memory` only opens when the user types it. A prompt hook
  mints a short-lived ticket and `checkctl run` refuses without one. `checkctl ticket --grant`
  is the human's fallback from their own terminal. One command to remember: it now ends with a
  ROUTE step (next phase, next mode, maturation pass), which replaces `--hard` (#9).
- Fableous orchestration: `protocols/orchestration.md`, `builder` and `scout` agents with
  model and effort pins that are compared against the registry, one validated JSON handoff
  envelope (`checkctl handoff`), `statectl dispatch` (worktree, stub, filled brief) and
  `statectl accept` (validate, merge, clean up), a delegation nudge, and
  `obsctl report --by agent` (#11).
- Long-run progress: `statectl progress`, a Progress panel on the console's NOW tab, a
  heartbeat that refreshes during long turns, and a periodic progress block for the chat.
- Secrets: a `secrets_placement` check in the ritual, `checkctl doctor` (a read-only health
  table), and `reference/secrets.md` on where keys belong (#7).
- Visibility and publishing: a `visibility` setting (`tracked` or `ignored`) that writes one
  managed `.gitignore` block on every install path, `distctl export` to copy a clean,
  allowlisted tree into a public checkout, and `reference/public-private.md` (#10).
- Folder context: nested `CLAUDE.md` files and `.claude/rules/` are discovered and linted
  (`context_health`), `mapctl context <path>` shows what loads for a file, and
  `mapctl context --suggest` proposes a folder guide only where the lessons justify one (#14).
- Communication: a short Communication block in the guide, and the `/adhd` brainstorm skill
  (adapted from UditAkhourii/adhd, MIT) with its frames, branch count and models in
  `config/brainstorm.json` (#8).
- Security: the policy gate now protects the protected tree on the Bash and PowerShell lanes,
  not only Write/Edit; the ritual ticket and its hook are closed to every identity; builders
  hold no grant in the protected tree. The gate is a tripwire for honest mistakes, not a
  sandbox: the README lists what it does not stop.
- Release engineering: pre-release tags publish as GitHub pre-releases; CI runs the suite on
  every push and pull request on both OS; the zips are LF-normalised and byte-identical
  across OS, and must equal a rebuild on `main`, on tags and on pull requests into `main`;
  a kit self-test runs the fresh zip's own suite inside an empty folder; work happens on
  `dev`, `main` is the last stable release (`reference/release-flow.md`).
- Fixes, Windows: hooks and tests find Git Bash explicitly (a bare `bash` can be the WSL
  launcher); hooks force UTF-8 for their Python children; hook files are pinned to LF; the
  gitignore-shadowing check no longer misses file-level patterns.
- Removed: `--hard`, the vault snapshot knobs, the empty `evolve` phase list, the journal
  `config` action and other settings nothing read. `heartbeat.json` is no longer tracked.
- Not in this release: a model-based yes/no gate (Jev-style, #12). It is in the proposal box
  with the bar for building it.

## v0.2.2 - 2026-08-26

Windows security hardening, and the console learns three things: its own port, its machine,
and its machine's vital signs.

- Brand: logo embedded in the README footer, the tour topbars and favicons, and the published
  demo console (demo-only, behind `DATA.demo`; the local console stays unbranded).
- Fix: `consolectl.py build` write gate now compares the full rendered page (masking only
  wall-clock fields), so template edits propagate instead of silently lagging.
- Fix: the console server binds exclusively on Windows (`SO_EXCLUSIVEADDRUSE`); previously
  `SO_REUSEADDR` let a second console silently steal a port that was in use.
- Release flow: this changelog, plus the `changelog_parity` check and the release steps in the
  project-memory skill (home-repo-only, gated by `distribution.enabled`).
- Distribution: the shipped `console.json` is normalized to the default port; a home repo's
  decided-once port is per-machine and never rides into the kits.
- Security fix, Windows: the policy gate's protected-tree ring compared backslash paths
  against posix prefixes and failed OPEN for sub-agents; the record ring missed drive-letter
  path mentions; the post-write validator never ran; and the PowerShell tool bypassed the
  gate entirely (unmatched in `settings.json`). All four are fixed, with case-insensitive
  matching where the filesystem is, and the full test suite now passes on Windows (212/212).
- Fix, Windows: `obsctl ingest` derived a transcript-directory slug that matched nothing
  (`:` and `\` were not converted), so token tracking was silently empty on Windows.
- Fix, Windows: `_lib.rel()` and `_lib.tilde()` now emit posix-form display paths on every
  platform, keeping committed surfaces (cards, messages) machine-form-free.
- Console ports: the shipped default is now `"auto"` - each project derives a stable port
  from its folder name (7100-7899), ending both the per-project port decision and the
  everyone-collides-on-7717 default; an explicit integer stays a decided-once override, and
  the console answers on `http://<folder>.localhost:<port>/` for a named tab.
- System monitor (opt-in): `sysmon.py` (stdlib probes: CPU, RAM, NVIDIA GPU/VRAM) behind
  `/live/system.json` and a System strip on the NOW tab between needs-human and the
  journal. Live-only by design - samples never enter the built payload, the record, or the
  published demo; kits ship `monitor.enabled: false`.
- Device identity: `state/machine.json` holds a 12-hex user|host fingerprint and a
  human-chosen alias (no username, no hostname committed); the session-start hook compares
  read-only and prints a loud DEVICE CHANGE line when the repo wakes on a different
  machine, until `statectl device "<alias>"` names it.

## v0.2.1 - 2026-08-26

Hardening from three adoption field reports.

- Adopt skill surgery: conflict reporting, sibling mode, and merge-rule clarifications from
  the field-report issues.
- The home-repo-only generators (`demo_build`, `dist_build`) are gated off outside the source
  repo: `distribution.enabled` fails closed, kits ship it false, so an adopting project can
  never package its own private `.claude/` or render its session state into `docs/`.
- Gitignore-shadow lint: a generic ignore pattern silently untracking shipped files now fails
  the ritual.
- Console port collisions fail loudly, naming the one-line fix (`console.json` port).

## v0.2.0 - 2026-08-25

- Theme-token-parity gate: light and dark palettes must define the same tokens.
- Lessons rotation (active window kept small) and new lessons from the retro.
- Tour: one-row top navigation - previous left, dots center, next right.

## v0.1.1 - 2026-08-25

The showcase release.

- Multi-page guided tour with progress tracking: understand (with the privacy risk map), set
  up, six hands-on exercises.
- Live demo console on the docs site: the real payload and template, marked as a serverless
  snapshot.
- Decision-led README (hero, button row, keyword strip), the Partner Guide, and the soft
  brand palette across the docs.
- Console MAP tab: disjoint edge palette with a connection legend; analysis card at the end
  of the STORY feed.

## v0.1.0 - 2026-08-25

First release: one place to interact, one place to control, one command to evolve.

- The `.claude/` operating system: five hooks, seven stdlib-only tools, four protocols, three
  agents.
- The single-page console (NOW / MAP / STORY / WORK) with live polling, the needs-human queue
  and its copy-context bridge.
- The `/project-memory` ritual - check, polish, publish, evolve - with every generator
  registered and a publish transaction that refuses half-built surfaces.
- Distribution: deterministic fresh-start and adopt-kit zips, self-seeding after adoption.
