# Meterlex — Meter Room

The design system for the Meterlex web app. **This file and `frontend/src/index.css`
change in the same commit.** A spec that drifts is worse than none.

It is a sibling of Fortunex's *Banknote* (`webapps/fortunex/DESIGN.md`) and shares its
form language — flat, hard-edged, measured colour, Archivo / Hanken Grotesk / IBM Plex
Mono — so the apps read as one family on a home screen. What it does not share is the
palette, because a palette is derived from the subject and the subject here is not money.

## Why "Meter Room"

The app is a meter. It reads how much was consumed, by which machine, through which
harness. So the interface is the room where the meters hang: an **odometer** for the
reading, one **meter card** per machine, hairline rules instead of boxes, and numbers
that are the largest thing on the screen. Nothing is a "card with a shadow" because
nothing in a meter room is.

The consequence to keep: **the number is the design.** If a screen's biggest element is
not a figure, that screen is wrong.

## Palette — derived from the harnesses

Colour is identity, and the only identities worth a colour are the **harnesses**: the
tools that burn the tokens. Each takes one measured swatch, from its own brand where it
has one.

Measured against paper `#FBFAF6` and ink `#151512` with
`.claude/skills/brb-flat-poster-theme/scripts/check-palette.py`, and validated as a
categorical set with the dataviz skill's `validate_palette.js`.

| Harness | Token | Hex | White on it | As text on paper | Class |
|---|---|---|---|---|---|
| Claude Code | `--h-claude` | `#B4441F` | 5.54:1 | 5.31:1 | **dual-role** |
| Codex | `--h-codex` | `#2F3A44` | 11.61:1 | 11.11:1 | **the neutral** |
| Ollama | `--h-ollama` | `#6E40C9` | 6.48:1 | 6.21:1 | **dual-role** |
| Antigravity | `--h-antigravity` | `#0E7B3C` | 5.36:1 | 5.13:1 | **dual-role** |
| Gemini CLI | `--h-gemini` | `#1B5EC4` | 6.10:1 | 5.84:1 | **dual-role** |
| Copilot | `--h-copilot` | `#8A5300` | 6.33:1 | 6.06:1 | **dual-role** |
| OpenClaw | `--h-openclaw` | `#00747F` | 5.52:1 | 5.28:1 | **dual-role** |

The teal sits just under the validator's chroma floor (0.087 against 0.1): a teal
that clears it at this lightness stops carrying white. It stays because, as below,
no chart asks colour alone to tell one harness from another.

**Codex is deliberately the one without a hue.** OpenAI's own mark is black, and a
six-hue categorical set cannot separate every pair under deuteranopia — the validator
says so. Rather than invent a seventh-best hue, the system spends its neutral here and
makes the rule explicit: *no chart distinguishes a harness by colour alone.* Every
harness mark is direct-labelled with its name, always.

Two more colours, and no others:

| Token | Light | Dark text | Meaning |
|---|---|---|---|
| `--over` | `#C4322B` | `#F2705F` | list price above what the subscription costs |
| `--under` | `#0E7B3C` | `#4FBF7E` | list price below it — the subscription is winning |

**Machines and models carry no colour.** They are ranked by the number, not painted.
This is the whole point: with six harness colours as the only hues, colour is signal.

### Fill role vs text role

The same trap as Banknote, and the same rule. In dark mode the saturated values stay
**fills** (they carry white at 5.4–6.5:1 on `--surface`); text uses the `--h-*-text`
brights (6.9–9.5:1 on the dark ground) which are **2.3–2.6:1 under white** and must never
fill a block behind white type. Codex inverts with the rest: `#2F3A44` is invisible on a
dark ground, so its dark fill is `#55677A` (5.82:1 under white).

## Form language

1. **Flat.** No shadows, gradients, blurs or glass. Depth is colour weight and scale.
2. **Hard-edged.** `--r: 0`. The pill (`99px`) is the single exception: segmented
   controls, chips and the active tab indicator.
3. **Hairlines, not boxes.** A 1px `--rule` separates; four borders around every element
   do not.
4. **One decoration, derived from the subject:** the odometer's digit cells, each digit in
   its own hairline cell like the drum of a gas meter. It is not ornament — it is what
   makes a 9-digit number readable at a glance. In euros it keeps two cent drums in `--ink-3`
   after a square decimal point, because a reading of €0,86 must not round to 1.

## Type

| Role | Family | Used for |
|---|---|---|
| Display | Archivo 600–800, `-0.02em` | every number, every heading |
| Reading | Hanken Grotesk 400–600 | labels, body |
| Technical | IBM Plex Mono 400–500 | machine names, model ids, session ids |

Machine names and model ids are mono because they are identifiers you copy, not prose.
Nothing else is mono. Anything with `font-variant-numeric: tabular-nums` is a figure and
is routed to Display automatically.

## Charts

Forms follow the dataviz skill; the decisions worth writing down:

- **Tokens per day is stacked by harness.** One colour per bar hid the mix — Claude Code leads
  almost every day, so a month read as one colour. The bar is now split, in one order for the whole
  period so a colour keeps its place, with 2px of ground between parts. Nothing depends on telling
  two hues apart: every part is named in the readout and in the legend under the chart.
- **A part is its own target.** Which part the pointer is on is worked out from its height in the
  stack, not from the element under it — a harness with 1% of a bar is a 2px band no pointer can
  enter. Focusing one part keeps its colour, outlines it, and recedes every other part in every
  bar; the legend focuses the same way and pins on a tap. ↑ ↓ walk the parts, Esc clears.
- **A small multiple keeps its readout above the plot.** Over 94px of bars a floating box covers
  what it describes, so the machine cards print one line — bucket, reading, the other unit — over
  their chart.
- **The readout belongs to the pointer.** It appears while the chart is being read — pointer on the
  plot, or keyboard focus — and leaves with it, so it never sits over the bars on its own. A finger
  lifting is not a departure. On a phone it is a panel above the plot and always shown.
- **The plot is the hit target.** Pointing anywhere on the chart picks the bar under the pointer;
  its readout gives the reading, the other unit, the replies, the harness split (with swatches)
  and its top three models. On a desktop the readout floats beside the selected column and flips
  to its left at the right edge; on a phone (≤719px) it is a panel above the plot, so it never
  covers the bar being read. The busiest bucket is selected on load and ← → Home End move between
  buckets. Drawn with plain elements — no chart library.
- **Harness comparison is a ranked horizontal bar list**, each row direct-labelled and in
  its own colour — identity, not a colour-matching exercise against a legend.
- **The token split is one 100% bar** (cache read · input · output · cache write ·
  reasoning) in a single-hue sequential ramp, with a 2px surface gap between segments,
  because it is parts of one whole.
- **Where the work happened is a map.** A squarified treemap of the folders: the area is what the
  folder read in the current unit, and the tile's colour is the harness that did most of the work
  there — colour is harness identity here as everywhere else. A folder whose harness is unknown
  takes `--tile-none` (white on it: 6.4:1 light, 8.4:1 dark). Every tile is also a row in the
  ranked list underneath, whose bar is split by the same harness colours, so nothing depends on
  reading the map.
- **Each machine gets its own bars, not a sparkline.** A shape with no axis and no
  numbers cannot answer "how many tokens on Tuesday", which is the question that
  screen exists for. The bars carry no colour; the busiest one is weighted, and
  it is named in text underneath so the figure reads without hovering.
- Every chart has a hover layer, and every figure it shows is also readable as text
  somewhere on the same screen.

## Layout

One column on a phone. From 1000px the Now screen splits: the reading, its rail and the chart run
the full width, then the folder map takes the left column beside the harness and machine readings,
with the folder list under the map. The measure is 1200px, not 1080, because two columns need it.

Sub-lines and section notes are `--ink-2`, not `--ink-3`: the lightest ink is for axis ticks and
disabled things, and a screen whose every second line whispers reads as faded.

## Units

One toggle beside the period control switches **every figure** between tokens and
euros, and is remembered between visits. Rankings re-sort to whatever is being
read — in euros a cheap-but-huge model must not sit above an expensive small one
— and the charts change measure from the same response, because the series
carries both.

**The token split is the exception**, and always reads tokens: cache read against
output is a breakdown of what the tokens *were*, and no per-component price
exists to convert it with. The four-stat tiles also keep showing both, since that
is where the comparison between list price and what you actually pay lives.

Money is always the **list price** in euros: what those tokens would have cost at
API rates, not what was billed.

## Periods

Periods are **local** (`LOCAL_TZ`, default `Europe/Paris`), never UTC: a week starts
Monday 00:00 Paris, a day at Paris midnight. The rows are stored as naive UTC, so
`backend/main.py` converts at both ends — see `_period_bounds`. A turn logged at 22:00
UTC counts on the next local day, and the tests pin exactly that.

## Marks

The app icon is a meter dial reading a fraction of full scale, drawn in `--h-claude` on
paper, with the same square full-bleed grid as Fortunex and Vitalex so the three sit
together on a home screen.

## Verification

Before shipping a change to this file or `index.css`:

- **Contrast**, alpha-composited, on every screen in both themes:
  `node ~/Server/.claude/skills/brb-pwa-mobile-ux/scripts/contrast-audit.mjs <cfg>`
- **320px reflow** and **tap targets**: `reflow-320.mjs`, `style-audit.mjs`.
- **Palette**: re-run `check-palette.py` for any new colour, and `validate_palette.js`
  if the categorical set changes.
