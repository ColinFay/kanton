# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two users share one file. An agent (a coding assistant driving the `kanton` CLI) writes and edits `montage.json` from a
transcript and a script. A person retouches the same file in the editor: a creator of vertical short-form video (Reels,
Shorts, TikTok) who knows mobile editors and wants their editing surface without the app driving the result. Inferred from the
brief: the person edits on a laptop, in short sessions, one reel at a time.

## Product Purpose

kanton is a forever free, open source, agent-first, lightweight video editor for vertical short-form video. A montage is a JSON cut
list; `kanton render` turns it into an MP4 with ffmpeg; the editor is an optional view on the same file. Success: a reel
cut by an agent and finished by a person, exported to the platforms' bitrate and loudness rules, with subtitles burned in
from the exact font of the creator's preset.

## Positioning

The file is the truth. Every command and the editor read and write the same JSON, so an agent and a person can edit the
same montage in turn with no project object model, no port, no "project open". Mobile editors do everything but an agent cannot
drive them; full NLEs expose a timeline object model that is fragile to script.

## Operating Context

- Rushes are phone or action-camera clips (GoPro HLG at 23.976 fps is a known case); music and stickers come from the
  creator's own folders. Looks (font, colours, stroke, line step, zoom ranges) live in a per-brand preset file outside
  the repo; the package ships only a neutral default.
- The editor runs locally (`kanton edit montage.json`, port 8788) in the browser, or inside the desktop app (pywebview
  window with ffmpeg bundled). The page is one dependency-free HTML file served by a small Python HTTP server.
- Validation ritual: `kanton qa` measures the spec and the export against the creator's rules (fragments, safe zone,
  loudness, bitrate, words intact); hypotheses without a verdict stay reported as hypotheses.

## Capabilities and Constraints

- Scope (as of 11 Nov 2026): cuts, stills and freeze frames, zooms (static, push, punch), speed and speed curves,
  smooth slow motion, reverse, stabilisation, crop, rotate, flips, fit on the canvas with blurred or coloured background, 61 transitions on the cut, per-clip and global looks
  (filters, adjust panel, LUT), timed effects, b-roll and picture-in-picture, stickers with alpha, masks, blend modes,
  keyframes (position, scale, rotation, opacity), in / out / loop animations, text layers (shadow, box, karaoke
  highlight, typewriter), subtitles from a transcription (auto captions in the editor), edit by the words, music with ducking and beats, voice
  effects, sounds and recorded voice-over, markers, export to mp4 / mov / webm / gif with a cover frame (from the editor
  too).
- Constraints that bind every feature: everything the editor can do must be writable as one line of JSON; ffmpeg does
  the pixels (no renderer of our own); Python ≥ 3.10 + Pillow + ffmpeg, no numpy, no import from any other project; no
  brand value in the code; times in seconds, durations rounded to whole frames; zoom 100 = full frame, never under.
- Terminology: block (a clip on the main track), overlay (video, image, sticker), text (role caption or title, or a
  preset role), music and sounds, preset, spec (`montage.json`), words (the transcript in the spec), blanks (pauses).

## Brand Commitments

Name: kanton. Mark: a rounded dark tile with a yellow "k" (packaging/icon.svg, the page favicon). The editor's incumbent
look: near-black panels, one yellow accent, system sans; this is the product's own identity, not a customer's brand.

## Evidence on Hand

- Synthetic media for every test is generated with ffmpeg (tests/conftest.py); no sample reel ships with the repo.
- No testimonials, metrics, or customer names exist; none may be invented.

## Product Principles

1. The file is the truth; a feature that needs state the file cannot carry does not go in.
2. ffmpeg does the pixels; kanton composes filtergraphs.
3. Agent-first: every operation is a command or a JSON edit; the editor never becomes the only way to do something.
4. Earned familiarity: a mobile-editor user must find every control where they expect it; brand lives in details, not surprise.
5. Hypotheses are reported as hypotheses, never hidden.
