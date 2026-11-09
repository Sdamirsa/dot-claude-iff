# v0.3.0 - the road to beta: what to test by hand

The automated suite passes on Windows and Ubuntu (CI, every push). What it cannot cover is
anything that only happens inside a live Claude Code session: hooks firing, prompts, the
console in a browser, a second repository adopting the kit. Those are below. When every box
is ticked once by a human, or its failure fixed and re-ticked, 0.3.0 goes to beta.

Write findings as GitHub issues, or straight into the proposal box from a session in this
repo: `python3 .claude/tools/statectl.py proposal add "<text>" --source human`.

## Where things run

Every step names one of these four places. Start every test in a **new** session: hooks and
agents load at session start.

| Place | What it is | How to get there |
|-------|------------|------------------|
| **A. Main session** | Claude Code started in this repo's main checkout | `cd` to the repo root, run `claude` |
| **B. Your terminal** | A plain shell in the repo root, no Claude Code involved | PowerShell, Git Bash or the VS Code terminal, in the repo root |
| **C. Browser** | Any browser on this machine | Open the URL the session start prints (`http://dot-claude-iff.localhost:7146/console.html` here) |
| **D. Elsewhere** | Claude Code started somewhere else: inside a worktree, or in another repository | Named in the step |

"Session start" means the block Claude Code prints before your first prompt. "The ritual"
means typing `/project-memory` yourself; nothing else opens it.

## Already evidenced (2026-10-29, the alpha.2 session)

These ran live during the session that cut alpha.2 and need no repeat unless something
changes: the session-start block (resume pointer, mode and phase line, ritual nudge, console
URL), `checkctl doctor` clean, the progress-report advisory firing on a file write in
Fableous Orchestrated mode, the console serving its page and live payload over HTTP, and the
worktree rules against a real `git worktree add` (suite only, not a live session).

Evidenced by this repo's own history, ruled so by the maintainer on 2026-11-08: item 1's ritual
end to end (runs closed on `dev`, the latest at commit 9c50c60) and the STATUS rewrite; item 5's
phase exits (the plan, build and review sign-offs are in the journal); item 6's dispatch, builder
and accept flow (tasks T14 to T20 carry their envelopes). Still to run live from those items:
item 1's plain-words refusal, item 5's deploy-phase proposal reflex, item 6's three sub-agent
denials. Run live in the review round of 2026-11-05 to 08: items 4, 7 and 10 (findings PR-17
to PR-23; the fixes landed in task 030b-t21).

## 1. The ritual opens for you and only for you - Place A

The biggest unknown: CHECK, POLISH, PUBLISH and ROUTE have never run end to end live.

- [ ] Type `/project-memory`. Expect: the ritual starts and CHECK runs. If it refuses with
      "no ritual ticket", the prompt hook did not fire: note your Claude Code version, then in
      **Place B** run `python3 .claude/tools/checkctl.py ticket --grant` and type the command
      again in the same session.
- [ ] In a fresh session, ask the agent in plain words to "run the project-memory ritual"
      without typing the command. Expect: it asks you to type it; if it tries `checkctl run`
      anyway, that refuses.
- [ ] At the end, ROUTE asks about the next phase, the next mode and a maturation pass.
- [ ] `.claude/STATUS.md` is rewritten by the ritual (the hand-written copy disappears).

## 2. Session start - Place A

- [ ] Evidenced once. Re-check only after a Claude Code update: the block shows the mode
      and, in Guided Solo or Fableous Orchestrated, the phase with its contract (five lines at
      most). Freestyle shows only the mode line.
- [ ] No hook errors. If Claude Code reports a problem with `settings.json` (for example an
      unknown `UserPromptExpansion` event), record the exact message.

## 3. Doctor - Place B

- [ ] Evidenced on this machine. On a second machine or a fresh clone:
      `python3 .claude/tools/checkctl.py doctor` shows no FAIL (line endings, bash, python).

## 4. Console - Place C, with a session open in Place A

- [ ] Open the URL the session start prints. Expect: the NOW tab shows a Progress panel, the
      top bar shows mode and phase, WORK lists proposals, MAP lists context files.
- [ ] Leave it open during a long turn. Expect: "last activity" keeps moving and the panel
      updates without a reload.
- [ ] Check it in dark mode and at half-screen width.

## 5. Modes and phases - Place A, commands in Place B

- [ ] In B: `python3 .claude/tools/statectl.py mode guided-solo`, then `statectl.py phase
      plan`. In A: create a task with `/plan-task`. In B: `statectl.py phase build`. Expect:
      the plan exit check passes only when the task file has a Definition of done, one Test
      command and a milestone.
- [ ] `statectl.py phase review` refuses until the task's Test passes; `--override "<why>"`
      lets you through and is logged in the journal.
- [ ] In B: `statectl.py phase deploy`. In A: ask for a new feature. Expect: the agent files a
      proposal instead of coding it.

## 6. Fableous Orchestrated - Place A

- [ ] In B: `python3 .claude/tools/statectl.py mode fableous`. In A: give a task big enough
      to delegate. Expect: the lead writes a task file, runs `statectl dispatch <id>`, and
      starts a `builder` in the worktree it cut.
- [ ] When the builder returns, the lead runs `statectl accept <id>`: the envelope validates
      and the branch merges.
- [ ] The lead posts a progress block at merges and roughly every 30 minutes of work.
- [ ] After a ritual has ingested the session, in B: `python3 .claude/tools/obsctl.py report
      --by agent` shows tokens split by agent.
- [ ] Ask the lead to have a sub-agent edit `.claude/hooks/` or `.claude/config/` with Write,
      with Bash and with PowerShell. Expect: each denied, with a reason.

## 7. Adopt into a second repository - Place D

- [ ] Fresh zip into an empty repo: unzip `.claude/dist/dot-claude-iff-fresh.zip` into a new
      folder with `git init`, start Claude Code there. Expect: `doctor` is clean and the first
      `/project-memory` completes.
- [ ] Adopt kit into an existing repo: start Claude Code in that repo and type `/adopt` with
      the kit path. Expect: the visibility question once, defaulting to `ignored` for a public
      remote, and the `.gitignore` block appears.
- [ ] `/adopt --upgrade` in `extrct_private` (it runs v0.2-era files plus its own worker
      rules). Expect: nothing of yours is overwritten, drift is reported, and the two new
      `CLAUDE.md` sections (worker, reviewer) land in its filled guide.

## 8. Secrets and export - Places A and B

- [ ] Put a fake key (`sk-` followed by forty letters) in a tracked file. Expect: the ritual
      CHECK (A) and `doctor` (B) both fail and neither prints the key. Remove it after.
- [ ] In B: `python3 .claude/tools/distctl.py export --to <empty folder with git init>
      --dry-run`, then without `--dry-run`. Expect: the folder has your project files, no
      `.claude/state`, no logs.

## 9. Folder context and brainstorm - Places B and A

- [ ] In B: `python3 .claude/tools/mapctl.py context <some file>` lists the guides that load
      for it.
- [ ] Add a `CLAUDE.md` in a subfolder and a rule under `.claude/rules/` with a `paths:` glob
      that matches nothing. Expect: the next CHECK (in the ritual, A) warns about the dead glob.
- [ ] In A: `/adhd <an open question>`. Expect: five branches on the cheaper model, then one
      critic pass; clusters, a shortlist with one pick, and traps, with no score chips.

## 10. A session in a worktree (Cview-ready backbone) - Place D

- [ ] In B: `git worktree add .claude/worktrees/t1 -b worktree-t1`, then write a `TASK.md`
      in that folder. Start Claude Code inside `.claude/worktrees/t1`. Expect: the first thing
      printed is a WORKTREE SESSION block, the first prompt is accepted (the worker may read
      TASK.md, its brief, and nothing else before answering), and nothing of main's
      orientation (resume block, RITUAL, CONSOLE) appears.
- [ ] In that session: ask for `python3 .claude/tools/statectl.py pointer x` (refuses with a
      one-line reason, then its `STATE_FAIL` trailer); type `/project-memory` (`checkctl`
      refuses the same way, trailer `CHECK_FAIL`); ask for a `git commit`
      on the branch (goes through); ask for a Write to `.claude/state/journal.jsonl` (the gate
      denies it with a reason naming DONE.md).
- [ ] End a turn with `TASK.md` present and no `DONE.md`. Expect: one advisory line, the turn
      is not blocked. Write `DONE.md`: the line stops.
- [ ] In B: `git status --short` in both checkouts is empty (the worker's own commits aside);
      the capture landed in `../dot-claude-iff_claude_iff/spool/`, and no `t1_claude_iff`
      folder appeared under `.claude/worktrees/`. Clean up: `git worktree remove
      .claude/worktrees/t1` and `git branch -d worktree-t1`.
- [ ] Under Cview (Place D, an empty repo adopted with the fresh zip): paste the agent block
      at the end of Cview's `docs/setup/repo-claude-setup.md` as the first prompt. Expect:
      eight PASS after the kit's own fixes (PR-19 and PR-20, in the beta.1 fix round): E3 passes
      because `statectl dispatch` and `accept` refuse while `.cview/` exists, E4 on intent (the
      root resolver avoids a git subprocess and lands on the same answer).

## Beta criteria

- Every box above ticked once by a human, or its failure fixed and re-ticked.
- One fresh adoption green on its own `doctor`. The upgrade adoption (item 7, `extrct_private`)
  ran once on 2026-11-08 and ended green, but by a 3-way merge the skill does not describe
  (PR-21); a green upgrade by the documented path is a criterion for the stable 0.3 tag, not
  for beta.
- CI green on both OS at the release commit (it is today).
- Then, in Place A, say "release 0.3.0-beta.1": the release steps in the project-memory skill
  bump the stamp and the citation, pin the changelog, rebuild the zips and tag on `dev`.

## Known limits of this alpha

- The policy gate reads shell commands statically. It stops honest mistakes; it is not a
  sandbox, and its resistance to deliberate evasion was not independently verified. Not a
  beta blocker.
- The periodic progress report fires on the lead's next file write, not on a timer.
- Between releases the zips on `dev` may lag the tree; `main` and every tag are exact.
- Item 4 was passed in a browser on 2026-11-06; the Claude Chrome extension is still untested.
- `/adopt --upgrade` assumes an unmodified install. On a target that customised system files
  the agent must classify each file against the source's history and 3-way merge by hand
  (PR-21); it worked once, on `extrct_private`, and is not yet the documented path.
- Before the beta.1 fix round, a session start on Windows could report its own console from an
  earlier session as another project's and print no URL (PR-17, fixed in task 030b-t21).
