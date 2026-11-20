#!/usr/bin/env python3
"""code-walk: what changed since the last walk was built.

  python3 .claude/skills/code-walk/scripts/walk_diff.py [--root DIR]
      [--old-rev REV] [--new-rev REV] [--skip PREFIX]... [--note]
      [--out FILE]

walk.py, one folder up, is the short way in. OLD is the commit the newest walk
under --root (default docs/code-walk) was built from: the "commit" in
its code-walk.html. Give --old-rev for a first walk or another base.
NEW is the working tree unless --new-rev is given. It writes Markdown:

  - the commits between OLD and NEW;
  - each file: added, removed, renamed or changed, with line counts;
  - each changed Python file: functions added, removed and changed;
  - imports between sibling Python files that appeared or went away.

--note writes the walk note instead: base, new, the diff
command, three lines to fill in, and a row per changed file.
--skip leaves out a path prefix (the walks folder always is).
It changes nothing in the repo. Standard library only.
"""
import argparse
import ast
import glob
import os
import re
import subprocess
import sys

REPO = "."
CACHE = {}


def git(*a, check=True):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          check=check).stdout


def files_at(rev, skip):
    if rev:
        out = git("ls-tree", "-r", "--name-only", rev)
    else:
        out = git("ls-files") + git("ls-files", "--others",
                                    "--exclude-standard")
    return {p for p in out.split("\n") if p and not p.startswith(skip)}


def read(rev, path):
    key = (rev, path)
    if key not in CACHE:
        try:
            if rev:
                CACHE[key] = git("show", f"{rev}:{path}").replace(
                    "\r\n", "\n")
            else:
                # universal newlines: a CRLF checkout must compare equal
                # to the LF copy git holds
                with open(os.path.join(REPO, path), encoding="utf-8",
                          errors="replace") as f:
                    CACHE[key] = f.read()
        except (subprocess.CalledProcessError, OSError):
            CACHE[key] = None
    return CACHE[key]


def last_walk(root):
    """(commit, built with uncommitted changes, page) of the newest walk."""
    best = None
    for page in glob.glob(os.path.join(REPO, root, "*", "code-walk.html")):
        with open(page, encoding="utf-8") as f:
            m = re.search(r'"commit":"([0-9a-f]+)(\+changes)?"', f.read())
        when = m and git("log", "-1", "--format=%ct", m.group(1),
                         check=False).strip()
        if when and (best is None or int(when) > best[0]):
            best = (int(when), m.group(1), bool(m.group(2)), page)
    return best[1:] if best else (None, False, None)


def defs(text):
    """{name: source} of top-level functions, classes and methods."""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return {}
    lines, out = text.splitlines(), {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out[n.name] = "\n".join(lines[n.lineno - 1:n.end_lineno])
            for m in getattr(n, "body", []) if isinstance(
                    n, ast.ClassDef) else []:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{n.name}.{m.name}"] = "\n".join(
                        lines[m.lineno - 1:m.end_lineno])
    return out


def imports(rev, files):
    """Edges (a, b): Python file a imports its sibling module b."""
    edges, by_dir = set(), {}
    py = [p for p in files if p.endswith(".py")]
    for p in py:
        by_dir.setdefault(os.path.dirname(p), {})[
            os.path.basename(p)[:-3]] = p
    for p in py:
        try:
            tree = ast.parse(read(rev, p) or "")
        except (SyntaxError, ValueError):
            continue
        sib = by_dir[os.path.dirname(p)]
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(
                n, ast.Import) else [n.module] if isinstance(
                n, ast.ImportFrom) and n.module else []
            for m in names:
                if m in sib and sib[m] != p:
                    edges.add((p, sib[m]))
    return edges


def nlines(text):
    return len(text.splitlines()) if text is not None else 0


def changes(old, new, skip):
    a, b = files_at(old, skip), files_at(new, skip)
    renames = {}
    out = git("diff", "-M", "--name-status", old, *([new] if new else []),
              check=False)
    for ln in out.splitlines():
        parts = ln.split("\t")
        if parts[0].startswith("R") and len(parts) == 3 and \
                parts[1] in a and parts[2] in b:
            renames[parts[1]] = parts[2]
    added = sorted(b - a - set(renames.values()))
    removed = sorted(a - b - set(renames))
    changed = [p for p in sorted(a & b) if read(old, p) != read(new, p)]
    return a, b, added, removed, renames, changed


def note(old, new, skip):
    _, _, added, removed, renames, changed = changes(old, new, skip)
    full = git("rev-parse", old).strip()
    tip = git("rev-parse", new).strip() if new else ""
    R = [f"# Walk note: {old} to {new or 'the working tree'}", "",
         f"- base: `{full}`",
         f"- new: `{tip or 'the working tree (commit, then put the hash here)'}`",
         f"- diff: `git diff -M {full}{' ' + tip if tip else ''}`", "",
         "## What this version is", "",
         "<about three lines: what is new, what behaves differently, "
         "what is gone>", "",
         "## Changed files", "",
         "| File | Change | What to look at |", "|---|---|---|"]
    R += [f"| `{p}` | added | |" for p in added]
    R += [f"| `{p}` | changed | |" for p in changed]
    R += [f"| `{o}` to `{n}` | renamed | |" for o, n in renames.items()]
    R += [f"| `{p}` | removed | |" for p in removed]
    return "\n".join(R) + "\n"


def report(old, new, skip):
    a, b, added, removed, renames, changed = changes(old, new, skip)
    R = [f"# Code walk changes: {old} to {new or 'the working tree'}", ""]
    log = git("log", "--oneline", "--no-merges", f"{old}..{new or 'HEAD'}")
    R += ["## Commits", ""] + ([f"- {x}" for x in log.splitlines()[:80]]
                               or ["- none"])
    if not new and git("status", "--porcelain"):
        R.append("- and changes not committed yet")
    R += ["", "## Files", "",
          f"{len(added)} added, {len(removed)} removed, {len(renames)} "
          f"renamed, {len(changed)} changed (left out: "
          f"{', '.join(skip)}).", "",
          "| What | File | Lines |", "|---|---|---|"]
    for p in added:
        R.append(f"| added | `{p}` | {nlines(read(new, p))} |")
    for p in removed:
        R.append(f"| removed | `{p}` | {nlines(read(old, p))} |")
    for o, n in renames.items():
        R.append(f"| renamed | `{o}` to `{n}` | "
                 f"{nlines(read(old, o))} to {nlines(read(new, n))} |")
    for p in changed:
        R.append(f"| changed | `{p}` | {nlines(read(old, p))} to "
                 f"{nlines(read(new, p))} |")
    R += ["", "## Functions in changed Python files", ""]
    any_py = False
    for p in changed + list(renames):
        q = renames.get(p, p)
        if not q.endswith(".py"):
            continue
        d0, d1 = defs(read(old, p)), defs(read(new, q))
        rows = (("added", [k for k in d1 if k not in d0]),
                ("removed", [k for k in d0 if k not in d1]),
                ("changed", [k for k in d0 if k in d1 and d0[k] != d1[k]]))
        if any(ks for _, ks in rows):
            any_py = True
            R.append(f"- `{q}`")
            R += [f"  - {label}: " + ", ".join(f"`{k}`" for k in ks)
                  for label, ks in rows if ks]
    if not any_py:
        R.append("- none")
    e0, e1 = imports(old, a), imports(new, b)
    R += ["", "## Imports between Python files", ""]
    if e0 == e1:
        R.append("- the same")
    R += [f"- new: `{x}` imports `{y}`" for x, y in sorted(e1 - e0)]
    R += [f"- gone: `{x}` no longer imports `{y}`"
          for x, y in sorted(e0 - e1)]
    return "\n".join(R) + "\n"


def main(argv):
    global REPO
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default="docs/code-walk")
    ap.add_argument("--old-rev")
    ap.add_argument("--new-rev")
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--note", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    REPO = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip()
    if not REPO:
        sys.exit("code-walk: not inside a git repository")
    old, dirty, page = (a.old_rev, False, None) if a.old_rev \
        else last_walk(a.root)
    if not old:
        sys.exit(f"code-walk: no built walk under {a.root}; give --old-rev")
    if page:
        print(f"base: {old}, the commit of {os.path.relpath(page, REPO)}"
              + (" (built with changes not committed)" if dirty else ""),
              file=sys.stderr)
    skip = tuple([a.root.rstrip("/") + "/"] + a.skip)
    text = (note if a.note else report)(old, a.new_rev, skip)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(a.out)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
