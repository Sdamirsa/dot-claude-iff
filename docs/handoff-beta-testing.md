# Handoffs: testing v0.3.0-alpha.2 toward beta

Superseded: v0.3.0-beta.1 shipped 2026-11-09; kept for the record.

Three handoffs for three machines. Each is the first message of a fresh Claude Code session;
paste it as is. They share one core (the checklist, the report shape, the constraints) and
differ in how the repo gets there and which checklist items the machine can run. Findings
land as GitHub issues or in the proposal box; the beta call is made against
`docs/alpha-test-checklist.md`, every box ticked once.

Before any of them: `dev` is pushed (head includes the CHECKOUT line at session start); the
canonical Windows clone is the one on the maintainer's PC; a fresh clone's record root is
empty, so its first session-start block says "no sealed history here", which is correct for a
fresh clone and not a fault.

---

## 1. Windows (the canonical clone, the maintainer's PC)

Where: a terminal in the canonical clone's root, then `claude`. The console opens in a
browser on this machine, so this is the one place items 4 and 6 run in full.

```
Objective: run the road-to-beta checklist for dot-claude-iff v0.3.0-alpha.2 on this Windows
machine and report, so the maintainer can decide on 0.3.0-beta.1.

Context: this is the canonical clone on branch dev (`git pull --ff-only origin dev` first;
report if it is not clean). The checklist is docs/alpha-test-checklist.md; each item names
where it runs (A main session = you, B my terminal = commands you hand me to run myself,
C browser = me, D elsewhere = a worktree or another repo). Item 1 (the ritual), 2 (session
start) and 3 (doctor) were already evidenced on this machine on 2026-10-29. Before testing,
one small task: surgically trim .claude/CLAUDE.md toward ~80 lines (proposal PR-14): keep it
a map, move explanations into the files it points at, keep the worker and reviewer sections
short, verify with `python3 .claude/tools/mapctl.py context .claude/CLAUDE.md` and the
context tests (`python3 .claude/tools/tests/run_tests.py context ritual -q`), one commit.

Run, in this order: item 4 (I open the console URL you print; tell me what to look for, I
report back), item 10 (the worktree session: you prepare the worktree and TASK.md, I start a
second Claude Code inside it and paste what it prints), item 5, item 6 (you are the lead;
dispatch a small real task to a builder), item 8, item 9, then item 7 last (adopt --upgrade
into a private repo running v0.2-era files; stop and ask before writing anything there).

Report after each item, not at the end: a table with item, box, PASS/FAIL/SKIP, and the
evidence (the exact line printed, the file written, the command's exit). On FAIL: the smallest
fix with a test if it is in .claude/ and you are sure of it, otherwise an issue draft for me to
file. Never run /project-memory yourself; I type it when we stop. Never push; never tag;
never create a release. Do not touch .claude/dist/. Finish with the table of every box and a
one-line beta verdict.
```

---

## 2. DGX (Linux, a fresh clone)

Where: an SSH shell on the DGX. Prerequisites: `git`, `python3` (3.8+), `bash`, the `claude`
CLI logged in. No browser, so item 4 is the HTTP half only; everything else runs.

```
Objective: run the road-to-beta checklist for dot-claude-iff v0.3.0-alpha.2 on this Linux
machine (a fresh clone) and report, so the maintainer can decide on 0.3.0-beta.1. Linux is the
second OS the hooks have to prove themselves on; CI covers ubuntu but no live session has.

Setup (do this first, report each step): `git clone https://github.com/Sdamirsa/dot-claude-iff
-b dev ~/dot-claude-iff`, `cd` into it, run `python3 .claude/tools/checkctl.py doctor` and
`python3 .claude/tools/tests/run_tests.py -q`; both must be clean before anything else. Then
start a new Claude Code session there so the hooks load (Claude Code asks once to trust them;
I will answer). The first session-start block will say "no sealed history here": expected on a
fresh clone, note it and move on.

The checklist is docs/alpha-test-checklist.md; each item names where it runs (A main session =
you, B my terminal = commands you hand me, C browser = not available here, D elsewhere = a
worktree or a second repo in /tmp). Run, in this order: item 2 (session start on Linux: paste
the block), item 3 (doctor, already run), item 4 HTTP half only (`curl -s -o /dev/null -w
"%{http_code}" http://127.0.0.1:<port>/console.html` and `/live/console.json`; the port is in
the session-start block), item 10 (worktree session; I start the second Claude Code inside the
worktree and paste what it prints), item 5, item 6 (dispatch a small real task to a builder;
check `.claude/worktrees/` and the merge), item 8, item 9, item 7 (fresh zip into an empty
`git init` repo under /tmp; then the adopt kit into a second one). Skip item 1 unless I type
/project-memory myself.

Report after each item: a table with item, box, PASS/FAIL/SKIP and the evidence (exact line,
file, exit code). Anything that differs from Windows (line endings, bash resolution, paths) is
a finding even when it passes. On FAIL: the smallest fix with a test if it is in .claude/ and
you are sure, committed on a branch `fix/<item>` (never on dev), otherwise an issue draft for
me to file. Never push; never tag; never run /project-memory; do not touch .claude/dist/.
Finish with the table of every box and a one-line beta verdict for Linux.
```

---

## 3. Claude Code on the cloud (claude.ai/code, a sandbox clone)

Where: claude.ai/code with the GitHub repository connected, branch `dev`. The sandbox has git,
python3 and bash; no browser of mine reaches its ports, and the sandbox starts fresh each
session, so this run is about the hooks, the tools and the suite on a third environment, plus
the adoption paths. Items 4 and 10's "second Claude Code" cannot run there.

```
Objective: run the parts of the road-to-beta checklist for dot-claude-iff v0.3.0-alpha.2 that
a cloud sandbox can run, and report, so the maintainer can decide on 0.3.0-beta.1.

Setup first, report each step: confirm the checkout is branch dev (`git branch --show-current`,
`git log --oneline -1`); `python3 .claude/tools/checkctl.py doctor`; `python3
.claude/tools/tests/run_tests.py -q` (run it once, in the background if you can).
Paste the session-start block you received: it should show a CHECKOUT line with "no sealed
history here" (expected in a fresh sandbox), the MODE line (Guided Solo, phase review) and no
hook errors. If Claude Code reported any problem loading .claude/settings.json, quote it.

The checklist is docs/alpha-test-checklist.md. Run what applies here: item 2 (the block
above), item 3 (doctor), item 4 HTTP half only (curl the console page and /live/console.json on
the port the block printed; if no console started, say so and why from the record root's
console.log), item 5 (modes and phases; I cannot type in your terminal, so run the B-place
commands yourself and say that you did), item 8 (the fake key: create it, run doctor, confirm
neither output prints the key, remove it; then the export dry run into /tmp), item 9 (mapctl
context; the dead-glob rule; skip /adhd unless the cheaper model is available to you), item 7
(fresh zip into an empty `git init` repo under /tmp, run its doctor and suite there; then the
adopt kit into a second empty repo, run /adopt against it and answer the visibility question
"ignored"), and item 10 as far as the tools go without a second session: `git worktree add
.claude/worktrees/t1 -b worktree-t1`, write TASK.md there, then from inside that folder run
`python3 .claude/tools/statectl.py pointer x` (must refuse in one line), `bash
.claude/hooks/session-start.sh < /dev/null` (must print a WORKTREE SESSION block), and
`echo '{"hook_event_name":"Stop"}' | bash .claude/hooks/heartbeat.sh` (must print one
systemMessage line); then `git status --short` in both checkouts must be empty apart from
TASK.md; remove the worktree after. Skip item 1 and item 6.

Report after each item: a table with item, box, PASS/FAIL/SKIP and the evidence (exact line,
file, exit code). On FAIL: the smallest fix with a test if it is in .claude/ and you are sure,
committed on a branch `fix/<item>` (never on dev) and pushed only if this session can push
branches, otherwise a diff in your report. Never tag; never create a release; never run
/project-memory; do not touch .claude/dist/. Finish with the table of every box and a one-line
verdict for this environment.
```

---

## After the three runs

Collect the three tables. Beta criteria (from the checklist): every box ticked once by a human
or its failure fixed and re-ticked; one upgrade adoption and one fresh adoption green on their
own doctor; CI green at the release commit. Then, in the Windows main session: "release
0.3.0-beta.1". The release steps in the project-memory skill bump the stamp and the citation,
pin the changelog, rebuild the zips and tag on `dev`; `release.yml` publishes the pre-release.
