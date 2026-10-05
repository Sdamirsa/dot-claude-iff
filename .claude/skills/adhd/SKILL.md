---
name: adhd
description: Parallel divergent ideation - isolated branches under different cognitive frames, then a separate critic pass that clusters, flags traps and shortlists. User-invoked only (/adhd), because one run costs about ten agent calls. For an open, divergent question (architecture, public API or SDK surface, schema, naming a real product, a fuzzy bug with no known root cause) you may SUGGEST "/adhd <problem>" in one line; never run it unasked. Not for lookups, syntax, bugs with a known root cause, or asks phrased "quick", "just", "standard" or "textbook".
disable-model-invocation: true
license: MIT
---

# ADHD

Stop picking the textbook answer. The first three answers the model would
give are the answers a senior engineer would give in thirty seconds.
Correct. Forgettable. The interesting answers live past number three, in
the awkward middle nobody walks into. This skill makes the model walk
there.

Adapted from [UditAkhourii/adhd](https://github.com/UditAkhourii/adhd)
(MIT, Copyright (c) 2026 ADHD contributors). The licence is in `LICENSE`
beside this file; what changed from upstream is in `UPSTREAM.md`.

## Step 0 - read the tuning

Read `.claude/config/brainstorm.json` before anything else. Every number of
branches, every frame and every model below comes from it:

| Key | Used for |
|---|---|
| `brainstorm.branch_count` | how many frames to pick, so how many parallel Diverge branches |
| `brainstorm.frames` | the frame catalogue: `name`, `vantage` prompt, `tags` |
| `brainstorm.branch_model` | the Agent tool `model` for every Diverge branch |
| `brainstorm.critic_model` | the Agent tool `model` for the critic and the deepen calls |

A model value of `inherit` means: leave the Agent call's `model` parameter
out, so the call runs on the session's model. If the file is missing or a
key is unreadable, say so in one line and use 5 branches, `sonnet` and
`inherit`; if `brainstorm.frames` is missing, stop and report it rather
than inventing frames. If `brainstorm.branch_count` exceeds the catalogue,
use every frame and say so.

## Pre-flight

The user typed `/adhd`: they opted in and accepted the cost. Do not
second-guess, go straight to Phase 1.

## The loop

Two strict phases. Mixing them kills idea quality, because the critic
strangles the generator.

### Phase 1 - Diverge (no critic)

For the problem P:

1. Pick `brainstorm.branch_count` frames from `brainstorm.frames`. Bias
   toward `code` and `design` tags when the problem is code-shaped. Always
   include at least one `wild` frame to keep range. Vary the picks across
   runs so the same problem produces different candidate sets when re-run.

2. Spawn one **parallel** Agent call per frame, all in the same message,
   each with `model` set from `brainstorm.branch_model`. Each Agent gets
   only:
   - the problem P
   - any context the user provided
   - the chosen frame's vantage prompt
   - an instruction that forbids evaluation

   The exact instruction to give each Agent:

   > You are in DIVERGENT mode. You are a generator, not a critic.
   > Generate 6 short distinct ideas under this frame. Each idea is one
   > phrase or one sentence. Do not evaluate. Do not rank. Do not hedge.
   > The first three obvious answers everyone would give are banned.
   > Push past them into the awkward middle.
   > Output a JSON array only. No prose before or after.
   > `[{"text": "...", "rationale": "..."}, ...]`

3. **Critical invariant.** The Agent calls must be parallel and isolated.
   Do NOT serialize them. Do NOT pass one branch's output as context to
   another. Branches that see each other anchor each other and the whole
   method collapses to a wider single thought.

A branch that fails or returns output you cannot parse is reported in the
Brief as failed, never silently dropped or re-generated in your own context.

### Phase 2 - Focus (critic on)

After all branches return, both calls below use `brainstorm.critic_model`.

1. **Critic pass.** One Agent call, given P, the user's context and the
   pooled ideas. It:
   - judges each idea on novelty (distance from the obvious default),
     viability (could it actually ship) and fit (does it address the stated
     problem) **in words, not numbers**: a one-line note where an idea
     stands out or falls short. A numeric rating from one model pass is
     invented precision; a stated reason can be checked.
   - flags any idea that looks attractive but is a trap (hidden cost, false
     economy, will not scale, premature abstraction), with a one-line reason.
   - groups ideas into 3 to 6 clusters by their underlying angle, not by
     surface keywords, labelled by angle: "remove the server plays",
     "cache-shaped plays", "batched-window plays".
   - names the 3 strongest non-trap ideas to deepen, each with the one-line
     reason it was chosen over its neighbours.

2. **Deepen the top 3.** One parallel Agent call per chosen idea:

   > You are in FOCUS mode. Take one promising idea and connect dots.
   > Sketch how it would actually work in 4 to 8 sentences. Name the
   > load-bearing risk. Name the first concrete step a coder would take.
   > Then generate 3 to 5 sub-ideas that branch off (variations,
   > combinations with other domains, things this unlocks).
   > Output JSON only.

## Output shape

After Phase 2, render in this order. Do not collapse it into a wall of
prose. The structure is the point.

1. **Brief.** One or two lines: the problem, any reframe used, and the
   tuning that ran (branch count, frames picked, both models, any failed
   branch).
2. **Wide set.** The full pool grouped by cluster, each cluster labelled by
   its angle, each idea one short phrase. No scores.
3. **Converge.** A shortlist of 2 to 4 ideas, each with why it is there.
   Mark exactly one as the pick (the non-obvious-but-viable one) with a
   one-line reason. List traps separately, each with the one-line reason it
   is a trap.
4. **Focus.** The 3 deepened branches. For each: the sketch, the
   load-bearing risk, the first concrete step, and the child ideas.
5. **Provocation.** One wildcard question or idea that opens a new
   direction the user can push into if nothing landed.

**Honesty wins.** Brevity and "commit to a pick" never override
`.claude/protocols/honesty.md`. "Not enough evidence, missing X" is a valid
pick: name X and how to get it. A failed branch, a trap you could not
resolve, or a pick resting on an assumption you did not check is stated as
such, not smoothed over.

## Anti-patterns

These are how this skill goes wrong. Watch for them.

- **Convergence disguised as divergence.** Ten minor variations of one idea
  is not breadth. If every candidate shares the same underlying assumption,
  you have not diverged. You have decorated.
- **Weird-for-weird's-sake with no convergence.** A pile of 30 unsorted
  absurdities is as useless as one safe answer. Always converge.
- **Walls of equally-weighted prose.** Cluster, label, pull out the best.
  Structure is half the value.
- **Refusing to commit.** After diverging, take a position on what is
  actually promising. "Here are 20 ideas, you decide" is a cop-out.
  Generate wide, but converge with a real opinion - and when the evidence
  does not support one, say what is missing (see Honesty wins).
- **Skipping the isolation invariant.** If you simulate parallel branches
  by writing them sequentially in one context, you have not done ADHD. You
  have done a wider single thought. The Agent tool gives each branch a
  fresh context. Use it.

## Calibration

- **How many ideas?** The branch count is `brainstorm.branch_count`; scale
  ideas per branch to stakes instead: about 4 for "name this function", up
  to 8 for "how should I position this product". The instruction above
  asks for 6.
- **How weird?** Read the room. Serious strategy work: flag the wild cards
  clearly so they do not read as unserious. Open brainstorming or play:
  let it run loose. Absurd ideas earn their place by seeding viable ones.
- **When to stop diverging?** Stop when new candidates start repeating the
  shape of existing ones. The space is mapped. Do not pad to hit a number.

## Cost

`brainstorm.branch_count` Diverge calls + 1 critic + 3 deepen calls per
run, about ten Agent calls at the shipped tuning and several times a
single-shot answer. That is why it is user-invoked: for decision points
where the cost of the obvious answer is high.

## Tuning

The knobs live in `.claude/config/brainstorm.json`, each with a card in
`.claude/config/registry.json`. The `/project-memory` ritual's EVOLVE step
may propose changes to them (a frame to add or retire, the branch count,
either model) under the same bar as any evolution change
(`.claude/protocols/evolution.md`): evidence from real runs, at most three
proposals, confirmed by the user. Never edit the file mid-run to suit one
problem.
