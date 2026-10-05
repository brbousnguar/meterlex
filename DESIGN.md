# Meterlex — Atlas, cut square

The design system for the Meterlex web app. **This file and `frontend/src/index.css`
change in the same commit.** A spec that drifts is worse than none.

It is the house **Atlas** theme (warm drafting paper, a soft serif, cards that lift a
little) with one change that makes it Meterlex's own: **every shape is cut square.**
Until 2026-09-30 the app wore *Meter Room*; that spec is kept in
[`docs/meter-room.md`](docs/meter-room.md) for the apps that still build on it.

## Why Atlas, and why square

An atlas is a book of plates drawn on paper, each one explaining one thing. That is
what a screen of this app is: the reading, the machines, the harnesses, the models.
So the ground is drafting paper with a faint dot grid, and everything sits on a card.

Atlas rounds its cards, its controls and its bars. Meterlex does not. A meter is an
instrument and its marks are cut, not moulded: bars end flat, tiles meet at corners,
the drums of the reading are small square cards. `--r: 0`, with no exception. The
style audit counts **0 distinct radii** across all screens, and that number is part
of the spec.

What stays from Meter Room, because it was never about the look: **the number is the
design.** If a screen's biggest element is not a figure, that screen is wrong.

## Ground and ink

Measured with `check-palette.py` against paper `#F7F2E8` and ink `#1F2A44`.

| Token | Hex | On paper | On a card | Use |
|---|---|---|---|---|
| `--paper` | `#F7F2E8` | — | — | the sheet; the page never goes darker than this |
| `--paper-top` | `#FAF6EE` | — | — | the top of the sheet, the header, inputs |
| `--surface` | `#FFFDF8` | — | — | cards, drums, the thumb of a control |
| `--surface-2` | `#EFE6D6` | — | — | wells: tracks, leading-zero and cent drums |
| `--ink` | `#1F2A44` | 12.78:1 | 14.02:1 | type, the needle, the busiest bar |
| `--ink-2` | `#55607A` | 5.63:1 | 6.18:1 | sub-lines, notes (5.08:1 in a well) |
| `--ink-3` | `#636C84` | 4.69:1 | 5.15:1 | labels, axis ticks: never in a well (4.23:1) |
| `--rule` | `#E3D7C2` | — | — | card borders and dashed rules, decorative only |
| `--accent` | `#D2643C` | 3.34:1 | 3.67:1 | clay: **fill only** (the mark, the tab rule, the decimal point) |
| `--accent-ink` | `#A4471F` | 5.38:1 | 5.90:1 | clay as type: eyebrows, links, focus rings |
| `--butter` | `#F2C14E` | 1.50:1 | — | **FILL-ONLY**, the one yellow: the needle's shadow in the mark |

The page ground is a gradient from `--paper-top` to `--paper` under a 22px dot grid.
It stops at `--paper` on purpose: `--under` on the darker foot that Atlas normally
uses would drop under 4.5:1.

## Palette — the harnesses

Colour is identity, and the only identities worth a colour are the **harnesses**: the
tools that burn the tokens. Each one takes a hue from the Atlas cut-paper set, and
each hue comes in up to three cuts, because one value cannot do all three jobs:

| Harness | Fill (bars, dots, swatches) | As type on paper / card | Map tile, and the type it carries |
|---|---|---|---|
| Claude Code | `#D2643C` clay | `#A4471F` 5.38 / 5.90 | `#B8532E` + paper 4.79:1 |
| Codex | `#1F2A44` ink | `#1F2A44` 12.78 / 14.02 | `#1F2A44` + paper 14.02:1 |
| Ollama | `#8D72D4` lilac | `#6B55B0` 5.30 / 5.82 | `#7A5FC8` + paper 4.81:1 |
| Antigravity | `#6FA87E` sage | `#3F7A50` 4.58 / 5.02 | `#6FA87E` + ink 5.15:1 |
| Gemini CLI | `#7FAEE9` sky | `#3A69A6` 5.02 / 5.51 | `#7FAEE9` + ink 6.20:1 |
| Copilot | `#D9A932` gold | `#7A5A0C` 5.71 / 6.27 | `#D9A932` + ink 6.57:1 |
| OpenClaw | `#286F73` teal | `#286F73` 5.21 / 5.72 | `#286F73` + paper 5.72:1 |
| Hermes | `#64307C` plum | `#64307C` 8.34 / 9.16 | `#64307C` + paper 9.16:1 |

Tokens: `--h-<name>`, `--h-<name>-text`, `--h-<name>-tile` with `--h-<name>-on`.

**The bright fills never set small type and never carry it.** That is the Atlas rule,
and it is why a map tile has a cut of its own: a tile prints a folder name at 11.5px,
so the two fills that carry neither white nor ink at 4.5:1 (clay, lilac) step one
shade deeper there, and each tile names the ink it carries.

Validated as a categorical set with the dataviz skill's `validate_palette.js`
(`--pairs all`, surface `#FFFDF8`), because a stack orders its parts by size and any
two harnesses can end up neighbours:

- **fills:** worst pair 8.1 under protanopia and deuteranopia (sage / clay), 15.2 in
  normal vision (sky / lilac). Both pass (targets 8 and 15).
- **tiles:** worst pair 9.7 (clay / teal) and 15.4 (sage / gold). Both pass.
- Sage, sky and gold sit under 3:1 against a card, and four hues are under the
  validator's chroma floor. That is the brief (muted, from the theme), and the relief
  is written into the charts: every part is named in a legend and a readout, parts
  are separated by 2px of ground, and no chart asks colour alone to tell two
  harnesses apart. Codex is ink on purpose, outside the lightness band: OpenAI's
  own mark has no hue, and a seventh hue would not have separated.
- **Hermes** (added 2026-10-05) is a deep plum one cut for all three jobs, a step
  under the band (L 0.41) as Codex is: no muted hue inside the band kept the floors
  against both the fills and the tiles. Adding it moves neither worst pair above;
  its own nearest are teal at 10.8 (CVD) and ink / the lilac tile at 16.2 / 16.1
  (normal vision), and it sits 19.1 from `--over`.

Three more colours, and no others:

| Token | Hex | Meaning |
|---|---|---|
| `--over` | `#B8324F` (5.22:1 on paper) | list price above what the subscription costs, a silent collector, a fee that bought nothing |
| `--under` | `#3F7A50` (4.58:1 on paper) | list price below it: the subscription is winning; a collector that is live |
| `--bar` | `#8F98AD` | a row that belongs to no harness: machines, models, folders, origins |

The change chip is a soft ground with a deep ink: `#8A2239` on `#F8DBE1` (6.84:1) when
the reading grew, `#2F5A3B` on `#DCEBDF` (6.42:1) when it fell.

**Machines, models and folders carry no colour.** They are ranked by the number, and
their bars are `--bar`. With eight harness hues as the only hues, colour is signal.

### The token split is ink

`--ramp-1` … `--ramp-5`: `#AAB4CC #8894B3 #69769B #475580 #1F2A44`, an ink wash from
light to deep (validated `--ordinal`: monotone, every step ≥ 0.06 L apart, the light
end 2.04:1 against a card). It is ink rather than the Atlas clay ramp because a hue
here would claim a harness: on the Harnesses screen every harness has a split of its
own, and a clay split inside the Codex card would say "Claude".

## Form language

1. **Cut square.** `--r: 0`. No pill, no disc, no rounded bar end. Status lamps,
   legend swatches and the decimal point of the reading are small squares.
2. **Cards lift, a little.** A card is `--surface` with a 1px `--rule` border and a
   long soft shadow (`--shadow`). A card never nests: inside one, stats are a ruled
   grid and lists are separated by dashed rules.
3. **Buttons press.** A button has a 1.5px ink outline and a hard 3px ink shadow that
   grows on hover and presses in on a tap (the steppers, Save, the links out).
4. **Controls slide.** A segmented control is a paper well with one card thumb that
   *moves* to the pressed option (`Seg` and `useThumb` in `App.tsx`). The tabs use
   the same thumb: a card in the header well on a desktop, a clay rule over the bar
   on a phone. The thumb is an element that stays; rebuild it and it cannot slide.
5. **One decoration, derived from the subject:** the odometer. One drum per digit,
   each a small cut card; leading zeros and cents sit back in a well in `--ink-2`.
   In euros it keeps two cent drums after a square clay decimal point, because a
   reading of €0,86 must not round to 1. Ten drums give the exact count but not its
   size, so **the line above the reading says the size**, in both units:
   `1.01B tokens, metered on 2 machines`. Nothing else is added around the drums. On
   a phone the unit word after the drums is dropped (the line above says it) and the
   drums take its room.

## Type

| Face | Role |
|---|---|
| Fraunces 650–700, `SOFT 50` | every number, every title, the drums |
| Inter 400–600 | reading, controls, tabs |
| JetBrains Mono 400–600 | machine names, model ids, folder names; uppercase with +.12em for eyebrows, stat labels and axis ticks |

Machine names and model ids are mono because they are identifiers you copy, not prose.

## Charts

Forms follow the dataviz skill, except where the house rule is stricter (square bars).

- **Tokens per day is stacked by harness**, in one order for the whole period so a
  colour keeps its place, with 2px of ground between parts. Every part is named in
  the readout and in the legend under the chart.
- **A part is its own target.** Which part the pointer is on is worked out from its
  height in the stack, not from the element under it: a harness with 1% of a bar is
  a 2px band no pointer can enter. Focusing one part keeps its colour, outlines it in
  ink, and recedes every other part in every bar; the legend focuses the same way and
  pins on a tap. ↑ ↓ walk the parts, Esc clears.
- **The readout belongs to the pointer.** It is a card that appears while the chart is
  being read (pointer on the plot, or keyboard focus) and leaves with it. The picked
  column is underlined in ink, between the bar and its label, for as long as the
  readout is up. On a phone (≤719px) the readout is a panel above the plot with one
  fixed minimum height, always shown, so it never covers the bar being read.
- **The plot is the hit target.** Pointing anywhere picks the bar under the pointer.
  The busiest bucket is selected on load and ← → Home End move between buckets. Drawn
  with plain elements, no chart library.
- **Bars grow from the axis** when a period loads; the gap between bars scales with
  the plot (`--bar-gap`, 2px–8px) so thirty days still read as thirty bars.
- **Ranked rows** are direct-labelled, with an 8px bar on a well. A bar split by
  harness lays its parts in a row. A row with nothing to compare (a silent machine,
  an idle harness) has no bar.
- **The token split is one 100% bar** in the ink wash (see above), 2px gaps.
- **Where the work happened is a map.** A squarified treemap of the folders: area is
  what the folder read in the current unit, colour is the harness that did most of
  the work there, in its tile cut. A folder with no known harness is a well with an
  inset rule. Every tile is also a row in the list beside it.
- **Each machine gets its own bars, not a sparkline**, with its readout printed as
  one line above the 94px plot. The bars wear the machine's main harness; the busiest
  is ink, and it is named in text underneath.
- Every figure a chart shows on hover is also readable as text on the same screen.

## Layout

One column on a phone, in the order of the markup: reading, stats, chart, split,
harnesses, machines, map, folders.

From 720px the tabs leave the foot of the screen for a well in the header, beside the
brand; from 900px the header's second line opens with the page title. From 1000px the
Now screen runs the reading, the stat cards, the chart, the split and the folder map
at full width, then the folder list beside the harness and machine cards. The measure
is 1200px.

**A harness that burned nothing is a row, not a card.** The Harnesses screen gives a
card to each harness that was used (a 4px bar of its colour on top) and lists the idle
ones in a single card, with the fee paid anyway in `--over`.

The header is sticky. On a phone it is plain `--paper-top`: a backdrop filter there
would make the header the containing block of the fixed tab bar inside it. On a
desktop the tabs are static, so the header is frosted.

## Units

One toggle beside the period control switches **every figure** between tokens and
euros, and is remembered between visits. Rankings re-sort to whatever is being
read, and the charts change measure from the same response, because the series
carries both.

**The token split is the exception**, and always reads tokens: cache read against
output is a breakdown of what the tokens *were*, and no per-component price exists to
convert it with. The four stat cards also keep showing both.

Money is always the **list price** in euros: what those tokens would have cost at
API rates, not what was billed.

## Periods

Periods are **local** (`LOCAL_TZ`, default `Europe/Paris`), never UTC: a week starts
Monday 00:00 Paris, a day at Paris midnight. The rows are stored as naive UTC, so
`backend/main.py` converts at both ends (see `_period_bounds`).

## Light only

Atlas is a light theme and so is this app: `color-scheme: light`, one `theme-color`.
The dark palette and the theme button that Meter Room had are gone, not hidden.

## Marks

The app icon is the clay tile of the Atlas mark, full bleed, with a meter dial cut
from paper in six flat facets: four are read, two are still to come, and the ink
needle casts the one butter shadow. `frontend/public/icon.svg` is the source; the
header draws the same polygons from the theme tokens, tilted 8° the way the Atlas
mark sits. Render the PNGs with
`rsvg-convert -w <size> -h <size> icon.svg -o icon-<size>.png` (512, 192, and 180 for
`apple-touch-icon.png`), and bump the `?v=` on the icon links and the cache name in
`sw.js` when the mark changes, or an installed app keeps the old one.

## Verification

Before shipping a change to this file or `index.css`, serve a build
(`scripts/ux.config.json` says how) and run, from the `brb-pwa-mobile-ux` skill:

- **Contrast**, alpha-composited, on every screen: `contrast-audit.mjs scripts/ux.config.json`
- **320px reflow**: `reflow-320.mjs scripts/ux.config.json`
- **Radii and tap targets**: `style-audit.mjs scripts/ux.config.json` (0 radii, nothing under 44×44)
- **The chart**: `node scripts/check-bars.mjs <url>`
- **Palette**: `check-palette.py` for any new colour, `validate_palette.js --pairs all`
  if the harness set changes, `--ordinal` if the ramp does.

2026-09-30, on this spec: contrast **0 failures** on 6 screens, reflow **320 = 320**
on all 6, **0** radii, **0** tap targets under 44×44, `check-bars` PASS.
