# Public and private - work with your agent privately, publish cleanly

This system writes a lot down: what you asked for, what the agent decided, what went wrong.
That record is the point, and it is yours. This page is the recommended way to keep it private
while the project itself is public.

## The recommended pattern

Work with your agent in a **private** repository. Publish releases to a separate **public**
repository with one deliberate command:

```
python3 .claude/tools/distctl.py export --to ../my-project-public --dry-run   # look first
python3 .claude/tools/distctl.py export --to ../my-project-public             # then copy
```

`../my-project-public` is an existing checkout of the public repository. The export writes a
clean copy of the project into it and stops. You review it there (`git status`, `git diff`),
then commit and push yourself. **The export never commits and never pushes:** it runs no git
command that changes anything, anywhere, and it never touches the target's `.git`.

A project that is private and stays private needs none of this: keep `visibility: tracked` and
commit `.claude/` with the code.

## What never leaves, and why

Three kinds of material never travel through an export, whatever you configure:

- **The record.** `RECORD_ROOT` (the sibling folder `<parent>/<repo>_claude_iff/`) holds the
  raw capture of every session: prompts, tool calls, transcripts. It lives outside the
  repository, so git cannot reach it, and the export never reads it (if you pointed it inside
  the project, the export skips that folder too). The committed record surface
  `.claude-iff/obs/` (daily rollups and the seal anchor) describes your sessions and stays out
  as well.
- **The memory spine and work state.** `.claude/STATUS.md`, `.claude/Project-log.jsonl`,
  `.claude/LESSONS.jsonl`, `.claude/tasks/`, `.claude/research/` and `.claude/state/` (the
  journal, the handoff, the needs-human queue). These hold your own words and your project's
  history.
- **Per-user and secret files.** Any `settings.local.json`, `.env`, `*.env`, `.env.local` or
  `.env.*.local`; `.claude/reference/private/`; and `.mcp.json` when it holds a literal
  credential (one that only uses `${VAR}` references exports normally). Agent worktrees
  (`.claude/worktrees/`) never export either.

That list is hard-coded in `distctl.py`. Configuration can narrow what an export carries; it
cannot widen past this floor.

## Visibility: `tracked` or `ignored`

`.claude/config/memory.json` carries `visibility`, which `/adopt` asks about once:

| Value | What git sees | Suits |
|---|---|---|
| `tracked` (the default) | `.claude/` and `.claude-iff/` are committed with the code; only per-user and derived files are ignored | a private repository; a team that shares the system, its tasks and its history through git |
| `ignored` | `.claude/` and `.claude-iff/` never reach git; they live only in this checkout | a public repository; a developer who keeps the agent's memory to themselves |

For a team: under `tracked`, everyone who clones gets the same system, hooks, skills, tasks and
log, and the journal merges like any other file. Under `ignored`, each person's `.claude/` is
their own and nothing the agent writes is shared; if teammates should have the system itself,
publish it through the export (`publish.json`, below) or share it some other way.

When the remote is public, `/adopt` recommends `ignored`: the journal and the project log hold
your own words.

The setting acts through a **managed block** in the root `.gitignore`, between two marker
lines. Only that block is ever rewritten; your own lines are never touched:

```
python3 .claude/tools/distctl.py gitignore            # show the block for the current value
python3 .claude/tools/distctl.py gitignore --apply    # write it (appended when absent)
```

Both values keep the secrets safety net (`.env`, `*.env`, `.env*.local`,
`settings.local.json`) and the python caches out of git.

To switch, edit `visibility` in `memory.json`, then run `gitignore --apply`. Going from
`tracked` to `ignored` in a repository that already committed `.claude/` also means untracking
the files, a git change only you make and one that keeps them on disk:
`git rm -r --cached .claude .claude-iff`, then commit. History that was already pushed still
holds the old files; a public repository with that history needs a fresh repository (or a
history rewrite) to be clean. The `gitignore_shadowing` check warns while the knob and git
disagree.

## What an export copies

1. Every file git does not ignore in the project (tracked, plus untracked but not ignored),
   outside `.claude/` and `.claude-iff/`.
2. From `.claude/` and `.claude-iff/`, only what `.claude/config/publish.json` `include`
   allows. These are read from the filesystem, so this works under `visibility: ignored`;
   under `tracked`, a file git ignores still stays out.
3. Minus the hard floor above.

```json
{
  "include": [".claude/skills/", ".claude/agents/*.md", ".claude/README.md"]
}
```

Globs are relative to the repository root, fnmatch style (`*` also crosses `/`); an entry
ending in `/` names a whole folder. The shipped default is empty: nothing from `.claude/` is
published until you list it.

**Secrets gate.** Before writing anything, the export runs the secrets scanner
(`.claude/reference/secrets.md`) over every file it plans to copy and refuses on any FAIL
finding, printing the file, the line and the pattern name, never the value. WARN findings are
printed for review and do not block.

**Refusals.** Nothing is written when the target does not exist or is not a folder; when the
target is the project, sits inside it, or contains it; when the project is not the top of a
git work tree; or when the target has a folder or a link where a file must go.

**Mirror and manifest.** `.claude-export-manifest.json` at the target root lists what the last
export placed there: relative paths, the source's `system_version` and a date, no machine
paths. The next export removes a listed file that has left the set, and never removes anything
the manifest does not list, so your own files in the public repository (its CI config, say)
are safe unless the project has a file at the same path, which the export overwrites. Commit
the manifest with the release. Folders left empty are not removed (git does not see them).

**One rewrite.** The root `.gitignore` is exported with its managed block re-rendered for
`tracked`, so a project kept `ignored` does not hand the public repository a rule that hides
the `.claude/` files you chose to publish.

`--dry-run` prints the add / update / remove plan and writes nothing. Exit code 0 means the
export ran (or, dry, would run); anything else means it refused.

## The release loop

```
cd my-project                                    # private working repo
python3 .claude/tools/distctl.py export --to ../my-project-public --dry-run
python3 .claude/tools/distctl.py export --to ../my-project-public
cd ../my-project-public
git status && git diff                           # you review
git add -A && git commit -m "release vX.Y.Z"     # you commit
git push                                         # you publish
```
