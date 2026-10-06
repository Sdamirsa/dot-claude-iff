# Task: T6 - secret placement check and doctor

_Created 2026-10-05 · Status: done_

Milestone: M-0.3.0-alpha · Closes: #7 · Shared contracts: `030a-00-milestone.md`

## Goal

A key in the wrong place is caught by the ritual and by `checkctl doctor`, and the docs say where keys belong.

**Definition of done:** `checkctl` has a `secrets_placement` check (registered in `memory.json` check phase) and a read-only `doctor` subcommand; a planted key-shaped string in a tracked fixture file FAILs with file, line and pattern name and never prints the value; `.mcp.json` literals under key-like names WARN and `${VAR}` passes; `.env` / `settings.local.json` not ignored FAILs when the file exists; `reference/secrets.md` exists; the wrong `.env` claim in `docs/understand.html` and the 'shell profile' hints are corrected to name `env` in `.claude/settings.local.json` as the per-user home.

**Test:** `python3 .claude/tools/tests/run_tests.py test_secrets -q`

## Context

- Claude Code facts (docs): per-user secrets go in `env` of `.claude/settings.local.json`; MCP keys via `claude mcp add --env` or `${VAR}` / `${VAR:-default}` in `.mcp.json` (command, args, env, url, headers); Claude Code only auto-ignores settings.local.json when it creates the file itself.
- Patterns: Anthropic/OpenAI/OpenRouter `sk-` families, GitHub `ghp_`/`github_pat_`, AWS `AKIA`, Slack `xox`, Google `AIza`, PEM private-key headers, and generic `(api[_-]?key|token|secret|password)` assigned a 20+ char literal. Allow an inline `iff:allow-secret` pragma and skip the tests folder.
- Scan scope: git-tracked text files only (fallback: walk with pruning when not a git repo). Stdlib only. Expose the scanner as a function taking a root so T7 can run it on an export folder.
- `doctor` rows: python version, bash, git, hooks wired and present, record root resolvable/writable/not cloud-synced, console port, heartbeat age, secrets placement, mode/phase readable. Exit non-zero on any FAIL. It must work before T3/T4 land (tolerate absent mode/phase/ticket).

## Plan

- [x] `secrets_placement` check + registry/memory wiring, done when: tests for each pattern family, pragma, and redaction pass
- [x] gitignore assertions via `git check-ignore`, done when: fixture tests pass
- [x] `checkctl doctor`, done when: a test runs it on a fixture project and reads the table
- [x] `reference/secrets.md` + doc corrections + probe + card, done when: `checkctl probe` green in fixture

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder
- **State files:** `.claude/state/handshakes/T6.json`
- **Updated:** 2026-10-06

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

