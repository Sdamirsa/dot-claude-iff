#!/usr/bin/env python3
"""code-walk: the one command, for the user and the agent alike.

  python3 .claude/skills/code-walk/walk.py changes [VERSION]
      print what changed since the last walk was built
  python3 .claude/skills/code-walk/walk.py note VERSION
      write the walk note, <walks>/VERSION/WALK-NOTE.md, to fill in
  python3 .claude/skills/code-walk/walk.py build VERSION
      build <walks>/VERSION/code-walk.html from code-walk.py beside it

VERSION names the walk's folder. When it is also a git tag or commit,
that is the code read; otherwise the working tree is.

  --root DIR     the walks folder (default docs/code-walk)
  --base REV     compare from REV instead of the last walk's commit
  --skip PREFIX  leave a path prefix out of the comparison (repeatable)
  --force        let `note` replace a note that exists

Run it from anywhere inside the repo. Standard library only.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "scripts"))
import walk_build  # noqa: E402
import walk_diff  # noqa: E402


def is_rev(name):
    return bool(name) and subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", name + "^{commit}"],
        capture_output=True).returncode == 0


def main(argv):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("verb", choices=["changes", "note", "build"])
    ap.add_argument("version", nargs="?")
    ap.add_argument("--root", default="docs/code-walk")
    ap.add_argument("--base")
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    if not top:
        sys.exit("code-walk: not inside a git repository")
    os.chdir(top)
    if a.verb != "changes" and not a.version:
        sys.exit(f"code-walk: {a.verb} needs a version, the walk's folder "
                 "name")
    rev = a.version if is_rev(a.version) else None
    folder = a.root.rstrip("/\\") + "/" + (a.version or "")
    if a.verb == "build":
        walk = folder + "/code-walk.py"
        if not os.path.isfile(walk):
            sys.exit(f"code-walk: no walk file at {walk}; write it first "
                     "(SKILL.md, step 3)")
        return walk_build.main([walk] + (["--rev", rev] if rev else []))
    args = ["--root", a.root]
    args += ["--old-rev", a.base] if a.base else []
    args += ["--new-rev", rev] if rev else []
    for prefix in a.skip:
        args += ["--skip", prefix]
    if a.verb == "note":
        note = folder + "/WALK-NOTE.md"
        if os.path.exists(note) and not a.force:
            sys.exit(f"code-walk: {note} exists; --force replaces it")
        os.makedirs(folder, exist_ok=True)
        args += ["--note", "--out", note]
    return walk_diff.main(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
