#!/usr/bin/env python3
"""ctxmap.py - folder context: which guides and rules Claude Code loads for a file, and whether
that context is healthy.

The engine behind `mapctl context`, the `context_health` CHECK step and the console's Context
list. One model, written once here, so the three can never disagree about what loads.

THE MODEL (Claude Code's documented behaviour, project scope only):

  guides   CLAUDE.md and CLAUDE.local.md. The ROOT guide is .claude/CLAUDE.md or ./CLAUDE.md
           (exactly one, by this system's convention); ./CLAUDE.local.md is the personal
           overlay at the root. Root guides load at launch. A FOLDER guide (one in a subfolder)
           loads on demand, when a file under that folder is read. Within one folder
           CLAUDE.local.md follows CLAUDE.md; across folders the outermost comes first.
  imports  `@path` inside a guide, outside code spans and fenced blocks, resolved relative to
           the importing file and loaded with it, at most four hops deep. A quoted path is not
           an import; `\\ ` escapes a space.
  rules    .claude/rules/**/*.md. Without `paths:` frontmatter a rule is always on (launch).
           With `paths:` it loads when a matching file is read or written. Frontmatter that
           does not parse makes Claude Code ignore it and load the rule for EVERY file - almost
           never what the author meant, so it is reported.

Out of scope: user-level (~/.claude) and managed guides, guides above the project root,
AGENTS.md, nested .claude/rules/ folders, and guides inside .claude/ other than the root guide
(that tree is the system's own; its conventions live in the root guide). Line counts are raw
file lines: the number a human edits, and the one the size target is stated in.

Discovery is the SHARED project: `git ls-files` (tracked plus untracked-not-ignored), or a
pruned walk outside git, never descending into .git, .claude/worktrees, node_modules,
virtualenvs or the record folder. A gitignored CLAUDE.local.md is personal: it never enters
the committed map, but `mapctl context <path>` still lists it (flagged) because it does load.

Stdlib only, like every core tool.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

# --------------------------------------------------------------------------- model constants

GUIDE_NAMES = ("CLAUDE.md", "CLAUDE.local.md")    # load order within one folder
ROOT_GUIDES = ("CLAUDE.md", ".claude/CLAUDE.md")  # the primary root guide: exactly one
ROOT_LOCAL = "CLAUDE.local.md"
RULES_PREFIX = ".claude/rules/"
MAX_IMPORT_HOPS = 4
BRACE_BUDGET = 1000          # expanded patterns per rule, as Claude Code budgets them
SUGGEST_MIN_ENTRIES = 2      # "2+ lessons or mistakes in one subtree"
DEFAULT_SIZE_TARGET = 200    # lines per guide or rule (Claude Code's own guidance)
DEFAULT_ALWAYS_ON_BUDGET = 400

PRUNE_SEGMENTS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
                            ".mypy_cache", ".pytest_cache", ".ruff_cache"})
PRUNE_PREFIXES = (".claude/worktrees/",)

# The one shape that crosses into map.json and the console payload (lesson L-4: a shape that
# crosses a tool boundary is a named contract in the producer, never re-specified by readers).
CONTEXT_CONTRACT = (
    "v", "root_guide", "always_on_lines", "always_on_budget", "size_target",
    "counts.guides", "counts.rules", "counts.imports", "findings.fail", "findings.warn",
    "entries[].path", "entries[].kind", "entries[].scope", "entries[].loads",
    "entries[].lines", "entries[].findings", "entries[].via",
)
ENTRY_FIELDS = ("path", "kind", "scope", "loads", "lines", "findings", "via")


def empty_section() -> dict:
    return {"v": 1, "root_guide": None, "always_on_lines": 0, "always_on_budget": None,
            "size_target": None, "counts": {"guides": 0, "rules": 0, "imports": 0},
            "findings": {"fail": 0, "warn": 0}, "entries": []}


# --------------------------------------------------------------------------- case and paths

# Case handling follows os.path.normcase (L-9): on a platform whose filesystem folds case both
# sides of every comparison are lower-cased. normcase itself is not used directly because on
# Windows it also rewrites "/" to "\", which would break glob escapes and posix splitting.
_CASE_FOLDS = os.path.normcase("Aa") == "aa"


def fold(text: str) -> str:
    return text.lower() if _CASE_FOLDS else text


def _parent(rel: str) -> str:
    return rel.rpartition("/")[0]


def _basename(rel: str) -> str:
    return rel.rpartition("/")[2]


def _inside_claude(rel_dir: str) -> bool:
    f = fold(rel_dir)
    return f == ".claude" or f.startswith(".claude/")


def _ancestors(rel_dir: str) -> list:
    """'a/b/c' -> ['a', 'a/b', 'a/b/c'] (outermost first); '' -> []."""
    if not rel_dir:
        return []
    parts = rel_dir.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _norm_rel(text: str) -> str:
    rel = text.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


# --------------------------------------------------------------------------- glob matcher
#
# Semantics: patterns are anchored at the project root ("*.md" is root-level markdown only);
# "**" as a whole segment spans any number of folders including none; "*" and "?" stay inside
# one segment; "[abc]" / "[a-z]" / "[!a]" are bracket expressions (one that never closes makes
# the pattern match nothing, as Claude Code documents); "\x" escapes x; "{a,b}" alternatives
# nest and multiply, within a per-rule budget past which a pattern stays unexpanded (its
# literal braces then match nothing). A leading "./" or "/" is dropped; a trailing "/" means
# "everything under". Dotfiles are matched like any other name: inclusive, so a rule is never
# reported as matching nothing because of a hidden-file convention.

def expand_braces(pattern: str) -> list:
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth, j, commas = 0, i, []
            while j < n:
                cj = pattern[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "{":
                    depth += 1
                elif cj == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif cj == "," and depth == 1:
                    commas.append(j)
                j += 1
            if j >= n:
                return [pattern]  # never closes: the rest is literal
            if commas:
                head, tail = pattern[:i], pattern[j + 1:]
                bounds = [i] + commas + [j]
                out = []
                for a, b in zip(bounds, bounds[1:]):
                    out.extend(expand_braces(head + pattern[a + 1:b] + tail))
                return out
        i += 1
    return [pattern]


def _segment_regex(seg: str):
    out, i, n = [], 0, len(seg)
    while i < n:
        c = seg[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(seg[i + 1]))
            i += 2
        elif c == "*":
            while i < n and seg[i] == "*":
                i += 1
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and seg[j] in "!^":
                j += 1
            if j < n and seg[j] == "]":
                j += 1
            while j < n and seg[j] != "]":
                j += 1
            if j >= n:
                return None  # a bracket that never closes: the pattern is invalid
            body = seg[i + 1:j]
            negate = body[:1] in ("!", "^")
            if negate:
                body = body[1:]
            body = "".join("\\" + ch if ch in "\\[]^" else ch for ch in body)
            out.append("[^/" + body + "]" if negate else "[" + body + "]")
            i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def compile_glob(pattern: str):
    """One brace-free pattern -> compiled regex over folded posix paths, or None (invalid)."""
    p = pattern.strip()  # never _norm_rel: a backslash here is an escape, not a separator
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if p.endswith("/"):
        p += "**"
    segs = [s for s in fold(p).split("/") if s]
    if not segs:
        return None
    pieces = []
    for idx, seg in enumerate(segs):
        last = idx == len(segs) - 1
        if seg == "**":
            pieces.append(".*" if last else "(?:[^/]+/)*")
            continue
        rx = _segment_regex(seg)
        if rx is None:
            return None
        pieces.append(rx if last else rx + "/")
    return re.compile("".join(pieces) + r"\Z", re.DOTALL)


def compile_globs(patterns) -> list:
    compiled, budget = [], BRACE_BUDGET
    for pattern in patterns:
        expanded = expand_braces(pattern)
        if len(expanded) > 1:
            if len(expanded) > budget:
                expanded = [pattern]  # over budget: used unexpanded, literal braces
            else:
                budget -= len(expanded)
        for p in expanded:
            rx = compile_glob(p)
            if rx is not None:
                compiled.append(rx)
    return compiled


def glob_match(patterns, rel_path: str) -> bool:
    target = fold(_norm_rel(rel_path))
    return any(rx.match(target) for rx in compile_globs(patterns))


# --------------------------------------------------------------------------- frontmatter
#
# A deliberately small reader for one key. It accepts `paths:` as a string (comma-separated
# patterns allowed), an inline `[a, b]` list, or a `- item` block list, quoted or not, and it
# ignores every other key the way Claude Code does. Anything it cannot read with certainty is
# "unparseable", never guessed - and it mirrors the YAML mistakes that really break parsing:
# an unquoted value starting with `*` (an alias), `@` or a backtick (reserved), braces inside
# an unquoted inline-list item, a tab used for indentation, a ": " inside a plain value.

_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)[ \t]*:(?:[ \t]+(.*))?$")


def _split_top_commas(text: str) -> list:
    out, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == "," and depth <= 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [s.strip() for s in out]


def _scalar(text: str, flow: bool = False):
    """One YAML scalar -> (value, None) or (None, why-it-cannot-parse)."""
    s = text.strip()
    if not s:
        return "", None
    if s[0] in "\"'":
        q = s[0]
        i, buf = 1, []
        while i < len(s):
            c = s[i]
            if q == '"' and c == "\\" and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                rest = s[i + 1:].strip()
                if rest and not rest.startswith("#"):
                    return None, f"text after a closing quote: {rest!r}"
                return "".join(buf), None
            buf.append(c)
            i += 1
        return None, "an unclosed quote"
    if " #" in s:
        s = s.split(" #", 1)[0].rstrip()
    if s[0] in "*@`%":
        return None, f"an unquoted value starting with {s[0]!r} (quote the pattern)"
    if s[0] in "&!|>{[":
        return None, f"a value starting with {s[0]!r}, which is not a plain string"
    if s == "-" or s.startswith(("- ", "-\t")):
        return None, "a list item on the key's own line"
    if ": " in s or s.endswith(":"):
        return None, "': ' inside an unquoted value"
    if flow and any(c in s for c in "{}[]"):
        return None, "braces or brackets in an unquoted inline-list item (quote it)"
    return s, None


def _inline_list(text: str):
    m = re.match(r"^(\[.*\])\s*(?:#.*)?$", text.strip())
    if not m:
        return None, "an inline list that does not close on its line"
    s = m.group(1)
    items, buf, quote, depth = [], [], None, 0
    for c in s[1:-1]:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == "," and depth == 0:
            items.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    items.append("".join(buf))
    out = []
    for raw in items:
        if not raw.strip():
            continue
        value, why = _scalar(raw, flow=True)
        if why:
            return None, why
        out.append(value)
    return out, None


def read_frontmatter(text: str):
    """-> (state, globs, reason). state: 'none' (no frontmatter, or no `paths:`) | 'scoped' |
    'unparseable'. For 'unparseable', reason says why; Claude Code then loads the rule for
    every file."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return "none", [], ""
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return "unparseable", [], "the frontmatter never closes with ---"

    def bad(why):
        return "unparseable", [], why

    key = None
    block = False     # current key opened a block (empty inline value)
    seen_paths = False
    raw_paths = None  # list of patterns once read
    for n, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] == "\t":
            return bad(f"line {n} is indented with a tab")
        stripped = line.strip()
        is_item = stripped == "-" or stripped.startswith(("- ", "-\t"))
        if is_item and block:
            if key == "paths":
                value, why = _scalar(stripped[1:])
                if why:
                    return bad(f"line {n}: {why}")
                raw_paths.append(value)
            continue
        if line[:1] == " ":
            if key == "paths":
                return bad(f"line {n}: an indented line under paths: that is not a - item")
            continue  # continuation of another key's value; Claude Code ignores that key
        m = _KEY_RE.match(line)
        if not m:
            return bad(f"line {n} is not `key: value`")
        key, value = m.group(1), (m.group(2) or "").strip()
        if value.startswith("#"):
            value = ""
        block = value == ""
        if key != "paths":
            if value and value[0] not in "|>[{&!":
                _, why = _scalar(value)
                if why:
                    return bad(f"line {n} ({key}): {why}")
            continue
        if seen_paths:
            return bad("paths: appears twice")
        seen_paths = True
        if block:
            raw_paths = []
        elif value.startswith("["):
            items, why = _inline_list(value)
            if why:
                return bad(f"line {n}: {why}")
            raw_paths = items
        else:
            scalar, why = _scalar(value)
            if why:
                return bad(f"line {n}: {why}")
            if scalar in ("null", "~"):
                return bad("paths: is null")
            raw_paths = _split_top_commas(scalar)
    if not seen_paths:
        return "none", [], ""
    globs = [g.strip() for g in raw_paths or [] if g and g.strip()]
    if not globs:
        return bad("paths: is empty")
    return "scoped", globs, ""


# --------------------------------------------------------------------------- markdown scanning

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _prose_and_spans(text: str):
    """-> (prose lines with code spans blanked and fenced blocks dropped, inline code spans)."""
    prose, spans, fence = [], [], None
    for line in text.splitlines():
        m = _FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line.strip()[len(m.group(1)):].strip():
                fence = None
            prose.append("")
            continue
        if m:
            fence = m.group(1)
            prose.append("")
            continue
        spans.extend(sm.group(2).strip() for sm in _SPAN_RE.finditer(line))
        prose.append(_SPAN_RE.sub(lambda sm: " " * len(sm.group(0)), line))
    return prose, spans


_IMPORT_RE = re.compile(r"(?:^|(?<=[\s(\[]))@((?:\\ |[^\s])+)")
_TRAILING_PUNCT = ".,;:!?)]}"


def import_tokens(text: str) -> list:
    prose, _spans = _prose_and_spans(text)
    out = []
    for line in prose:
        for m in _IMPORT_RE.finditer(line):
            token = m.group(1)
            if token[0] in "\"'":
                continue  # a quoted path is not imported at all
            token = token.replace("\\ ", " ").rstrip(_TRAILING_PUNCT)
            if token:
                out.append(token)
    return out


def _pathlike(token: str) -> bool:
    return ("/" in token or token.startswith((".", "~"))
            or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", token)))


# --------------------------------------------------------------------------- the index

@dataclass
class ContextFile:
    path: str             # repo-relative posix (an import outside the project: as written)
    kind: str             # guide | rule | import
    scope: str            # root | folder | always | scoped | unparseable | import
    lines: int
    text: str = field(default="", repr=False)
    folder: str = ""      # folder guides: the folder they govern
    globs: list = field(default_factory=list)
    reason: str = ""      # unparseable rules: why
    local: bool = False   # a CLAUDE.local.md
    personal: bool = False  # on disk but not in the shared index (gitignored)


@dataclass
class Import:
    importer: str
    token: str
    path: str | None      # repo-relative, when inside the project
    depth: int            # hops from the guide the walk started at
    external: bool = False
    exists: bool = False

    @property
    def too_deep(self) -> bool:
        return self.depth > MAX_IMPORT_HOPS


@dataclass
class Index:
    root: Path
    files: list
    source: str                       # "git" | "walk"
    root_guides: list = field(default_factory=list)
    folder_guides: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    _imports: dict = field(default_factory=dict, repr=False)
    _loaded: dict = field(default_factory=dict, repr=False)
    _lookup: tuple = field(default=(), repr=False)

    def guides(self) -> list:
        return self.root_guides + self.folder_guides

    def lookups(self) -> tuple:
        """(folded file -> file, folded dir -> dir, folded basename -> [files])."""
        if not self._lookup:
            files = {fold(f): f for f in self.files}
            dirs, base = {}, {}
            for f in self.files:
                for d in _ancestors(_parent(f)):
                    dirs.setdefault(fold(d), d)
                base.setdefault(fold(_basename(f)), []).append(f)
            self._lookup = (files, dirs, base)
        return self._lookup

    def imports(self, guide: ContextFile) -> list:
        """Every `@import` reachable from `guide`, pre-order, each file once. Loaded ones carry
        exists=True and depth <= MAX_IMPORT_HOPS; the rest are findings, not context."""
        key = fold(guide.path)
        if key not in self._imports:
            self._imports[key] = _walk_imports(self.root, guide)
        return self._imports[key]

    def loaded_imports(self, guide: ContextFile) -> list:
        key = fold(guide.path)
        if key not in self._loaded:
            out = []
            for imp in self.imports(guide):
                if imp.exists and not imp.external and not imp.too_deep:
                    text = _read(self.root / imp.path)
                    out.append(ContextFile(imp.path, "import", "import", _count_lines(text), text))
            self._loaded[key] = out
        return self._loaded[key]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _count_lines(text: str) -> int:
    return len(text.splitlines())


def _resolve_import(root: Path, importer: str, token: str, depth: int):
    if token.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", token) or token.startswith("/"):
        absolute = Path(token).expanduser()
        try:
            rel = absolute.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return Import(importer, token, None, depth, external=True,
                          exists=absolute.is_file())
    else:
        joined = os.path.normpath(os.path.join(_parent(importer), token)).replace("\\", "/")
        if joined == ".." or joined.startswith("../"):
            return Import(importer, token, None, depth, external=True,
                          exists=(root / _parent(importer) / token).is_file())
        rel = joined
    exists = (root / rel).is_file()
    if not exists and not _pathlike(token):
        return None  # "@alice" in prose is a mention, not a broken import
    return Import(importer, token, rel, depth, exists=exists)


def _walk_imports(root: Path, guide: ContextFile) -> list:
    out, seen = [], {fold(guide.path)}

    def visit(path: str, text: str, depth: int) -> None:
        for token in import_tokens(text):
            imp = _resolve_import(root, path, token, depth + 1)
            if imp is None:
                continue
            if imp.path is not None:
                if fold(imp.path) in seen:
                    continue
                seen.add(fold(imp.path))
            out.append(imp)
            if imp.exists and not imp.external and not imp.too_deep:
                visit(imp.path, _read(root / imp.path), depth + 1)

    visit(guide.path, guide.text, 0)
    return out


def _prune_prefixes(root: Path) -> tuple:
    prefixes = list(PRUNE_PREFIXES)
    try:
        rel = _lib.record_root().resolve().relative_to(root.resolve()).as_posix()
        if rel and rel != ".":
            prefixes.append(rel.rstrip("/") + "/")
    except (ValueError, OSError):
        pass  # the usual case: the record is a sibling folder, outside the project
    return tuple(fold(p) for p in prefixes)


def _pruned(rel: str, prefixes: tuple) -> bool:
    f = fold(rel)
    if any(seg in _PRUNE_FOLDED for seg in f.split("/")):
        return True
    return any(f.startswith(p) or (f + "/") == p for p in prefixes)


_PRUNE_FOLDED = frozenset(fold(s) for s in PRUNE_SEGMENTS)


def _git_files(root: Path):
    """Tracked plus untracked-not-ignored, or None outside git. One process, no shell."""
    try:
        res = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root), capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    text = res.stdout.decode("utf-8", errors="replace")
    return [p for p in text.split("\0") if p and not p.endswith("/")]


def _walk_files(root: Path, prefixes: tuple) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if _pruned(rel + "/", prefixes) or (Path(dirpath) / d / "pyvenv.cfg").exists():
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        out.extend(f"{rel_dir}/{f}" if rel_dir else f for f in filenames)
    return out


def project_files(root: Path):
    prefixes = _prune_prefixes(root)
    files, source = _git_files(root), "git"
    if files is None:
        files, source = _walk_files(root, prefixes), "walk"
    return sorted({f for f in files if not _pruned(f, prefixes)}), source


def _load(root: Path, rel: str, kind: str, scope: str, folder: str = "") -> ContextFile | None:
    path = root / rel
    if not path.is_file():
        return None  # deleted on disk but still in the git index
    text = _read(path)
    cf = ContextFile(rel, kind, scope, _count_lines(text), text, folder=folder,
                     local=fold(_basename(rel)) == fold(ROOT_LOCAL))
    if kind == "rule":
        state, globs, reason = read_frontmatter(text)
        cf.scope = {"none": "always", "scoped": "scoped", "unparseable": "unparseable"}[state]
        cf.globs, cf.reason = globs, reason
    return cf


def discover(root: Path | None = None) -> Index:
    root = Path(root or _lib.project_root())
    files, source = project_files(root)
    index = Index(root=root, files=files, source=source)
    by_fold = {fold(f): f for f in files}
    guide_folds = {fold(n) for n in GUIDE_NAMES}

    for name in (*ROOT_GUIDES, ROOT_LOCAL):
        rel = by_fold.get(fold(name))
        cf = _load(root, rel, "guide", "root") if rel else None
        if cf:
            index.root_guides.append(cf)

    for rel in files:
        folder = _parent(rel)
        if not folder or _inside_claude(folder) or fold(_basename(rel)) not in guide_folds:
            continue
        cf = _load(root, rel, "guide", "folder", folder=folder)
        if cf:
            index.folder_guides.append(cf)
    index.folder_guides.sort(key=lambda g: (fold(g.folder), g.local, fold(g.path)))

    for rel in files:
        f = fold(rel)
        if f.startswith(fold(RULES_PREFIX)) and f.endswith(".md"):
            cf = _load(root, rel, "rule", "always")
            if cf:
                index.rules.append(cf)
    return index


def context_input_paths(index: Index | None = None) -> list:
    """The files a context-aware generator's output depends on: guides, rules and their loaded
    imports. Guides can live in any folder, so this is discovered, never declared."""
    index = index or discover()
    paths = [cf.path for cf in index.guides() + index.rules]
    for g in index.guides():
        paths += [imp.path for imp in index.loaded_imports(g)]
    return sorted(set(paths))


# --------------------------------------------------------------------------- settings

def settings() -> dict:
    cfg = (_lib.load_config("memory") or {}).get("context") or {}

    def as_int(key, default):
        try:
            return max(1, int(cfg.get(key, default)))
        except (TypeError, ValueError):
            return default
    return {"size_target": as_int("size_target_lines", DEFAULT_SIZE_TARGET),
            "always_on_budget": as_int("always_on_budget_lines", DEFAULT_ALWAYS_ON_BUDGET)}


# --------------------------------------------------------------------------- load order

def _launch_set(index: Index) -> list:
    """(ContextFile, via) pairs that load at launch: root guides with their imports, then rules
    without (or with unparseable) `paths:`."""
    out = []
    for g in index.root_guides:
        out.append((g, None))
        out += [(imp, g.path) for imp in index.loaded_imports(g)]
    out += [(r, None) for r in index.rules if r.scope in ("always", "unparseable")]
    return out


def always_on_lines(index: Index) -> int:
    seen, total = set(), 0
    for cf, _via in _launch_set(index):
        if fold(cf.path) not in seen:
            seen.add(fold(cf.path))
            total += cf.lines
    return total


def _reason(cf: ContextFile, via) -> str:
    if cf.kind == "import":
        return f"imported by {via}"
    if cf.kind == "guide" and cf.scope == "root":
        return ("personal overlay of the root guide, loads at launch" if cf.local
                else "root guide, loads at launch")
    if cf.kind == "guide":
        return f"folder guide for {cf.folder}/, loads when a file under it is read"
    if cf.scope == "unparseable":
        return f"frontmatter does not parse ({cf.reason}): loads for every file"
    if cf.scope == "always":
        return "rule without paths:, loads at launch"
    return "paths: " + ", ".join(cf.globs)


def _scope_label(cf: ContextFile) -> str:
    if cf.kind == "guide":
        return ("root (local)" if cf.local else "root") if cf.scope == "root" else cf.folder + "/"
    if cf.kind == "import":
        return "import"
    if cf.scope == "unparseable":
        return "always (frontmatter unparseable)"
    if cf.scope == "always":
        return "always"
    return ", ".join(cf.globs)


def resolve(target: str, index: Index | None = None) -> dict:
    """The ordered chain of guides and rules that load for one repo-relative path."""
    index = index or discover()
    root = index.root
    rel = _norm_rel(target).rstrip("/") if target not in ("", ".", "./") else ""
    is_dir = (target.endswith("/") or (root / rel).is_dir()) if rel else True
    chain, notes, seen = [], [], set()
    shared = {fold(f) for f in index.files}

    def add(cf: ContextFile, loads: str, reason: str, via=None) -> bool:
        if fold(cf.path) in seen:
            return False
        seen.add(fold(cf.path))
        chain.append({"order": len(chain) + 1, "path": cf.path, "kind": cf.kind,
                      "loads": loads, "lines": cf.lines, "reason": reason, "via": via,
                      "personal": cf.personal})
        return True

    def add_guide(g: ContextFile, loads: str) -> None:
        if not add(g, loads, _reason(g, None)):
            return
        for imp in index.imports(g):
            if imp.external:
                notes.append(f"{imp.importer} imports {imp.token} from outside the project "
                             f"(not counted)")
            elif imp.too_deep:
                notes.append(f"{imp.importer} imports {imp.token} at hop {imp.depth}: past "
                             f"{MAX_IMPORT_HOPS}, so it never loads")
            elif not imp.exists:
                notes.append(f"{imp.importer} imports {imp.token}, which does not exist")
        for cf in index.loaded_imports(g):
            add(cf, loads, _reason(cf, _importer_of(index, g, cf.path)),
                via=_importer_of(index, g, cf.path))

    def personal(folder: str, name: str):
        rel_path = f"{folder}/{name}" if folder else name
        if fold(rel_path) in shared or not (root / rel_path).is_file():
            return None
        cf = _load(root, rel_path, "guide", "root" if not folder else "folder", folder=folder)
        if cf:
            cf.personal = True
        return cf

    for g in index.root_guides:
        add_guide(g, "launch")
    extra = personal("", ROOT_LOCAL)
    if extra:
        add_guide(extra, "launch")
    for r in index.rules:
        if r.scope in ("always", "unparseable"):
            add(r, "launch", _reason(r, None))

    folders = _ancestors(rel if is_dir else _parent(rel))
    by_folder = {}
    for g in index.folder_guides:
        by_folder.setdefault(fold(g.folder), []).append(g)
    for folder in folders:
        if _inside_claude(folder):
            continue
        present = {fold(_basename(g.path)): g for g in by_folder.get(fold(folder), [])}
        for name in GUIDE_NAMES:
            g = present.get(fold(name)) or personal(folder, name)
            if g:
                add_guide(g, "on-read")

    if is_dir:
        if any(r.scope == "scoped" for r in index.rules):
            notes.append("a folder was given: scoped rules match files, so none are listed")
    else:
        for r in index.rules:
            if r.scope == "scoped":
                hit = next((g for g in r.globs if glob_match([g], rel)), None)
                if hit:
                    add(r, "on-match", f"paths: {hit} matches")

    for item in chain:
        if item["personal"]:
            item["reason"] += " (gitignored: loads for you, not in the shared map)"
    return {"path": rel, "exists": (root / rel).exists() if rel else True,
            "chain": chain, "total_lines": sum(i["lines"] for i in chain),
            "always_on_lines": sum(i["lines"] for i in chain if i["loads"] == "launch"),
            "notes": notes}


def _importer_of(index: Index, guide: ContextFile, path: str) -> str:
    for imp in index.imports(guide):
        if imp.path is not None and fold(imp.path) == fold(path):
            return imp.importer
    return guide.path


# --------------------------------------------------------------------------- health

@dataclass
class Finding:
    level: str    # FAIL | WARN
    code: str
    path: str     # the context file the finding is about
    message: str

    def line(self) -> str:
        return f"{self.level} {self.path}: {self.message}"


_KNOWN_EXT = frozenset(
    "md markdown py pyi sh bash zsh ps1 bat cmd json jsonl js mjs cjs ts tsx jsx toml yaml "
    "yml txt html htm css scss cfg ini lock csv tsv ipynb rs go java kt rb php sql xml svg "
    "png jpg jpeg gif pdf zip".split())
_NOT_IN_A_PATH = set(" \t*?[]{}<>$|;=(),'\"@!^%&+:`\\")


def _looks_like_path(candidate: str) -> bool:
    """Conservative on purpose: a false 'stale path' warning on every ritual teaches people to
    ignore the check. A path needs a slash or a known extension; globs, placeholders, commands,
    URLs, absolute and home paths, flags and code are never paths."""
    c = candidate
    if not c or len(c) > 240 or "://" in c or "..." in c:
        return False
    if c.startswith(("www.", "/", "~", "-")) or re.match(r"^[A-Za-z]:", c):
        return False
    if any(ch in _NOT_IN_A_PATH for ch in c):
        return False
    rel = _norm_rel(c)
    core = rel.rstrip("/")
    if not core or core in (".", ".."):
        return False
    if any(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", seg) for seg in core.split("/")):
        return False  # YYYYMMDD-style placeholder segments
    if "/" in rel:
        return True  # a trailing slash ("config/") names a folder, which is a path too
    stem, dot, ext = core.rpartition(".")
    return bool(stem and dot and ext.lower() in _KNOWN_EXT)


def backticked_paths(text: str) -> list:
    _prose, spans = _prose_and_spans(text)
    out = []
    for span in spans:
        c = span.split("#", 1)[0] if not span.startswith("#") else span
        if _looks_like_path(c) and c not in out:
            out.append(c)
    return out


def _path_exists(index: Index, cf: ContextFile, candidate: str) -> bool:
    core = _norm_rel(candidate).rstrip("/")
    for base in (index.root, index.root / _parent(cf.path)):
        if (base / core).exists():
            return True
    if "/" not in core:
        return fold(core) in index.lookups()[2]
    return False


def _git_ignored(root: Path, candidates: list) -> set:
    """Which candidates the repo's own ignore rules name: a machine-local location (a private
    folder, a scratch dir) is legitimately absent from a clone, so it is not a stale path."""
    if not candidates:
        return set()
    # A directory-only pattern ("scratch/") cannot match a path git cannot stat, so a folder
    # that is absent from this clone is probed through a child: a leading path component is
    # always judged as a directory.
    owner = {}
    for c in candidates:
        core = _norm_rel(c).rstrip("/")
        for q in (core, core + "/__ctxmap_probe__"):
            owner[q] = c
    # NUL-separated bytes, never text mode: on Windows a text-mode pipe turns "\n" into "\r\n"
    # and git then asks about "name\r", which no pattern matches.
    try:
        res = subprocess.run(["git", "check-ignore", "-z", "--stdin"],
                             input="\0".join(owner).encode("utf-8") + b"\0",
                             cwd=str(root), capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    if res.returncode not in (0, 1):
        return set()
    hits = res.stdout.decode("utf-8", errors="replace").split("\0")
    return {owner[h] for h in hits if h in owner}


def _norm_line(line: str):
    t = line.strip()
    if not t or t.startswith(("```", "~~~")) or re.fullmatch(r"[|:\-\s]+", t):
        return None
    t = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    if len(t) < 24 or len(re.findall(r"[a-z0-9]{2,}", t)) < 4:
        return None
    return t


def _norm_lines(text: str) -> set:
    return {n for n in (_norm_line(x) for x in text.splitlines()) if n}


def health(index: Index | None = None, cfg: dict | None = None) -> list:
    index = index or discover()
    cfg = cfg or settings()
    findings = []

    primaries = [g for g in index.root_guides if not g.local]
    if len(primaries) > 1:
        findings.append(Finding(
            "FAIL", "root_guide_multiple", primaries[1].path,
            f"a second root guide beside {primaries[0].path}: keep exactly one, or the "
            f"project's always-on instructions split in two and drift"))

    seen_imports = set()
    for g in index.guides():
        for imp in index.imports(g):
            key = (fold(imp.importer), imp.token)
            if imp.external or key in seen_imports:
                continue
            seen_imports.add(key)
            if imp.too_deep:
                findings.append(Finding(
                    "WARN", "import_too_deep", imp.importer,
                    f"@{imp.token} is hop {imp.depth} from {g.path}; Claude Code stops at "
                    f"{MAX_IMPORT_HOPS}, so it never loads"))
            elif not imp.exists:
                findings.append(Finding("FAIL", "import_missing", imp.importer,
                                        f"@{imp.token} does not exist"))

    for r in index.rules:
        if r.scope == "unparseable":
            findings.append(Finding(
                "WARN", "frontmatter_unparseable", r.path,
                f"frontmatter does not parse ({r.reason}); Claude Code ignores it and loads "
                f"this rule for every file"))
        elif r.scope == "scoped":
            compiled = compile_globs(r.globs)
            if not any(rx.match(fold(f)) for f in index.files for rx in compiled):
                findings.append(Finding(
                    "WARN", "rule_matches_nothing", r.path,
                    f"paths ({', '.join(r.globs)}) match no file in the project, so this rule "
                    f"never loads"))

    for cf in index.guides() + index.rules:
        if cf.lines > cfg["size_target"]:
            findings.append(Finding(
                "WARN", "file_over_size", cf.path,
                f"{cf.lines} lines, over the {cfg['size_target']}-line target "
                f"(memory.json context.size_target_lines)"))

    total = always_on_lines(index)
    if total > cfg["always_on_budget"]:
        anchor = index.root_guides[0].path if index.root_guides else "(always-on)"
        findings.append(Finding(
            "WARN", "always_on_over_budget", anchor,
            f"always-on context is {total} lines (root guide, its imports, unscoped rules), "
            f"over the {cfg['always_on_budget']}-line budget "
            f"(memory.json context.always_on_budget_lines)"))

    stale = {}
    for cf in index.guides() + index.rules:
        missing = [c for c in backticked_paths(cf.text) if not _path_exists(index, cf, c)]
        if missing:
            stale[cf.path] = missing
    if stale and index.source == "git":
        ignored = _git_ignored(index.root, sorted({c for v in stale.values() for c in v}))
        stale = {p: [c for c in v if c not in ignored] for p, v in stale.items()}
    for path, missing in sorted(stale.items()):
        if missing:
            shown = ", ".join(missing[:4]) + (f" (+{len(missing) - 4} more)" if len(missing) > 4 else "")
            findings.append(Finding("WARN", "stale_path", path,
                                    f"{len(missing)} backticked path(s) do not exist: {shown}"))

    for g in index.folder_guides:
        above = []
        for a in index.root_guides:
            above += [a] + index.loaded_imports(a)
        for a in index.folder_guides:
            if a.folder != g.folder and fold(g.folder).startswith(fold(a.folder) + "/"):
                above += [a] + index.loaded_imports(a)
        if not above:
            continue
        theirs = set().union(*(_norm_lines(a.text) for a in above))
        dup = _norm_lines(g.text) & theirs
        if dup:
            names = ", ".join(sorted({a.path for a in above if _norm_lines(a.text) & dup}))
            findings.append(Finding(
                "WARN", "duplicate_lines", g.path,
                f"{len(dup)} line(s) repeat an ancestor guide ({names}); a nested guide adds "
                f"to its ancestors, it does not restate them"))

    order = {"FAIL": 0, "WARN": 1}
    return sorted(findings, key=lambda f: (order.get(f.level, 2), fold(f.path), f.code))


def health_summary(index: Index | None = None):
    """-> (status, message, details) for checkctl's context_health step."""
    index = index or discover()
    findings = health(index)
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]
    status = "FAIL" if fails else ("WARN" if warns else "OK")
    cfg = settings()
    if not index.guides() and not index.rules:
        return status, "no guides or rules yet", [f.line() for f in findings]
    message = (f"{len(index.guides())} guide(s), {len(index.rules)} rule(s), always-on "
               f"{always_on_lines(index)}/{cfg['always_on_budget']} lines")
    if findings:
        message += f": {len(fails)} fail, {len(warns)} warn"
    return status, message, [f.line() for f in findings]


# --------------------------------------------------------------------------- map section

def ordered_files(index: Index) -> list:
    """(ContextFile, loads, via) in load order: launch set, folder guides with their imports,
    then scoped rules. Each file once."""
    out, seen = [], set()

    def put(cf, loads, via=None):
        if fold(cf.path) not in seen:
            seen.add(fold(cf.path))
            out.append((cf, loads, via))

    for cf, via in _launch_set(index):
        put(cf, "launch", via)
    for g in index.folder_guides:
        put(g, "on-read")
        for imp in index.loaded_imports(g):
            put(imp, "on-read", _importer_of(index, g, imp.path))
    for r in index.rules:
        if r.scope == "scoped":
            put(r, "on-match")
    return out


def map_section(index: Index | None = None, findings: list | None = None) -> dict:
    """The `context` section of map.json: derived entries, never cards (an adopter must not
    write a card per guide). Paths only, all repo-relative; no content, no machine paths."""
    index = index or discover()
    findings = health(index) if findings is None else findings
    cfg = settings()
    per_path = {}
    for f in findings:
        per_path[fold(f.path)] = per_path.get(fold(f.path), 0) + 1
    entries = []
    for cf, loads, via in ordered_files(index):
        entries.append({"path": cf.path, "kind": cf.kind, "scope": _scope_label(cf),
                        "loads": loads, "lines": cf.lines,
                        "findings": per_path.get(fold(cf.path), 0), "via": via})
    primary = next((g.path for g in index.root_guides if not g.local), None)
    return {
        "v": 1,
        "root_guide": primary,
        "always_on_lines": always_on_lines(index),
        "always_on_budget": cfg["always_on_budget"],
        "size_target": cfg["size_target"],
        "counts": {"guides": len(index.guides()), "rules": len(index.rules),
                   "imports": sum(1 for e in entries if e["kind"] == "import")},
        "findings": {"fail": sum(1 for f in findings if f.level == "FAIL"),
                     "warn": sum(1 for f in findings if f.level == "WARN")},
        "entries": entries,
    }


def section_from_map(map_data) -> dict:
    """The consumer side of CONTEXT_CONTRACT: map.json's `context` section, shape-checked,
    degrading to the empty shape for a project that has no map (or an older one)."""
    out = empty_section()
    src = map_data.get("context") if isinstance(map_data, dict) else None
    if not isinstance(src, dict):
        return out
    for key in ("v", "root_guide", "always_on_lines", "always_on_budget", "size_target"):
        if key in src:
            out[key] = src[key]
    for key in ("counts", "findings"):
        if isinstance(src.get(key), dict):
            out[key].update({k: src[key][k] for k in out[key] if k in src[key]})
    out["entries"] = [{k: e.get(k) for k in ENTRY_FIELDS}
                      for e in src.get("entries") or [] if isinstance(e, dict)]
    return out


# --------------------------------------------------------------------------- suggest
#
# Evidence-based only: a folder guide (or, inside .claude/, a scoped rule) is proposed where
# 2+ distinct lessons or logged mistakes name files in one subtree that nothing covers yet.
# Deepest subtree first; an entry already explained by a deeper proposal (or by a deeper guide
# or rule that exists) does not count again for an ancestor, so one cluster of evidence yields
# exactly one proposal. Nothing is ever created.

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-~/]+")


def _strings(value) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _resolve_evidence(index: Index, token: str):
    t = _norm_rel(token).rstrip("/")
    if not t or t.startswith(("../", "~", "/", "//")):
        return None
    files, dirs, base = index.lookups()
    for cand in (t, ".claude/" + t):
        if fold(cand) in files:
            return files[fold(cand)], False
        if fold(cand) in dirs:
            return dirs[fold(cand)], True
    if "/" not in t:
        hits = base.get(fold(t), [])
        if len(hits) == 1:
            return hits[0], False
    return None


def evidence(index: Index | None = None) -> list:
    """Lessons and logged mistakes, each with the project paths it names. Schemas as they
    really are: LESSONS.jsonl rows {id, what, root_cause, prevention, ...} carry paths in
    prose; Project-log mistakes {date, title, summary, artifacts[], ...} carry them in
    `artifacts` (often relative to .claude/) and in prose. Every string field is read."""
    index = index or discover()
    cd = index.root / ".claude"
    rows = []
    for n, row in enumerate(_lib.read_jsonl(cd / "LESSONS.jsonl"), start=1):
        rows.append((str(row.get("id") or f"lesson#{n}"), str(row.get("what", ""))[:80], row))
    log_n = 0
    for row in _lib.read_jsonl(cd / "Project-log.jsonl"):
        if row.get("type") != "mistake":
            continue
        log_n += 1
        rows.append((f"log:{row.get('date', '?')}#{log_n}", str(row.get("title", ""))[:80], row))
    # Ids: a lesson keeps its own (L-3); a logged mistake is log:<date>#<n>, n counting the
    # log's mistake entries in file order, so the same file always yields the same ids.
    out = []
    for entry_id, title, row in rows:
        paths = {}
        for text in _strings(row):
            for tok in _TOKEN_RE.findall(text):
                tok = tok.rstrip(".-")
                if not ("/" in tok or re.search(r"\.[A-Za-z0-9]{1,8}$", tok)):
                    continue
                hit = _resolve_evidence(index, tok)
                if hit:
                    paths[hit[0]] = hit[1]
        out.append({"id": entry_id, "title": title, "paths": paths})
    return out


def _covered(index: Index, folder: str, paths: list) -> bool:
    f = fold(folder)
    for g in index.folder_guides:
        gf = fold(g.folder)
        if f == gf or f.startswith(gf + "/"):
            return True
    for r in index.rules:
        if r.scope == "scoped" and any(glob_match(r.globs, p) for p in paths):
            return True
    return False


def suggest(index: Index | None = None) -> dict:
    index = index or discover()
    entries = evidence(index)
    by_folder, paths_in = {}, {}   # folder -> entry ids; (folder, entry id) -> paths
    for e in entries:
        for path, is_dir in e["paths"].items():
            for folder in _ancestors(path if is_dir else _parent(path)):
                by_folder.setdefault(folder, set()).add(e["id"])
                paths_in.setdefault((folder, e["id"]), set()).add(path)
    claims, proposals = {}, []
    for folder in sorted(by_folder, key=lambda d: (-d.count("/"), fold(d))):
        below = set().union(*(ids for d, ids in claims.items()
                              if fold(d).startswith(fold(folder) + "/")))
        remaining = by_folder[folder] - below
        if fold(folder) == ".claude":
            continue  # the system tree as a whole is the root guide's subject
        named = sorted(set().union(*(paths_in[(folder, i)] for i in by_folder[folder])))
        if _covered(index, folder, named):
            claims[folder] = by_folder[folder]
            continue
        if len(remaining) < SUGGEST_MIN_ENTRIES:
            continue
        claims[folder] = by_folder[folder]
        if _inside_claude(folder):
            slug = re.sub(r"[^A-Za-z0-9]+", "-", folder[len(".claude/"):]).strip("-").lower()
            proposal = {"kind": "scoped-rule", "folder": folder,
                        "target": f"{RULES_PREFIX}{slug or 'claude'}.md",
                        "paths": [folder + "/**"]}
        else:
            proposal = {"kind": "folder-guide", "folder": folder,
                        "target": f"{folder}/CLAUDE.md", "paths": []}
        proposal["entries"] = sorted(remaining)
        proposal["evidence"] = sorted(set().union(*(paths_in[(folder, i)] for i in remaining)))
        proposals.append(proposal)
    proposals.sort(key=lambda p: fold(p["folder"]))
    return {"entries_read": len(entries),
            "entries_with_paths": sum(1 for e in entries if e["paths"]),
            "min_entries": SUGGEST_MIN_ENTRIES, "proposals": proposals}


# --------------------------------------------------------------------------- CLI rendering
# mapctl owns the command and the verdict token; these build the body.

def cli(path: str | None, suggest_mode: bool = False, as_json: bool = False):
    """-> (exit_code, state) after printing the body. state: OK | WARN | FAIL."""
    root = Path(_lib.project_root())
    if suggest_mode:
        result = suggest(discover(root))
        if as_json:
            print(json.dumps(result, indent=2))
            return 0, "OK"
        props = result["proposals"]
        print(f"CONTEXT SUGGEST: {len(props)} proposal(s) from {result['entries_with_paths']} "
              f"lesson(s)/mistake(s) naming project paths (nothing is created)")
        for p in props:
            what = (f"scoped rule {p['target']} with paths: {', '.join(p['paths'])}"
                    if p["kind"] == "scoped-rule" else f"folder guide {p['target']}")
            print(f"  {what}")
            print(f"      {len(p['entries'])} entries: {', '.join(p['entries'])}")
            print(f"      evidence: {', '.join(p['evidence'][:6])}"
                  + (f" (+{len(p['evidence']) - 6} more)" if len(p["evidence"]) > 6 else ""))
        if not props:
            print(f"  nothing earned yet: no subtree has {SUGGEST_MIN_ENTRIES}+ lessons or "
                  f"mistakes without a guide or rule covering it")
        return 0, "OK"

    index = discover(root)
    if path is not None:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                print(f"CONTEXT: {path} is outside the project")
                return 1, "FAIL"
        else:
            rel = _norm_rel(path)
            if rel == ".." or rel.startswith("../"):
                print(f"CONTEXT: {path} is outside the project")
                return 1, "FAIL"
        result = resolve(rel + ("/" if path.endswith(("/", "\\")) and rel else ""), index)
        if as_json:
            print(json.dumps(result, indent=2))
            return 0, "OK"
        missing = "" if result["exists"] else "  (does not exist yet)"
        print(f"CONTEXT for {result['path'] or '.'}{missing}: {len(result['chain'])} file(s), "
              f"{result['total_lines']} line(s)")
        width = max([len(i["path"]) for i in result["chain"]] + [4])
        for i in result["chain"]:
            print(f"  {i['order']:>2}. {i['path']:<{width}}  {i['lines']:>5}  {i['loads']:<8}  "
                  f"{i['reason']}")
        cfg = settings()
        print(f"  total {result['total_lines']} line(s); always-on {result['always_on_lines']} "
              f"of the {cfg['always_on_budget']}-line budget")
        for note in result["notes"]:
            print(f"  note: {note}")
        return 0, "OK"

    findings = health(index)
    section = map_section(index, findings)
    if as_json:
        section["findings_detail"] = [f.__dict__ for f in findings]
        print(json.dumps(section, indent=2))
    else:
        c = section["counts"]
        print(f"CONTEXT: {c['guides']} guide(s), {c['rules']} rule(s), {c['imports']} import(s); "
              f"always-on {section['always_on_lines']} of {section['always_on_budget']} lines")
        width = max([len(e["path"]) for e in section["entries"]] + [4])
        for e in section["entries"]:
            flag = f"  [{e['findings']} finding(s)]" if e["findings"] else ""
            print(f"  {e['path']:<{width}}  {e['kind']:<6}  {e['loads']:<8}  {e['lines']:>5}  "
                  f"{e['scope']}{flag}")
        if not section["entries"]:
            print("  no guides or rules yet")
        print(f"  findings: {section['findings']['fail']} fail, {section['findings']['warn']} warn")
        for f in findings:
            print(f"    {f.line()}")
    fails = any(f.level == "FAIL" for f in findings)
    warns = any(f.level == "WARN" for f in findings)
    return (1, "FAIL") if fails else (0, "WARN" if warns else "OK")
