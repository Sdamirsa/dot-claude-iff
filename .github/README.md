# dot-claude-iff

**A `.claude/` operating system for agent-driven projects.** It harnesses the agent, instruments
the work, and hands you one page to watch it all.

> **One place to interact** - Claude Code · **one place to control** - the console ·
> **one command to evolve** - `/project-memory`

![The console beside Claude Code](../.claude/reference/side-by-side.svg)

**[Take the guided tour](https://sdamirsa.github.io/dot-claude-iff/)** ·
**[Open the demo console](https://sdamirsa.github.io/dot-claude-iff/demo/console.html)** (the
real instrument, loaded with this repo's real data) ·
**[Download from Releases](../../releases/latest)**

[**⭐ Star this repo**](../../stargazers) if the ideas are useful ·
[**🐛 Open an issue**](../../issues/new) if something is wrong or missing

---

## What it does

- **Records** every event into a local, append-only record - and phones home to no one.
- **Survives** interruptions: any session can be picked up cold, by anyone.
- **Shows** you everything on one live page: what is running, what needs you, what it costs,
  how the system is wired, and how the project evolved.
- **Asks** you properly: every question arrives with context and one concrete action, and your
  answer travels back with the context attached.
- **Closes** each work block with a single command, then **improves itself** only on evidence
  you approve.
- **Installs** into any repo - new or existing - and merges, never overwrites.

Runs on `bash`, `python3` and `git`. Installs nothing, imports nothing, sends nothing.

## The six decisions it is built on

**1 · Communication carries context, both ways.**
The agent cannot open a question for you without stating, in plain language, what is going on
and what to do - the CLI refuses otherwise. The console renders each question with a **copy
context** button: you paste, type your answer after `My answer:`, and the agent receives the
full situation with your reply. A two-word answer lands with nothing lost.

**2 · Memory is managed, not accumulated.**
An append-only journal is the single source of truth; everything readable (status, handoff,
boards) is a projection rebuilt from it. Decisions land in a log, mistakes become lessons with
mechanical prevention rules, and a resume pointer is written to disk *before* risky work - the
one thing a crash cannot take from you.

**3 · Progress beats like a pulse.**
A heartbeat marks every turn; hooks capture every event; token usage is counted per model (and
priced, or honestly marked "in plan" on a subscription). The record's raw tier keeps verbatim
history forever in a sibling folder **outside the repo** - so `git push` structurally cannot
publish a prompt, a file body, or a secret, and a gate fails the build if a committed file so
much as names your home directory.

**4 · One command stops, publishes, learns, and evolves.**
`/project-memory` runs four phases under one transaction: **check** reality against the record,
**polish** memory and rebuild every derived surface, **publish** (seal, commit, ask before
pushing), **evolve** - propose at most three evidence-backed changes, which land only with your
approval. Every generator in the system runs inside this ritual and nowhere else, so nothing
can silently rot. Removal is evolution too: unused tools get proposed for demotion or archival.

**5 · The system explains itself.**
Every component - agent, tool, hook, store, human - has a card with curated relations, compiled
into a clickable map (click to trace connections, click again for details). An anatomist agent
keeps the cards true; a lint fails the ritual on a wrong edge. The evolution story renders on a
dual clock: wall time, or cumulative tokens.

**6 · Adoption is a merge, never a takeover.**
`dot-claude-iff-fresh.zip` seeds a new repo; `dot-claude-iff-adopt-kit.zip` installs into an
existing one - a pre-existing `.claude/` is the designed case, every conflict is reported and
your version wins by default. Both zips are rebuilt by the ritual itself, so a release can
never lag the system.

## See it

| | |
|---|---|
| **[Demo console](https://sdamirsa.github.io/dot-claude-iff/demo/console.html)** | The actual console page, snapshotted from this repo: the live NOW panel, the 49-component map with focus mode, the story clock, the work board. |
| **[Guided tour](https://sdamirsa.github.io/dot-claude-iff/)** | Three steps - understand (with the honest privacy section), set up, then six small exercises with what-happens-underneath and where-to-look-on-the-console. |
| **[The manual](../.claude/README.md)** | The daily shape, the three laws, the five human collaboration habits, every command. |

## Trust posture, in three sentences

This system makes **zero network calls** - the one `urllib` site in the codebase is the opt-in
analysis engine, off by default, pointed only at an endpoint you configure (local Ollama keeps
even that on-machine). Claude Code itself talks to Anthropic exactly as it does without this
system; what this adds is a *local* record of what happened, and a mechanical guarantee that
nothing verbatim from that record can reach a commit. Don't trust it - grep it: the
[tour's privacy section](https://sdamirsa.github.io/dot-claude-iff/understand.html) hands you
the commands.

## Contribute back

**[⭐ Star](../../stargazers)** the repo to say the ideas earned it.
**[🐛 Open an issue](../../issues/new)** when something is wrong, unclear, or missing - and
hold it to this system's own communication contract: say what is going on, why it matters, and
the one concrete thing you are asking for. That is exactly how the agents here are required to
ask *you*.
