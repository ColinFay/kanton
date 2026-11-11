# CLAUDE.md — kanton

Forever free, open source, agent-first, lightweight video editor for vertical short-form video. A montage is a JSON cut list
(`montage.json`); `kanton render` turns it into an MP4 with ffmpeg; an optional web timeline edits the same file.

## Principles (do not trade these away)

- **The file is the truth.** Every command and the editor read and write the same JSON. A feature that needs state
  the file cannot carry does not go in. "Everything the editor can do must be writable as one line of JSON."
- **ffmpeg does the pixels.** kanton composes filtergraphs and runs the `ffmpeg`/`ffprobe` binaries as subprocesses.
  No codec, no renderer, no proxy system of our own. Pillow renders text layers to PNG (exact font files).
- **Standalone.** Python ≥ 3.10 + Pillow + ffmpeg. No numpy. No import from any other project. No brand value in
  the code: looks and rules live in presets (`src/kanton/presets/default.json` is the only one shipped).
- **Lightweight.** Scope = what a short-form video editor does on one JSON file (`docs/features.md` is the feature map):
  cuts, blanks, stills, zooms, speed and curves, reverse, stabilisation, crop, rotate, flips, fit on the canvas, transitions, looks, effects,
  b-roll, picture-in-picture, stickers, masks, blends, keyframes, animations, text layers, karaoke captions, music and
  beats, voice effects, sounds, markers, export formats. Still no project object model: no tracks with state, no proxies of our own.
- **Agent-first.** Every operation is a CLI command on a JSON file: no app to drive, no port, no "project open".

## Layout

```
src/kanton/
  spec.py        the cut list: load, preset merge, normalise, timeline (blocks back to back, whole frames)
  render.py      pass 1 segment per block (cached in .kanton-tmp/) + transition patches, pass 2 concat, pass 3 effects,
                 overlays (masks, blends, motion), texts (states), sounds, ducking, export formats
  fx.py          catalogs and filter chains: transitions, looks + adjust keys, timed effects, blends, masks (Pillow)
  motion.py      keyframes and animations → one `perspective` filter per layer + per-frame opacity (sendcmd)
  speed.py       speed curves: points along the source → reel time in closed form; one `setpts`, `atempo` by `asendcmd`
  beats.py       beats of a music without numpy: log-energy onsets, tempo by autocorrelation, grid pulled to the onsets
  text.py        Pillow text layers: font lookup, wrap, baseline layout, shadow, box, karaoke / typewriter states
  captions.py    words with times → cues (pauses, width, min/max frames, snap to cuts)
  transcribe.py  word timestamps: mlx-whisper, faster-whisper, another interpreter, or a words JSON
  words.py       edit by the words: transcript in the spec (`words`), cut / keep / move / fix rewrite blocks + captions
  blanks.py      cut the pauses inside blocks (energy or word gaps), replayable from blocks_raw, re-times texts
  qa.py          rules measured on a spec and an export (fragments, safe zone, loudness, bitrate, words intact)
  fcproj.py      FilmCraft project → spec (undocumented command, kept on purpose, do not advertise)
  cli.py         argparse subcommands; app.py = desktop app (pywebview) that also forwards CLI subcommands
  editor/        local HTTP server + one dependency-free HTML page (timeline, inspector, preview)
  presets/ fonts/ (Anton, OFL)
packaging/       PyInstaller spec, ffmpeg relocation (bundle_ffmpeg.py), build.sh, THIRD_PARTY.md
tests/           pytest; synthetic media generated with ffmpeg in conftest.py (nothing to download)
```

## Conventions

- **TDD, tests first.** Write the test file, run it red, implement, run it green, then move on. Tests must run
  without external files or network. When a test expectation turns out wrong, say so.
- `.venv/bin/python -m pytest` (~45 s). Build the app: `./packaging/build.sh` → `dist/kanton.app` + DMG.
- Zoom 100 = the source fills the frame with no border; the renderer refuses anything under 100.
- A transition never consumes time: each side is rendered past the cut (`render_handle`), cross-faded with xfade into a
  patch overlaid on the cut. Blocks stay back to back; captions and reel times never move.
- Speed (constant or curve), `smooth` and `stabilize` act first in a block's picture chain, at source size: the zoom
  after them counts the block's own frames (a push with speed ≠ 1 used to overshoot). Curves, voices, beats: hypotheses.
- Keyframes, animations, static scale / rotation of a layer = `motion.build` → `perspective` on a (W+2)×(H+2) padded
  canvas; opacity over time = `sendcmd` + `colorchannelmixer@tag`. Blends run in RGB (`gbrp`), masks are grey PNGs.
- Looks (`fx.FILTERS`) and effect strengths are hypotheses until validated on real footage; `qa` says so.
- `.impeccable/`, `PRODUCT.md`, `DESIGN.md`: design context for the editor page (impeccable skill); not product code.
  A hand-written spec zooms on the preset `zoom.focus`; an imported FilmCraft spec sets `focus` to the frame centre.
- Times in seconds in the spec; durations are rounded to whole frames at load. Text `y` = baseline of the last line.
- Export: x264 crf 17, maxrate 94 % of min(25, 2400 / duration) Mbit/s, 2-pass loudnorm at −16.5 LUFS, AAC 48 kHz.
- A pass-1 segment stops at the block's frame count (`-frames:v`), not only at `-t`: from a 23.976 fps GoPro source at
  30 fps, `-t` keeps one extra frame on some blocks, the reel drifts behind the spec (+0.5 s over 90 blocks) and the
  export, cut at the spec duration, loses its last words. Guarded by `test_segment_frames_match_spec_from_23976_source`.
- ffmpeg/ffprobe are found through `KANTON_FFMPEG`/`KANTON_FFPROBE`, then the bundle (`sys._MEIPASS/bin`), then PATH.
- Editor port defaults to 8788. Hypotheses without a verdict stay reported as such by `qa` (zoom ranges, blur, grade).
- Licence: MIT. The bundled Homebrew ffmpeg is GPL v3 (see `packaging/THIRD_PARTY.md`); keep calling it as a
  separate process, never link it.
