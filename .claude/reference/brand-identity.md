<!-- last-reviewed: 2026-08-25 -->
# Brand identity - the softly adopted subset

This repo's public face (the docs site under `docs/`) softly adopts the **am-tribe** identity.
The full brand guide is private and lives gitignored at
`.claude/reference/private/am-tribe-brand-guide.md`; this file is the committed subset that the
docs actually use, and the rules for where the brand stops.

## What is adopted

**One motto**, in the tour's opening and closing footers only:

> "Good unite hearts, cover all, break none."

**Fonts** (docs site only, via Google Fonts with full system fallbacks - the page renders fine
if the font host is unreachable): Nunito 400/600/700/800 for text, JetBrains Mono 400 for code.
Vazirmatn stays in the stack as the Persian fallback for future content.

**Colors** (light theme): the am-tribe neutrals and three brand hues, in roughly the brand's
80/12/8 hierarchy.

| Token | Value | Brand name |
|---|---|---|
| page background | `#FAF9F6` | warm off-white |
| headings | `#0F172A` | slate-950 |
| body | `#1E293B` | slate-800 |
| muted | `#64748B` | slate-500 |
| borders / hover | `#E2E8F0` / `#F1F5F9` | slate-200 / slate-100 |
| accent (dominant) | `#2563EB` | Ocean Blue |
| ok / growth | `#059669` | Earth Green |
| warm callouts | `#D97706` | Moon Amber |

## Where the brand stops, and why

- **The console and the distribution zips carry no brand.** They install into other people's
  repos; a generalizable system stays brand-neutral and zero-fetch (no external fonts, no
  external anything). The console footer's links are derived from the adopter's own git
  remote, never from this repo.
- **The system's four kind colors are semantics, not decoration**, and are unchanged: agent
  orange, code blue, data green, human gold. They happen to echo the brand's ocean/earth/moon
  family, which is why the two palettes sit together without a fight.
- **Dark mode is the docs site's own addition** (the brand is light-only) and keeps its
  pre-brand values; only the light theme is brand-toned.
- **No heavier adoption**: no logo, no narrative sections, no Persian-carpet palette, no
  section color-coding. If that ever changes, it changes here first.
