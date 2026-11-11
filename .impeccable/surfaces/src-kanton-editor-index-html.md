---
version: 1
slug: "src-kanton-editor-index-html"
primary_target: "src/kanton/editor/index.html"
related_targets: []
---

# Editor page (src/kanton/editor/index.html)

Mode: Operate. The visitor is a creator who knows mobile editors, finishing one vertical reel that an agent (or they) cut as
`montage.json`. Task frequency: a few sessions a week, 10–40 minutes each, on a laptop, often with the reel's own audio
playing. Success: every feature the spec carries is reachable in two clicks, the preview shows the result
closely enough to decide, and Save writes the same JSON an agent would.

Audience and job: pick a clip, trim/split, add b-roll or a sticker, place a text, choose a transition or a look, set a
keyframe, listen, preview, export. Proof/content: the spec's own blocks, overlays, texts, sounds, markers, effects; the
catalog served by `/api/catalog`; the preset's roles and filters.

Constraints: one dependency-free HTML file served by the Python server; no build step, no framework, no CDN; keeps every
existing behaviour (Media Source player of server-cut pieces, words panel with cut / keep / move / copy / fix, I/O/S
keys, drag and trim, snapping, pywebview start screen, layout endpoint for texts, proxy preview); the incumbent visual
world stays (near-black panels, one yellow accent, system sans).

## Direction contract

THESIS: a studio bench, not a form. Library on the left, the reel in the middle, the selected thing's controls on the
right, time below; the arrangement mobile-editor users carry in their hands, executed with kanton's restraint. Refused: the
single inspector column of the current page, where every feature would become another field in a long list.

OWN-WORLD: near-black ground (#14151a), slightly cooler panel (#1d1f26), hairline separations (#2d303a), one yellow
accent (#f4c70f) for the selection, the playhead and the primary action only; system sans, 13px UI, 11px track labels,
tabular numerals for times; track colours stay the four incumbent hues (video blue, overlay violet, text gold, audio
green) with an added effects magenta; controls share one shape (6px radius, 1px hairline, filled yellow for primary).
Icons are drawn SVG, one stroke weight, never emoji.

STORY: the creator understands in one glance where their media, their reel and their controls are; believes the preview
is what they will get; acts by clicking a catalog item (a transition, a look, an effect, an animation) and seeing it land
on the selected clip or at the playhead.

FIRST VIEWPORT (1440×900): top bar 44px: name, status, Undo / Redo, Preview, Export (format), Save. Left 300px: vertical
tab rail (Media, Audio, Text, Stickers, Effects, Transitions, Filters, Adjust) with its list. Centre: the 9:16 stage at
the largest size that fits above the timeline, with the safe zone, on-canvas selection boxes and handles, transport
(play, time, frame step, zoom-to-fit). Right 320px: the inspector of the selection, grouped in collapsible sections, or
the words panel when nothing is selected. Bottom 300px: toolbar (split, delete, duplicate, freeze, marker, snap, timeline
zoom) over a ruler with markers and lanes: Video, Overlays (packed lanes), Text, Effects, Audio (music + sounds).
Primary action: Save, top right, yellow.

FORM: the three-pane NLE bench, brief-pinned by the user's reference to a mobile editor; position 1 of the ordered list by the
brief's own words. seed: none (unavailable)

Signature interaction: a catalog item lands where it belongs. Clicking a transition applies it to the selected clip's
cut and draws a small chevron on the cut; clicking a look paints the selected clip (or the whole reel with ⇧) and the
preview updates through a CSS approximation at once; clicking an effect drops it on the effects lane at the playhead.
Motion grammar: 150–200 ms ease-out on panel and selection changes; no entrance choreography.

Honest risk: density. Eight tabs and a deep inspector can overwhelm; the answer is grouping and defaults shown, not
fewer features.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and
every shipping raster carrying its provenance

## Pass 2 (11 Nov 2026): what a mobile-editor hand still missed

Brief inferred from the user's "go, don't prompt me" (no interview round). Mode stays Operate; the incumbent world stays.
Added where a mobile-editor user reaches for them: filmstrip and waveform on the clips, waveform and beat dots on the music;
Speed as Normal | Curve with named ramps and a draggable log-scale graph; Stabilize in Framing; Voice effect in Sound
(clips, music, sounds); Beats in the music inspector; Auto captions in the Text tab and the empty words panel; Record
voiceover at the top of Audio; drop files anywhere to import; Export renders the file next to the project. Snapping now
takes cuts, the playhead, markers and beats while dragging, and a click without a move never changes the file.
Anti-goals kept: no state outside the spec (beats live in `music.beats`, curves in `speed`), no thumbnails in the file.
Verdict: mechanical detector clean; checked at 1440×900 in Chrome.
