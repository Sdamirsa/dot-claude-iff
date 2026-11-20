# Upstream

- **Source:** https://github.com/Sdamirsa/vllmme, folder `.claude/skills/code-walk/`, by the
  same maintainer as this project (a private repository; the adaptation is the author's own
  and ships here under this project's licence).
- **Fetched:** 2026-11-20, at upstream commit `cd4c53ce411852d65917755ae66cbe94424871a3`
  (the last commit touching the folder).

## What changed

Kept: the page (`scripts/page.html`, byte for byte), the diagram and line-lookup helpers
(`walklib.py`), the builder and its checks (`walk_build.py`), and the rule that a walk never
types a line number.

- **General.** No path, file name, chapter group or person of the source project remains.
  The walks folder is a default (`docs/code-walk/`) that the user or `--root` overrides;
  nothing but the walks folder itself is left out of a diff unless `--skip` says so.
- **A chapter per change.** Upstream walks every line of the repo and audits that coverage.
  Here a version's walk shows what changed, fed by a walk note; walking a whole file or
  repo stays possible with the same builder. `walk_audit.py` (every line shown) and
  `walk_part.py` (chapters written apart) were not taken.
- **`walk_diff.py` rewritten.** It still starts from the commit the last walk was built on,
  now found by itself as the newest page under the walks folder. It no longer rebuilds the
  old walk to compare steps, and `--note` writes the walk note's skeleton. Upstream calls
  that note a hand-off; here that word already means the builder's envelope and the resume
  note, so it is the walk note, `WALK-NOTE.md`.
- **One command.** `walk.py changes|note|build <version>` wraps the scripts, so the user and
  the agent type the same short line.
- **User-invoked.** `disable-model-invocation: true`: the skill costs the agent's context
  nothing until `/code-walk` is typed, and no guide outside this folder describes it.
- **Windows.** Git output is read as UTF-8 and paths are normalised to `/`.
