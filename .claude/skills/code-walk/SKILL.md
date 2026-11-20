---
name: code-walk
description: Build the code walk of a version - one offline HTML page, a chapter per change, each a diagram plus short notes that point at file:line. User-invoked only (/code-walk).
argument-hint: "[version] [base-rev] [walks folder]"
disable-model-invocation: true
---

# Code walk

A code walk is one HTML page that reads a version's changes with you. One chapter per
change. A chapter opens with a diagram, then a few short steps; each step lights part of the
diagram and marks its lines in the real code (`file:line`). On a wide screen the picture and
the code sit side by side; on a phone the picture comes first and the code sits under it.
The page loads nothing from the network: it opens from disk, a clone or an attachment.

Everything the skill needs is in this folder. It runs only when the user types `/code-walk`.

| Path | What it is |
|---|---|
| `scripts/walk_diff.py` | what changed since the last walk was built; `--handoff` writes the hand-off note |
| `scripts/walk_build.py` | builds the page from a walk file, and checks every step against the code |
| `scripts/walklib.py` | `W`: read the repo at one revision, find lines, draw diagrams |
| `scripts/page.html` | the page: layout, colours, script |

Scripts are Python standard library only; run them from the repo root as
`python3 .claude/skills/code-walk/scripts/<name>.py`.

## Where walks live

One folder per version under the walks folder: `docs/code-walk/<version>/` unless the user
names another folder or the repo already keeps its walks elsewhere (then pass `--root` to
`walk_diff.py`). Each holds:

- `HANDOFF.md`: the hand-off note the walk is fed from;
- `code-walk.py`: the walk file, the source of the page;
- `code-walk.html`: the built page, stamped with the commit it was built from.

Older versions' folders are never edited: each page matches its own code.

## 1. The hand-off note

When a version ships, whoever built it writes `HANDOFF.md` in the version's folder:

```
python3 .claude/skills/code-walk/scripts/walk_diff.py --handoff --new-rev <new> --out <walks>/<version>/HANDOFF.md
```

The base is the commit the newest walk was built from; for a first walk give
`--old-rev <base>`. The script fills in the base commit, the new commit, the
`git diff -M` command and a row per changed file. The builder then writes, by hand:

- **What this version is**: about three lines. What is new, what behaves differently, what
  is gone.
- **What to look at**, per file: the function, branch or setting a reader should see.
  A row that needs no reader ("regenerated", "version bump") says so.

If `/code-walk` is typed and there is no note, write it first, from the commits and the
diff, and show the three lines to the user before going on.

## 2. Understand the change

Read the note, then run `walk_diff.py` (same arguments, without `--handoff`) for the
commits, the changed functions and the imports that moved, then read
`git diff -M <base> <new> -- <file>` for every file the note says to look at. A step can
build and still be wrong, so read the code before writing about it.

Group the files into changes: one feature, fix or removal is one chapter, whatever number
of files it touches. Three to eight chapters suit most versions; rows the note marks as
needing no reader get no chapter.

## 3. Write the walk file

`<walks>/<version>/code-walk.py` is Python run by the builder with `W` in scope:

```python
TITLE = "<project> code walk"
VERSION = "<version>"

def ch_login():
    f = W.Fig(660, 300)                       # 660 wide; height as needed
    f.label(20, 26, "EACH REQUEST")
    f.box("check", 20, 60, 260, 52, "check_token()", "new: expiry")
    f.box("store", 20, 200, 260, 44, "store.py", "another file", "ext")
    f.link("e_store", "check", "store")
    assert not f.crossings([("check", "store")])
    return {"id": "login", "title": "Login expiry",
            "svgs": [f.svg("How a token is checked")],
            "steps": [W.step(
                "Tokens now expire",
                "`check_token()` compares the stored time with now and refuses an old token.",
                "src/auth.py", [W.span("src/auth.py", "check_token")],
                hl=["check"], note="Next: where the time is stored.")]}

CHAPTERS = [ch_login]
```

- A chapter is `{"id", "title", "svgs": [...], "steps": [...]}`; `id` is small letters and
  hyphens, and the page's address for a step is `#id-N`. `GROUPS = {"Label": [ids]}`
  labels runs of chapters; `FILES` (globs) adds files to the "Open any file" list.
- A step is `W.step(title, body, file, ranges, hl, note, d)`. Keep it short: `body` is two
  or three plain sentences, one idea, names in backticks; `note` is one sentence of why,
  or a pointer to the next chapter. Two to six steps per chapter.
- `ranges` is `[[first, last], ...]` in `file`. Never type a line number: look it up with
  `W.span(file, "Class.method")` (Python), `W.line_of(file, "text")` or `W.defs(file)`,
  so the build fails when the code moves instead of pointing at the wrong line.
- `hl` lists the keys of the boxes and edges of diagram `d` that the step lights. Every
  step lights something; a click on a lit box opens its step.
- Diagrams: box kinds are `""` a part of this change, `"ext"` another file (dashed),
  `"bad"` a stop or refusal, `"group"` a frame; `f.zone()` frames an area, `f.edge()`
  draws a line through points. Titles short (about 18 characters in a 135-wide box).
  Colours come from the page; never set one in a diagram.

## 4. Build and check

```
python3 .claude/skills/code-walk/scripts/walk_build.py <walks>/<version>/code-walk.py --rev <new>
```

`--rev` builds from that commit, so the page matches the released code and carries its
hash; without it the working tree is used. `--fragment FILE` also writes the page without
`<html>`, `<head>` and `<body>`, for a host that adds its own.

The build fails, and says where, when a step's lines are outside its file, a looked-up
line is gone, or a step lights a box its diagram does not have. Fix the walk; never
silence the check. Then, before reporting done:

- open the page and go through every step, light and dark, and at phone width;
- no secret, token, private host name or personal data in a step or a diagram; the code
  shown is only what the repo already tracks;
- nothing outside the version's folder changed.

Report the page's path, the chapters, and anything the note asked for that the walk does
not show.

## A whole file or repo

The same builder walks code that did not change: `/code-walk <path>` makes a walk whose
chapters follow a file top to bottom (one step per function), or a repo from its entry
point down. Put it in its own folder beside the versions.
