# v0.3.0-alpha.1 - what to test by hand

The automated suite passes on Windows and Ubuntu. What it cannot cover is anything that only
happens inside a live Claude Code session: hooks firing, prompts, the console in a browser.
Those are below, most important first. Write findings as GitHub issues or straight into the
proposal box (`python3 .claude/tools/statectl.py proposal add "<text>" --source human`).

Start every test in a **new** Claude Code session: hooks and agents load at session start.

## 1. The ritual opens for you and only for you

- [ ] Type `/project-memory`. The ritual should start (CHECK runs). If it refuses with "no
      ritual ticket", the prompt hook did not fire: note your Claude Code version, then run
      `python3 .claude/tools/checkctl.py ticket --grant` in your own terminal and try again.
- [ ] In a fresh session, ask the agent to "run the project-memory ritual" without typing the
      command. It should ask you to type it, and `checkctl run` should refuse if it tries.
- [ ] At the end, the ROUTE step asks about next phase, next mode and a maturation pass.
- [ ] `STATUS.md` is rewritten and no longer mentions `--hard`.

## 2. Session start

- [ ] The start block shows the mode and, in Guided Solo or Fableous Orchestrated, the
      phase with its contract (five lines at most). Freestyle shows only the mode line.
- [ ] No hook errors appear. If Claude Code reports a problem with `settings.json` (for
      example an unknown `UserPromptExpansion` event), record the exact message: on an older
      version that key may be rejected.

## 3. Doctor

- [ ] `python3 .claude/tools/checkctl.py doctor` shows no FAIL on your machine.
- [ ] On a second machine or a fresh clone it still shows no FAIL (line endings, bash, python).

## 4. Console

- [ ] Open the console URL the session start prints. The NOW tab shows a Progress panel, the
      top bar shows mode and phase, WORK lists proposals, MAP lists context files.
- [ ] Leave it open during a long turn: "last activity" keeps moving and the panel updates
      without a reload.
- [ ] Check it in dark mode and at half-screen width.

## 5. Modes and phases

- [ ] `statectl mode guided-solo`, then `statectl phase plan`, create a task with
      `/plan-task`, then `statectl phase build`: the plan exit check should pass only when the
      task file has a Definition of done, one Test command and a milestone.
- [ ] `statectl phase review` refuses until the task's Test passes; `--override "<why>"` lets
      you through and is logged.
- [ ] In deploy, ask for a new feature: the agent should file a proposal instead of coding it.

## 6. Fableous Orchestrated

- [ ] `statectl mode fableous`, then give a task big enough to delegate. The lead should
      write a task file, run `statectl dispatch <id>`, and start a `builder` in the worktree.
- [ ] When the builder returns, `statectl accept <id>` validates the envelope and merges.
- [ ] The lead posts a progress block at merges and roughly every 30 minutes of work.
- [ ] `python3 .claude/tools/obsctl.py report --by agent` shows tokens split by agent after a
      ritual has ingested the session.
- [ ] Try to have a sub-agent edit `.claude/hooks/` or `.claude/config/` with Write, Bash and
      PowerShell: each should be denied.

## 7. Adopt into a second repository

- [ ] Fresh zip into an empty repo: `doctor` is clean, the first `/project-memory` completes.
- [ ] Adopt kit into an existing repo: `/adopt` asks the visibility question once, defaults
      to `ignored` for a public remote, and the `.gitignore` block appears.
- [ ] `/adopt --upgrade` from v0.2.2: nothing of yours is overwritten; drift is reported.

## 8. Secrets and export

- [ ] Put a fake key (for example `sk-` followed by forty letters) in a tracked file: the
      ritual CHECK and `doctor` both fail and neither prints the key.
- [ ] `python3 .claude/tools/distctl.py export --to <empty folder with git init> --dry-run`,
      then without `--dry-run`: the folder has your project files, no `.claude/state`, no logs.

## 9. Folder context and brainstorm

- [ ] `python3 .claude/tools/mapctl.py context <some file>` lists the guides that load for it.
- [ ] Add a `CLAUDE.md` in a subfolder and a rule under `.claude/rules/` with a `paths:` glob
      that matches nothing: the next CHECK warns about the dead glob.
- [ ] `/adhd <an open question>`: five branches run on the cheaper model, then one critic
      pass; the answer has clusters, a shortlist with one pick, and traps, with no score chips.

## Known limits of this alpha

- No hook has run in a live session before this release.
- The policy gate reads shell commands statically. It stops honest mistakes; it is not a
  sandbox, and its resistance to deliberate evasion was not independently verified.
- The periodic progress report fires on the lead's next file write, not on a timer.
- Between releases the zips on `dev` may lag the tree; `main` and every tag are exact.
