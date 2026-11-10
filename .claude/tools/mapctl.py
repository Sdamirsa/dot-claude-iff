#!/usr/bin/env python3
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import _lib

__doc__ = """mapctl.py - the system map: card discovery, two-tier lint, and compile for
.claude/system-map/.

One JSON card per component lives in .claude/system-map/cards/<id>.json. `scan` auto-discovers
components on disk (agents, skills, hooks, tools, protocols, config, a small table of known
state stores, the human) and creates/refreshes stub cards; it NEVER guesses a `layer` or a
curated relation - those are the anatomist's job, done with evidence. `lint` is the week-one
signal: ERROR findings fail CHECK (a wrong or missing card is worse than an honest gap), WARN
findings only inform. `compile` folds every card into map.json, write-gated so an unchanged
result never touches the file (and never shows up in a git diff). `show` prints a human summary.

Guides (CLAUDE.md files) and rules (.claude/rules/) are not cards: ctxmap.py derives them into
map.json's `context` section at compile, so a project can grow folder guides without writing a
card per guide, and lint never asks for one. `context` asks what loads for a file.

    mapctl.py scan       # discover components, create/refresh stub cards
    mapctl.py lint       # two-tier validation; exit 1 on any ERROR
    mapctl.py compile    # write system-map/map.json (write-gated)
    mapctl.py architecture   # write system-map/architecture.json + docs/ARCHITECTURE.md
    mapctl.py anatomy [--out docs/anatomy.html]   # the interactive anatomy page (docs site)
    mapctl.py show [--id ID]
    mapctl.py context [<path>] [--json] | --suggest   # folder context (ctxmap.py)
"""

import argparse
import ast
import json
import os
import re
from pathlib import Path

import ctxmap

# --------------------------------------------------------------------------- known, declared components
#
# These are not discovered by globbing a directory: they are architectural facts about this
# system, declared once here. `auto.hash` is always "" for them (there is no single source
# file whose drift would make a description stale - `store.record` is a whole external tree,
# `store.cards` is the very directory this tool writes into, and hashing it would make compile
# rewrite on every run). `auto.lazy: true` exempts them from the ghost/path-exists ERROR: a
# freshly cloned or freshly adopted project legitimately has none of these files yet: the
# store is real, its backing file is just not written until the component that owns it runs.

KNOWN_STORES = [
    {"id": "store.journal", "path": ".claude/state/journal.jsonl", "title": "Journal",
     "description": "Append-only truth: one action vocabulary, shared by every writer and the projector.",
     "glyphs": ["append-only"]},
    {"id": "store.session", "path": ".claude/state/session.json", "title": "Session snapshot",
     "description": "Derived point-in-time session snapshot, projected from the journal.",
     "glyphs": ["derived"]},
    {"id": "store.handoff", "path": ".claude/state/HANDOFF.md", "title": "Handoff note",
     "description": "Derived human-readable resume note, projected from the journal.",
     "glyphs": ["derived"]},
    {"id": "store.machine", "path": ".claude/state/machine.json", "title": "Machine identity",
     "description": "Which machine this repo last ran on: a salted user|host fingerprint plus a "
                     "human-chosen alias - never a username or hostname. statectl device is the "
                     "only writer; the session-start hook compares read-only and says so loudly "
                     "when the repo wakes somewhere new.",
     "glyphs": []},
    {"id": "store.needs_human", "path": ".claude/state/needs-human.jsonl", "title": "Needs-human queue",
     "description": "Async needs-human queue: SEV0-3 bands, queue-as-view. Bundles the append-only "
                     "source (needs-human.jsonl) and its derived projection (needs-human.json).",
     "glyphs": ["append-only", "derived"]},
    {"id": "store.proposals", "path": ".claude/state/proposals.jsonl", "title": "Proposal box",
     "description": "Ideas out of scope right now (statectl proposal add/list/resolve, ids PR-<n>): "
                     "append-only, its own store like needs-human so a parked idea outlives any "
                     "session. Open count lands in session.json; listed on the console WORK tab.",
     "glyphs": ["append-only"]},
    {"id": "store.project_log", "path": ".claude/Project-log.jsonl", "title": "Project log",
     "description": "Append-only decision/deliverable/milestone/mistake/tooling log.",
     "glyphs": ["append-only"]},
    {"id": "store.lessons", "path": ".claude/LESSONS.jsonl", "title": "Lessons",
     "description": "Mistakes distilled into mechanical prevention rules, rendered in console WORK.",
     "glyphs": ["append-only"]},
    {"id": "store.cards", "path": ".claude/system-map/cards", "title": "Cards",
     "description": "One JSON card per component; the system map's own source of truth.",
     "glyphs": []},
    {"id": "store.map", "path": ".claude/system-map/map.json", "title": "System map",
     "description": "Compiled system map: nodes, edges, and lint results, rendered by the console MAP tab.",
     "glyphs": ["derived"]},
    {"id": "store.architecture", "path": ".claude/system-map/architecture.json",
     "title": "Architecture spec",
     "description": "The architecture diagram's spec, one scope per diagram (groups, boxes, arrows), "
                     "written by mapctl architecture with docs/ARCHITECTURE.md rendered from it.",
     "glyphs": ["derived"]},
    {"id": "store.code_graph", "path": ".claude/system-map/code-graph.json", "title": "Code graph",
     "description": "The normalised code graph (files, classes, functions; contains, imports, calls), "
                     "written by graphctl build from graphify's output under the record root or the "
                     "stdlib walker. Gitignored: machine-local, rebuilt by the ritual, never shipped.",
     "glyphs": ["derived"]},
    {"id": "store.story", "path": ".claude/state/story-feed.json", "title": "Story feed",
     "description": "Dual-clock narrative feed: operations & errors, anatomy evolution, data evolution.",
     "glyphs": ["derived"]},
    {"id": "store.rollups", "path": ".claude-iff/obs/rollups", "title": "Daily rollups",
     "description": "Daily allowlisted totals only, committed in-repo (the iff test).",
     "glyphs": ["read-only"]},
    {"id": "store.anchor", "path": ".claude-iff/obs/anchor.json", "title": "Seal anchor",
     "description": "SHA-256 chain over sealed segments; tamper evidence for the record.",
     "glyphs": []},
    {"id": "store.record", "path": None, "title": "Record root",
     "description": "RECORD_ROOT: raw capture, sealed raw, segments, transcripts, analysis, vault - "
                     "sibling folder, out of git.",
     "glyphs": ["read-only"], "external_to_repo": True},
    {"id": "store.heartbeat", "path": ".claude/state/heartbeat.json", "title": "Heartbeat",
     "description": "Liveness signal, overwritten once per turn by the Stop hook ('turn ended') and "
                     "mid-turn by the activity pulse ('working', via the policy gate and the "
                     "sub-agent capture lane, throttled by progress.pulse_seconds). Not the resume "
                     "guarantee - the journal pointer is.",
     "glyphs": []},
    {"id": "store.memory_run", "path": ".claude/state/memory-run.json", "title": "Ritual run checkpoint",
     "description": "The /project-memory transaction: run id, phase, step, and who opened it "
                     "(invoked_by: user-ticket, with the ticket's ts). PUBLISH refuses unless "
                     "POLISH completed in the same run id.",
     "glyphs": []},
    {"id": "store.ritual_ticket", "path": ".claude/state/ritual-ticket.json", "title": "Ritual ticket",
     "description": "Proof the USER typed /project-memory or /adopt: {skill, ts, session_id, event}. "
                     "Written by the prompt hook, or by `checkctl ticket --grant` that the human "
                     "runs in their own terminal (the gate denies the file and that subcommand to "
                     "every agent); checked by checkctl run and complete, consumed by complete. A "
                     "tripwire, not cryptography; gitignored.",
     "glyphs": []},
    {"id": "store.handshakes", "path": ".claude/state/handshakes", "title": "Handshake envelopes",
     "description": "Agent-to-agent Structured Return envelopes (stub at dispatch, envelope at delivery).",
     "glyphs": ["envelope"]},
    {"id": "store.orchestration", "path": ".claude/state/orchestration.json",
     "title": "Orchestration runtime",
     "description": "fableous-orchestrated runtime, per machine and gitignored: when each sub-agent "
                     "began (the builder stop check's 'since'), the lead's code edits since the "
                     "last dispatch (the delegation nudge), and when the periodic progress report "
                     "last went to the lead (both organised modes). Written by the hooks only.",
     "glyphs": []},
    {"id": "store.generators", "path": ".claude/state/generators.json", "title": "Generator ledger",
     "description": "Law-1 anti-rot ledger: content hash of each generator's inputs/output, stamped "
                     "after it runs through the ritual.",
     "glyphs": ["derived"]},
    {"id": "store.dist", "path": ".claude/dist", "title": "distribution zips",
     "description": "The two release artifacts: dot-claude-iff-fresh.zip (new repo) and "
                     "dot-claude-iff-adopt-kit.zip (existing repo). Derived: rebuilt by the "
                     "ritual and by the release step; equal to a rebuild on main and on every "
                     "tag, while between releases the copies on dev may lag. Never a builder's.",
     "glyphs": ["derived"]},
]

HUMAN = {"id": "human.maintainer", "path": None, "title": "Maintainer",
         "description": "The project maintainer: approves gates, reviews evolution proposals, "
                         "owns publish/push decisions."}

# Real, named, non-lazy components that do not fall under any glob rule below: the console
# server/template live in .claude/console/, not .claude/tools/; layers.json lives in
# .claude/system-map/, not .claude/config/. Ids match the ones checkctl.py's probe ledger
# already uses for these same paths (map.layers, console.server, console.template) - a peer
# tool established the convention first, so mapctl follows it rather than inventing a third.
SINGLETONS = [
    {"id": "map.layers", "rel": "system-map/layers.json", "kind": "data", "title": "Map Layers",
     "extract": "config"},
    {"id": "console.server", "rel": "console/console.py", "kind": "code", "title": "Console Server",
     "extract": "docstring"},
    {"id": "console.template", "rel": "console/console.template.html", "kind": "data",
     "title": "Console Template", "extract": "static",
     "description": "The console's single HTML shell (head, theme, styles, tabs) with one JSON "
                     "token replaced by consolectl's payload at build time."},
    {"id": "console.anatomy-template", "rel": "console/anatomy.template.html", "kind": "data",
     "title": "Anatomy Template", "extract": "static",
     "description": "The anatomy page's HTML shell (the docs site's three-level map: layers, one "
                     "component, its file) with one JSON token replaced by mapctl anatomy's "
                     "payload at build time."},
]

# Guides and rules are deliberately absent: they are derived context entries (ctxmap.py), so
# neither the missing-card ERROR nor the ghost check ever applies to them. A hand-written card
# for one is allowed and linted like any other card, but never required.
GLOB_ID_PREFIXES = ("agent.", "skill.", "hook.", "tool.", "protocol.", "config.")
SINGLETON_IDS = {s["id"] for s in SINGLETONS}


# --------------------------------------------------------------------------- text extraction

def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def first_sentence(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if not collapsed:
        return ""
    m = re.match(r"(.+?\.)(?:\s|$)", collapsed)
    return m.group(1).strip() if m else collapsed


def module_first_sentence(source_text: str) -> str:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return ""
    return first_sentence(ast.get_docstring(tree) or "")


def config_comment_description(obj) -> str:
    comment = obj.get("_comment") if isinstance(obj, dict) else None
    if isinstance(comment, list):
        joined = " ".join(str(x) for x in comment)
    elif isinstance(comment, str):
        joined = comment
    else:
        joined = ""
    return first_sentence(joined)


def parse_frontmatter(text: str) -> dict:
    """Minimal `key: value` frontmatter reader, tolerant of simple folded continuations.
    Not a YAML parser - the frontmatter this system ships is deliberately flat."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[key] = val
        elif key is not None:
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def hook_comment_description(text: str) -> str:
    lines = text.splitlines()
    i = 1 if lines and lines[0].startswith("#!") else 0
    parts = []
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("#"):
            parts.append(s.lstrip("#").strip())
            i += 1
            continue
        break
    return " ".join(p for p in parts if p).strip()


def protocol_title_and_description(text: str):
    lines = text.splitlines()
    heading, idx = "", -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            heading = re.sub(r"^#+\s*", "", s).strip()
            idx = i
            break
    body = "\n".join(lines[idx + 1:]) if idx >= 0 else text
    sentence = first_sentence(body)
    if not heading:
        return "", sentence
    if sentence and not sentence.startswith(heading):
        return heading, f"{heading}. {sentence}"
    return heading, heading


def titleize(stem: str) -> str:
    s = stem.lstrip("_").replace("_", " ").replace("-", " ").strip()
    return s.title() if s else stem


# --------------------------------------------------------------------------- static relation seeding
#
# Suggestions only, and scoped to tool.* cards per spec: a store id is suggested only when a
# quoted literal naming it appears on the SAME LINE as one of the _lib path-locator calls.
# Without that same-line gate, the KNOWN_STORES table's own path strings (which sit in THIS
# file's source as plain data) would suggest nearly every store for tool.mapctl itself.

_LOCATOR_FUNCS = ("state_dir()", "iff_dir()", "claude_dir()", "map_dir()", "console_dir()",
                   "record_paths()")

_LITERAL_TO_STORE = {
    "journal.jsonl": "store.journal",
    "session.json": "store.session",
    "HANDOFF.md": "store.handoff",
    "needs-human.jsonl": "store.needs_human",
    "needs-human.json": "store.needs_human",
    "proposals.jsonl": "store.proposals",
    "Project-log.jsonl": "store.project_log",
    "LESSONS.jsonl": "store.lessons",
    "map.json": "store.map",
    "code-graph.json": "store.code_graph",
    "story-feed.json": "store.story",
    "anchor.json": "store.anchor",
    "rollups": "store.rollups",
    "heartbeat.json": "store.heartbeat",
    "memory-run.json": "store.memory_run",
    "ritual-ticket.json": "store.ritual_ticket",
    "handshakes": "store.handshakes",
    "orchestration.json": "store.orchestration",
    "generators.json": "store.generators",
}


def suggest_relations(source_text: str):
    reads, writes = set(), set()
    for line in source_text.splitlines():
        if re.search(r"journal_append\s*\(", line):
            writes.add("store.journal")
        if re.search(r"\bjournal_(?:path|read)\s*\(", line):
            reads.add("store.journal")
        if re.search(r"record_paths\(\)\s*\[", line):
            reads.add("store.record")
            writes.add("store.record")
        if not any(fn in line for fn in _LOCATOR_FUNCS):
            continue
        for lit in re.findall(r'["\']([^"\']+)["\']', line):
            base = lit.rsplit("/", 1)[-1]
            sid = _LITERAL_TO_STORE.get(base) or _LITERAL_TO_STORE.get(lit)
            if sid:
                reads.add(sid)
                writes.add(sid)
    return sorted(reads), sorted(writes)


# --------------------------------------------------------------------------- paths / hashing

def cards_dir() -> Path:
    return _lib.map_dir() / "cards"


def map_json_path() -> Path:
    return _lib.map_dir() / "map.json"


def resolve_path(path_str):
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else _lib.project_root() / p


def compute_hash(path: Path) -> str:
    """Content hash of a real, discovered source file. Never called for a declared
    (store/human) card - see the KNOWN_STORES comment for why. Line endings are normalised
    first: a Windows checkout (autocrlf) holds CRLF where git and every other OS hold LF, and
    a raw-byte hash made every card read stale on the other OS."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _component(id_: str, kind: str, title: str, description: str, source: Path) -> dict:
    return {
        "id": id_, "kind": kind, "title": title, "description": description,
        "path": _lib.rel(source), "source": source, "hash": compute_hash(source),
        "glyphs": [],
    }


# --------------------------------------------------------------------------- discovery

def discover_agents() -> list:
    out = []
    d = _lib.claude_dir() / "agents"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        fm = parse_frontmatter(_safe_read(f))
        out.append(_component(f"agent.{f.stem}", "agent", fm.get("name", f.stem),
                               fm.get("description", ""), f))
    return out


def discover_skills() -> list:
    out = []
    d = _lib.claude_dir() / "skills"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*/SKILL.md")):
        fm = parse_frontmatter(_safe_read(f))
        stem = f.parent.name
        out.append(_component(f"skill.{stem}", "code", fm.get("name", stem),
                               fm.get("description", ""), f))
    return out


def discover_hooks() -> list:
    out = []
    d = _lib.claude_dir() / "hooks"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.sh")):
        out.append(_component(f"hook.{f.stem}", "code", titleize(f.stem),
                               hook_comment_description(_safe_read(f)), f))
    return out


def discover_tools() -> list:
    out = []
    d = _lib.tools_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.py")):
        text = _safe_read(f)
        comp = _component(f"tool.{f.stem}", "code", titleize(f.stem), module_first_sentence(text), f)
        reads, writes = suggest_relations(text)
        comp["suggested_reads"], comp["suggested_writes"] = reads, writes
        out.append(comp)
    return out


def discover_protocols() -> list:
    out = []
    d = _lib.claude_dir() / "protocols"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        title, desc = protocol_title_and_description(_safe_read(f))
        out.append(_component(f"protocol.{f.stem}", "data", title or titleize(f.stem), desc, f))
    return out


def discover_configs() -> list:
    out = []
    d = _lib.config_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        obj = _lib.read_json(f, {})
        out.append(_component(f"config.{f.stem}", "data", titleize(f.stem),
                               config_comment_description(obj), f))
    return out


def discover_singletons() -> list:
    out = []
    cd = _lib.claude_dir()
    for s in SINGLETONS:
        f = cd / s["rel"]
        if not f.is_file():
            continue
        if s["extract"] == "docstring":
            desc = module_first_sentence(_safe_read(f))
        elif s["extract"] == "config":
            desc = config_comment_description(_lib.read_json(f, {}))
        else:
            desc = s.get("description", "")
        out.append(_component(s["id"], s["kind"], s["title"], desc, f))
    return out


def discover_stores() -> list:
    out = []
    for s in KNOWN_STORES:
        out.append({
            "id": s["id"], "kind": "data", "title": s["title"], "description": s["description"],
            "path": s["path"], "source": None, "hash": "", "glyphs": list(s["glyphs"]),
            "lazy": True, "external_to_repo": bool(s.get("external_to_repo")),
        })
    return out


def discover_human() -> list:
    return [{
        "id": HUMAN["id"], "kind": "human", "title": HUMAN["title"], "description": HUMAN["description"],
        "path": HUMAN["path"], "source": None, "hash": "", "glyphs": [], "lazy": True,
    }]


def discover_globbed() -> list:
    """Everything found by walking the filesystem - agents/skills/hooks/tools/protocols/config
    plus the console/layers singletons. Excludes the declared (store/human) components, which
    are never "missing a card" in the glob sense: they are declared, not discovered."""
    out = []
    out += discover_agents()
    out += discover_skills()
    out += discover_hooks()
    out += discover_tools()
    out += discover_protocols()
    out += discover_configs()
    out += discover_singletons()
    return out


def discover_all() -> list:
    return discover_globbed() + discover_stores() + discover_human()


# --------------------------------------------------------------------------- card io

def load_card_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {_lib.rel(path)}: {exc}"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"malformed card JSON in {_lib.rel(path)}: {exc}"
    if not isinstance(obj, dict) or "id" not in obj:
        return None, f"malformed card JSON in {_lib.rel(path)}: missing 'id'"
    return obj, None


def load_all_cards():
    d = cards_dir()
    cards_by_id, malformed, dup = {}, [], []
    seen: dict = {}
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        obj, err = load_card_file(f)
        if err:
            malformed.append(err)
            continue
        cid = obj["id"]
        if cid in seen:
            dup.append(f"duplicate card id '{cid}': {seen[cid]} and {f.name}")
            continue
        seen[cid] = f.name
        cards_by_id[cid] = obj
    return cards_by_id, malformed, dup


# --------------------------------------------------------------------------- scan

def refresh_or_create_card(observed: dict, cards_by_id: dict):
    existing = cards_by_id.get(observed["id"])
    if existing is None:
        card = {
            "id": observed["id"], "kind": observed["kind"], "layer": None,
            "package": None, "title": observed["title"], "path": observed["path"],
            "description": observed["description"],
            "reads": [], "writes": [], "invokes": [], "flows": [],
            "glyphs": list(observed.get("glyphs") or []),
            "auto": {
                "discovered": _lib.today(),
                "hash": observed["hash"],
                "suggested_reads": list(observed.get("suggested_reads") or []),
                "suggested_writes": list(observed.get("suggested_writes") or []),
            },
        }
        if observed.get("lazy"):
            card["auto"]["lazy"] = True
        if observed.get("external_to_repo"):
            card["auto"]["external_to_repo"] = True
        return card, "created"

    card = dict(existing)
    card["id"] = observed["id"]
    card["kind"] = observed["kind"]
    card["title"] = observed["title"]
    card["path"] = observed["path"]
    if existing.get("description") == observed["description"]:
        card["description"] = observed["description"]
    # else: keep the existing (curated, or simply not-yet-re-synced) description as is - the
    # hash-staleness WARN below is what flags this case for a human/anatomist to review.

    auto = dict(existing.get("auto") or {})
    auto["discovered"] = auto.get("discovered") or _lib.today()
    auto["hash"] = observed["hash"]
    auto["suggested_reads"] = list(observed.get("suggested_reads") or [])
    auto["suggested_writes"] = list(observed.get("suggested_writes") or [])
    if observed.get("lazy"):
        auto["lazy"] = True
    if observed.get("external_to_repo"):
        auto["external_to_repo"] = True
    card["auto"] = auto

    for key, default in (("layer", None), ("package", None), ("reads", []), ("writes", []),
                          ("invokes", []), ("flows", []), ("glyphs", [])):
        card.setdefault(key, default)

    status = "unchanged" if card == existing else "refreshed"
    return card, status


def cmd_scan(_args) -> int:
    _lib.ensure_dir(cards_dir())
    cards_by_id, malformed, _dup = load_all_cards()
    malformed_ids = set()
    for f in sorted(cards_dir().glob("*.json")):
        obj, err = load_card_file(f)
        if err:
            malformed_ids.add(f.stem)

    observed_all = discover_all()
    observed_glob_ids = {o["id"] for o in discover_globbed()}

    created, refreshed, unchanged = [], [], []
    for obs in observed_all:
        if obs["id"] in malformed_ids:
            continue  # do not clobber a broken card file; report it, let a human fix it
        card, status = refresh_or_create_card(obs, cards_by_id)
        if status != "unchanged":
            _lib.atomic_write_json(cards_dir() / f"{obs['id']}.json", card, durable=True)
        {"created": created, "refreshed": refreshed, "unchanged": unchanged}[status].append(obs["id"])
        cards_by_id[obs["id"]] = card

    ghosts = []
    for cid, card in cards_by_id.items():
        if not cid.startswith(GLOB_ID_PREFIXES):
            continue
        if cid in observed_glob_ids:
            continue
        path = card.get("path")
        full = resolve_path(path) if path else None
        if full is not None and not full.exists():
            ghosts.append(cid)

    # Declared components (stores, human) are never discovered by the glob walk, so a card
    # whose store is missing from KNOWN_STORES is invisible to every count above: it neither
    # refreshes nor ghosts, it just silently stops being accounted for. Surface that.
    declared_ids = {s["id"] for s in KNOWN_STORES} | {HUMAN["id"]}
    undeclared = sorted(
        cid for cid in cards_by_id
        if cid.startswith(("store.", "human.")) and cid not in declared_ids
    )

    print(f"MAP SCAN: {len(created)} created, {len(refreshed)} refreshed, "
          f"{len(unchanged)} unchanged, {len(ghosts)} ghost(s), {len(malformed)} malformed")
    for cid in created:
        print(f"  CREATED    {cid}")
    for cid in refreshed:
        print(f"  REFRESHED  {cid}")
    for cid in ghosts:
        print(f"  GHOST      {cid} (card exists, source file gone)")
    for cid in undeclared:
        print(f"  UNDECLARED {cid} (card exists, but this store is not declared in "
              f"mapctl KNOWN_STORES - scan cannot account for it)")
    for m in malformed:
        print(f"  MALFORMED  {m}")
    index = ctxmap.discover()
    print(f"  CONTEXT    {len(index.guides())} guide(s), {len(index.rules)} rule(s): derived "
          f"into map.json at compile, never cards")

    _lib.print_verdict("MAP", True, warn=bool(ghosts or malformed or undeclared))
    return 0


# --------------------------------------------------------------------------- lint

def missing_card_errors(cards_by_id: dict) -> list:
    errors = []
    for obs in discover_globbed():
        if obs["id"] not in cards_by_id:
            errors.append(f"{obs['id']}: component exists with no card ({obs['path']}) - run `mapctl.py scan`")
    return errors


def compute_lint(cards_by_id: dict, malformed: list, dup: list, layers_cfg):
    errors = list(malformed) + list(dup)
    warnings = []

    layers_ok = isinstance(layers_cfg, dict) and isinstance(layers_cfg.get("spines"), list)
    valid_layers = None
    if layers_ok:
        valid_layers = {s.get("id") for s in layers_cfg.get("spines", []) if isinstance(s, dict)}
        valid_layers |= {f.get("id") for f in layers_cfg.get("flows", []) if isinstance(f, dict)}
    else:
        errors.append("layers.json is missing or malformed; layer validation skipped")

    for cid in sorted(cards_by_id):
        card = cards_by_id[cid]
        auto = card.get("auto") or {}
        lazy = bool(auto.get("lazy"))
        path = card.get("path")
        full = resolve_path(path) if path else None

        if full is not None and not lazy and not full.exists():
            errors.append(f"{cid}: path does not exist (ghost): {path}")

        layer = card.get("layer")
        if layer is not None and valid_layers is not None and layer not in valid_layers:
            errors.append(f"{cid}: layer '{layer}' is not declared in layers.json")

        for rel_type in ("reads", "writes", "invokes"):
            for target in card.get(rel_type) or []:
                if target not in cards_by_id:
                    errors.append(f"{cid}: {rel_type} -> '{target}' does not exist (dangling edge)")

        relations_empty = not (card.get("reads") or card.get("writes") or card.get("invokes"))
        suggested_nonempty = bool(auto.get("suggested_reads") or auto.get("suggested_writes"))
        if relations_empty and suggested_nonempty:
            warnings.append(f"{cid}: relations are empty but auto.suggested_reads/writes has candidates")

        if full is not None and not lazy and full.exists():
            current = compute_hash(full)
            old = auto.get("hash")
            if old and current and old != current:
                warnings.append(f"{cid}: auto.hash is stale (source changed since last scan; description may be stale)")

        if layer is None:
            warnings.append(f"{cid}: layer is null (unplaced)")

    return errors, warnings


def cmd_lint(_args) -> int:
    cards_by_id, malformed, dup = load_all_cards()
    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    errors, warnings = compute_lint(cards_by_id, malformed, dup, layers_cfg)
    errors += missing_card_errors(cards_by_id)

    print(f"MAP LINT: {len(errors)} error(s), {len(warnings)} warning(s)")
    for e in errors:
        print(f"  ERROR  {e}")
    for w in warnings:
        print(f"  WARN   {w}")

    _lib.print_verdict("MAP", not errors, warn=bool(warnings))
    return 1 if errors else 0


# --------------------------------------------------------------------------- compile

_KIND_ORDER = {"agent": 0, "code": 1, "data": 2, "human": 3, "external": 4}
_UNPLACED_ORDER = 9999


def _layer_order(layers_cfg) -> dict:
    order = {}
    if isinstance(layers_cfg, dict):
        for s in layers_cfg.get("spines", []) or []:
            if isinstance(s, dict) and s.get("id"):
                order[s["id"]] = s.get("order", 0)
        for i, f in enumerate(layers_cfg.get("flows", []) or []):
            if isinstance(f, dict) and f.get("id"):
                order[f["id"]] = f.get("order", 100 + i)
    return order


def build_map(cards_by_id: dict, layers_cfg, errors: list, warnings: list, context=None) -> dict:
    order_by_layer = _layer_order(layers_cfg)

    def sort_key(card):
        layer = card.get("layer")
        lo = order_by_layer.get(layer, _UNPLACED_ORDER) if layer else _UNPLACED_ORDER
        return (lo, _KIND_ORDER.get(card.get("kind"), 99), card.get("id", ""))

    ordered_ids = sorted(cards_by_id, key=lambda k: sort_key(cards_by_id[k]))

    nodes = []
    for cid in ordered_ids:
        card = cards_by_id[cid]
        path = card.get("path")
        exists = True if path is None else bool(resolve_path(path) and resolve_path(path).exists())
        nodes.append({
            "id": card.get("id"), "kind": card.get("kind"), "layer": card.get("layer"),
            "package": card.get("package"), "title": card.get("title"), "path": path,
            "description": card.get("description"), "glyphs": card.get("glyphs") or [],
            "flows": card.get("flows") or [], "exists": exists,
        })

    edges = []
    for cid in sorted(cards_by_id):
        card = cards_by_id[cid]
        for rel_type in ("reads", "writes", "invokes"):
            for target in card.get(rel_type) or []:
                edges.append({"from": cid, "to": target, "type": rel_type})

    by_kind: dict = {}
    by_layer: dict = {}
    unplaced = 0
    for card in cards_by_id.values():
        by_kind[card.get("kind")] = by_kind.get(card.get("kind"), 0) + 1
        layer = card.get("layer") or "UNPLACED"
        by_layer[layer] = by_layer.get(layer, 0) + 1
        if not card.get("layer"):
            unplaced += 1

    return {
        "v": 1,
        "generated_at": _lib.utc_now(),
        "project": _lib.project_name(),
        "layers": {
            "spines": (layers_cfg or {}).get("spines", []) if isinstance(layers_cfg, dict) else [],
            "flows": (layers_cfg or {}).get("flows", []) if isinstance(layers_cfg, dict) else [],
            "unplaced": (layers_cfg or {}).get("unplaced", {}) if isinstance(layers_cfg, dict) else {},
        },
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes), "edges": len(edges),
            "by_kind": dict(sorted(by_kind.items())),
            "by_layer": dict(sorted(by_layer.items())),
            "unplaced": unplaced,
        },
        "lint": {"errors": errors, "warnings": warnings},
        "context": context if context is not None else ctxmap.empty_section(),
    }


def cmd_compile(_args) -> int:
    cards_by_id, malformed, dup = load_all_cards()
    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    errors, warnings = compute_lint(cards_by_id, malformed, dup, layers_cfg)
    errors = errors + missing_card_errors(cards_by_id)

    map_obj = build_map(cards_by_id, layers_cfg, errors, warnings, context=ctxmap.map_section())

    out_path = map_json_path()
    existing = _lib.read_json(out_path, None)
    new_cmp = {k: v for k, v in map_obj.items() if k != "generated_at"}
    wrote = True
    if isinstance(existing, dict):
        old_cmp = {k: v for k, v in existing.items() if k != "generated_at"}
        if json.dumps(new_cmp, sort_keys=True) == json.dumps(old_cmp, sort_keys=True):
            wrote = False
    if wrote:
        _lib.atomic_write_json(out_path, map_obj, durable=False)

    note = "" if wrote else " (unchanged, not rewritten)"
    print(f"MAP COMPILE: {map_obj['stats']['nodes']} nodes, {map_obj['stats']['edges']} edges, "
          f"{len(errors)} error(s), {len(warnings)} warning(s){note}")
    _lib.print_verdict("MAP", not errors, warn=bool(warnings))
    return 1 if errors else 0


# --------------------------------------------------------------------------- architecture
#
# `architecture` writes system-map/architecture.json (one spec, several scopes) and renders
# docs/ARCHITECTURE.md from it: one Mermaid flowchart per scope, groups as subgraphs, every box
# a click link to its file. The `claude` scope is built from the cards, deterministically:
# groups are the layers; data cards under a FOLD_FOLDERS folder (and the external record root)
# collapse into one folder box each; test files never become boxes; relations aggregate
# between the boxes (weight = count, self-edges dropped); past `architecture.max_nodes` the
# lowest-degree boxes of a group fold into that group's "other" box. Scopes with
# `source: code` come from graphctl.architecture_scope(root) when that tool is installed. Every
# path is checked against the tree: a box pointing nowhere fails the build (gates fail closed),
# except a lazy card's not-yet-written store, which `lint` exempts too (WARN, no link).
#
# Mermaid ids: every character outside [A-Za-z0-9_] becomes "_" (tool.statectl ->
# tool_statectl, agent.retro-analyst -> agent_retro_analyst, tool._lib -> tool__lib); a
# collision gets a numeric suffix in id order; groups render as g_<group id>.

ARCH_DEFAULT_SCOPES = [{"id": "claude", "root": ".claude", "source": "cards"},
                       {"id": "tools", "root": ".claude/tools", "source": "code"}]
ARCH_DEFAULT_MAX_NODES = 40
ARCH_SCOPE_TITLES = {"claude": ".claude system"}
FOLD_FOLDERS = (".claude/state", ".claude/config", ".claude/system-map", ".claude-iff")
RECORD_FOLDER = "folder.record"
_REL_STYLE = {"reads": "dashed", "writes": "solid", "invokes": "solid"}
_ARCH_KIND = {"agent": "agent", "code": "code", "data": "data", "human": "human",
              "folder": "folder", "file": "file"}
_MERMAID_SHAPES = {"agent": ('(["', '"])'), "data": ('[("', '")]'), "human": ('(("', '"))'),
                   "folder": ('[["', '"]]')}


def architecture_json_path() -> Path:
    return _lib.map_dir() / "architecture.json"


def architecture_md_path() -> Path:
    return _lib.project_root() / "docs" / "ARCHITECTURE.md"


def is_test_path(path) -> bool:
    """A test file never becomes a box: anything under a tests/ or test/ folder, or a file
    named test_*.py / *_test.py."""
    if not path:
        return False
    parts = str(path).replace("\\", "/").split("/")
    name = parts[-1]
    return (any(p in ("tests", "test") for p in parts[:-1])
            or bool(re.match(r"^test_.*\.py$", name)) or bool(re.match(r"^.*_test\.py$", name)))


def fold_folder_of(card: dict, folders=FOLD_FOLDERS):
    """The folder box a card folds into, or None: data cards only, the record root (a store
    outside the repo) into its own box, everything else under the first matching folder."""
    if card.get("kind") != "data":
        return None
    if (card.get("auto") or {}).get("external_to_repo"):
        return RECORD_FOLDER
    path = str(card.get("path") or "").replace("\\", "/")
    for folder in folders:
        if path == folder or path.startswith(folder + "/"):
            return "folder." + folder.rsplit("/", 1)[-1].lstrip(".")
    return None


def arch_group_id(layer) -> str:
    return str(layer or "UNPLACED").lower()


def arch_label(card_id: str) -> str:
    """Short box label: the id after its kind prefix (tool.statectl -> statectl); a skill
    shows as the command a user types."""
    prefix, _, rest = card_id.partition(".")
    rest = rest or card_id
    return f"/{rest}" if prefix == "skill" else rest


def fold_cards(cards_by_id: dict, layer_order: dict, folders=FOLD_FOLDERS):
    """Cards -> (boxes by id, card id -> box id). Folder boxes take the layer most of their
    members sit in (ties: the earlier layer), so a folded store keeps a home group."""
    boxes, member_of, folded = {}, {}, {}
    for cid in sorted(cards_by_id):
        card = cards_by_id[cid]
        if is_test_path(card.get("path")):
            continue
        fid = fold_folder_of(card, folders)
        if fid:
            folded.setdefault(fid, []).append(card)
            member_of[cid] = fid
            continue
        boxes[cid] = {"id": cid, "group": arch_group_id(card.get("layer")), "label": arch_label(cid),
                      "path": card.get("path"), "kind": _ARCH_KIND.get(card.get("kind"), "data"),
                      "_lazy": bool((card.get("auto") or {}).get("lazy"))}
        member_of[cid] = cid
    for fid, members in sorted(folded.items()):
        counts: dict = {}
        for m in members:
            counts[m.get("layer") or "UNPLACED"] = counts.get(m.get("layer") or "UNPLACED", 0) + 1
        layer = sorted(counts, key=lambda k: (-counts[k], layer_order.get(k, _UNPLACED_ORDER), k))[0]
        if fid == RECORD_FOLDER:
            label, path = "record root", None
        else:
            path = next(f for f in folders if fid == "folder." + f.rsplit("/", 1)[-1].lstrip("."))
            label = path + "/"
        boxes[fid] = {"id": fid, "group": arch_group_id(layer), "label": label, "path": path,
                      "kind": "folder", "members": sorted(m["id"] for m in members),
                      "_lazy": all((m.get("auto") or {}).get("lazy") for m in members)}
    return boxes, member_of


def card_relations(cards_by_id: dict) -> list:
    """Every card relation as (from, to, type), dangling targets dropped (lint names those)."""
    out = []
    for cid in sorted(cards_by_id):
        for rel_type in ("reads", "writes", "invokes"):
            for target in cards_by_id[cid].get(rel_type) or []:
                if target in cards_by_id:
                    out.append((cid, target, rel_type))
    return out


def aggregate_edges(relations, box_of: dict) -> list:
    """Relations (from, to, label[, weight]) mapped onto boxes and summed per (from, to, label);
    a relation whose ends land in one box, or outside every box, is dropped."""
    weights: dict = {}
    for rel in relations:
        src, dst, label = rel[0], rel[1], rel[2]
        weight = rel[3] if len(rel) > 3 else 1
        a, b = box_of.get(src), box_of.get(dst)
        if a is None or b is None or a == b:
            continue
        weights[(a, b, label)] = weights.get((a, b, label), 0) + weight
    return [{"from": a, "to": b, "label": label, "style": _REL_STYLE.get(label, "solid"),
             "weight": w} for (a, b, label), w in sorted(weights.items())]


def cap_boxes(boxes: dict, edges: list, max_nodes: int):
    """Fold the lowest-degree boxes (folder boxes excepted) into one "other" box per group until
    at most max_nodes remain. Returns (boxes, remap: old box id -> new box id). An "other" box
    left with one member is undone: it would cost a box and hide one."""
    degree = {bid: 0 for bid in boxes}
    for e in edges:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    remap = {bid: bid for bid in boxes}
    others: dict = {}
    count = len(boxes)
    candidates = sorted((bid for bid, b in boxes.items() if b["kind"] != "folder"),
                        key=lambda bid: (degree[bid], bid))
    for bid in candidates:
        if count <= max_nodes:
            break
        group = boxes[bid]["group"]
        if group not in others:
            others[group] = []
            count += 1
        others[group].append(bid)
        count -= 1
    out = {bid: dict(b) for bid, b in boxes.items()}
    for group, members in sorted(others.items()):
        if len(members) < 2:
            continue
        oid = f"other.{group}"
        paths = [boxes[m]["path"] for m in members if boxes[m].get("path")]
        try:
            common = os.path.commonpath(paths).replace("\\", "/") if paths else None
        except ValueError:
            common = None
        out[oid] = {"id": oid, "group": group, "label": f"other ({len(members)})",
                    "path": common or None, "kind": "folder", "members": sorted(members),
                    # A fold of planned (lazy) cards is lazy too: its missing path is then a
                    # WARN, not an error that blocks the whole spec for an adopter with many
                    # planned cards (found in extrct_private's upgrade, PR-22).
                    "_lazy": all(boxes[m].get("_lazy") for m in members)}
        for m in members:
            del out[m]
            remap[m] = oid
    return out, remap


def arch_max_nodes(cfg) -> int:
    value = (cfg.get("architecture") or {}).get("max_nodes") if isinstance(cfg, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return ARCH_DEFAULT_MAX_NODES
    return value


def arch_scopes(cfg) -> list:
    value = (cfg.get("architecture") or {}).get("scopes") if isinstance(cfg, dict) else None
    if value is None:
        return [dict(s) for s in ARCH_DEFAULT_SCOPES]
    if not isinstance(value, list) or not all(isinstance(s, dict) and s.get("id") for s in value):
        raise ValueError("console.json architecture.scopes must be a list of {id, root, source}")
    return value


def build_cards_scope(scope_cfg: dict, cards_by_id: dict, layers_cfg, max_nodes: int) -> dict:
    """The cards-sourced scope (`claude`), in the architecture.json shape."""
    order = _layer_order(layers_cfg)
    boxes, member_of = fold_cards(cards_by_id, order)
    relations = card_relations(cards_by_id)
    edges = aggregate_edges(relations, member_of)
    group_order = {arch_group_id(k): v for k, v in order.items()}
    boxes, remap = cap_boxes(boxes, edges, max_nodes)
    box_of = {cid: remap.get(bid, bid) for cid, bid in member_of.items()}
    edges = aggregate_edges(relations, box_of)

    meta = {}
    if isinstance(layers_cfg, dict):
        for s in list(layers_cfg.get("spines") or []) + list(layers_cfg.get("flows") or []):
            if isinstance(s, dict) and s.get("id"):
                meta[arch_group_id(s["id"])] = s
        if isinstance(layers_cfg.get("unplaced"), dict):
            meta["unplaced"] = layers_cfg["unplaced"]

    def g_order(gid):
        return _UNPLACED_ORDER if gid == "unplaced" else group_order.get(gid, _UNPLACED_ORDER - 1)

    used = sorted({b["group"] for b in boxes.values()}, key=lambda g: (g_order(g), g))
    groups = [{"id": g, "title": (meta.get(g) or {}).get("title") or g.upper(),
               "blurb": (meta.get(g) or {}).get("blurb") or ""} for g in used]
    rank = {g: i for i, g in enumerate(used)}
    kind_rank = {"agent": 0, "code": 1, "file": 1, "data": 2, "folder": 3, "human": 4}
    nodes = sorted(boxes.values(), key=lambda b: (rank[b["group"]], kind_rank.get(b["kind"], 9), b["id"]))
    folded = sum(len(b.get("members") or []) for b in nodes)
    return {"id": scope_cfg["id"], "title": scope_cfg.get("title") or ARCH_SCOPE_TITLES.get(
                scope_cfg["id"], scope_cfg.get("root") or scope_cfg["id"]),
            "root": scope_cfg.get("root") or ".claude", "source": "cards",
            "groups": groups, "nodes": nodes, "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges), "folded": folded}}


def build_code_scope(scope_cfg: dict, warnings: list):
    """A `source: code` scope from graphctl (tier 2), or None with a WARN when the tool is not
    installed, has no graph yet, or breaks: an optional view never fails the build."""
    try:
        import graphctl
    except ImportError:
        warnings.append(f"scope '{scope_cfg['id']}' skipped: graphctl.py is not installed")
        return None
    try:
        scope = graphctl.architecture_scope(scope_cfg.get("root") or ".")
    except Exception as exc:  # telemetry-grade input: report it, keep the cards scope
        warnings.append(f"scope '{scope_cfg['id']}' skipped: graphctl.architecture_scope raised "
                        f"{type(exc).__name__}: {exc}")
        return None
    if not isinstance(scope, dict) or not isinstance(scope.get("nodes"), list):
        warnings.append(f"scope '{scope_cfg['id']}' skipped: graphctl returned no scope")
        return None
    nodes = [dict(n) for n in scope["nodes"] if isinstance(n, dict) and n.get("id")]
    ids = {n["id"] for n in nodes}
    edges = [dict(e) for e in scope.get("edges") or []
             if isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids
             and e.get("from") != e.get("to")]
    for e in edges:
        e.setdefault("label", "imports")
        e.setdefault("style", "solid")
        e.setdefault("weight", 1)
    groups = [dict(g) for g in scope.get("groups") or [] if isinstance(g, dict) and g.get("id")]
    known = {g["id"] for g in groups}
    for n in nodes:
        n.setdefault("group", "code")
        n.setdefault("label", n["id"])
        n.setdefault("kind", "file")
        if n["group"] not in known:
            known.add(n["group"])
            groups.append({"id": n["group"], "title": str(n["group"]).upper(), "blurb": ""})
    stats = dict(scope.get("stats") or {})
    stats.update({"nodes": len(nodes), "edges": len(edges)})
    stats.setdefault("folded", 0)
    return {"id": scope_cfg["id"], "title": scope.get("title") or scope_cfg.get("title")
            or ARCH_SCOPE_TITLES.get(scope_cfg["id"], scope_cfg.get("root") or scope_cfg["id"]),
            "root": scope_cfg.get("root") or ".", "source": "code",
            "groups": groups, "nodes": nodes, "edges": edges, "stats": stats}


def validate_scope_paths(scope: dict, root: Path, warnings: list) -> list:
    """Every box path against the real tree. A missing path is an error, except for a lazy
    card's store not written yet (lint's own exemption): that box loses its path, with a WARN."""
    errors = []
    for node in scope["nodes"]:
        lazy = node.pop("_lazy", False)
        path = node.get("path")
        if path is None:
            continue
        posix = Path(str(path).replace("\\", "/")).as_posix()
        node["path"] = posix
        if (Path(posix) if Path(posix).is_absolute() else root / posix).exists():
            continue
        if lazy:
            warnings.append(f"{scope['id']}: {node['id']} has no {posix} yet (lazy store): no link")
            node["path"] = None
        else:
            errors.append(f"{scope['id']}: {node['id']} path does not exist: {posix}")
    return errors


def build_architecture(warnings: list, errors: list) -> dict:
    """The whole spec, in memory; `errors` non-empty means the caller must not write it."""
    cards_by_id, malformed, dup = load_all_cards()
    errors.extend(malformed + dup)
    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    cfg = _lib.load_config("console")
    root = _lib.project_root()
    try:
        scopes_cfg = arch_scopes(cfg)
    except ValueError as exc:
        errors.append(str(exc))
        scopes_cfg = []
    max_nodes = arch_max_nodes(cfg)
    scopes = []
    for scope_cfg in scopes_cfg:
        source = scope_cfg.get("source", "cards")
        if source == "cards":
            scope = build_cards_scope(scope_cfg, cards_by_id, layers_cfg, max_nodes)
        elif source == "code":
            scope = build_code_scope(scope_cfg, warnings)
        else:
            errors.append(f"scope '{scope_cfg.get('id')}': unknown source {source!r} (cards|code)")
            continue
        if scope is None:
            continue
        errors.extend(validate_scope_paths(scope, root, warnings))
        scopes.append(scope)
    return {"v": 1, "generated_at": _lib.utc_now(), "project": _lib.project_name(), "scopes": scopes}


def parse_github_remote(url: str):
    """'<owner>/<repo>' for a GitHub remote (https, ssh or scp form), else None."""
    url = (url or "").strip()
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url,
                 re.IGNORECASE)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def link_base(root: Path | None = None, branch: str | None = None):
    """'https://github.com/<owner>/<repo>/blob/<branch>/' when origin is on GitHub, else None
    (relative links). `branch` pins the branch (the published anatomy page links `main`);
    without it, the checked-out branch. Never raises: _lib.git_output returns '' when git is
    missing."""
    repo = parse_github_remote(_lib.git_output(["remote", "get-url", "origin"], root))
    if not repo:
        return None
    branch = branch or _lib.git_output(["rev-parse", "--abbrev-ref", "HEAD"], root) or "HEAD"
    return f"https://github.com/{repo}/blob/{branch}/"


def mermaid_ids(node_ids) -> dict:
    out, used = {}, set()
    for nid in sorted(node_ids):
        base = re.sub(r"[^A-Za-z0-9_]", "_", str(nid))
        if not base or not base[0].isalpha():
            base = "n_" + base
        safe, i = base, 2
        while safe in used:
            safe, i = f"{base}_{i}", i + 1
        used.add(safe)
        out[nid] = safe
    return out


def _mermaid_text(text) -> str:
    return str(text).replace('"', "#quot;").replace("\n", " ")


def node_url(path: str, base) -> str:
    from urllib.parse import quote
    quoted = quote(path, safe="/-_.~")
    return base + quoted if base else "../" + quoted


def render_mermaid(scope: dict, base) -> str:
    ids = mermaid_ids(n["id"] for n in scope["nodes"])
    lines = ["flowchart LR"]
    for group in scope["groups"]:
        members = [n for n in scope["nodes"] if n.get("group") == group["id"]]
        if not members:
            continue
        gid = "g_" + re.sub(r"[^A-Za-z0-9_]", "_", str(group["id"]))
        lines.append(f'  subgraph {gid}["{_mermaid_text(group.get("title") or group["id"])}"]')
        for n in members:
            left, right = _MERMAID_SHAPES.get(n.get("kind"), ('["', '"]'))
            lines.append(f"    {ids[n['id']]}{left}{_mermaid_text(n.get('label') or n['id'])}{right}")
        lines.append("  end")
    for e in scope["edges"]:
        arrow = "-.->" if e.get("style") == "dashed" else "-->"
        label = e.get("label") or ""
        if (e.get("weight") or 1) > 1:
            label = f"{label} x{e['weight']}"
        lines.append(f"  {ids[e['from']]} {arrow}|{_mermaid_text(label)}| {ids[e['to']]}")
    for n in scope["nodes"]:
        if n.get("path"):
            lines.append(f'  click {ids[n["id"]]} "{node_url(n["path"], base)}"')
    return "\n".join(lines)


def render_architecture_md(spec: dict, base) -> str:
    out = [
        "# Architecture",
        "",
        "<!-- Derived file: written by `python3 .claude/tools/mapctl.py architecture` from",
        "     .claude/system-map/architecture.json (the generator architecture_build, run by",
        "     /project-memory). Hand edits are lost on the next build: change the cards instead. -->",
        "",
        "One diagram per scope. Boxes are components, grouped by layer: a rectangle is code, a",
        "stadium an agent, a cylinder a data store, a circle the human, a double-edged box a folder",
        "(stores folded together, or a group's least-connected components). Solid arrows write or",
        "invoke, dashed arrows read; `xN` is how many card relations an arrow stands for. Click a",
        "box to open its file.",
    ]
    for scope in spec["scopes"]:
        out += ["", f"## {scope['title']} (`{scope['id']}`)", "",
                f"Root `{scope['root']}`, built from the "
                + ("system-map cards." if scope["source"] == "cards" else "code graph (graphctl)."),
                "", "```mermaid", render_mermaid(scope, base), "```"]
        groups = [g for g in scope["groups"] if g.get("blurb")]
        if groups:
            out += [""] + [f"- **{g['title']}**: {g['blurb']}" for g in groups]
        folded = [n for n in scope["nodes"] if n.get("members")]
        if folded:
            out += ["", "Folded boxes:", ""]
            out += [f"- `{n['label']}`: " + ", ".join(f"`{m}`" for m in n["members"]) for n in folded]
    return "\n".join(out) + "\n"


def cmd_architecture(_args) -> int:
    warnings, errors = [], []
    spec = build_architecture(warnings, errors)
    if errors:
        print(f"MAP ARCHITECTURE: {len(errors)} error(s); nothing written")
        for e in errors:
            print(f"  ERROR  {e}")
        for w in warnings:
            print(f"  WARN   {w}")
        _lib.print_verdict("MAP", False)
        return 1

    out_path = architecture_json_path()
    existing = _lib.read_json(out_path, None)
    wrote_json = True
    if isinstance(existing, dict):
        old = {k: v for k, v in existing.items() if k != "generated_at"}
        new = {k: v for k, v in spec.items() if k != "generated_at"}
        wrote_json = json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True)
    if wrote_json:
        _lib.atomic_write_json(out_path, spec, durable=False)

    md = render_architecture_md(spec, link_base())
    md_path = architecture_md_path()
    try:
        old_md = md_path.read_text(encoding="utf-8")
    except OSError:
        old_md = None
    wrote_md = old_md != md
    if wrote_md:
        _lib.atomic_write_text(md_path, md, durable=False)

    for scope in spec["scopes"]:
        st = scope["stats"]
        print(f"MAP ARCHITECTURE: scope {scope['id']}: {st['nodes']} boxes, {st['edges']} arrows, "
              f"{st.get('folded', 0)} folded")
    print(f"  {_lib.rel(out_path)}" + ("" if wrote_json else " (unchanged, not rewritten)"))
    print(f"  {_lib.rel(md_path)}" + ("" if wrote_md else " (unchanged, not rewritten)"))
    for w in warnings:
        print(f"  WARN   {w}")
    _lib.print_verdict("MAP", True, warn=bool(warnings))
    return 0


# --------------------------------------------------------------------------- anatomy
#
# `anatomy` renders ONE standalone page for the docs site (docs/anatomy.html by default): the
# template .claude/console/anatomy.template.html with one JSON token replaced by the payload,
# the consolectl build pattern (no runtime fetch). Level 1 is every layer of layers.json as a
# tile with its components as chips; level 2 one component with its relations both ways; level
# 3 its file, linked on GitHub (branch ANATOMY_BRANCH) or relatively when origin is not GitHub.
# Sources: map.json gives the component set and its order (the compiled map); a card, when
# present, is the authority for its own fields and relations (map.json may lag it by a
# compile, with a WARN); layers.json the tiles; architecture.json the folded box each store
# sits in on the diagram (optional, WARN when absent). The payload carries ids, labels,
# titles, layers, kinds, paths, links, descriptions and relations only, never `auto`. Gates
# fail closed: no map.json, no layers.json, a broken template or a linked path that does not
# exist writes nothing; a lazy store not written yet loses its link with a WARN (lint's own
# exemption). Write-gated: an unchanged page is not rewritten.

ANATOMY_TOKEN = "__ANATOMY_DATA__"
ANATOMY_DEFAULT_OUT = "docs/anatomy.html"
ANATOMY_BRANCH = "main"
ANATOMY_UNPLACED = "UNPLACED"
_ANATOMY_RELATIONS = ("reads", "writes", "invokes")


def anatomy_template_path() -> Path:
    return _lib.console_dir() / "anatomy.template.html"


def anatomy_layers(layers_cfg: dict) -> list:
    """Tiles in order: the spines, then the flow layers (each by `order`), unplaced last."""
    def ranked(items, start):
        good = [x for x in items or [] if isinstance(x, dict) and x.get("id")]
        return sorted(good, key=lambda x: (x.get("order", start), str(x["id"])))
    out = []
    for item in ranked(layers_cfg.get("spines"), 0) + ranked(layers_cfg.get("flows"), 100):
        out.append({"id": str(item["id"]), "title": item.get("title") or str(item["id"]),
                    "blurb": item.get("blurb") or ""})
    unplaced = layers_cfg.get("unplaced") if isinstance(layers_cfg.get("unplaced"), dict) else {}
    out.append({"id": ANATOMY_UNPLACED, "title": unplaced.get("title") or "unplaced",
                "blurb": unplaced.get("blurb") or ""})
    return out


def anatomy_url(path: str, base, prefix: str) -> str:
    """The file link: on GitHub under `base`, else relative to the page's own folder."""
    from urllib.parse import quote
    quoted = quote(path, safe="/-_.~")
    return base + quoted if base else prefix + quoted


def anatomy_tracked(root: Path):
    """The repo's tracked paths (posix), or None when that cannot be told (no git, not a repo).
    A GitHub link to a file that exists here but is gitignored would 404 on the published page."""
    out = _lib.git_output(["ls-files", "-z"], root)
    return {p for p in out.split("\0") if p} or None


def anatomy_linkable(path: str, tracked) -> bool:
    """A path (file or folder) GitHub serves: tracked itself, or a folder holding tracked files."""
    if tracked is None:
        return True
    return path in tracked or any(t.startswith(path.rstrip("/") + "/") for t in tracked)


def anatomy_disambiguate(nodes: list) -> None:
    """Chip labels are the id after its prefix; labels that would read alike (orchestration
    the protocol, the config and the store; ritual-ticket and ritual_ticket) carry their prefix
    in brackets instead, so no two chips on the page look the same."""
    def key(label):
        return label.lower().replace("_", "-")
    counts: dict = {}
    for n in nodes:
        counts[key(n["label"])] = counts.get(key(n["label"]), 0) + 1
    for n in nodes:
        prefix, _, rest = n["id"].partition(".")
        if counts[key(n["label"])] > 1 and rest:
            n["label"] = f"{n['label']} ({prefix})"


def anatomy_boxes(arch) -> dict:
    """card id -> the label of the folded box it sits in on the cards-sourced diagram."""
    out = {}
    if not isinstance(arch, dict):
        return out
    for scope in arch.get("scopes") or []:
        if not isinstance(scope, dict) or scope.get("source") != "cards":
            continue
        for node in scope.get("nodes") or []:
            if isinstance(node, dict) and node.get("members"):
                for member in node["members"]:
                    out.setdefault(member, node.get("label") or node.get("id"))
    return out


def build_anatomy(base, prefix: str, warnings: list, errors: list):
    """The page payload, in memory (nothing is written); None, or `errors` non-empty, means the
    caller must not write it."""
    root = _lib.project_root()
    map_obj = _lib.read_json(map_json_path(), None)
    if not isinstance(map_obj, dict) or not isinstance(map_obj.get("nodes"), list):
        errors.append(f"{_lib.rel(map_json_path())} is missing or malformed: run `mapctl.py compile`")
        return None
    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    if not isinstance(layers_cfg, dict) or not isinstance(layers_cfg.get("spines"), list):
        errors.append("layers.json is missing or malformed: the tiles cannot be laid out")
        return None
    cards_by_id, malformed, dup = load_all_cards()
    errors.extend(malformed + dup)
    arch = _lib.read_json(architecture_json_path(), None)
    if not isinstance(arch, dict):
        warnings.append("no architecture.json: the folded-box notes are left out "
                        "(run `mapctl.py architecture`)")
    boxes = anatomy_boxes(arch)
    layers = anatomy_layers(layers_cfg)
    layer_ids = {layer["id"] for layer in layers}

    map_nodes = {}
    for n in map_obj["nodes"]:
        if isinstance(n, dict) and n.get("id") and n["id"] not in map_nodes:
            map_nodes[n["id"]] = n
    map_edges: dict = {}
    for e in map_obj.get("edges") or []:
        if isinstance(e, dict) and e.get("type") in _ANATOMY_RELATIONS:
            map_edges.setdefault(e.get("from"), []).append((e.get("to"), e["type"]))
    order = list(map_nodes)
    for cid in sorted(cards_by_id):
        if cid not in map_nodes:
            warnings.append(f"{cid}: card not in map.json yet (run `mapctl.py compile`); shown anyway")
            order.append(cid)
    for cid in order:
        if cid not in cards_by_id:
            warnings.append(f"{cid}: in map.json but has no card; shown from map.json")

    tracked = anatomy_tracked(root) if base else None
    nodes, ids = [], set(order)
    for cid in order:
        src = cards_by_id.get(cid) or map_nodes[cid]
        layer = src.get("layer") or ANATOMY_UNPLACED
        if layer not in layer_ids:
            warnings.append(f"{cid}: layer '{layer}' is not in layers.json; shown as unplaced")
            layer = ANATOMY_UNPLACED
        path = src.get("path")
        url, local = None, False
        if path:
            path = Path(str(path).replace("\\", "/")).as_posix()
            full = Path(path) if Path(path).is_absolute() else root / path
            lazy = bool(((cards_by_id.get(cid) or {}).get("auto") or {}).get("lazy"))
            if full.exists() and anatomy_linkable(path, tracked):
                url = anatomy_url(path, base, prefix)
            elif full.exists() or (lazy and tracked is not None):
                local = True  # machine-local (gitignored): real here, absent on GitHub
            elif lazy:
                warnings.append(f"{cid} has no {path} yet (lazy store): no link")
            else:
                errors.append(f"{cid}: path does not exist: {path}")
        node = {"id": cid, "label": arch_label(cid), "title": src.get("title") or cid,
                "kind": src.get("kind") if src.get("kind") in _KIND_ORDER else "data",
                "layer": layer, "path": path or None, "url": url,
                "description": src.get("description") or ""}
        if local:
            node["local"] = True
        if cid in boxes:
            node["box"] = boxes[cid]
        nodes.append(node)
    anatomy_disambiguate(nodes)

    edges, seen = [], set()
    for cid in order:
        card = cards_by_id.get(cid)
        rels = ([(t, r) for r in _ANATOMY_RELATIONS for t in card.get(r) or []] if card
                else map_edges.get(cid, []))
        for target, rel in rels:
            key = (cid, target, rel)
            if target in ids and target != cid and key not in seen:
                seen.add(key)
                edges.append([cid, target, rel])

    arch_md = architecture_md_path()
    arch_url = anatomy_url(arch_md.relative_to(root).as_posix(), base, prefix) if arch_md.exists() else None
    return {"v": 1, "project": map_obj.get("project") or _lib.project_name(),
            "compiled": str(map_obj.get("generated_at") or "")[:10], "base": base,
            "arch_url": arch_url, "layers": layers, "nodes": nodes, "edges": edges}


def render_anatomy(payload: dict, template_path: Path | None = None) -> str:
    """The template with its one token replaced. Every `<` is escaped inside the JSON, so no
    card text can close the data block or open a comment in it."""
    template_path = template_path or anatomy_template_path()
    text = template_path.read_text(encoding="utf-8")
    count = text.count(ANATOMY_TOKEN)
    if count != 1:
        raise _lib.LibError(f"expected exactly one {ANATOMY_TOKEN} in {_lib.rel(template_path)}, "
                            f"found {count}")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace(ANATOMY_TOKEN, data.replace("<", "\\u003c"), 1)


def cmd_anatomy(args) -> int:
    root = _lib.project_root()
    out_arg = getattr(args, "out", None) or ANATOMY_DEFAULT_OUT
    out_path = Path(out_arg) if Path(out_arg).is_absolute() else root / out_arg
    prefix = Path(os.path.relpath(root, out_path.parent)).as_posix() + "/"
    prefix = "" if prefix == "./" else prefix
    warnings, errors = [], []
    payload = build_anatomy(link_base(branch=ANATOMY_BRANCH), prefix, warnings, errors)
    html = None
    if payload is not None and not errors:
        try:
            html = render_anatomy(payload)
        except (OSError, _lib.LibError) as exc:
            errors.append(f"template: {exc}")
    if errors or html is None:
        print(f"MAP ANATOMY: {len(errors)} error(s); nothing written")
        for e in errors:
            print(f"  ERROR  {e}")
        for w in warnings:
            print(f"  WARN   {w}")
        _lib.print_verdict("MAP", False)
        return 1

    try:
        old = out_path.read_text(encoding="utf-8")
    except OSError:
        old = None
    wrote = old != html
    if wrote:
        _lib.atomic_write_text(out_path, html, durable=False)
    print(f"MAP ANATOMY: {len(payload['nodes'])} components in {len(payload['layers'])} layers, "
          f"{len(payload['edges'])} relations, file links "
          + ("on GitHub" if payload["base"] else "relative"))
    print(f"  {_lib.rel(out_path)}" + ("" if wrote else " (unchanged, not rewritten)"))
    for w in warnings:
        print(f"  WARN   {w}")
    _lib.print_verdict("MAP", True, warn=bool(warnings))
    return 0


# --------------------------------------------------------------------------- show

def cmd_show(args) -> int:
    cards_by_id, malformed, dup = load_all_cards()

    if args.id:
        card = cards_by_id.get(args.id)
        if not card:
            print(f"MAP SHOW: no such card '{args.id}'")
            _lib.print_verdict("MAP", False)
            return 1
        auto = card.get("auto") or {}
        print(f"{card.get('id')}  [{card.get('kind')}]  layer={card.get('layer') or 'UNPLACED'}")
        print(f"  title:       {card.get('title', '')}")
        print(f"  path:        {card.get('path')}")
        print(f"  description: {card.get('description', '')}")
        print(f"  reads:       {card.get('reads') or []}")
        print(f"  writes:      {card.get('writes') or []}")
        print(f"  invokes:     {card.get('invokes') or []}")
        print(f"  flows:       {card.get('flows') or []}")
        print(f"  glyphs:      {card.get('glyphs') or []}")
        print(f"  auto:        discovered={auto.get('discovered')} hash={(auto.get('hash') or '')[:12]}")
        print(f"               suggested_reads={auto.get('suggested_reads') or []}")
        print(f"               suggested_writes={auto.get('suggested_writes') or []}")
        _lib.print_verdict("MAP", True)
        return 0

    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    errors, warnings = compute_lint(cards_by_id, malformed, dup, layers_cfg)
    errors = errors + missing_card_errors(cards_by_id)

    by_kind: dict = {}
    by_layer: dict = {}
    unplaced = 0
    for card in cards_by_id.values():
        by_kind[card.get("kind")] = by_kind.get(card.get("kind"), 0) + 1
        layer = card.get("layer") or "UNPLACED"
        by_layer[layer] = by_layer.get(layer, 0) + 1
        if not card.get("layer"):
            unplaced += 1

    print(f"MAP: {_lib.project_name()}  ({len(cards_by_id)} component(s))")
    print(f"  by kind:  {dict(sorted(by_kind.items()))}")
    print(f"  by layer: {dict(sorted(by_layer.items()))}")
    print(f"  unplaced: {unplaced}")
    print(f"  lint:     {len(errors)} error(s), {len(warnings)} warning(s)")
    _lib.print_verdict("MAP", not errors, warn=bool(warnings))
    return 1 if errors else 0


# --------------------------------------------------------------------------- context

def cmd_context(args) -> int:
    """What Claude Code loads for a file, the context file list with its findings, or
    evidence-based proposals. ctxmap.py is the engine; this only owns the verdict token,
    which goes to stderr under --json so stdout stays pure JSON."""
    if args.suggest and args.path:
        print("mapctl context: give a path or --suggest, not both", file=sys.stderr)
        _lib.print_verdict("MAP", False)
        return 2
    code, state = ctxmap.cli(args.path, suggest_mode=args.suggest, as_json=args.json)
    print(f"MAP_{state}", file=sys.stderr if args.json else sys.stdout)
    return code


# --------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mapctl.py", description="The system map: scan / lint / compile / architecture / anatomy / show / context.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="auto-discover components and create/refresh stub cards")
    sub.add_parser("lint", help="two-tier validation of the card set (ERROR fails, WARN informs)")
    sub.add_parser("compile", help="write .claude/system-map/map.json (write-gated)")
    sub.add_parser("architecture", help="write system-map/architecture.json and docs/ARCHITECTURE.md "
                                        "(Mermaid, one diagram per scope; write-gated)")
    ap = sub.add_parser("anatomy", help="write the interactive anatomy page (layers, one component, "
                                        "its file; write-gated)")
    ap.add_argument("--out", default=None,
                    help=f"output path, relative to the project root (default {ANATOMY_DEFAULT_OUT})")
    sp = sub.add_parser("show", help="human-readable summary of the map or one card")
    sp.add_argument("--id", default=None)
    cp = sub.add_parser("context", help="guides and rules that load for a path (none: list all)")
    cp.add_argument("path", nargs="?", default=None, help="repo-relative (or absolute) path")
    cp.add_argument("--suggest", action="store_true",
                    help="propose folder guides / scoped rules from lessons and logged mistakes")
    cp.add_argument("--json", action="store_true")
    return p


COMMANDS = {"scan": cmd_scan, "lint": cmd_lint, "compile": cmd_compile, "show": cmd_show,
            "context": cmd_context, "architecture": cmd_architecture, "anatomy": cmd_anatomy}


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
