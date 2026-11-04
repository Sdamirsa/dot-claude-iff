#!/usr/bin/env python3
"""graphctl.py - the code graph: files, classes, functions and the edges between them.

One slim graph, `.claude/system-map/code-graph.json` (gitignored, derived, never committed),
from one of two sources:

  graphify  `<RECORD_ROOT>/graph/graphify-out/graph.json`, when the HUMAN ran the extraction in
            their own terminal (`build` prints the exact, version-pinned command). graphctl
            only normalises that file: it never runs uvx or graphify, and never
            `graphify install` (whose PreToolUse hook would land in the protected tree).
  walker    otherwise, a stdlib walk of the shared project files (`ctxmap.project_files`):
            Python through `ast` (classes, functions, methods with line spans; imports that
            name an in-repo file; calls whose callee resolves in the same file or an imported
            in-repo module, confidence 0.6, every other call dropped) and `.sh` files through
            their `source` / `.` lines (to in-repo files, as imports).

THE SCHEMA (the wave-3 contract, `.claude/tasks/030b-00-milestone.md`):

  nodes  [{id, kind: file|class|function|method, label, path, line, end_line}]
  edges  [{from, to, type: contains|imports|calls|inherits, confidence}]
  stats  {nodes, edges, files, kinds{}, types{}}

Labels follow graphify's convention on both sources: a file by its filename, a class bare, a
function `name()`, a method `.name()`. `path` is repo-relative posix.

`architecture_scope(root)` collapses the graph to files and folders in the architecture
spec's node/edge shape (groups = folders, nodes kind file|folder, edges imports|calls with a
weight), test files excluded, `max_nodes` honoured by folding the lowest-degree files of a
folder into one "other" node; `mapctl architecture` calls it for every code scope.

    python3 .claude/tools/graphctl.py build    # normalise graphify's output, else walk
    python3 .claude/tools/graphctl.py update   # the same, without the extraction advice
    python3 .claude/tools/graphctl.py status   # counts, age, where graphify output was looked for

Stdlib only, like every core tool. Reads under the record root only in `graph/`, and never
writes there.
"""

from __future__ import annotations

import argparse
import ast
import os
import posixpath
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import ctxmap  # noqa: E402

SCHEMA_VERSION = 1
GRAPH_FILE = "code-graph.json"
NODE_KINDS = ("file", "class", "function", "method")
EDGE_TYPES = ("contains", "imports", "calls", "inherits")
CALL_CONFIDENCE = 0.6
DEFAULT_MAX_NODES = 40

# The pin used when console.json carries no `code_graph.graphify_version` (the version the
# research note measured). The knob is the source of truth; this only keeps a stripped config
# from printing an unpinned command.
GRAPHIFY_DEFAULT_VERSION = "0.9.102"
# graph.json's `graph.schema_version` values this normaliser was written against. Fields
# change between graphify releases, so anything else fails loudly instead of half-reading.
GRAPHIFY_SCHEMA_VERSIONS = (1,)
GRAPHIFY_EXCLUDES = (".claude/worktrees", ".claude-iff")
GRAPHIFY_RELATIONS = {
    "contains": "contains", "method": "contains", "defines": "contains",
    "imports": "imports", "imports_from": "imports",
    "calls": "calls", "indirect_call": "calls",
    "inherits": "inherits",
}
GRAPHIFY_CONFIDENCE = {"EXTRACTED": 1.0, "INFERRED": 0.8, "AMBIGUOUS": 0.5}
# A bare graphify label is a class only in a language that has classes; elsewhere (a shell
# script's env-var nodes, settings keys) it names something the slim schema has no kind for.
CLASS_LANGUAGES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".java", ".kt", ".go", ".rs",
                   ".cs", ".rb", ".swift", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".scala")

# On top of ctxmap's pruning: the committed record surface holds no code worth a node.
EXTRA_PRUNE_PREFIXES = (".claude-iff/",)
TEST_SEGMENTS = ("tests", "test")
EDGE_STYLE = {"imports": "solid", "calls": "dashed"}

_LINE_RE = re.compile(r"^L(\d+)")
_SCRIPT_DIR_RE = re.compile(
    r"^(?:\$\(\s*dirname\s+[^)]*\)|\$\{BASH_SOURCE(?:\[0\])?%/\*\}"
    r"|\$\{?(?:SCRIPT_DIR|SCRIPTDIR|HERE|DIR|HOOK_DIR|HOOKS_DIR)\}?)(/.*)$")
_PROJECT_DIR_RE = re.compile(r"^\$\{?CLAUDE_PROJECT_DIR\}?(/.*)$")


class GraphError(RuntimeError):
    """A graphify output graphctl cannot read faithfully: the build fails, nothing is written."""


# --------------------------------------------------------------------------- paths and knobs

def graph_path() -> Path:
    return _lib.map_dir() / GRAPH_FILE


def graphify_dir() -> Path:
    """`<RECORD_ROOT>/graph`: the --out folder of the human's extraction, the only part of the
    record this tool ever looks at."""
    return _lib.record_root() / "graph"


def graphify_output() -> Path:
    return graphify_dir() / "graphify-out" / "graph.json"


def graphify_version() -> str:
    value = _lib.config_get("console", "code_graph.graphify_version")
    return str(value).strip() if isinstance(value, str) and value.strip() else GRAPHIFY_DEFAULT_VERSION


def extract_command() -> str:
    """The exact extraction the human runs from the repo root, pinned: printed, never run."""
    parts = ["uvx", "--from", f"graphifyy=={graphify_version()}", "graphify", "extract", ".",
             "--code-only", "--out", graphify_dir().as_posix()]
    for pattern in GRAPHIFY_EXCLUDES:
        parts += ["--exclude", pattern]
    return " ".join(shlex.quote(p) for p in parts)


def _fold(text: str) -> str:
    return os.path.normcase(text).replace("\\", "/")


# --------------------------------------------------------------------------- shared shape

def _node(nid, kind, label, path, line=None, end_line=None) -> dict:
    return {"id": nid, "kind": kind, "label": label, "path": path,
            "line": line, "end_line": end_line}


class _Builder:
    """Nodes and edges with first-wins nodes and (from, to, type)-unique edges."""

    def __init__(self) -> None:
        self.nodes: dict = {}
        self.edges: dict = {}

    def add_node(self, node: dict) -> bool:
        if node["id"] in self.nodes:
            return False
        self.nodes[node["id"]] = node
        return True

    def add_edge(self, src, dst, etype, confidence) -> None:
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        key = (src, dst, etype)
        prior = self.edges.get(key)
        if prior is None or confidence > prior:
            self.edges[key] = confidence


def _assemble(builder: _Builder, source: str, graphify: dict) -> dict:
    nodes = sorted(builder.nodes.values(),
                   key=lambda n: (n["path"], n["line"] or 0, n["kind"] != "file", n["id"]))
    edges = [{"from": s, "to": d, "type": t, "confidence": round(float(c), 3)}
             for (s, d, t), c in sorted(builder.edges.items())]
    kinds = {k: 0 for k in NODE_KINDS}
    for n in nodes:
        kinds[n["kind"]] += 1
    types = {t: 0 for t in EDGE_TYPES}
    for e in edges:
        types[e["type"]] += 1
    return {
        "v": SCHEMA_VERSION,
        "generated_at": _lib.utc_now(),
        "source": source,
        "graphify": graphify,
        "nodes": nodes,
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges), "files": kinds["file"],
                  "kinds": kinds, "types": types},
    }


# --------------------------------------------------------------------------- the walker

def code_files(root: Path) -> list:
    """Repo-relative posix paths of the `.py` and `.sh` files the walker reads: the shared
    project (`git ls-files`, else a pruned walk), minus the committed record surface."""
    files, _source = ctxmap.project_files(root)
    out = []
    for rel in files:
        folded = _fold(rel)
        if not folded.endswith((".py", ".sh")):
            continue
        if any(folded.startswith(_fold(p)) for p in EXTRA_PRUNE_PREFIXES):
            continue
        if (root / rel).is_file():
            out.append(rel)
    return sorted(out)


def _child_blocks(stmt) -> list:
    """Statement lists nested in a compound statement that are still the same scope (a `def`
    under `if`/`try`/`with`/`for` at module level is a module-level def)."""
    blocks = []
    for name in ("body", "orelse", "finalbody"):
        value = getattr(stmt, name, None)
        if isinstance(value, list):
            blocks.append(value)
    for handler in getattr(stmt, "handlers", None) or []:
        blocks.append(handler.body)
    return blocks


class _PyModule:
    def __init__(self, path: str, tree) -> None:
        self.path = path
        self.tree = tree
        self.top: dict = {}          # module-level name -> node id (classes and functions)
        self.methods: dict = {}      # class node id -> {method name: node id}
        self.def_ids: dict = {}      # id(ast node) -> node id
        self.aliases: dict = {}      # dotted local name -> imported in-repo module path
        self.from_names: dict = {}   # local name -> (module path, original name)


def _py_symbols(mod: _PyModule, builder: _Builder) -> None:
    def visit(body, parent_id, parent_kind, prefix):
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                kind, label = "class", stmt.name
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if parent_kind == "class" else "function"
                label = ("." if kind == "method" else "") + f"{stmt.name}()"
            else:
                for block in _child_blocks(stmt):
                    visit(block, parent_id, parent_kind, prefix)
                continue
            qual = f"{prefix}.{stmt.name}" if prefix else stmt.name
            nid = f"{mod.path}::{qual}"
            if not builder.add_node(_node(nid, kind, label, mod.path, stmt.lineno,
                                          getattr(stmt, "end_lineno", None))):
                continue  # a redefinition: the first definition keeps the id
            mod.def_ids[id(stmt)] = nid
            builder.add_edge(parent_id, nid, "contains", 1.0)
            if parent_kind == "file":
                mod.top.setdefault(stmt.name, nid)
            if parent_kind == "class":
                mod.methods.setdefault(parent_id, {}).setdefault(stmt.name, nid)
            visit(stmt.body, nid, kind, qual)

    visit(mod.tree.body, mod.path, "file", "")


def _resolve_module(dotted: str, from_file: str, level: int, index: dict):
    """The in-repo file a module name points at, or None. Absolute names are tried from the
    importing file's folder up to the repo root (this repo's tools put their own folder on
    sys.path); relative ones from the package `level` folders up."""
    parts = [p for p in (dotted or "").split(".") if p]
    folder = posixpath.dirname(from_file)
    if level > 0:
        for _ in range(level - 1):
            folder = posixpath.dirname(folder)
        bases = [folder]
    else:
        bases, cur = [], folder
        while True:
            bases.append(cur)
            if not cur:
                break
            cur = posixpath.dirname(cur)
    for base in bases:
        stem = "/".join([p for p in [base, *parts] if p])
        candidates = [f"{stem}.py", f"{stem}/__init__.py"] if parts else [f"{stem}/__init__.py"]
        for cand in candidates:
            hit = index.get(_fold(cand.lstrip("/")))
            if hit:
                return hit
    return None


def _py_imports(mod: _PyModule, builder: _Builder, index: dict) -> None:
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_module(alias.name, mod.path, 0, index)
                if not target:
                    continue
                builder.add_edge(mod.path, target, "imports", 1.0)
                mod.aliases[alias.asname or alias.name] = target
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            target = _resolve_module(node.module or "", mod.path, level, index) \
                if node.module or level else None
            if target:
                builder.add_edge(mod.path, target, "imports", 1.0)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                sub_name = f"{node.module}.{alias.name}" if node.module else alias.name
                sub = _resolve_module(sub_name, mod.path, level, index)
                if sub:  # `from pkg import module`
                    builder.add_edge(mod.path, sub, "imports", 1.0)
                    mod.aliases[local] = sub
                elif target:
                    mod.from_names[local] = (target, alias.name)


def _dotted(expr):
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        return f"{head}.{expr.attr}" if head else None
    return None


def _resolve_ref(expr, mod: _PyModule, cls, modules: dict):
    """(node id, same_file) for a name used as a callee or a base class, or None when it does
    not resolve within this file or an imported in-repo module."""
    if isinstance(expr, ast.Name):
        if expr.id in mod.top:
            return mod.top[expr.id], True
        if expr.id in mod.from_names:
            path, name = mod.from_names[expr.id]
            other = modules.get(path)
            if other and name in other.top:
                return other.top[name], False
        return None
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        if head is None:
            return None
        if head in ("self", "cls") and cls and expr.attr in mod.methods.get(cls, {}):
            return mod.methods[cls][expr.attr], True
        if head in mod.aliases:
            other = modules.get(mod.aliases[head])
            if other and expr.attr in other.top:
                return other.top[expr.attr], False
        if head in mod.top and expr.attr in mod.methods.get(mod.top[head], {}):
            return mod.methods[mod.top[head]][expr.attr], True
    return None


def _py_refs(mod: _PyModule, builder: _Builder, modules: dict) -> None:
    def scan(node, caller, cls):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nid = mod.def_ids.get(id(child))
                if nid is None:
                    scan(child, caller, cls)
                    continue
                if isinstance(child, ast.ClassDef):
                    for base in child.bases:
                        hit = _resolve_ref(base, mod, cls, modules)
                        if hit:
                            builder.add_edge(nid, hit[0], "inherits",
                                             1.0 if hit[1] else CALL_CONFIDENCE)
                    scan(child, nid, nid)
                else:
                    scan(child, nid, cls)
                continue
            if isinstance(child, ast.Call):
                hit = _resolve_ref(child.func, mod, cls, modules)
                if hit:
                    builder.add_edge(caller, hit[0], "calls", CALL_CONFIDENCE)
            scan(child, caller, cls)

    scan(mod.tree, mod.path, None)


def _sh_target(token: str, script: str, index: dict):
    folder = posixpath.dirname(script)
    m = _SCRIPT_DIR_RE.match(token)
    p = _PROJECT_DIR_RE.match(token)
    if m:
        candidates = [posixpath.join(folder, m.group(1).lstrip("/"))]
    elif p:
        candidates = [p.group(1).lstrip("/")]
    elif "$" in token or "`" in token or token.startswith(("/", "~")):
        return None
    else:  # bash resolves a bare relative path against the cwd (hooks run at the repo root)
        candidates = [token, posixpath.join(folder, token)]
    for cand in candidates:
        norm = posixpath.normpath(cand)
        if norm.startswith("../") or norm == "..":
            continue
        hit = index.get(_fold(norm))
        if hit:
            return hit
    return None


def _sh_sources(path: str, text: str, builder: _Builder, index: dict) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("source ", "source\t", ". ", ".\t")):
            continue
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        if len(tokens) < 2 or tokens[0] not in ("source", "."):
            continue
        target = _sh_target(tokens[1], path, index)
        if target:
            builder.add_edge(path, target, "imports", 1.0)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def walk_graph(root: Path) -> _Builder:
    builder = _Builder()
    files = code_files(root)
    index = {_fold(f): f for f in files}
    modules: dict = {}
    shells = []
    for rel in files:
        text = _read(root / rel)
        builder.add_node(_node(rel, "file", posixpath.basename(rel), rel, 1,
                               max(1, len(text.splitlines()))))
        if _fold(rel).endswith(".sh"):
            shells.append((rel, text))
            continue
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError):
            continue  # unparseable: the file node stays, its symbols do not
        modules[rel] = _PyModule(rel, tree)
    for mod in modules.values():
        _py_symbols(mod, builder)
    for mod in modules.values():
        _py_imports(mod, builder, index)
    for mod in modules.values():
        _py_refs(mod, builder, modules)
    for rel, text in shells:
        _sh_sources(rel, text, builder, index)
    return builder


# --------------------------------------------------------------------------- graphify

def _repo_rel(source_file, root: Path):
    """graphify's `source_file` as a repo-relative posix path (it may be absolute or relative
    to the extraction root, which the printed command makes the repo root); None outside."""
    if not isinstance(source_file, str) or not source_file.strip():
        return None
    text = source_file.strip().replace("\\", "/")
    if Path(text).is_absolute() or re.match(r"^[A-Za-z]:/", text):
        base = _fold(root.resolve().as_posix()).rstrip("/") + "/"
        folded = _fold(text)
        if not folded.startswith(base):
            return None
        text = text[len(base):]
    norm = posixpath.normpath(text)
    if norm in (".", "") or norm.startswith("../") or norm == "..":
        return None
    return norm


def _graphify_kind(raw: dict, label: str, path: str):
    if label == posixpath.basename(path):
        return "file"
    if label.endswith("()"):
        return "method" if label.startswith(".") else "function"
    if raw.get("_callable_class"):  # graphify marks a class callable too: the class flag wins
        return "class"
    if raw.get("_callable"):
        return "function"
    if _fold(path).endswith(CLASS_LANGUAGES):
        return "class"
    return None


def _graphify_confidence(raw: dict) -> float:
    for key in ("confidence_score", "confidence"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(1.0, float(value)))
    return GRAPHIFY_CONFIDENCE.get(str(raw.get("confidence") or "").upper(), 1.0)


def normalise_graphify(data, root: Path) -> _Builder:
    """graphify's NetworkX node-link graph.json into the slim schema. Docstring (`rationale`)
    and external-module (`concept`) nodes are dropped, as is every relation the schema has no
    type for; a file with symbols but no file node gets one."""
    if not isinstance(data, dict):
        raise GraphError("graph.json is not a JSON object")
    meta = data.get("graph") if isinstance(data.get("graph"), dict) else {}
    version = meta.get("schema_version")
    if version not in GRAPHIFY_SCHEMA_VERSIONS:
        raise GraphError(
            f"graph.json schema_version {version!r} is not one graphctl reads "
            f"(supported: {', '.join(map(str, GRAPHIFY_SCHEMA_VERSIONS))}); the pin is "
            f"graphifyy=={graphify_version()}: rerun that version, or update graphctl's normaliser")
    raw_nodes = data.get("nodes")
    raw_edges = data.get("links") if "links" in data else data.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise GraphError("graph.json has no node list or no links/edges list")

    builder = _Builder()
    symbols = []
    for raw in raw_nodes:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        if raw.get("file_type") != "code" or raw.get("external") or raw.get("type") == "external":
            continue
        path = _repo_rel(raw.get("source_file"), root)
        if path is None:
            continue
        label = str(raw.get("label") or "")
        kind = _graphify_kind(raw, label, path)
        if kind is None:
            continue
        loc = _LINE_RE.match(str(raw.get("source_location") or ""))
        node = _node(str(raw["id"]), kind, label, path, int(loc.group(1)) if loc else None)
        if kind == "file":
            builder.add_node(node)
        else:
            symbols.append(node)
    file_ids = {n["path"]: n["id"] for n in builder.nodes.values()}
    for node in symbols:
        builder.add_node(node)
        if node["path"] not in file_ids:
            builder.add_node(_node(node["path"], "file", posixpath.basename(node["path"]),
                                   node["path"], 1))
            file_ids[node["path"]] = node["path"]
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        etype = GRAPHIFY_RELATIONS.get(str(raw.get("relation") or ""))
        if etype is None:
            continue
        builder.add_edge(str(raw.get("source")), str(raw.get("target")), etype,
                         _graphify_confidence(raw))
    return builder


# --------------------------------------------------------------------------- build / load

def build_graph(root: Path | None = None) -> dict:
    """The slim graph, in memory: graphify's output when it exists, else the walker. Raises
    GraphError on a graphify output it cannot read faithfully."""
    root = Path(root or _lib.project_root())
    out = graphify_output()
    found = out.is_file()
    info = {"found": found, "path": _lib.tilde(out), "version": graphify_version()}
    if found:
        data = _lib.read_json(out, None)
        if data is None:
            raise GraphError(f"{_lib.tilde(out)} is not readable JSON")
        return _assemble(normalise_graphify(data, root), "graphify", info)
    return _assemble(walk_graph(root), "walker", info)


def write_graph(graph: dict) -> Path:
    return _lib.atomic_write_json(graph_path(), graph)


def load_graph():
    """The written graph, or None when it is missing or not this schema."""
    data = _lib.read_json(graph_path(), None)
    if not isinstance(data, dict) or data.get("v") != SCHEMA_VERSION:
        return None
    if not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
        return None
    return data


# --------------------------------------------------------------------------- architecture

def _is_test(path: str) -> bool:
    parts = _fold(path).split("/")
    name = parts[-1]
    return any(seg in TEST_SEGMENTS for seg in parts[:-1]) or name.startswith("test_") \
        or name.endswith("_test.py")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "root"


def _unique(base: str, taken: set) -> str:
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    taken.add(out)
    return out


def _norm_root(root) -> str:
    text = str(root or ".").replace("\\", "/").strip()
    norm = posixpath.normpath(text) if text else "."
    return "" if norm in (".", "/") else norm.strip("/")


def architecture_scope(root, max_nodes: int = DEFAULT_MAX_NODES, graph: dict | None = None) -> dict:
    """The code graph under `root` (repo-relative), collapsed to files and folders in the
    architecture spec's shape: {groups, nodes, edges, stats}. Groups are the files' folders;
    nodes are files (kind `file`) and, past `max_nodes`, one "other" node per folder (kind
    `folder`) holding its lowest-degree files; edges are `imports`/`calls` between boxes,
    `weight` = the number of graph edges folded into one arrow, self-edges dropped. Test files
    are excluded, and so is any file no longer on disk, so every returned path exists.

    Pure over `graph`; with none given it loads code-graph.json, or builds the graph in memory
    (without writing it) when that file is missing."""
    if graph is None:
        graph = load_graph() or build_graph()
    project = _lib.project_root()
    scope = _norm_root(root)
    prefix = _fold(scope + "/") if scope else ""

    def in_scope(path) -> bool:
        return isinstance(path, str) and (not prefix or _fold(path).startswith(prefix))

    owner = {}       # graph node id -> file path
    files = set()
    for node in graph.get("nodes") or []:
        path = node.get("path")
        if not in_scope(path) or _is_test(path) or not (project / path).is_file():
            continue
        owner[node.get("id")] = path
        if node.get("kind") == "file":
            files.add(path)
    owner = {nid: p for nid, p in owner.items() if p in files}

    pairs: dict = {}
    for edge in graph.get("edges") or []:
        if edge.get("type") not in EDGE_STYLE:
            continue
        a, b = owner.get(edge.get("from")), owner.get(edge.get("to"))
        if a and b and a != b:
            key = (a, b, edge["type"])
            pairs[key] = pairs.get(key, 0) + 1
    degree = {f: 0 for f in files}
    for (a, b, _t), w in pairs.items():
        degree[a] += w
        degree[b] += w

    by_folder: dict = {}
    for f in sorted(files):
        by_folder.setdefault(posixpath.dirname(f), []).append(f)

    # Fold the lowest-degree file into its folder's "other" node until the count fits. The first
    # fold in a folder takes two files at once, or it would only swap a file for "other".
    folded: dict = {d: [] for d in by_folder}
    cap = max(1, int(max_nodes))

    def count() -> int:
        return sum(len(fs) - len(folded[d]) + (1 if folded[d] else 0) for d, fs in by_folder.items())

    while count() > cap:
        best = None
        for d, fs in by_folder.items():
            left = sorted((f for f in fs if f not in folded[d]), key=lambda f: (degree[f], f))
            need = 1 if folded[d] else 2
            if len(left) < need:
                continue
            pick = left[:need]
            rank = (max(degree[f] for f in pick), sum(degree[f] for f in pick), pick[0])
            if best is None or rank < best[0]:
                best = (rank, d, pick)
        if best is None:
            break  # more folders than the cap allows: nothing left that shrinks the count
        folded[best[1]].extend(best[2])

    taken: set = set()
    groups, nodes, box = [], [], {}
    for d in sorted(by_folder):
        rel_dir = d[len(scope):].lstrip("/") if scope and _fold(d).startswith(_fold(scope)) else d
        gid = _unique(f"dir.{_slug(rel_dir)}", taken)
        kept = [f for f in by_folder[d] if f not in folded[d]]
        groups.append({"id": gid, "title": d or ".",
                       "blurb": f"{len(by_folder[d])} file(s) in {d or 'the repo root'}"})
        for f in kept:
            rel = f[len(scope):].lstrip("/") if scope else f
            nid = _unique(f"file.{_slug(rel)}", taken)
            box[f] = nid
            nodes.append({"id": nid, "group": gid, "label": posixpath.basename(f),
                          "path": f, "kind": "file"})
        if folded[d]:
            nid = _unique(f"other.{_slug(rel_dir)}", taken)
            for f in folded[d]:
                box[f] = nid
            nodes.append({"id": nid, "group": gid, "label": f"other ({len(folded[d])} files)",
                          "path": d or ".", "kind": "folder"})

    arrows: dict = {}
    for (a, b, etype), w in pairs.items():
        src, dst = box[a], box[b]
        if src != dst:
            arrows[(src, dst, etype)] = arrows.get((src, dst, etype), 0) + w
    edges = [{"from": s, "to": d, "label": t, "style": EDGE_STYLE[t], "weight": w}
             for (s, d, t), w in sorted(arrows.items())]
    return {"groups": groups, "nodes": nodes, "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      "folded": sum(len(v) for v in folded.values())}}


# --------------------------------------------------------------------------- cli

def _ago(seconds) -> str:
    if seconds is None:
        return "at an unknown time"
    s = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return f"{s}s ago"


def _counts(graph: dict) -> str:
    st = graph.get("stats") or {}
    return (f"{st.get('nodes', len(graph.get('nodes') or []))} nodes, "
            f"{st.get('edges', len(graph.get('edges') or []))} edges, {st.get('files', '?')} files")


def _advice() -> list:
    return [f"  WARN   no graphify output at {_lib.tilde(graphify_output())}: the stdlib "
            f"walker built this graph. For graphify's graph, run in your own terminal, from the "
            f"repo root:",
            f"           {extract_command()}",
            "         then `python3 .claude/tools/graphctl.py update`. graphctl never runs uvx or "
            "graphify itself, and never `graphify install`."]


def cmd_build(args) -> int:
    verb = args.command.upper()
    try:
        graph = build_graph()
    except GraphError as exc:
        print(f"GRAPH {verb}: graphify output unreadable, nothing written")
        print(f"  FAIL   {exc}")
        _lib.print_verdict("GRAPH", False)
        return 1
    path = write_graph(graph)
    where = f" ({graph['graphify']['path']})" if graph["source"] == "graphify" else ""
    print(f"GRAPH {verb}: source {graph['source']}{where}, {_counts(graph)} -> {_lib.rel(path)}")
    warn = graph["source"] != "graphify" and args.command == "build"
    if warn:
        print("\n".join(_advice()))
    _lib.print_verdict("GRAPH", True, warn=warn)
    return 0


def cmd_status(_args) -> int:
    graph = load_graph()
    out = graphify_output()
    print("GRAPH STATUS")
    if graph is None:
        print(f"  graph      not built: run `python3 .claude/tools/graphctl.py build` in your "
              f"terminal ({_lib.rel(graph_path())})")
    else:
        print(f"  graph      {_lib.rel(graph_path())}: {_counts(graph)}, source "
              f"{graph.get('source', '?')}, built {_ago(_lib.age_seconds(graph.get('generated_at')))}")
    state = "found" if out.is_file() else "not found"
    print(f"  graphify   output {state} at {_lib.tilde(out)}")
    if not out.is_file():
        print(f"             to extract, run in your terminal: {extract_command()}")
    print(f"  pin        graphifyy=={graphify_version()} (console.json code_graph.graphify_version)")
    _lib.print_verdict("GRAPH", True, warn=graph is None)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="graphctl.py", description="The code graph: build / update / status.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="normalise graphify's output, else walk; print the extraction command")
    sub.add_parser("update", help="re-normalise an existing graphify output, or rerun the walker")
    sub.add_parser("status", help="node and edge counts, age, whether graphify output was found")
    return p


COMMANDS = {"build": cmd_build, "update": cmd_build, "status": cmd_status}


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
