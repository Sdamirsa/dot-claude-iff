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

    mapctl.py scan       # discover components, create/refresh stub cards
    mapctl.py lint       # two-tier validation; exit 1 on any ERROR
    mapctl.py compile    # write system-map/map.json (write-gated)
    mapctl.py show [--id ID]
"""

import argparse
import ast
import json
import re
from pathlib import Path

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
     "description": "Liveness signal, overwritten once per turn by the Stop hook. Not the resume "
                     "guarantee - the journal pointer is.",
     "glyphs": []},
    {"id": "store.memory_run", "path": ".claude/state/memory-run.json", "title": "Ritual run checkpoint",
     "description": "The /project-memory transaction: run id, phase, step. PUBLISH refuses unless "
                     "POLISH completed in the same run id.",
     "glyphs": []},
    {"id": "store.handshakes", "path": ".claude/state/handshakes", "title": "Handshake envelopes",
     "description": "Agent-to-agent Structured Return envelopes (stub at dispatch, envelope at delivery).",
     "glyphs": ["envelope"]},
    {"id": "store.generators", "path": ".claude/state/generators.json", "title": "Generator ledger",
     "description": "Law-1 anti-rot ledger: content hash of each generator's inputs/output, stamped "
                     "after it runs through the ritual.",
     "glyphs": ["derived"]},
    {"id": "store.dist", "path": ".claude/dist", "title": "distribution zips",
     "description": "The two release artifacts: dot-claude-iff-fresh.zip (new repo) and "
                     "dot-claude-iff-adopt-kit.zip (existing repo). Derived: rebuilt by every "
                     "ritual so a download can never lag the repo.",
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
]

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
    "Project-log.jsonl": "store.project_log",
    "LESSONS.jsonl": "store.lessons",
    "map.json": "store.map",
    "story-feed.json": "store.story",
    "anchor.json": "store.anchor",
    "rollups": "store.rollups",
    "heartbeat.json": "store.heartbeat",
    "memory-run.json": "store.memory_run",
    "handshakes": "store.handshakes",
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
    (store/human) card - see the KNOWN_STORES comment for why."""
    return _lib.sha256_file(path) or ""


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


def build_map(cards_by_id: dict, layers_cfg, errors: list, warnings: list) -> dict:
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
    }


def cmd_compile(_args) -> int:
    cards_by_id, malformed, dup = load_all_cards()
    layers_cfg = _lib.read_json(_lib.map_dir() / "layers.json", None)
    errors, warnings = compute_lint(cards_by_id, malformed, dup, layers_cfg)
    errors = errors + missing_card_errors(cards_by_id)

    map_obj = build_map(cards_by_id, layers_cfg, errors, warnings)

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


# --------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mapctl.py", description="The system map: scan / lint / compile / show.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="auto-discover components and create/refresh stub cards")
    sub.add_parser("lint", help="two-tier validation of the card set (ERROR fails, WARN informs)")
    sub.add_parser("compile", help="write .claude/system-map/map.json (write-gated)")
    sp = sub.add_parser("show", help="human-readable summary of the map or one card")
    sp.add_argument("--id", default=None)
    return p


COMMANDS = {"scan": cmd_scan, "lint": cmd_lint, "compile": cmd_compile, "show": cmd_show}


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
