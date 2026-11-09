# Task: beta.1 fix round - the review round's findings

_Created 2026-11-08 · Status: done_

Milestone: M-0.3.0-beta.1

## Goal

Close the five fix proposals the human review round produced (PR-12, 17, 18, 19, 20) and
reword the alpha checklist where the live run showed its expectations were off, so beta.1 can
be cut with the checklist and the kit agreeing.

**Definition of done:** all of the following hold on `dev`:
- session-start autostart: a live pidfile whose process this interpreter did not spawn is
  treated as alive on Windows (no `os.kill(pid, 0)` as the liveness probe); the URL line prints
  instead of the collision line; test in `test_hooks.py` (PR-17).
- both zips ship a `.gitattributes` at the kit root (`*.sh text eol=lf`,
  `.claude/hooks/* text eol=lf`); `/adopt` merges it like the gitignore block; `doctor` WARNs
  when the repo has no eol=lf rule for `.claude/hooks/*`; test in `test_dist.py` (PR-18).
- `.cview/` is in the managed `.gitignore` block (distctl renderer) and in the home
  `.gitignore`; `.claude/CLAUDE.md` and `skills/adopt/CLAUDE.template.md` worker sections say
  "never open the ritual from a worktree, never stage or commit anything under
  `.claude/state/`" in words the nudge-wording guard accepts (PR-19).
- `statectl dispatch` and `statectl accept` refuse in one line when `.cview/` exists at the
  repo root, with a test in `test_orchestration.py` (PR-20, ruled option a).
- `skills/adhd/UPSTREAM.md` says branches on `sonnet` is OUR choice and upstream pins no
  model (PR-12).
- `mapctl.cap_boxes`: a fold whose members are all planned (lazy) cards is itself lazy, so an
  adopter with many planned cards gets a WARN, not a blocked spec; test in
  `test_architecture.py` (PR-22, reference patch in extrct_private's mapctl.py).
- `docs/alpha-test-checklist.md`: "refuses in one line" reads "refuses with a one-line
  reason"; item 10's first-prompt box allows reading TASK.md; item 10's Cview box reads
  "eight PASS after the kit's own fixes"; items 1, 5, 6 move to "Already evidenced" with the
  date and the three boxes still to run live; the upgrade criterion moves to the stable tag
  with a note; known limits gain the upgrade and the PR-17 history.
- proposals PR-12, 17, 18, 19, 22 resolved `planned` pointing here; zips rebuilt; suite green.

**Test:** `python3 .claude/tools/tests/run_tests.py`

## Context

- Phase review, guided-solo, ruled 2026-11-08 by the maintainer (journal decision): one card,
  the lead implements, priority a workable system for dot-claude-iff users without breaking
  Cview.
- PR-17 root cause: `os.kill(pid, 0)` raises `WinError 87` on Python 3.14 for a process the
  caller did not spawn and TerminateProcess-kills on older Pythons (docs). Reproduced
  2026-11-05 in this repo. Stdlib fix: on win32, ctypes `OpenProcess(0x1000)` +
  `GetExitCodeProcess == 259`; elsewhere keep `os.kill`.
- PR-18 evidence: `git add -A` in the fresh adoption printed LF->CRLF for every kit file
  (Git for Windows system `core.autocrlf=true`); hooks were LF only because nothing had
  touched them yet.
- PR-19 reference diff: the sibling folder `../cview-fresh` commits 9a0667f and
  2ee7c58 (throwaway repo; copy the wording, do not merge).
- Cview's eight requirements: the sibling repo's `../Cview/docs/setup/repo-claude-setup.md`.

## Plan

- [x] PR-17 hook probe + test, done when: `test_hooks.py` has a test with a live foreign pid
      and the hook prints the URL; a fresh session start here prints the URL line.
- [x] PR-18 gitattributes in distctl + adopt + doctor WARN, done when: both zips list
      `.gitattributes`; `test_dist.py` asserts it; `doctor` on a tree without the rule WARNs.
- [x] PR-19 gitignore + worker sentence (CLAUDE.md, adopt template, home .gitignore), done
      when: `git check-ignore .cview/` succeeds here; nudge-wording guard green.
- [x] PR-20 refusal in dispatch/accept, done when: test with a `.cview/` dir at root gets exit
      1 and one reason line from both commands.
- [x] PR-22 lazy fold in cap_boxes + test, done when: `test_architecture.py` has a case with an
      all-lazy fold and the spec builds with a WARN.
- [x] PR-12 UPSTREAM.md sentence, done when: the file says the branch model is ours.
- [x] checklist rewording per the DoD, done when: the six edits are in and
      `docs/alpha-test-checklist.md` has no "refuses in one line".
- [x] resolve PR-12/17/18/19/22 as planned -> this card (done 2026-11-09); zips rebuilt by the
      ritual's generator (anti-rot law: never by hand); suite green; done when: `statectl proposal list` shows them resolved and the Test passes.
- [x] verification pass with the human: fresh adoption + Cview paste expects eight PASS;
      one session start here shows the console URL, done when: both reported.


## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|
| - | - | none open; all five rulings recorded in the journal 2026-11-08 | | | |

## Outcome

Done 2026-11-09. All six fixes landed in commit 36e67c0 (suite 898 green), the ritual run of
the same day rebuilt the zips, and the Windows CI run went green at 5cfdf92 after a graphctl
path fix that the fix round itself did not cover (the GitHub runner's 8.3 temp name; see the
journal). Verification by the maintainer, both recorded as journal notes:

- Place D: a fresh adoption from the rebuilt zip gave no CRLF warnings on `git add`, doctor
  13 ok with the new `gitattributes` row, and the Cview paste eight PASS with zero commits
  (E3 conditional on the `.cview/` refusal, E4 on behaviour, both as ruled).
- Place A: a new session here printed the console URL line while the console belonged to an
  earlier session; the collision line is gone.
- The three live boxes owed from items 1, 5, 6 passed in the same pass (plain-words refusal,
  deploy-phase proposal reflex, three sub-agent denials; a native PowerShell tool route is
  untested because the sub-agent had none).

Left open, on purpose: the zips are stale again because 5cfdf92 touched two kit files after
the rebuild; the release step rebuilds and verifies them on the release commit. New findings
from the pass went to PR-24 (CI status row in CHECK) and PR-25 (the human cannot see the
session-start block; ask the session to quote it).

