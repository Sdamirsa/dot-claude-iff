# Task: interactive anatomy map on the docs landing page

_Created 2026-11-10 · Status: todo_

Milestone: M-0.3.0

## Goal

A reader of the docs site sees the whole system at one glance and can drill in: the anatomy
(layers and their components), then one component (what it is, what it reads, writes and
invokes), then the file itself on GitHub. Built by the ritual like every other derived
surface, so the published map can never lag the system.

**Definition of done:** all of the following hold in the worktree:

- `python3 .claude/tools/mapctl.py anatomy [--out docs/anatomy.html]` renders ONE standalone
  HTML page from `.claude/system-map/architecture.json`, `map.json`, `layers.json` and the
  cards, with the payload inlined (the same pattern as `consolectl build`: a template plus a
  JSON payload, no runtime fetch). The template lives at
  `.claude/console/anatomy.template.html`. Zero external resources except the Google Fonts
  import `docs/tour.css` already makes; the page links `tour.css` (relative) for the docs
  brand and theme tokens and still reads when that stylesheet is missing. Light and dark both
  read (the `data-theme` attribute and `prefers-color-scheme`, as in `docs/index.html`).
- **Cascade, three levels, one page.** Level 1, the anatomy: every layer from `layers.json`
  (MEMORY, GOVERN, OBSERVE, CONSOLE, EVOLVE, unplaced) as a large tile with its blurb and its
  components as labelled chips coloured by kind (agent, code, data, human: the four semantic
  colours in `tour.css`); a legend; NO edge hairball at this level. Level 2, one component:
  click a chip (or a layer title to zoom one layer) and a detail panel opens with the card's
  title, path, description and its relations grouped (reads, writes, invokes, read by,
  written by, invoked by), each relation itself a clickable chip that moves the cascade on;
  the chosen chip and its neighbours stay lit, the rest dim; a breadcrumb (anatomy > layer >
  component) and Escape step back out. Level 3, the file: an "Open on GitHub" link to the
  blob URL from the existing `mapctl` GitHub-base helper (branch `main`), falling back to the
  relative path when the origin is not GitHub. `#node=<id>` in the URL opens the cascade at
  that component on load, and `?embed=1` hides the page's own topbar for the landing-page
  iframe.
- Readable on a phone (about 400 px): tiles stack, the detail panel becomes a sheet below the
  tiles, no horizontal page scroll; labels never overlap; no element narrower than its text.
- **Registered generator** `anatomy_build` in `checkctl.GENERATORS` (tool `mapctl.py`, tag
  MAP, args `anatomy --out docs/anatomy.html`, inputs the four sources and the template,
  output `docs/anatomy.html`), listed in `HOME_ONLY_GENERATORS` (it renders this repo's
  system into a published folder, like `demo_build`) and appended to the `polish` list in
  `.claude/config/memory.json` after `demo_build`. `checkctl run --phase check` reports it
  under `generator_freshness`, `checkctl generators` lists it, and the parity tests in
  `test_ritual.py` and the home-only gating tests in `test_dist.py` pass with it.
- **Landing page**: `docs/index.html` gains a section right after the header (before
  `#overview`) titled "The anatomy, live" with two lines of prose, an `<iframe>` of
  `anatomy.html?embed=1` (full width, about 560 px tall, `loading="lazy"`, a `title`), and a
  link "Open the full map". `docs/understand.html` gets one link to `anatomy.html` where it
  describes the system map. Hand-written markup; only `anatomy.html` is generated.
- Docs and map: `.claude/README.md` and `.claude/CLAUDE.md` command lines list `anatomy`
  for mapctl; `.claude/system-map/cards/tool.mapctl.json` description mentions it; if
  `checkctl probe` enumerates generator outputs, `docs/anatomy.html` is probed too.
- Tests in `.claude/tools/tests/test_anatomy.py` (use `FixtureCase`): the page builds from
  the fixture and carries every node of `map.json` with its path and a file link; the link
  uses the GitHub base when the helper returns one and the relative path otherwise (patch the
  helper); every path the page links exists in the fixture tree; the page makes no external
  request other than the fonts import and github.com links (grep `src=`, `href=`, `url(`,
  `fetch(`); the payload JSON parses; `?embed=1` handling and `#node=` handling are present
  in the script (string presence is acceptable for the JS, the rendering itself is exercised
  by the lead in a browser); the generator row is in `GENERATORS`, home-only, and in the
  polish list. Each test must be able to fail.

**Test:** `python3 .claude/tools/tests/run_tests.py -q test_anatomy test_ritual test_dist test_architecture`

## Context

- Maintainer's ask, 2026-11-10: "an interactive map of the system on the landing page ...
  like a cascade: one big anatomy of the system, then upon interacting more of the component,
  then upon a second interaction go to the file. Using the mechanism we have, added to the
  ritual of releasing and freshness. Also keep it as a separate page."
- Mechanism to reuse: `mapctl.py architecture` already builds `architecture.json` (scopes ->
  groups -> nodes with id, group, label, path, kind; edges) and the Mermaid
  `docs/ARCHITECTURE.md`; `map.json` carries the full cards (description, reads, writes,
  invokes) and `layers.json` the layer blurbs. `consolectl build --demo` shows the
  template-plus-inlined-payload pattern and `demo_build` in `checkctl.GENERATORS` the
  home-only generator registration (`HOME_ONLY_GENERATORS`, `distribution.enabled`).
- Hooks and tools are bash + python3 stdlib only; the page is vanilla HTML, CSS and JS, no
  library, no CDN (the docs site's only external host is Google Fonts, via `tour.css`).
- Brand: the docs site carries the am-tribe tokens in `tour.css`; the console and the kit do
  not. The anatomy page is a docs page, so it may use `tour.css`.
- The lead reviews the diff, merges with `statectl accept`, adds the CHANGELOG bullet and
  runs the ritual that builds the page for real.

## Plan

- [ ] Renderer `mapctl.py anatomy` + template, done when: the command writes the page from
      this repo's own map and `test_anatomy.py` builds it from the fixture.
- [ ] Cascade UI (three levels, breadcrumb, Escape, `#node=`, `?embed=1`, phone layout),
      done when: opened in a browser from `docs/anatomy.html`, all three levels work by click
      and the embed hides the topbar.
- [ ] Generator registration (`GENERATORS`, `HOME_ONLY_GENERATORS`, `memory.json` polish
      list, probe), done when: `checkctl generators` lists `anatomy_build` and the parity and
      gating tests pass.
- [ ] Landing page section + understand link + command docs + card, done when: the files
      named above carry the text and `grep -n anatomy docs/index.html` shows the iframe.
- [ ] Task Test and the whole suite green, envelope written, `checkctl handoff` exits 0.

## Checkpoint

- **Last completed:** nothing yet; card written 2026-11-10.
- **Next action:** dispatch the builder (lead), builder reads the card and the sources.
- **State files:** `.claude/state/handshakes/031-anatomy-map.json` (builder's envelope, in
  the worktree), `docs/anatomy.html` (output), `.claude/system-map/cards/tool.mapctl.json`.
- **Updated:** 2026-11-10

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

