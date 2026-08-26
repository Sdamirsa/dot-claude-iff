# STATUS

_Rewritten by `/project-memory`. Read this first, every session._

## Current focus

v0.2.2 is released (tag, push and GitHub release with both zips). It carries a security fix
that mattered: the policy gate was failing **open** on Windows through four independent holes,
so the protected tree was unguarded on that platform. Alongside it, the console learned three
things - its own port (derived from the folder name, no per-project decision), its machine
(a named device identity that flags a move), and its machine's vital signs (an opt-in,
live-only CPU/RAM/GPU strip). The suite is green on Windows for the first time.

## Active tasks

- none. New work starts with `/plan-task`.

## Next steps

1. **Restart the Claude Code session** before trusting the gate on Windows: the PowerShell
   entry in `settings.json`'s PreToolUse matcher only arms at session start, so this
   platform's second shell lane stays ungated until then.
2. Name any other machine that runs this repo: `statectl.py device "<alias>"`. Until a box is
   named, every session start prints the DEVICE line.
3. Re-run `/adopt` against a real second repo to validate the port-"auto" path end to end
   (a fresh adopter should never be asked to choose a port).
4. Configure the analysis engine (STORY tab setup guide) and run a first retrospective pass.
5. Hard-gear session when ready: `/project-memory --hard`, with the queued items (a durable
   proposal drop-box for gate-denied sub-agent work; verifier's scripted invoker;
   memory-spine stores in mapctl; a `command` kind for adopter `commands/*.md`).

## Blockers / open decisions

- none. The needs-human queue is empty; both feature approvals from this session are resolved.

## Watch-outs

- Build a path for prefix matching with `as_posix()` and test containment with
  `os.path.normcase`; run the suite on every OS the system claims before tagging (L-9).
- A gate that enumerates harness tool names needs one test per shell lane - a lane with no
  test is a lane with no gate (L-10).
- README-embedded assets use percentage widths; a local render at the wrong container width
  is false confidence (L-8).
