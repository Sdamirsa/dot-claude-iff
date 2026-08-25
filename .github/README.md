# dot-claude-iff

A `.claude/` operating system for agent-driven projects.

**One place to interact** (Claude Code) · **one place to control** (a live console) · **one
command to evolve** (`/project-memory`) - on an always-on local record, behind fail-closed
gates.

![The console beside Claude Code](../.claude/reference/side-by-side.svg)

## Start here

1. **[The guided tour](https://sdamirsa.github.io/dot-claude-iff/)** - understand it (including
   the honest privacy section), set it up, and learn it through six small exercises.
   (Same page in the repo: [docs/index.html](../docs/index.html).)
2. **[Get a zip from Releases](../../releases/latest)** -
   `dot-claude-iff-fresh.zip` for a new repo (follow its `START-HERE.md`), or
   `dot-claude-iff-adopt-kit.zip` for an existing repo (follow its `ADOPT.md`; your files are
   merged, never overwritten).
3. **[The manual](../.claude/README.md)** - the daily shape, the three laws, the five human
   collaboration habits, and every command.

## What it is, in one paragraph

Work happens in stable, resumable blocks. Hooks record every event into an append-only record
that lives in a sibling folder outside the repo (verbatim content never enters git - an
allowlist gates what can be committed). A single HTML console shows the live NOW, the system
map (every component as a card with curated relations), the project's evolution story on a
dual clock (wall time or tokens), and the work board - and hands you paste-ready context when
the agent needs a human. One ritual closes each block: check reality, curate memory, rebuild
every derived surface, seal and commit, then propose at most three evidence-backed
improvements that only land with your approval.

Requirements: `bash`, `python3`, `git`, a browser. No pip, no node, no telemetry.

## Trust posture

The system makes zero network calls (grep it: the one `urllib` site is the opt-in analysis
engine, off by default, pointed only at an endpoint you configure). Claude Code itself talks
to Anthropic as it always does - this system adds a local record of what happened, and a
mechanical guarantee that a `git push` can never publish a prompt, a file body, or a secret
from that record. Details and verify-it-yourself commands are in the
[guided tour](https://sdamirsa.github.io/dot-claude-iff/).
