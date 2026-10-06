# Upstream

- **Source:** https://github.com/UditAkhourii/adhd, file `skills/adhd/SKILL.md`, plus `LICENSE`.
- **Licence:** MIT, Copyright (c) 2026 ADHD contributors. `LICENSE` here is the upstream file,
  byte for byte; the copyright and permission notice must stay with every copy.
- **Fetched:** 2026-10-05, at upstream commit `55ed38514cd996f6096b51c8320331a8a5dced19`
  (the last commit touching `SKILL.md`; repository HEAD was `dd08acc3`).

## What changed

Kept: the method (isolated parallel branches under different cognitive frames, then a separate
critic pass that clusters, flags traps and shortlists, then deepening the top ideas), the
divergent and focus instructions, the frame catalogue, the anti-patterns, the output order.

- **User-invoked.** Frontmatter `disable-model-invocation: true`; the description tells the
  agent it may suggest `/adhd` for open, divergent questions. Upstream's self-judge pre-flight
  is dropped: the skill only runs when the user typed `/adhd`.
- **Tunable.** Branch count, the frame catalogue, the branch model and the critic model moved
  to `.claude/config/brainstorm.json` (one registry card each). Defaults are upstream's branch
  count and frames, branches on `sonnet`, critic on `inherit`. Branches are dispatched with the
  Agent tool's per-call `model`.
- **No invented precision.** Upstream's 0-10 scores, weighted ranking and score chips next to
  each idea are removed; the critic judges novelty, viability and fit in words and states a
  one-line reason for each pick.
- **Honesty wins.** Brevity and "commit to a pick" never override
  `.claude/protocols/honesty.md`; "not enough evidence, missing X" is a valid pick; failed
  branches are reported.
- **Tuning section.** The `/project-memory` EVOLVE step may propose changes to
  `brainstorm.json` under the evolution bar.
- **Removed:** the companion npm CLI section and the pointer to upstream's `SOURCE-SPEC.md`
  (not vendored here; read it upstream).
