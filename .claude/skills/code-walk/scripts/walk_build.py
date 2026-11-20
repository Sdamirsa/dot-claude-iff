#!/usr/bin/env python3
"""code-walk: build one standalone page from a walk file.

  python3 .claude/skills/code-walk/scripts/walk_build.py \
      <walks>/<version>/code-walk.py [--rev REV] [--out FILE] [--fragment FILE]

The walk file (see SKILL.md) names the chapters, their pictures and
their steps. This script reads the repo at REV (default: the working
tree), checks every step against the files, colours the code, and
writes code-walk.html next to the walk file: one HTML file,
nothing loaded from anywhere else, that opens in any browser.
--fragment also writes the page without <html>/<head>/<body>, for a
host that adds its own. Standard library only.
"""
import argparse
import builtins
import datetime
import glob
import html
import importlib.util
import io
import json
import keyword
import os
import re
import subprocess
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import walklib as W  # noqa: E402


# ---- colouring -----------------------------------------------------------

def esc(s):
    return html.escape(s, quote=False)


def render(text, marks):
    """Lines of HTML: runs of one class become <i class=X>."""
    out, off = [], 0
    for ln in text.split("\n"):
        parts, cur, buf = [], None, []
        for i, ch in enumerate(ln):
            c = marks[off + i]
            if c != cur and buf:
                parts.append(f'<i class="{cur}">{esc("".join(buf))}</i>'
                             if cur else esc("".join(buf)))
                buf = []
            cur = c
            buf.append(ch)
        if buf:
            parts.append(f'<i class="{cur}">{esc("".join(buf))}</i>'
                         if cur else esc("".join(buf)))
        out.append("".join(parts))
        off += len(ln) + 1
    if out and out[-1] == "" and text.endswith("\n"):
        out.pop()
    return out


BUILT = set(dir(builtins)) | {"self", "cls"}


def py(text):
    marks = [None] * len(text)
    starts = [0]
    for ln in text.split("\n"):
        starts.append(starts[-1] + len(ln) + 1)

    def paint(a, b, c):
        p = starts[a[0] - 1] + a[1]
        q = starts[b[0] - 1] + b[1]
        for i in range(max(p, 0), min(q, len(marks))):
            marks[i] = c
    sig, name = None, None
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            kind = tokenize.tok_name[t.type]
            c = None
            if t.type == tokenize.COMMENT:
                c = "c"
            elif t.type == tokenize.STRING:
                c = "d" if t.string[:3] in ('"""', "'''") and sig in (
                    None, tokenize.NEWLINE, tokenize.INDENT,
                    tokenize.DEDENT) else "s"
            elif kind.startswith("FSTRING") or kind.startswith("TSTRING"):
                c = "s"
            elif t.type == tokenize.NUMBER:
                c = "n"
            elif t.type == tokenize.NAME:
                if keyword.iskeyword(t.string):
                    c = "k"
                elif name in ("def", "class"):
                    c = "f"
                elif t.string in BUILT:
                    c = "b"
            if c:
                paint(t.start, t.end, c)
            if t.type not in (tokenize.NL, tokenize.COMMENT):
                sig = t.type
            name = t.string if t.type == tokenize.NAME else None
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return render(text, marks)


STR = r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
RULES = {
    "sh": [(r"(?:(?<=\s)|^)#.*", "c"), (STR, "s"),
           (r"\$\{[^}\n]*\}|\$[A-Za-z_@#?*0-9]\w*", "b"),
           (r"\b(?:if|then|else|elif|fi|for|do|done|while|case|esac|"
            r"local|return|function|in)\b", "k")],
    "toml": [(r"#.*", "c"), (r"'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"", "s"),
             (STR, "s"), (r"^[ \t]*[A-Za-z0-9_.-]+(?=[ \t]*=)", "f"),
             (r"\b(?:true|false)\b", "k"), (r"\b-?[0-9][0-9_.]*\b", "n")],
    "js": [(r"/\*[\s\S]*?\*/|//.*", "c"), (STR + r"|`(?:\\.|[^`\\])*`", "s"),
           (r"\b(?:const|let|var|function|return|if|else|for|of|in|while|"
            r"new|async|await|try|catch|finally|throw|break|continue|"
            r"switch|case|default|typeof|instanceof|class|null|undefined|"
            r"true|false|this)\b", "k"),
           (r"\b[0-9][0-9_.]*\b", "n"),
           (r"\b[A-Za-z_$][\w$]*(?=\s*\()", "f")],
    "css": [(r"/\*[\s\S]*?\*/", "c"), (STR, "s"), (r"@[\w-]+", "k"),
            (r"--[\w-]+", "f"), (r"#[0-9a-fA-F]{3,8}\b", "n"),
            (r"\b[0-9][0-9.]*(?:px|em|rem|%|vh|vw|s|ms)?\b", "n")],
    "html": [(r"<!--[\s\S]*?-->", "c"), (r"</?[A-Za-z][\w-]*|/?>", "k"),
             (r'"[^"]*"', "s")],
    "md": [(r"^#{1,6} .*", "f"), (r"`[^`\n]+`", "s"),
           (r"^```.*", "c")],
}
LANG = {".py": "py", ".sh": "sh", ".toml": "toml", ".js": "js", ".ts": "js",
        ".jsx": "js", ".tsx": "js", ".json": "js", ".css": "css",
        ".html": "html", ".md": "md", ".txt": "sh"}


def scan(text, lang):
    marks = [None] * len(text)
    rules = RULES.get(lang)
    if rules:
        rx = re.compile("|".join(f"({r})" for r, _ in rules), re.M)
        for m in rx.finditer(text):
            c = rules[m.lastindex - 1][1]
            for i in range(m.start(), m.end()):
                marks[i] = c
    return render(text, marks)


def colour(path, text):
    lang = LANG.get(os.path.splitext(path)[1])
    if lang is None and text.startswith("#!") and "python" in text[:40]:
        lang = "py"
    return py(text) if lang == "py" else scan(text, lang)


def tree_lines(tree):
    # columns sized by the rows that have a note; a long row runs on
    w = max([len(t) for t, n, _ in tree.rows if n] or [20]) + 2
    nw = max((len(n) for _, n, _ in tree.rows), default=0) + 2
    out = []
    for t, note, count in tree.rows:
        cut = len(t) - len(t.lstrip("│├└─ "))
        name = t[cut:]
        cls = "k" if name.endswith("/") else "f"
        s = (f'<i class="c">{esc(t[:cut])}</i><i class="{cls}">{esc(name)}'
             f'</i>' + " " * max(2, w - len(t)))
        s += f'<i class="c">{esc(note)}</i>' + " " * (nw - len(note))
        if count is not None:
            s += f'<i class="n">{count:>5}</i>'
        out.append(s.rstrip())
    return out


# ---- the walk file ---------------------------------------------------------

def md(s):
    """Step text: escaped, with `code` in backticks."""
    s = html.escape(s or "", quote=False)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def load_walk(path):
    spec = importlib.util.spec_from_file_location("walk", path)
    mod = importlib.util.module_from_spec(spec)
    mod.W = W
    spec.loader.exec_module(mod)
    return mod


def git(*a):
    try:
        return subprocess.run(["git", "-C", W.REPO, *a], check=True,
                              capture_output=True, text=True,
                              encoding="utf-8").stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build(walk_path, rev):
    W.REPO = subprocess.run(["git", "-C", os.path.dirname(
        os.path.abspath(walk_path)), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True).stdout.strip() or os.getcwd()
    W.REV = rev
    mod = load_walk(walk_path)
    chapters = [c() if callable(c) else c for c in mod.CHAPTERS]
    groups = getattr(mod, "GROUPS", {})     # {label: [chapter ids]}
    for c in chapters:
        c.setdefault("group", next((g for g, ids in groups.items()
                                    if c["id"] in ids), ""))
    files = {}
    tree = getattr(mod, "TREE", None)
    if tree is not None:
        files["repo tree"] = {"lines": tree_lines(tree), "tree": True}
    want = []
    for pat in getattr(mod, "FILES", []):
        if rev:
            want += [p for p in git("ls-tree", "-r", "--name-only", rev,
                                    "--").split("\n") if glob.fnmatch
                     .fnmatch(p, pat)] if any(ch in pat for ch in "*?[") \
                else [pat]
        else:
            got = sorted(os.path.relpath(p, W.REPO).replace(os.sep, "/")
                         for p in glob.glob(os.path.join(W.REPO, pat),
                                            recursive=True)
                         if os.path.isfile(p))
            want += got or [pat]
    for c in chapters:
        for s in c["steps"]:
            if s["f"] not in files and s["f"] not in want:
                want.append(s["f"])
    for p in dict.fromkeys(want):
        if p in files:
            continue
        files[p] = {"lines": colour(p, W.src(p))}
    problems = []
    for c in chapters:
        keys = [set(re.findall(r'data-k="([^"]+)"', svg)) for svg in c["svgs"]]
        for i, s in enumerate(c["steps"], 1):
            where = f"{c['id']} step {i}"
            n = len(files[s["f"]]["lines"])
            for a, b in s["r"]:
                if not 1 <= a <= b <= n:
                    problems.append(f"{where}: lines {a}-{b} outside "
                                    f"{s['f']} (1-{n})")
            miss = set(s["hl"]) - keys[s.get("d", 0)]
            if miss:
                problems.append(f"{where}: no box or edge {sorted(miss)}")
            s["b"], s["w"] = md(s["b"]), md(s["w"]) if s["w"] else None
    if problems:
        raise SystemExit("code-walk: the walk does not match the code:\n  "
                         + "\n  ".join(problems))
    commit = git("rev-parse", "--short", rev or "HEAD")
    dirty = "" if rev or not git("status", "--porcelain", "--", *files.keys()
                                 - {"repo tree"}) else "+changes"
    return {"title": mod.TITLE, "version": mod.VERSION,
            "commit": commit + dirty,
            "built": datetime.date.today().isoformat(),
            "next": getattr(mod, "NEXT", []), "chapters": chapters,
            "files": files}


def page(data, full):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "page.html"), encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")
    title = html.escape(f"{data['title']} {data['version']}")
    head, body = tpl.split("<!--BODY-->")
    head = head.replace("{{TITLE}}", title)
    body = body.replace("{{DATA}}", blob)
    if not full:
        return head.split("<!--HEAD-->")[1] + body
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset="
            "\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-"
            "width, initial-scale=1, viewport-fit=cover\">\n"
            + head.split("<!--HEAD-->")[1] + "</head>\n<body>\n" + body
            + "</body>\n</html>\n")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("walk")
    ap.add_argument("--rev")
    ap.add_argument("--out")
    ap.add_argument("--fragment")
    a = ap.parse_args(argv)
    data = build(a.walk, a.rev)
    out = a.out or os.path.join(os.path.dirname(a.walk), "code-walk.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page(data, True))
    if a.fragment:
        with open(a.fragment, "w", encoding="utf-8") as f:
            f.write(page(data, False))
    steps = sum(len(c["steps"]) for c in data["chapters"])
    print(f"{out}: {len(data['chapters'])} chapters, {steps} steps, "
          f"{len(data['files'])} files, {os.path.getsize(out) // 1024} KiB")


if __name__ == "__main__":
    main(sys.argv[1:])
