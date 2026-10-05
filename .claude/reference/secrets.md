# Secrets - where keys belong, and what never goes in git

A key in a committed file is the one mistake a later commit cannot undo: deleting the line leaves
the key in history, and every clone and fork already has it. So the rule is placement, decided
before the key is ever typed, and a ritual check that catches the slip before PUBLISH commits it.

## Where each kind of key lives

| Key | Home | Why there |
|---|---|---|
| Keys for tools this system runs (`ANALYZE_API_KEY` for `obsctl analyze`; `OPENROUTER_API_KEY` / `OPENAI_API_KEY` are honored too) | the `env` block of `.claude/settings.local.json` | Per-user and gitignored. Claude Code applies that block to the session it runs, so Bash commands, hooks and the console the session autostarts all see the variable. |
| The same key, for commands you run outside Claude Code | your shell (`export ANALYZE_API_KEY=...` in a profile or a one-off) | The alternative, not the default: a key exported in a profile reaches every program you start. |
| MCP server keys, your own servers | `claude mcp add --env KEY=value ...` at local or user scope | Stored in your user config, outside the repo. |
| MCP server keys, servers shared through the repo's `.mcp.json` | `${VAR}` or `${VAR:-default}` in `.mcp.json`, with the value set in `settings.local.json`'s `env` or your shell | Claude Code expands these in `command`, `args`, `env`, `url` and `headers`; the committed file carries the name, never the value. Note that `claude mcp add --scope project --env KEY=value` writes the literal INTO `.mcp.json`: use `${KEY}` there instead. |
| Machine-local paths (not secrets) | `.env` at the repo root, `CLAUDE_IFF_*` names only | The one thing this system reads from `.env` is a `CLAUDE_IFF_*` override such as the record-root path. |

A minimal `.claude/settings.local.json`:

```json
{
  "env": {
    "ANALYZE_API_KEY": "<your key>"
  }
}
```

A shared `.mcp.json` entry that carries no secret:

```json
{
  "mcpServers": {
    "tracker": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${TRACKER_TOKEN}" }
    }
  }
}
```

## `.env` is not a key store here

Claude Code does not load `.env` files, and neither does this system for keys: a key in `.env` is
invisible to both unless your own shell loads it first. `.env` stays gitignored because other
tools in your project may use it, and because `CLAUDE_IFF_*` paths name your machine.

## What never goes in git

- Any key, token, password or private key, in any tracked file, including docs and examples
  (use an obvious placeholder such as `<your key>`).
- `.claude/settings.local.json`, `.env` and `.env*.local`. Claude Code adds
  `settings.local.json` to the ignore rules only when it creates the file itself; a file made by
  hand or copied in is not ignored unless `.gitignore` says so.
- An `env` block holding a key in the committed `.claude/settings.json`: everyone who clones the
  repo gets it.
- A literal key in `.mcp.json`.
- The record folder: it lives outside the repo, so git cannot reach it at all.

## How it is enforced

- **`secrets_placement`**, a CHECK step of `/project-memory` (`checkctl.py`). It scans what the
  next `git add -A` would carry (tracked plus untracked-not-ignored files; a pruned walk when the
  project is not a git repository) for vendor key shapes (Anthropic, OpenAI and OpenRouter `sk-`
  families, GitHub `ghp_` / `github_pat_`, AWS `AKIA`, Slack `xox`, Google `AIza`, PEM private-key
  headers), which FAIL, and for a key-like name assigned a long literal, which WARNs. In
  `.mcp.json`, a literal under a key-like name in `env`, `headers`, `args` or the URL WARNs (FAIL
  when it has a vendor shape) and `${VAR}` passes. A vendor-shaped value in the committed
  `.claude/settings.json` `env` block FAILs. A per-user secret file that exists but is not
  gitignored FAILs; outside a git repository that row is SKIP.
- **Findings never carry the value.** Each one names the file, the line and the pattern, nothing
  else: findings are printed to your terminal and stored in the committed ritual record.
- **Never scanned:** `.git/`, `.claude/worktrees/`, the record folder, the suite's own tests
  folder, binaries, and the VALUES inside `settings.local.json` and `.env` (their sanctioned job
  is to hold keys; the check only asserts they stay out of git).
- **False alarms:** add `iff:allow-secret` in a comment on that line, or list the path in
  `policy.json` `secrets.allow_paths` (prefixes or globs; also the escape hatch for a stack that
  commits its `.env` on purpose).
- **`python3 .claude/tools/checkctl.py doctor`** runs the same check read-only, beside the
  machine prerequisites (python, bash, git, hooks, record root, console port, heartbeat).

## If a key reached a commit

Rotate it at the provider first: that is the only fix. Then remove it from the file and, if the
repository is public or shared, from history too. Deleting the line alone protects nothing.
