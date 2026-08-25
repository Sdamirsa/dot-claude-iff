<!-- last-reviewed: 2026-08-25 -->
# dot-claude-iff - system design (v3, FINAL for Phase 2)

**Status: decisions settled 2026-08-25. This version is the implementation contract for Phase 2.**
v1 → Opus adversarial review (verified against 14,005 real captured events) → v2 → maintainer decisions → **v3**.

## 0. Thesis

> **One place to interact** (Claude Code) · **one place to control** (the console) · **one command to evolve** (`/project-memory`) - running on an always-on record (observe) behind fail-closed gates (govern).

Interact is human→system; control is system→human; evolve is system→itself. Observability and governance are substrate, not surfaces: the record makes control truthful, the gates make interaction safe.

Name: **iff** - *everything lives in `.claude/`; a thing lives elsewhere if and only if it must.*

Sources: two local systems under `temp-to-analyse/` - a simplified adaptive `.claude` template (the memory spine) and a long-run multi-agent crawler, private, post its 2026-08 maturation (the runtime spine). Both gitignored, never published.

---

## 1. Settled decisions (2026-08-25)

| # | Decision | Settled as |
|---|---|---|
| 1 | Record location | **Sibling folder**: `<repo-parent>/<repo-name>_claude_iff/` (RECORD_ROOT), auto-created, overridable via `record.root`. Out-of-repo, out-of-git, structurally un-pushable. `/adopt` warns if the path looks cloud-synced. |
| 2 | Redaction & retention | Allowlist redaction at seal (unchanged). **Raw is kept FOREVER by default** - no auto-expiry (expiry exists only as an off-by-default knob). Raw = agent ground truth: out-of-repo, agent-write-denied, never archivable by others. Sealed raw gzip-compacted; CHECK reports total record size loudly. The agent's ONLY capability over raw is **analysis via a separate model call** (`obsctl analyze` → Ollama/OpenRouter/Claude API); analysis products stay in RECORD_ROOT/analysis/ and are promoted into `.claude/research/` only by human-gated copy. |
| 3 | Console tech | **Plain HTML** - single file, smart/interactive (tabs, popups, map, story scrubber), auto-updating via adaptive polling + stdlib `console.py`. NiceGUI documented as the named upgrade when the console must *execute* commands, not emit them. |
| 4 | Publish | **Commit always, push asks** once per ritual (`push: always|ask|never`, default `ask`). |
| 5 | Layers | **Spines-only at adoption** (MEMORY · GOVERN · OBSERVE · CONSOLE · EVOLVE); project components start in a visible "unplaced" band; flow layers are defined *through the project* via evolution. The anatomist must be designed to genuinely read, analyze, and understand relations - it is the map's intelligence, not a formatter. |
| 6 | Profiles | **Single profile: full.** No minimal/standard tiers. Instead, evolution has **two gears** (§6): **soft** (default, inside every ritual) and **hard** (dedicated maturation session). Evolution also **prunes**: unused tools/subagents/skills are detected from record usage stats and proposed for demotion or archival - user-gated. |
| 7 | Runtime | **Two-level rule**: vital/low-level (all hooks + the six core tools) = bash + python3 **stdlib only**, forever. Everything else - project-registered steps, optional features like `obsctl analyze` - runs under **uv** (PEP 723 inline deps) as the first-class extension tier. |
| 8 | Handshakes | **JSON envelope default**: `{agent_id, task_id, status, artifacts[], notes}` in `state/handshakes/` (stub at dispatch → envelope at delivery), validated on write; markdown prose lives in `notes`; the Task Brief stays markdown. |

Three laws (unchanged from v2): **(1) anti-rot** - every generator registered by name and run only through the ritual, freshness linted by content hash; **(2) gates fail closed, telemetry fails open, always**; **(3) the record is radioactive** - verbatim content never enters git; in-repo surfaces carry metadata through an allowlist; append-only is tamper-evident, not tamper-proof.

---

## 2. Where things live

```
.claude/                          # THE SYSTEM
  CLAUDE.md                       # project contract, ≤80 lines; opens with the thesis triad
  README.md                       # manual: console serve one-liner, half-screen setup, upgrade path
  settings.json                   # hook wiring ($CLAUDE_PROJECT_DIR-portable)
  STATUS.md                       # 15-25 lines, rewritten each ritual
  Project-log.jsonl               # append-only decision/deliverable/milestone/mistake/tooling log
  LESSONS.jsonl                   # mistakes → mechanical prevention rules; rendered in console WORK
  protocols/                      # handshake · human-gates · honesty · evolution (incl. gears + pruning)
  rules/  tasks/  research/  reference/
  reference/glossary.md           # controlled vocabulary (system terms seeded; project terms grow)
  reference/environment.md       # auto-derived: models, effort, platform (from capture metadata)
  skills/  project-memory/  plan-task/  adopt/          # exactly three commands
  agents/  anatomist.md [Sonnet] · retro-analyst.md [Sonnet] · verifier.md [inherit]
  hooks/   session-start · heartbeat · obs-capture · policy-gate · post-write-validate   # stdlib tier
  config/                         # ⚠ protected tree - main session only
    memory.json                   # ritual registry (names, never shell) · push policy · data_series
    policy.json                   # protected paths · per-agent grants · bash deny patterns
    model-prices.json             # maintainer-verified only; empty ⇒ loud CHECK warning
    registry.json                 # every knob incl. model+effort pins · system_version
  state/
    journal.jsonl                 # append-only truth (one action vocabulary, shared writer/projector)
    session.json · HANDOFF.md     # derived
    heartbeat.json · needs-human.jsonl · handshakes/
    generators.json               # freshness by SHA-256 (never mtime)
    memory-run.json               # ritual transaction checkpoint
  system-map/  layers.json · cards/*.json · map.json (compiled)
  console/     console.template.html · console.html (built) · console.py (stdlib server)
  tools/       _lib.py · statectl · obsctl · mapctl · consolectl · checkctl    # stdlib tier

.claude-iff/                      # in-repo committed record surface - agent-write-denied
  README.md                       # the iff test; pointer to RECORD_ROOT
  obs/anchor.json                 # SHA-256 head of last seal (tamper evidence)
  obs/rollups/YYYY-MM-DD.json     # daily allowlisted totals only

<repo-parent>/<repo-name>_claude_iff/        # RECORD_ROOT - sibling folder, never in git
  spool/<session>.jsonl           # raw hook events (verbatim; O_APPEND)
  sealed-raw/YYYY-MM-DD.jsonl.gz  # raw events compacted after sealing - KEPT FOREVER (ground truth)
  segments/YYYY-MM-DD.jsonl       # allowlisted metadata segments (chmod 0444 on seal)
  raw/transcripts/                # verbatim transcript copies (ingest backstop)
  analysis/                       # obsctl-analyze products (model-labeled retrospectives)
  vault/                          # snapshots: git bundle + ~/.claude chats · disk guard · retention count
```

**The iff test**: `.claude-iff/` holds only what must be *committed but agent-untouchable*. Everything verbatim or bulky lives in the sibling RECORD_ROOT - visible, inspectable, backupable, and structurally un-pushable. Repo root stays clean (a root `.gitignore` is the one standard exception).

## 3. `/project-memory` - check · polish · publish · evolve

Main session only; steps run by registered NAME from `config/memory.json` (data never executes strings); transaction checkpoint in `state/memory-run.json`; generators write `.tmp` and rename atomically as a set; PUBLISH refuses without POLISH completion for the same run id; `--resume` continues a died run; advisory lock = one ritual writes projections, any session may append.

- **CHECK**: journal parses · heartbeat · generator freshness (content hashes) · cards lint (two-tier) · registry lint · price-table loud-if-empty · **record size report** · needs-human sync · task reality check ("reality wins") · project-registered checks (uv tier).
- **POLISH**: log entries (provenance-tagged) · LESSONS curation · STATUS rewrite · tasks closed, gates surfaced · **all generators**: map scan+compile, story, console, project-registered · CLAUDE.md iff conventions moved · registered QC/polish steps.
- **PUBLISH**: ingest (corrected token harness) · seal (allowlist) + gzip-compact raw · rollup · anchor · optional vault snapshot · commit always · push per policy (default ask).
- **EVOLVE**: soft gear by default, hard gear on demand - §6.

Ritual-skipping is nudged at next SessionStart ("last `/project-memory`: N sessions ago").

## 4. Observability

Lanes: hook capture (lean event set default; full breadth a flag; fails open) · heartbeat (Stop overwrite; pointer-before-risky-ops is the real resume guarantee) · transcript ingest at PUBLISH (read-only; metadata to segments; verbatim copies to raw/) · opt-in decorator lane (`_lib.obslog.append()`; produced 87% of the crawler's record - honest number).

**Token harness contract**: `rglob` over every `~/.claude/projects/` dir prefix-matching the slug (session files + `<session>/subagents/*` + worktree dirs - 502/515 files a naive glob misses); per-file byte cursors; per-message dedupe; cost over all four token classes + tier; per-class `unknown`, never guessed.

**The record as ground truth** (decision 2): raw kept forever, gzip-compacted at seal; write-denied to every identity (Write/Edit AND Bash pattern deny on RECORD_ROOT); tamper-evident via 0444 + committed anchor. The single sanctioned agent pathway over raw: **`obsctl analyze`** - batches raw events/transcripts to a configured model (Ollama / OpenRouter / Claude API; uv tier), labels against a small seeded taxonomy (errors · decisions · system evolution · data evolution), writes products to `RECORD_ROOT/analysis/`; promotion into the repo is a human-gated copy. The console STORY tab and the retro-analyst may read analysis products, never raw.

**Story**: dual clock (wall / cumulative output tokens), three lanes (operations & errors · anatomy evolution · data evolution via registered `data_series`); one schema, producer+renderer contract-tested.

## 5. System map

Net-new build (the crawler proved the pattern, not the pipeline). Card schema as v2 (`id · kind[agent|code|data|human|external] · layer · title · path · description · reads · writes · invokes · flows · glyphs · auto{discovered,hash}`).

Pipeline: `mapctl scan` (auto identity + static I/O seeding) → **anatomist enrichment** → `mapctl lint` (ERROR: missing card/dead path/dangling edge → fails CHECK; WARN: empty relations/stale description → reported) → `compile` → console MAP (kind→color: orange agent · blue code · green data · gold human · gray external; six edge styles; backbone + flows views; measured wire routing + overlap assertion).

**Anatomist mandate** (decision 5): not a formatter - it reads component sources, traces who invokes whom and who touches what, proposes placement and relations with cited evidence, surfaces anything it cannot classify as a design smell, and implements approved evolution changes with the right primitive (CLAUDE.md line < rule < skill < subagent). Dispatched unconditionally at `/adopt`, on any component change, and in every hard-gear session. Its agent definition is a Phase 2 first-class deliverable with its own pass-tests.

Layers: spines shipped populated; project components start in a visible **unplaced band**; flow layers are added through the project via evolution.

## 6. Evolution - two gears + pruning

**Soft gear (default - runs inside every ritual's EVOLVE):** session digest → retro-analyst (Sonnet, propose-only) → main-agent filter → BLOCKING user gate → implement → anatomist reconciles cards. Bar: ≤3 proposals, each ≥2 occurrences or 1 concrete failure. Proposals may be **additions or demotions**.

**Hard gear (`/project-memory --hard` - a dedicated maturation session):** for deep refinement of the `.claude` system or the project's work-package pipeline. Procedure: full anatomist audit (folder health per the claude-folder-guide thresholds, map reconciliation, drift review) + **usage-evidence pruning sweep** + plan/spec-style task cards (description · to-do · satisfaction · pass-test - the crawler's maturation format) + decision list for the maintainer → approved cards implemented, each landing with a `checkctl` probe. Hard gear is how "a session focused on improving the system" becomes a governed procedure instead of an ad-hoc rebuild.

**Pruning (both gears):** the record knows what actually runs - skill invocations, agent dispatches, tool executions. Components with zero/low use across N sessions become pruning candidates: **demote** down the ladder (subagent → skill → rule → CLAUDE.md line) or **archive** (never delete). Always user-gated; every change logged as `tooling`.

## 7. Governance & protocols

Protected (main-session-only): `.claude/hooks/`, `.claude/config/`, `.claude/agents/`, `.claude/protocols/`, `settings*`, `.claude-iff/`, RECORD_ROOT (Write/Edit + Bash pattern). `post-write-validate` parse-checks every JSON/JSONL write under `.claude/` (fails closed). Subagents: `deny_bash: ["git"]`; write grants per agent in `policy.json`.

Carried intact: two-stage evidence filter · NO-CHANGES-is-success · reality-wins resume · NEEDS-HUMAN async rule (+ id/date per row) · provenance tags · `user-confirmed-over-pushback` · one archive convention · intervention-size ordering · propose-only agents by tool restriction. Handshakes per decision 8. Needs-human queue: SEV0-3 bands, queue-as-view.

Dropped (with reasons - unchanged from v2): per-event hash chains + DuckDB · OTel lane · AQL sampling · 14-touchpoint HITL · worktree merge gate · multi-page viz · PRISMA machinery (pattern kept) · domain eval harness (pattern documented) · full 11-event capture default. **Un-dropped vs v2:** raw retention (now keep-forever), profiles (now full-only).

## 8. Adoption

`/adopt`: inventory → BLOCKING frame confirmation (mission, invariants, git tracking, collision policy: merge-never-overwrite + report; cloud-sync warning for RECORD_ROOT) → install **full** → fill placeholders (grep-verified) → spines-only layers → scan seeds cards → anatomist dispatched unconditionally → build console → **probe hooks actually fire** (Stop-event sentinel) → first heartbeat → Structured Return. `/adopt --upgrade`: diff shipped assets vs local, report drift, apply non-conflicting; CHECK warns when `system_version` is behind.

Model routing (all pins in `config/registry.json`, linted): main session = maintainer's choice (Fable/Opus for complex work) · retro-analyst + anatomist = Sonnet · verifier = inherit · heavy-reasoning future agents = Opus.

## 9. Phase 2 build order

0. Hooks-fire smoke test · `_lib.py` (two-tier atomic I/O, obslog) · fixture repo · unit-test scaffold.
1. Memory spine + protocols (incl. gears/pruning in evolution.md) + three skills (skeleton ritual w/ transaction checkpoint).
2. Policy gate + post-write-validate (full protected set, RECORD_ROOT bash deny).
3. State & continuity: statectl, journal vocabulary registry, projections, heartbeat, session-start.
4. Observability: obsctl spool→seal(allowlist)→gzip-compact→rollup→anchor · corrected token harness · story feed + contract test · size report.
5. Console: payload fn · template · hardened server (console-dir-only, Host check) · NOW + WORK tabs.
6. Cards: ~30 hand-written system cards → compile + render (MAP tab) → scan automation + two-tier lint. Anatomist agent definition + pass-tests.
7. STORY tab · glossary · environment note · `obsctl analyze` (uv tier) · `/adopt` + `--upgrade`.
8. Hard-gear procedure · polish pass · rename sweep last · `checkctl` probe ledger complete · **this repo self-hosts**.

Every step lands with a pass-test; `checkctl` accumulates one probe per component (the crawler's `t_*` ledger pattern).
