# Distribution boundary - keeping home-repo-only content out of the kits

`distctl.py` builds the two distribution zips from this repo's own git-tracked `.claude/` tree
(`_payload_entries`). Three mechanisms keep this repo's private, machine-local, or historical
content from riding along. A new home-only surface picks ONE of these; it never invents a fourth.

## 1. Structural placement outside `.claude/`

Cheapest, no code: put the content where packaging never looks. `_payload_entries` walks
`.claude/` only, and `.claude/skills/adopt/SKILL.md`'s copy manifest is likewise `.claude/`-
scoped, so a root-level file structurally cannot travel - `CHANGELOG.md` lives at the repo root
for exactly this reason. Use when the content does not need to live inside `.claude/` at all.

## 2. The `distribution.enabled` knob

For behavior that ships everywhere (the code is fine to distribute) but must only ACT in the
home repo: the `demo_build`/`dist_build` generators (`checkctl.py`'s `HOME_ONLY_GENERATORS`,
`distribution_enabled()`) and the `changelog_parity` CHECK step (`check_changelog_parity`). Reads
`.claude/config/memory.json`'s `distribution.enabled`; an absent key reads false, so an adopting
project fails closed. Use when the component must not RUN outside the source repo.

## 3. distctl content normalization

For a config file that MUST ship (an adopter needs the key) but whose current value is this
machine's or this repo's own choice. `distctl.py` rewrites specific keys on the way into the
zips: `_adopter_memory_config` forces `distribution.enabled` false; `_adopter_console_config`
forces `port` to `"auto"` and `monitor.enabled` false. Use when the SHAPE of a config must ship
but a particular VALUE would leak a decision only valid here.

## Decision rule

1. Can the content live outside `.claude/`? Use placement (1).
2. Must it ship, but only act at home? Use the knob (2).
3. Must it ship as a config value that differs per machine/repo? Normalize it in distctl (3),
   and add a `test_dist.py` assertion for what the kit actually lands with.

Mechanisms 2 and 3 already have tests (`test_dist.py::TestDistributionGate`); a new home-only
surface extends one of these three, never a fourth.
