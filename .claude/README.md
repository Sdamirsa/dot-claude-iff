# dot-claude-iff · the Partner Guide

*The manual, and the learning path for collaborating with your agent.*

**[Repo](https://github.com/Sdamirsa/dot-claude-iff)** ·
**[Demo console](https://sdamirsa.github.io/dot-claude-iff/demo/console.html)** ·
**[Guided tour](https://sdamirsa.github.io/dot-claude-iff/)**

| You are | Start with |
|---|---|
| **A human partner** | [The daily shape](#the-daily-shape) (the half-screen habit), then [the five collaboration habits](#for-humans-how-to-collaborate-with-the-ai) - the whole learning path is ten minutes of reading and one session of practice. |
| **An agent** | [The three laws](#three-laws), the session ritual in `CLAUDE.md`, and [the commands](#commands). The protocols in `.claude/protocols/` are your contracts. |

---

A generalizable `.claude/` operating system for any project.

**One place to interact** (Claude Code) · **one place to control** (the console) · **one command
to evolve** (`/project-memory`) - on an always-on record, behind fail-closed gates.

Interact is human to system, control is system to human, evolve is system to itself. Three
surfaces, one place each. Observability and governance are the substrate underneath: the record
is what makes control truthful, the gates are what make interaction safe.

Everything lives in `.claude/`. A thing lives elsewhere **if and only if** it must, which is
what the name means.

## What is new in 0.3

Each item with the one command to try it.

- **Modes**: how organised the work is - freestyle (the default: record, gates and ritual
  only), guided-solo (a phase contract at session start), fableous-orchestrated (a lead
  dispatching builders). `python3 .claude/tools/statectl.py mode guided-solo`
- **Phases**: plan → build → review → deploy; leaving a phase runs its exit check.
  `python3 .claude/tools/checkctl.py phase-exit --from plan`, then
  `python3 .claude/tools/statectl.py phase build`
- **The ritual is yours alone**: only your own prompt opens `/project-memory`, and it ends with
  ROUTE (next phase, next mode, maturation pass). If the prompt hook never fires on your
  install, run `python3 .claude/tools/checkctl.py ticket --grant` in your own terminal.
- **Proposals**: ideas parked instead of built. `python3 .claude/tools/statectl.py proposal add
  "idea" --source human`
- **Fableous orchestration**: the lead cuts a worktree per builder and merges what passes.
- **Worktree sessions (Cview-ready)**: a session started inside a linked worktree
  (`.claude/worktrees/<task>/`) is a worker: SessionStart prints a WORKTREE SESSION block,
  main's state is refused to it, the record stays the project's one record, and `CLAUDE.md`
  tells it what to write (`DONE.md` / `BLOCKED.md`). Nothing depends on Cview.
  `python3 .claude/tools/statectl.py dispatch <task>`, `statectl.py accept <task>`, and each
  builder's envelope checked by `python3 .claude/tools/checkctl.py handoff <task> --run`.
- **Progress view**: one model for the console panel and the terminal.
  `python3 .claude/tools/statectl.py progress`
- **Code graph and architecture view**: `graphctl.py` builds the code graph from graphify's
  output (an extraction you run in your own terminal; `build` prints the command) or, without
  it, a stdlib walker; `mapctl.py architecture` renders the system-map cards as a diagram
  spec; the console's MAP tab shows one architecture diagram per scope, with a scope switcher.
  The ritual's POLISH rebuilds both. `python3 .claude/tools/graphctl.py build`. In the home
  repo, `mapctl.py anatomy` also renders the docs site's interactive anatomy page
  (`docs/anatomy.html`: layers, then one component, then its file), a home-only generator.
- **Secrets check and doctor**: can this machine run the system, and are keys where they
  belong (`reference/secrets.md`)? `python3 .claude/tools/checkctl.py doctor`. Its
  `gitattributes` row checks that the hooks are pinned to LF; its `smart_app_control` row
  catches Windows 11 blocking a uv-managed `python3` and names the remedy.
- **Stop the console**: ends the console server a session start launched, so the repo folder
  can be moved or deleted (on Windows a running console holds it).
  `python3 .claude/tools/consolectl.py stop`
- **Visibility and export**: keep the system tracked or ignored in git, and copy it into a
  public checkout without this project's history. `python3 .claude/tools/distctl.py gitignore`,
  `python3 .claude/tools/distctl.py export --to <checkout> --dry-run`
  (`reference/public-private.md`). In the source repo, `distctl.py verify` says whether the
  committed zips equal a rebuild.
- **Folder context**: which guides and rules load where, and which are stale or noisy.
  `python3 .claude/tools/mapctl.py context`
- **`/adhd`**: a divergent brainstorm you invoke yourself; the agent may suggest it in one line,
  never run it. Type `/adhd <question>`.

**The policy gate is a tripwire for honest mistakes, not a sandbox.** It reads every Write,
Edit, Bash and PowerShell call and refuses writes into the record, the ritual ticket and (for
sub-agents) the protected tree. Reading shell statically cannot stop a determined adversary:
a script written first and then run, a payload decoded at run time, a program missing from the
gate's writer tables, a path assembled at run time inside interpreter code, an archive or patch
applied in the main session's own root, and a renamed copy of the prompt hook all pass. Review
of the diff, the git denylist and your own approval are the other layers.

## Install it into a project

Three ways in, one adopt skill behind them (the zips: [Get it into your own
project](#get-it-into-your-own-project)):

- **A new or empty repo**: unzip `dot-claude-iff-fresh.zip` into its root, open Claude Code
  there, trust the hooks, and type the `/adopt` prompt from the unzipped `START-HERE.md`
  yourself (typing it is what lets the system's ritual run).
- **An existing repo**: unzip `dot-claude-iff-adopt-kit.zip` anywhere outside it, open Claude
  Code in your repo, and paste the instruction from the kit's `ADOPT.md`.
- **From a clone**: clone this repo to a folder outside yours and have the agent follow the
  clone's `.claude/skills/adopt/SKILL.md` end to end.

Adoption inventories the target, asks a blocking round of framing questions, installs with
merge-never-overwrite, fills the placeholders, seeds the map with spine layers only, dispatches the anatomist to fill in relations, builds the console, and
**probes that hooks actually fire** (project hooks require your trust first, so "silently off"
is a real state worth checking).

Requirements: `bash`, `python3` and `git`. That is the whole floor for the hooks and every
tool in `.claude/tools/`, forever. Project-registered steps and optional features may use `uv`.
On Windows, use Git Bash and make `python3` a python.org interpreter: Windows 11 Smart App
Control blocks uv-managed Python builds, and venvs built on them, as unsigned
([astral-sh/uv#22869](https://github.com/astral-sh/uv/issues/22869)), and `python3` can also be
the Microsoft Store stub. `python3 .claude/tools/checkctl.py doctor`
reports both, in its `smart_app_control` and `python3` rows.

## The daily shape

![The console beside Claude Code: half the screen watches, half works](reference/side-by-side.svg)

Works the same whether Claude Code runs in a terminal, in VS Code, or in the desktop app: give
the console one half of the screen and Claude Code the other.

The console is one HTML file with four tabs. It reads; it never writes. Where an action makes
sense, it hands you the exact command to paste, because a control surface that mutates state
behind your back is a second source of truth.

| Tab | What it answers |
|-----|-----------------|
| **NOW** | How far the current milestone is (the Progress panel, same model as `statectl.py progress`), is anything running, what is the resume pointer, what needs a human, what did the last turns do, what have we spent |
| **MAP** | What this system is made of, how the pieces relate, which layer each belongs to, and the architecture view per scope (the system from its cards, code scopes from the code graph) |
| **STORY** | How the project evolved, on a dual clock (wall time, or cumulative output tokens) |
| **WORK** | Active tasks and their next actions, the decision log, active watch-outs, research index |

Every panel says how fresh it is: `live` (heartbeat, journal, in-flight agents, queue) versus
`as of the last ritual` (map, story, curated summaries). The static file works offline over
`file://` as a labelled snapshot; `console.py` adds live polling on 127.0.0.1 only.

## Stable work blocks

The unit of collaboration here is a stable work block: a stretch of work that can survive an
interruption and be picked up cold, by anyone, human or AI. The system supplies the agent's
half of that harness: a pointer written to disk before anything risky, a journal that is the
source of truth, task checkpoints that name their done-evidence, handshake envelopes between
agents, and one ritual that seals each block. The console is how you watch a block without
interrupting it.

Communication is contractual in both directions. When the agent needs you, it must bring the
full situation: every needs-human item carries what is going on, why it matters, and the one
concrete thing to do, and the CLI refuses to open an item without them. The same bar applies
to you. The quality of the context you give is the quality of the block you get back.

## For humans: how to collaborate with the AI

Five habits. Each is short because the system does the heavy lifting; your part is precision.

**1 · Brief like a colleague, not a ticket.** Give the goal, the constraints, and what done looks
like. The difference:

```
Weak:   fix the login bug
Strong: Login fails for emails containing "+". Reproduce with
        tests/test_auth.py::test_plus_sign (currently failing). Do not touch
        the session middleware. Done means that test passes and the rest of
        the auth suite stays green.
```

**2 · Answer through the console.** When the queue has an item for you, press its "copy context"
button, paste into the Claude Code terminal, and type after `My answer:`. The agent receives
the full situation with your reply, so a two-word answer lands with nothing lost:

```
Answering NH-... (decide, SEV1): Pick the cache layer
Context: The deploy needs a cache decision; the wrong default doubles memory.
You asked me to: reply with redis or in-process
My answer: redis, we already run it in prod
```

**3 · Debug with evidence, not paraphrase.** Paste the actual output, the exact error, the failing
command. Say what you expected and what you saw. "It's broken" costs a round trip; a pasted
traceback usually costs none.

**4 · Correct once, then make it stick.** If you correct the same thing twice, say "log this as a
lesson". It becomes a row in `LESSONS.jsonl` with a mechanical prevention rule, and the agent
will cite it back before repeating the mistake. That is the system learning; feed it.

**5 · Close your blocks.** Type `/project-memory` at the end of a session, and answer gates when
asked; silence is not approval. Only you can open the ritual (the agent suggests it, and
`checkctl` refuses a run your own prompt did not open). An unclosed block is the one thing this
system cannot protect.

## Get it into your own project

Grab a zip from the repo's **GitHub Releases** (built by CI from a tested tree), or from
`.claude/dist/` in a checkout of `main` or a release tag - the same generator builds both, and
there they are guaranteed equal to a rebuild (on `dev` they may lag between releases):

- **`dot-claude-iff-fresh.zip`**: for a NEW or empty repo. Unzip into the repo root, open
  Claude Code there, trust the hooks when asked, and follow the unzipped `START-HERE.md`.
- **`dot-claude-iff-adopt-kit.zip`**: for an EXISTING repo, with or without its own `.claude/`.
  Unzip anywhere OUTSIDE your repo, open Claude Code in your repo, and paste the one-line
  instruction from the kit's `ADOPT.md`. The agent follows the adopt skill; your existing
  files are merged, never overwritten.

## The one command

`/project-memory` runs four phases, then ROUTE, and is the only place derived surfaces are
rebuilt. You type
it; the agent cannot. Your prompt mints a short-lived ritual ticket (the prompt hook writes
`.claude/state/ritual-ticket.json`, which the gate denies to every agent), and `checkctl`
refuses to open or complete a ritual without one. A tripwire, not cryptography.

- **CHECK** - does reality match the record? Journal parses, heartbeat present, generators
  fresh, cards lint, knobs registered, prices present, record size, queue synced, task
  checkpoints verified against disk. A failure stops the ritual before anything is published.
- **POLISH** - curate memory (log, lessons, status, tasks), then run *every* generator.
- **PUBLISH** - ingest token metadata, seal the record through the allowlist, roll up, anchor,
  commit, and push per policy (default: ask once).
- **EVOLVE** - a session digest goes to the retro-analyst, which proposes at most three
  evidence-backed changes; you confirm; the anatomist implements and updates the cards.
- **ROUTE** - one question set the next session starts from: stay in the lifecycle phase or
  advance (the exit check runs; review asks for your sign-off), keep or switch the mode, and
  whether to run a maturation pass now: full anatomy audit, a pruning sweep against actual
  usage, proposals turned into task cards for a plan phase, and a decision list for you.

Why one command: in the system this was distilled from, the single regenerator that nobody
wired into the ritual sat frozen for two and a half months while everything downstream quietly
served stale data. So there is exactly one ritual, every generator is registered in
`.claude/config/memory.json`, and a lint checks they ran.

## Three laws

1. **Anti-rot.** A generator not registered in the ritual will rot. Freshness is decided by
   content hash, never mtime (git does not preserve mtimes; an mtime rule fires at random on
   every clone).
2. **Gates fail closed, telemetry fails open.** A broken validator blocks the write. A broken
   capture hook loses one event and nothing else. Both directions are deliberate.
3. **The record is radioactive.** Raw capture contains prompts, file contents and tool output
   verbatim. It never enters git. It lives in a sibling folder outside the repo; only
   allowlisted metadata rollups and a tamper-evidence anchor are committed.

## Where things are

```
.claude/            the system: memory spine, protocols, skills, agents, hooks, config,
                    state, system-map, console, tools
.claude-iff/        committed record surface: anchor + redacted daily rollups (write-denied)
<parent>/<repo>_claude_iff/
                    RECORD_ROOT: raw capture, sealed raw (kept forever), segments,
                    transcripts, analysis products
```

The record is a sibling folder rather than a hidden state directory so it stays visible and
inspectable next to the project it belongs to. Override it with `record_root` in
`.claude/config/policy.json`.

## Commands

```
python3 .claude/tools/statectl.py   start|pointer|task|milestone|decision|loop|note|intent|gate
                                    need|tooling|device|refresh|resume|status
                                    mode|phase|proposal|progress
                                    dispatch|accept   # the lead only; refused while .cview/ exists
python3 .claude/tools/checkctl.py   run --phase check|polish|publish (inside the ritual) · status
                                    probe · generators · doctor
                                    phase-exit --from plan|build|review|deploy
                                    handoff <task_id> [--run] [--root <worktree>]
                                    ticket --grant   # you, in your own terminal; never an agent
python3 .claude/tools/distctl.py    export --to <checkout> [--dry-run] · gitignore · verify
python3 .claude/tools/mapctl.py     scan|lint|compile|show|context|architecture|anatomy
python3 .claude/tools/graphctl.py   build|update|status   # code graph; extraction is yours
python3 .claude/tools/obsctl.py     ingest|seal|rollup|anchor|report|story|size|analyze
python3 .claude/tools/consolectl.py build|payload|serve|stop   # stop frees the folder (Windows)
python3 .claude/tools/sysmon.py                       # CPU, RAM, GPU snapshot (optional)
python3 .claude/console/console.py                    # serve the console on loopback; prints the URL
python3 .claude/tools/tests/run_tests.py              # the whole suite
```

`statectl dispatch` and `accept` refuse when `.cview/` exists at the repo root: Cview owns the
worktrees there.

The habit that matters most: **set the pointer before anything long or risky.**

```
python3 .claude/tools/statectl.py pointer "<the very next concrete action>"
```

A usage-limit cutoff or a crash mid-turn never fires the Stop hook, so the heartbeat cannot
save you. What survives is the sentence you wrote to disk before you started.

## Reading the record

Read it freely; it is plain JSONL. To analyse it, use the one sanctioned pathway, which batches
raw events to a separate model and writes labelled products into `analysis/`:

```
python3 .claude/tools/obsctl.py analyze --dry-run
```

Products reach the repo only by a human-gated copy into `.claude/research/`.

## Extending it

Don't, until the evidence says so. `.claude/protocols/evolution.md` sets the bar: at least two
occurrences or one concrete failure, at most three proposals per session, and the smallest
intervention that holds the fix (a CLAUDE.md line beats a rule beats a skill beats a
sub-agent). `NO-CHANGES` is a good outcome. Evolution also removes: unused components are
proposed for demotion or archival on the same evidence, never deleted.
