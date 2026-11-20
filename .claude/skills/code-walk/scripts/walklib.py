"""code-walk: helpers for a walk file (<walks>/<version>/code-walk.py).

A walk file imports this module as W. Everything here reads the repo
at one revision (set by walk_build.py), so line numbers in a built
page always match the code it shows. Standard library only.
"""
import ast
import html
import math
import os
import subprocess

REPO = "."          # set by walk_build.py
REV = None          # None: the working tree; else a git revision


# ---- reading the repo at one revision ------------------------------------

def src(path):
    """The text of a repo file."""
    if REV:
        return subprocess.run(["git", "-C", REPO, "show", f"{REV}:{path}"],
                              check=True, capture_output=True, text=True,
                              encoding="utf-8").stdout
    with open(os.path.join(REPO, path), encoding="utf-8") as f:
        return f.read()


def listdir(path):
    """File and folder names in a repo folder, sorted."""
    if REV:
        out = subprocess.run(["git", "-C", REPO, "ls-tree", "--name-only",
                              f"{REV}:{path}"], check=True,
                             capture_output=True, text=True,
                             encoding="utf-8").stdout
        return sorted(out.splitlines())
    return sorted(os.listdir(os.path.join(REPO, path)))


def nlines(path):
    return len(src(path).splitlines())


def line_of(path, text, start=1):
    """The first line at or after `start` that contains `text`. Fails
    the build when it is gone, so a walk never points at a stale line."""
    for i, ln in enumerate(src(path).splitlines(), 1):
        if i >= start and text in ln:
            return i
    raise SystemExit(f"code-walk: {path}: no line contains {text!r}")


def defs(path):
    """{name: (first, last)} for top-level defs and classes of a Python
    file, and "Class.method" for methods."""
    out = {}
    for n in ast.parse(src(path)).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out[n.name] = (n.lineno, n.end_lineno)
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                        out[f"{n.name}.{m.name}"] = (m.lineno, m.end_lineno)
    return out


def span(path, name):
    """[first, last] of a def or class, for a step's ranges."""
    d = defs(path)
    if name not in d:
        raise SystemExit(f"code-walk: {path}: no def {name}")
    return list(d[name])


# ---- the folder tree, shown in the code pane -----------------------------

class Tree:
    """Rows of a folder tree: text, note, line count. Tags name rows so
    a step can point at them: tree.ranges("src", "docs")."""

    def __init__(self):
        self.rows, self.tags = [], {}

    def add(self, text, note="", tags=(), count=None):
        self.rows.append([text, note, count])
        for t in [tags] if isinstance(tags, str) else tags:
            self.tags.setdefault(t, []).append(len(self.rows))

    def ranges(self, *tags):
        ls = sorted({n for t in tags for n in self.tags[t]})
        out = []
        for n in ls:
            if out and n == out[-1][1] + 1:
                out[-1][1] = n
            else:
                out.append([n, n])
        return out

    def folder(self, path, note="", tags=(), last=False, files=None,
               notes=None, depth=1, count=True):
        """A folder row and one row per file in it."""
        lead = "└── " if last else "├── "
        self.add(lead + path.rstrip("/").split("/")[-1] + "/", note,
                 [path, *([tags] if isinstance(tags, str) else tags)])
        names = files if files is not None else [
            x for x in listdir(path) if not x.startswith(".")]
        bar = "    " if last else "│   "
        for i, f in enumerate(names):
            end = i == len(names) - 1
            self.add(bar + ("└── " if end else "├── ") + f,
                     (notes or {}).get(f, ""), [path, f, *(
                         [tags] if isinstance(tags, str) else tags)],
                     nlines(f"{path}/{f}") if count else None)


# ---- pictures ------------------------------------------------------------

def esc(s):
    return html.escape(str(s), quote=True)


class Fig:
    """One diagram. Boxes and edges carry a key (data-k) that steps
    list in "hl" to light them up; a click on a box opens its step."""

    def __init__(self, w, h):
        self.w, self.h, self.parts, self.boxes = w, h, [], {}

    def zone(self, x, y, w, h, label):
        self.parts.append(
            f'<g class="zone"><rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="10"/><text class="zl" x="{x + 10}" y="{y + 16}">{esc(label)}'
            f'</text></g>')

    def label(self, x, y, text):
        self.parts.append(f'<text class="zl" x="{x}" y="{y}">{esc(text)}'
                          '</text>')

    def box(self, k, x, y, w, h, title, sub=None, kind="", inline=False,
            more=None):
        """kind: "" a part, "ext" another file (dashed), "bad" a stop,
        "group" a frame around other boxes. more: a third line."""
        self.boxes[k] = (x, y, w, h)
        p = [f'<g class="node {kind}" data-k="{k}"><rect x="{x}" y="{y}" '
             f'width="{w}" height="{h}" rx="7"/>']
        if inline:
            p.append(f'<text x="{x + 9}" y="{y + h / 2 + 4}"><tspan class="t">'
                     f'{esc(title)}</tspan><tspan class="u"> {esc(sub)}'
                     '</tspan></text>')
        elif sub:
            ty = y + h / 2 - 3 - (7 if more else 0)
            p.append(f'<text class="t" x="{x + 10}" y="{ty}">{esc(title)}'
                     f'</text><text class="u" x="{x + 10}" y="{ty + 14}">'
                     f'{esc(sub)}</text>')
            if more:
                p.append(f'<text class="u" x="{x + 10}" y="{ty + 28}">'
                         f'{esc(more)}</text>')
        elif title:
            p.append(f'<text class="t" x="{x + 10}" y="{y + h / 2 + 4}">'
                     f'{esc(title)}</text>')
        p.append("</g>")
        self.parts.append("".join(p))

    def center(self, k):
        x, y, w, h = self.boxes[k]
        return x + w / 2, y + h / 2

    def clip(self, k, tx, ty):
        cx, cy = self.center(k)
        x, y, w, h = self.boxes[k]
        dx, dy = tx - cx, ty - cy
        sx = (w / 2) / abs(dx) if dx else math.inf
        sy = (h / 2) / abs(dy) if dy else math.inf
        s = min(sx, sy)
        return cx + dx * s, cy + dy * s

    def link(self, k, a, b, **kw):
        """A straight edge between the borders of boxes a and b."""
        ax, ay = self.center(a)
        bx, by = self.center(b)
        self.edge(k, [self.clip(a, bx, by), self.clip(b, ax, ay)], **kw)

    def edge(self, k, pts, label=None, at=None, dash=False, curve=None):
        """A line through pts with an arrowhead at the end; curve: one
        control point for a bend between the first and last point."""
        d = "M%.1f %.1f" % pts[0]
        if curve:
            d += " Q%.1f %.1f %.1f %.1f" % (*curve, *pts[-1])
            tx, ty = curve
        else:
            d += "".join(" L%.1f %.1f" % p for p in pts[1:])
            tx, ty = pts[-2]
        ex, ey = pts[-1]
        ang = math.atan2(ey - ty, ex - tx)
        a1 = (ex - 7 * math.cos(ang - 0.42), ey - 7 * math.sin(ang - 0.42))
        a2 = (ex - 7 * math.cos(ang + 0.42), ey - 7 * math.sin(ang + 0.42))
        p = [f'<g class="edge{" dash" if dash else ""}" data-k="{k}">'
             f'<path d="{d}"/>',
             '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f"/>' % (
                 ex, ey, *a1, *a2)]
        if label:
            p.append(f'<text class="el" x="{at[0]}" y="{at[1]}">'
                     f'{esc(label)}</text>')
        p.append("</g>")
        self.parts.append("".join(p))

    def crossings(self, pairs):
        """Pairs (a, b) whose straight line runs through a third box."""
        bad = []
        for a, b in pairs:
            ax, ay = self.center(a)
            bx, by = self.center(b)
            for k, (x, y, w, h) in self.boxes.items():
                if k in (a, b):
                    continue
                for i in range(1, 200):
                    px = ax + (bx - ax) * i / 200
                    py = ay + (by - ay) * i / 200
                    if x - 3 <= px <= x + w + 3 and y - 3 <= py <= y + h + 3:
                        bad.append((a, b, k))
                        break
        return bad

    def svg(self, title):
        return (f'<svg viewBox="0 0 {self.w} {self.h}" role="img" '
                f'aria-label="{esc(title)}" '
                'preserveAspectRatio="xMidYMid meet">'
                + "".join(self.parts) + "</svg>")


def step(title, body, file, ranges, hl=(), note=None, d=0):
    """One step: what the left half says and lights up, and which lines
    the right half shows. ranges: [[first, last], ...] in `file`."""
    return {"t": title, "b": body, "w": note, "f": file,
            "r": [list(r) for r in ranges], "hl": list(hl), "d": d}
