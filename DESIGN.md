# Design system — kanton editor

The editor (`src/kanton/editor/index.html`) is one dependency-free HTML page: a studio bench with the library on the
left, the reel in the middle, the selection's controls on the right and time below. Operate mode: earned familiarity for
a mobile-editor user, brand in the details.

## Tokens

| token | value | use |
|---|---|---|
| `--bg` | `#14151a` | page ground, stage area |
| `--panel` | `#1d1f26` | top bar, tab rail, inspector, toolbars |
| `--panel2` | `#181a20` | library list, timeline ground |
| `--line` | `#2d303a` | every hairline separation and control border |
| `--ink` | `#e8e8ec` | text |
| `--muted` | `#9a9db0` | labels, hints, secondary text (≥ 4.5:1 on the panels) |
| `--dim` | `#6b6e80` | tertiary labels (keyframe field names, summary chevrons) |
| `--acc` | `#f4c70f` | the one accent: selection, playhead, markers, primary action, focus ring |
| `--acc-ink` | `#1a1500` | text on the accent |
| `--err` | `#e06060` | errors in the status |
| `--r` | `6px` | control radius (chips are pills; cards and the stage use 8px) |
| `--t` | `160ms cubic-bezier(.2,.7,.2,1)` | every hover / selection transition |

Track colours: video `--v1 #3a6ea5`, overlays `--ov #8a5fbf`, text `--tx #c9a227` (dark ink on it), audio `--mu #3f8f6b`,
effects `--fxc #b84a7a`; a still block is a darker video blue (`#2f5a87`) with a lighter border.

## Type

One family: the system sans (`-apple-system, "Segoe UI", Inter, sans-serif`), tabular numerals everywhere.
13px UI text · 12px section titles and secondary labels (uppercase, 0.04em tracking, weight 600) · 11px track labels,
hints, keyframe fields, tab labels and ruler ticks (the floor) · 15px wordmark · the preset's own font (`KantonText`, served
from `/font`) only inside the stage and the text area, at the reel's size.

## Controls

One shape: 1px `--line` border, `--r` radius, `#262933` fill; hover lifts the border to `#4a4e5c`, active darkens,
disabled is 40% opacity, primary is the accent fill with dark text and weight 600, an "on" state borders in the accent.
Inputs and selects share the darker field fill `#12131a`. Checkboxes and ranges take the accent. Focus is a 2px accent
outline, offset 1px, on `:focus-visible` only. Scrollbars are 8px, thumb `#3a3d49`, track transparent. Selection colour
is the accent with dark ink.

Icons are inline SVG symbols (`<symbol id="i-…">`), 16px (18px in the tab rail), 1.6px rounded stroke, `currentColor`,
no fill; never a glyph or emoji.

## Layout

Rows: top bar 44px · main · timeline `minmax(230px, 34vh)` · key legend. Main columns: library 300px (56px tab rail +
list) · stage · inspector 320px. The stage is the largest 9:16 that fits, with an 8px radius and a soft offset shadow;
everything drawn in it lives in 1080×1920 coordinates under one `scale()` transform. Lanes are 36px; blocks 26px with
7px trim handles; the video lane is 56px (blocks 48px), audio lanes 44px (blocks 36px); the ruler 22px with flag markers
(labels on the timeline ground so a tick never shows through); the playhead is a 1px accent line with a triangular grip.

## Timeline pictures

A clip carries a filmstrip: frames from a server sprite (`/strip/KEY.jpg`, one row, 2 frames a second at most), one tile
per tile-width, each tile the source frame under its centre (speed, curve and reverse followed). Its label sits on a
dark pill (`rgba(12,13,18,.66)`) over the frames. A clip with sound shows its waveform in a 12px band at the bottom (white
75 % on a dark band); music and sounds show a centred waveform in pale green (`rgba(225,255,238,.5)`), square-root
scaled. Beats are 5px accent dots along the top of the music. Only the visible part of a block is drawn; scrolling
redraws on the next frame.

## Speed

The clip's Speed group opens on a two-part segmented control (Normal | Curve; the open half is an inset 2px accent
underline). Curve shows the named ramps as 64×34 tiles drawing their line (the chosen one in the accent), then the
editor: a 288×120 field, speed on a log scale (0.1×, 1×, 10× hairlines labelled in the right margin), the curve a 2px
accent line, points as 5.5px rings that fill on hover; drag to move, double-click to add or remove. A readout says
"source → reel" seconds.

## Busy and live states

A long job (export, auto captions) keeps its button at full opacity with a progress cursor and says what it does
("Exporting… 12 s", "Transcribing…"). Recording a voice-over turns its button into "Stop recording · N s" with an 8px
`--err` dot and border. Dropping files shows a full-window 2px dashed accent frame on the dimmed bench.

## States

Selected: 2px accent outline on a timeline block, accent border on a stage box, accent border on a library item.
Hover on list items and chips: `#22252e` / `#262933` fill. Empty states teach (no clip, no words, no sticker, no
sound) in `--muted` with the key action in `--ink`. Errors go to the status in `--err`, prefixed ✗. Collapsible
inspector groups use `<details>` with a rotated hairline chevron.

## Motion

160ms ease-out on hover, selection, chevrons and tab changes; no entrance choreography; the only continuous motion is
the preview itself (playhead, layers).

## Preview approximations

The stage approximates the export: looks as CSS `filter`, vignette as an inset shadow, keyframes and animations as CSS
transforms from a port of `motion.py`, masks as `clip-path` (rect, rounded, circle), blends as `mix-blend-mode`,
text shadow as `text-shadow`, the text box as a rounded div behind the lines. Each such element carries the tooltip
"preview approximation". Flips, rotation, crop, fit, stills and reverse are cut by the server into the player's pieces.
