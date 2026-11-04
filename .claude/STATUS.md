# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.3.0-alpha.2 is the published pre-release (from `dev`). The review round has not started;
this session was research and planning: the console's next feature is a GitDiagram-style
architecture diagram (PR-15, design in `.claude/research/2026-11-04-code-graph-console.md`,
sample the maintainer approved), the open proposals are triaged, and a three-session track
to 0.3.0-beta.1 is set.

## Active tasks

- none. The next session opens with the build round below; PR-15 becomes a task card there.

## Next steps

1. **Enter build**: `python3 .claude/tools/statectl.py phase build --override "build round
   from the 2026-11-04 triage"` (or a sign-off of what was tested).
2. **Six small fixes, one commit and one test each, in order:** PR-14 CLAUDE.md trim,
   PR-16 CREDITS.md, PR-9 generator output-hash freshness, PR-10 accept removes the stub,
   PR-8 consent before remote analysis, PR-5 deny rules under the gate.
3. **PR-15** as one task card for a builder in a worktree (mode fableous): `architecture.json`
   per scope, `.claude` scope from the cards first, `docs/ARCHITECTURE.md` Mermaid with GitHub
   click links, the MAP tab scope switcher, `graphctl.py build|update|status`; graphify is the
   `src/` skeleton and the drill-down. Lead reviews with `/code-review`, accepts, ritual.
4. **Review round** (next session after): checklist items 4, 10, 7, then `/adhd` with the
   PR-12 comparison; findings become proposals; sign-off.
5. **Release 0.3.0-beta.1** from `dev`. After it holds: PR-11, PR-4 maturation, merge to
   `main`, stable tag, PR-13 Zenodo.

## Blockers / open decisions

- graphify extraction is a human-terminal command by the maintainer's choice; the ritual only
  normalises what exists. Pinned-binary option recorded in the research note.
- Open proposals: `python3 .claude/tools/statectl.py proposal list` (13 open, triage logged).

## Watch-outs

- A reference URL is not a specification: show one sample of the intended output before
  filing a feature proposal (L-13).
- Check `git rev-parse --show-toplevel` before committing; read the CHECKOUT line (L-11).
- Names of files the system reads but never ships go in prose, never backticks (L-12).
- The policy gate refuses interpreter code that names the record root next to a write call;
  read record files through `cat ... |` into the script instead.
