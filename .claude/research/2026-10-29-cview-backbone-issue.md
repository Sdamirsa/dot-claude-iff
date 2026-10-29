# Issue draft for github.com/Sdamirsa/dot-claude-iff (written 2026-10-29, from the Cview side)

Copy of the issue text handed to the user; the canonical list it rests on is
`docs/setup/repo-claude-setup.md` (Essential E1 to E8). Kept here so a resumed session knows
what was proposed upstream and can check what landed.

---

## Title

Cview-ready backbone: make the worktree-and-review essentials the default hygiene, so an adopted repo works alone and under Cview with no extra setup

## Why

dot-claude-iff runs one session per repository today. Cview runs one worker session per git
worktree plus a main session that orchestrates and reviews; it merges a task branch into `main`
only when the human clicks, and it refuses to merge into a dirty main checkout. Everything Cview
needs from a repo's `.claude/` turned out to be plain hygiene: hooks that know which checkout
they run in, no hook that mutates git, derived state that a worker cannot commit by accident,
a start hook that never blocks a first turn, and two short role sections. Each of those also
makes the single-session case cleaner. The goal of this issue is one backbone: a repo adopted
with dot-claude-iff works on its own, and is already ready for Cview; nothing in dot-claude-iff
depends on Cview.

The list below is the Essential table of Cview's `docs/setup/repo-claude-setup.md`, mapped onto
dot-claude-iff components. That page also ends with an agent prompt that audits a repo against
the same eight points; it is the acceptance test here.

## How to build it (deep, not bulky)

- No new gate, no new ritual, no new knob unless an existing one is wrong. Detect the context,
  never ask: a linked worktree is `git rev-parse --git-common-dir` not equal to `.git`; a Cview
  task is a `TASK.md` at the checkout root.
- Hygiene is default-on. Anything role-specific (worker, reviewer) is a short section that only
  applies in its detected context, and it is prose for the model, not a gate.
- Smallest change that passes each point. Where a point is already true, add the test that
  keeps it true and move on.

## The eight points

1. **Root resolution** (E4). Every hook and tool derives the repo root from
   `git rev-parse --show-toplevel` (or its own cwd when git is absent), never from a stored
   path; the record root (`<root>_claude_iff`) and scratch folders land beside that root. Test:
   run the SessionStart and PostToolUse hooks from a linked worktree in a temp repo and assert
   every path they wrote is under the worktree or its sibling record folder.
2. **One writer per tracked state file** (E2). Only the main checkout's session writes
   `.claude/state/*` and the journal. In a linked worktree the hooks write their session
   state to the record root instead, and the ritual is refused with one line. New adoptions
   ignore the derived files (`session.json`, `HANDOFF.md`, `needs-human.json`) by default; the
   journal stays tracked and main-only. Test: a worker session in a worktree leaves
   `git status --short` empty in both checkouts.
3. **Hooks and tools never mutate git** (E3). No `git push`, `reset`, `stash`, `checkout` or
   `worktree` from any hook or tool run by a hook. The lead's `statectl dispatch` and `accept`
   are user-invoked commands and stay the only place git history moves; `git push` stays
   human-only. Test: a grep over `.claude/hooks/` and the hook-invoked tools.
4. **Ignore the containers** (E5). The adopt template adds `.claude/worktrees/` and the record
   root pattern to `.gitignore` (Cview also writes `.git/info/exclude`, which clones do not
   carry).
5. **Worker section in CLAUDE.md** (E6), applied when `TASK.md` exists at the root: the task is
   `TASK.md`; edit only the files its Owns section lists; commit often, never push; never edit
   `TASK.md`; write `DONE.md` (`# DONE`, `summary:`, `tests:`) or `BLOCKED.md` with the
   question, then stop; never run the end-of-session rituals or commit state files. The
   SessionStart hook prints a WORKTREE SESSION block in that context (extrct_private has a
   first version of this since 2026-10-29; upstream it).
6. **Reviewer section in CLAUDE.md** (E7), applied in the main checkout when `.cview/` exists:
   REVIEW messages arrive in this terminal; run `/code-review main...worktree-<task>`; decide
   with `cview verdict <task> pass|reject --reasons "..."`; never edit in a worktree; the human
   merges. Prose only, no gate.
7. **SessionStart never blocks** (E8). Tickets and gates apply to the ritual commands a user
   types, not to a session's first turn; in a worktree the hook prints and exits 0 whatever the
   state. Test: a session in a worktree accepts its first prompt with no tool call needed.
8. **A gentle end-of-turn reminder for workers.** When `TASK.md` exists and neither `DONE.md`
   nor `BLOCKED.md` does, the Stop hook adds one advisory line ("the task is open: write
   DONE.md or BLOCKED.md when you stop for good"). Advisory, never blocking: Cview's recorder,
   not the hook, decides what done means.

## A hypothesis worth testing in the same change

A session's identity is its checkout: main, or one linked worktree. dot-claude-iff's `dispatch`
already cuts a worktree per builder and `accept` merges it; a Cview worker is the same shape
seen from the hooks. If the WORKTREE SESSION block and point 2 apply to builders too, sub-agent
and worker sessions stop being special cases: one detection, one set of rules, and the
handshake envelope stays dot-claude-iff's contract while `DONE.md` stays Cview's. Validate on a
real dispatch before making it the rule.

## Acceptance

- The agent block at the end of Cview's `docs/setup/repo-claude-setup.md` reports eight PASS on
  a fresh `/adopt` into an empty repo, with no edits.
- The tests named in points 1, 2, 3 and 7 exist in dot-claude-iff's own suite and are green.
- `CLAUDE.md` grows by two short sections and nothing else; no new file under `.claude/config/`.

## Non-goals

Any Cview code or dependency in dot-claude-iff; orchestration logic beyond detection and the
two sections; changing the handshake envelope; making any of this configurable before someone
needs it to be.

---

## What landed in dot-claude-iff (2026-10-29, v0.3.0-alpha.2)

The copy above is the proposal as handed over from the Cview side. This section is the
dot-claude-iff side: where each point lives, and the one place the design departs from the
text.

| Point | Where | Test |
|-------|-------|------|
| 1 root resolution | `_lib.worktree_info` (the one detector: a `.git` FILE pointing at `<main>/.git/worktrees/<name>`, the answer `git rev-parse --git-common-dir` gives without a process per hook call), `_lib.main_checkout`; the record root anchors at the main checkout from a worktree, so one project has one record | `test_worktree.TestRecordRoot`, `TestHooksFromARealWorktree` (a real `git worktree add`; the four hooks write only under the worktree or the record root) |
| 2 one writer | `_lib.main_state_guard` in `journal_append`; statectl's projections are a no-op in a worktree and every mutating command refuses in one line; `checkctl run` and `complete` refuse in one line; the managed `.gitignore` block ignores `session.json`, `HANDOFF.md`, `needs-human.json` (the journal stays tracked); the gate rings `.claude/state/` of a worktree copy (handshakes/ carved out) for every identity | `TestNoStateWritesFromAWorktree`, `TestGateWorker`, both checkouts clean in `TestHooksFromARealWorktree` |
| 3 no git from hooks | unchanged; the lead's `dispatch`/`accept` stay the one place history moves | `TestHooksNeverMutateGit` (an ast probe over the tools, a code grep over the hooks, `git push` nowhere) |
| 4 containers ignored | `.claude/worktrees/` was already in the block; the three projections joined it | `TestContainersAndSections`, `git check-ignore` in the real-worktree test |
| 5 worker section | `.claude/CLAUDE.md` and the adopt template; the WORKTREE SESSION block in `session-start.sh` | `TestContainersAndSections`, the real-worktree test |
| 6 reviewer section | same two files, prose only | same |
| 7 SessionStart never blocks | the worker block prints and exits 0; no ticket or gate stands before a first turn anywhere | the real-worktree test (exit 0, no decision JSON) |
| 8 end-of-turn reminder | `heartbeat.sh` (the Stop hook) emits one `systemMessage` line while TASK.md is open and neither DONE.md nor BLOCKED.md exists; never `decision: block` | the real-worktree test |

**One departure.** A worker keeps write access to its COPY of the protected tree (hooks,
tools, config). extrct_private denies it; here it stays open, because this repo's own builders
already edit hooks and tools in their dispatch worktrees and the lead reviews the diff before
`accept` merges it. Under Cview the main session's `/code-review` plays the same role. What a
worker cannot do: run `dispatch` or `accept`, write `.claude/state/` (handshakes/ excepted),
open the ritual.

**The hypothesis.** Validated by the suite and by hand on a real git worktree, not yet on a
live builder dispatch: `python3 .claude/tools/statectl.py pointer ...` run from inside a
dispatch worktree (no `CLAUDE_PROJECT_DIR` in the environment, as a builder's Bash call has it)
finds its root from its own location and refuses, which is exactly the mistake handshake T4
recorded in alpha.1. The gate's state ring also applies to the lead's builders from main's
side. The WORKTREE SESSION block only fires for a session STARTED in the worktree (a Cview
worker); a dispatched builder is a sub-agent of main's session and gets the builder brief
instead, so the two stay different in that one respect, by the shape of Claude Code's hooks.

**Not done here.** The acceptance step that runs Cview's agent prompt against a fresh
`/adopt` into an empty repo is the next adoption's job (item 10 of
`docs/alpha-test-checklist.md`).
