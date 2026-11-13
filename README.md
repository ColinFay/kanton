# kanton

**Forever free, open source, agent-first, lightweight video editor for vertical short-form video.**

A montage is a JSON cut list. `kanton render` turns it into an MP4 with ffmpeg. Nothing else holds state:
an agent writes or edits the file, a human retouches it in the (optional) timeline, the renderer is deterministic.

```bash
uv tool install git+https://github.com/ColinFay/kanton    # or: pipx install git+https://github.com/ColinFay/kanton   (needs ffmpeg on the PATH)

kanton transcribe rush.mp4 --from 12 --to 95        # → rush.words.json (words with times)
# write montage.json (see below), or let an agent write it
kanton blanks montage.json --mode words             # cut the pauses, replayable
kanton captions montage.json                        # subtitles from the transcription, snapped to the cuts
kanton preview montage.json                         # half-size proxy in seconds
kanton qa montage.json --export montage.mp4         # rules: fragments, safe zone, loudness, bitrate…
kanton render montage.json                          # x264, 1080×1920, loudness-normalised
```

## Why

Mobile editors do everything but an agent cannot drive them. Full editors expose a timeline object model
(tracks, ripple, locks, caption tracks) that is fragile to script. kanton keeps one text file as the only
truth and lets ffmpeg do what it has done for twenty years. What you get:

- **agent-first**: every operation is a command on a JSON file; no app, no bridge, no port, no "project open".
- **subtitles that just work**: transcription → word times → cues cut on the pauses, snapped to the cuts,
  rendered with the exact font file, stroke and line step of your preset, burned in at export. Always.
- **Full-featured, one file**: cuts, stills and freeze frames, zooms, speed and speed curves, smooth slow motion, reverse,
  stabilisation, crop, rotate, flips, letterbox on a blurred background, 61 transitions, looks and an adjust panel, timed effects, b-roll and picture-in-picture, stickers,
  masks, blend modes, keyframes, animations, text shadows and boxes, karaoke and typewriter captions, music with ducking
  and beats, voice effects, sound effects, markers, export to mp4 / mov / webm / gif. Every one of them is a JSON value: see
  [docs/features.md](docs/features.md) for the feature map.
- **QA built in**: the renderer refuses a border; `qa` measures fragments, safe zone, text length,
  loudness, bitrate, file size, and can transcribe the export to prove no word was lost.

## The spec

```json
{
  "kanton": 1,
  "preset": "mybrand",
  "sources": {"face": "/rushes/GX014923.MP4", "broll": "/rushes/GX011903.MP4", "music": "/music/break.mp3"},
  "blocks": [
    {"src": "face", "in": 12.34, "out": 15.80, "zoom": [100, 105]},
    {"src": "face", "in": 16.10, "out": 19.00, "zoom": 116, "dx": -18, "cut": "dissolve", "cut_dur": 0.4},
    {"src": "face", "at": 19.00, "dur": 1.0, "cut": "fadewhite"},
    {"src": "face", "in": 20.00, "out": 22.00, "flip": true, "cut": "blur", "grade": "cinema", "volume": -3}
  ],
  "overlays": [
    {"kind": "video", "src": "broll", "in": 5.0, "t": 3.2, "dur": 2.5, "x": 60, "y": 260, "width": 960, "mask": {"shape": "rounded", "radius": 40}},
    {"kind": "image", "src": "strava.png", "t": 6.0, "dur": 3, "x": 100, "y": 700, "width": 880, "anim_in": "pop",
     "keyframes": [{"t": 0, "y": 700}, {"t": 3, "y": 640, "ease": "ease"}]},
    {"kind": "sticker", "src": "sticker.mov", "t": 8.0, "dur": 4.5, "loop": true, "blend": "screen"}
  ],
  "texts": [
    {"text": "TOUT LE MONDE\nS'EN FOUT", "t": 0, "dur": 2.1, "role": "title", "y": 700, "shadow": true, "anim_in": "slide_up"},
    {"text": "de ton allure sur strava", "t": 2.1, "dur": 1.4, "role": "caption"}
  ],
  "music": {"src": "music", "in": 10.0, "gain_db": -31.5, "fade_out": 0.7, "duck": true},
  "sounds": [{"src": "whoosh.wav", "t": 3.2, "gain_db": -6}],
  "effects": [{"type": "shake", "t": 3.2, "dur": 0.4, "amount": 0.6}],
  "markers": [{"t": 3.2, "label": "b-roll"}],
  "grade": {"filter": "warm", "intensity": 0.5, "vignette": 0.24},
  "export": {"format": "mp4", "cover": 1.2}
}
```

- **blocks** are the main track, played back to back. `in`/`out` are source seconds; `at` + `dur` freeze a frame
  (`dur` alone on an image: a still). `zoom` 100 = the source fills the frame with no border, 116 = 16 % closer,
  `[a, b]` = linear push over the block. `dx`/`dy` shift the framing in output pixels. `flip` / `vflip` mirror,
  `rotate` (degrees), `crop` `[x, y, w, h]` in source pixels, `fit` `cover` (default) or `contain` over a `background`
  (`"blur"` or a colour), `speed` (2 = twice as fast, pitch kept; or a curve: `"hero"`, `"bullet"`, `"montage"`,
  `"jump_cut"`, `"flash_in"`, `"flash_out"`, or points `[[position 0–1, speed 0.1–10], …]` along the source),
  `smooth` (interpolated frames where the clip is slowed), `stabilize`, `reverse`. `cut` = the transition into the block:
  `blur`, `flash`, `punch`, or any xfade name (`dissolve`, `wipeleft`, `fadeblack`, `circleopen`, `zoomin`…) with
  `cut_dur` seconds; a transition never shortens the reel. `grade` = a look for this block only. Sound: `mute`,
  `volume` (dB), `fade_in`, `fade_out`, `denoise`, `pitch` (semitones), `voice` (chipmunk, deep, robot, echo, hall,
  telephone, megaphone, vibrato). `effects` = timed effects over the block.
- **overlays** sit above the main track at reel time `t` for `dur` seconds: `video` (b-roll full frame, or a
  picture-in-picture box with `x`, `y`, `width`, `height`; `in`, `zoom`, `flip`, `fit`, `volume` to hear it),
  `image` (`width`, `x`, `y`; centred if no x/y), `sticker` (a video with alpha, or a GIF). All take `opacity`,
  `rotation`, `scale`, `loop`, `chroma`, `grade`, `blend` (screen, multiply, overlay, add, lighten, darken,
  difference, softlight, hardlight), `mask` (`rect`, `circle`, `rounded`, `linear`; `feather`, `radius`, `invert`,
  `rect`, `angle`), `keyframes` (`t` from the overlay start, `x`, `y`, `scale`, `rotation`, `opacity`, `ease`
  linear / ease / in / out), `anim_in` / `anim_out` (fade, slide_up, slide_down, slide_left, slide_right, pop, zoom,
  spin; `{"type", "dur"}`) and `anim_loop` (pulse, shake, wobble, float, spin; `dur` = period, `amount`).
- **texts** are layers rendered with the preset's font: `role` `caption` (what `kanton captions` writes), `title`,
  or any role styled in the preset. Per-text overrides: `y` (baseline of the last line), `x`, `size`, `fill`,
  `stroke`, `stroke_width`, `font`, `max_width`, `uppercase`, `align`, `line_step`; `shadow` (`true` or `{color, dx,
  dy, blur, opacity}`), `box` (`{color, opacity, padding, radius}`), `opacity`, `rotation`; `words` (`[{word, t,
  dur}]` in reel time) with `highlight` (`{fill, stroke, scale}`: the word being said) or `reveal` (`word` / `char`:
  typewriter); the same `keyframes` and `anim_*` as overlays.
- **music** is mixed under the voice: `gain_db`, `fade_in`, `fade_out`, `t` (start in the reel), `in`, `dur`, `pitch`,
  `loop`, `duck` (`true` or `{threshold, ratio, attack, release}`: the music gives way to the voice), `voice`, `beats`
  (seconds of the file, written by `kanton beats`). **sounds** is a list of the same (sound effects, voice-over, the
  audio of a clip).
- **grade** is applied to the whole picture: a look name (`warm`, `cool`, `bw`, `vintage`, `vivid`, `fade`, `cinema`,
  `noir`, `pastel`, `golden`, or a look from the preset's `filters`), or keys: `contrast`, `saturation`, `brightness`,
  `gamma`, `exposure`, `temperature` (Kelvin, 6500 = neutral), `tint`, `hue`, `vibrance`, `sharpen`, `highlights`,
  `shadows`, `grain`, `fade`, `vignette` (0–1), `lut` (.cube); `{"filter": name, "intensity": 0–1, ...overrides}`.
- **effects** are timed on the reel: `type` (blur, shake, rgb_split, glitch, vhs, old_film, pulse, pixelate, mirror,
  negative, strobe, fisheye, grain, flash), `t`, `dur`, `amount` (0–1). **markers**: `[{t, label}]`.
- **zoom**: `{"focus": [x, y]}` the point the zoom closes on (preset default). **output**, **export** (`format` mp4 /
  mov / webm / gif, `cover` seconds → a PNG next to the export), **transitions** (`duration`), **canvas**
  (`background`), **captions** (`style` plain / karaoke / typewriter), **hlg_lut** override the preset.
- `blocks_raw` is written by `kanton blanks`: the ranges you chose by hand, from which `blocks` is rebuilt.
- `words` is written by `kanton words`: the transcript of each source (`{"face": [{"word", "start", "end"}]}`, source
  seconds), reused by every later edit and by `captions`.

Times are seconds. Durations are rounded to whole frames at render time. `kanton catalog` prints every name.

## Presets

A preset is a JSON file with the look and the rules. The package ships `default` (Anton, white, black stroke,
bottom baseline at 1432, Instagram safe zone). Yours extends it:

```json
{
  "extends": "default",
  "text": {"font": "/fonts/MyBrand-Bold.otf", "size": 64, "fill": "#FFD400", "stroke_width": 6, "line_step": 72, "max_lines": 3},
  "zoom": {"focus": [540, 640], "validated": [100, 106], "sourced": [110, 120]},
  "hlg_lut": "/luts/hlg2sdr.cube"
}
```

`"preset": "mybrand"` in a spec resolves to `mybrand.json` next to the spec, in `~/.config/kanton/presets/`, or to a
path. Fonts are looked up next to the preset, then in the usual font folders. No font is shipped except
Anton (OFL).

Preset sections: `text` (plus `roles`: `{"title": {"size": 80, "y": 900, "anim_in": "pop"}}` = text templates, and
`highlight` for karaoke captions), `safe_zone`, `zoom` (`focus`, `validated`, `sourced` ranges reported by `qa`),
`captions` (`gap`, `min_frames`, `max_frames`, `snap_frames`, `lead_frames`, `style`), `blanks` (`min`, `pre`, `post`,
`min_cut`, `min_keep_frames`, `db`), `audio` (`lufs`, `tp`, `lra`, `cut_fade_frames`), `export` (`crf`,
`preset`, `maxrate_cap_mbps`, `mbit_seconds`, `max_mb`, `min_mbps`), `transitions` (`duration`), `canvas`
(`background`), `filters` (named looks: `{"brand": {"temperature": 7000, "saturation": 1.2}}`), `output`, `hlg_lut`.

## Commands

| command | what it does |
|---|---|
| `new CLIP… [-o montage.json] [--preset]` | start a montage: one block per clip, whole clips |
| `render SPEC [-o out] [--proxy] [--format mp4\|mov\|webm\|gif] [--cover T]` | segments per block (cached in `.kanton-tmp/`), transition patches, concat, 2-pass loudness, overlays, texts, music, x264 at `crf` with the API-safe `maxrate` |
| `catalog [GROUP] [--json]` | every name an agent can use: transitions, effects, filters, blends, masks, animations, grade keys, curves, voices |
| `beats SPEC\|AUDIO` | the beats of the spec's music → `music.beats` (or printed for an audio file) |
| `preview SPEC` | the same at half size, fast encode |
| `show SPEC` | the timeline as text |
| `transcribe MEDIA [--from --to] [-o words.json]` | words with times (see backends) |
| `captions SPEC [--words src=file]` | caption texts from the transcription of the blocks' sources (cached) |
| `words SPEC [--cut A-B[@K]] [--keep A-B[@K]] [--move A-B[@K] --before N] [--copy A-B[@K]] [--fix N=TEXT]` | edit by the words: transcribes the sources once into the spec (`words`), prints the words numbered, rewrites the blocks and the captions |
| `frame MEDIA --at T -o out.png` | one image of a media at T seconds |
| `blanks SPEC [--mode energy\|words] [--dry] [--sim]` | cut the pauses inside the blocks; `--sim` transcribes the result so you can read what is left |
| `qa SPEC [--export FILE] [--text REF]` | measure against the rules, exit 1 on errors |
| `probe MEDIA` | size, fps, duration, rotation, HLG, alpha |
| `clean SPEC` | drop the cache |
| `edit SPEC` | the editor in the browser: library, preview, inspector, multi-track timeline with filmstrips, waveforms and beats; auto captions, voice-over recording, drag-and-drop import, export |

## Transcription backends

`kanton transcribe` and `captions` need word timestamps. In order: `mlx-whisper` if importable
(`pip install 'kanton[mlx]'`, Apple Silicon), `faster-whisper` (`pip install 'kanton[whisper]'`), or another
interpreter that has one of them: `KANTON_WHISPER_PYTHON=/path/to/python` or `--python`. Any other tool works
too: pass `--words src=file.json` with `[{"word", "start", "end"}]` (whisper's segments JSON is accepted).
`KANTON_WHISPER_MODEL` overrides the model. The desktop app ships faster-whisper: the first Auto captions asks before downloading
the model (once, kept in the Hugging Face cache or `KANTON_MODEL_DIR`), or lets you pick its folder if it is already on the disk
(checked: it must be the CTranslate2 large-v3-turbo; the folder is remembered in `~/.config/kanton/speech-model`).

## Rules measured by `qa`

- no block under 9 frames (a fragment), blocks of 9–19 frames reported;
- the first frame is the main track, never a b-roll;
- no zoom under 100 (border); levels outside the preset's validated range reported as hypotheses;
- overlays inside the frame; transitions no longer than the clips beside them; looks reported as hypotheses;
- texts within 25–210 frames, within `max_lines` lines of `max_width`, inside the safe zone, not overlapping;
- export: 1080×1920 at the spec's fps, h264, duration equal to the spec, bitrate between the quality floor and
  the API ceiling (`min(25, 2400 / duration)` Mbit/s), AAC 48 kHz, file ≤ 300 MB, loudness at −16.5 LUFS ± 0.5;
- with `--text`: transcription of the export against the reference text, gaps ≥ 0.5 s between words.

## Design

- The file is the truth. The editor and every command read and write the same JSON. If a feature needs state
  the file cannot carry, it is out.
- ffmpeg does the pixels. kanton composes filtergraphs; it has no codec, no renderer, no proxy system.
- Standalone: Python ≥ 3.10, Pillow, ffmpeg. No brand value in the code: looks and rules live in presets.

## Agent in the editor

The editor's Agent tab talks to an agent that edits the spec file. Default: a **token**, no CLI to install.

| Variable | Meaning |
|---|---|
| `KANTON_AGENT_TOKEN` | the token (else `ANTHROPIC_API_KEY`, else `OPENAI_API_KEY`) |
| `KANTON_AGENT_API` | `anthropic` (default) or `openai` (OpenAI and compatible servers) |
| `KANTON_AGENT_URL`, `KANTON_AGENT_MODEL` | base URL and model, defaults per API |
| `KANTON_AGENT=cli` | command-line agent instead: `claude -p` (`KANTON_CLAUDE` or the PATH) |
| `KANTON_AGENT_CMD` | any other agent's command (implies `cli`): the message is its last argument, its stdout the reply |

The token agent has three tools: `kanton`, `read_file`, `write_file` (the last two stay in the spec's folder).

## Desktop app

`kanton.app` is the same code in a native window (pywebview), with ffmpeg inside: no Python, no Homebrew needed
on the target Mac. It opens on a start screen (open a `montage.json`, or start a new montage from a video), and
the binary inside the bundle still answers every CLI subcommand:

```bash
./packaging/build.sh                                   # → dist/kanton.app and dist/kanton-<version>-macos-arm64.dmg
dist/kanton.app/Contents/MacOS/kanton render montage.json
```

The build relocates the Homebrew ffmpeg and its libraries into the bundle (`packaging/bundle_ffmpeg.py`): that build
is GPL v3, see `packaging/THIRD_PARTY.md` for what that implies. The app is signed ad hoc only: on another Mac,
right-click ▸ Open the first time, or `xattr -dr com.apple.quarantine kanton.app`.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest        # synthetic media generated with ffmpeg, no fixtures to download
```

MIT. Anton is distributed under the SIL Open Font License (see `src/kanton/fonts/OFL-Anton.txt`).
