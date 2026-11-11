# Feature map

What a short-form video editor does, how kanton writes it in `montage.json`, and what is still missing. Every line of the spec is one JSON
value; the editor and the CLI read and write the same file. "Hypothesis" marks a look or a value that has not been
validated on real footage yet (`kanton qa` reports them).

| Feature | kanton | Where |
|---|---|---|
| Split, trim, delete, reorder clips | `blocks` (one per clip, `in`/`out`), editor S / I / O / ⌫ / drag, `kanton words` by the words | spec, editor, CLI |
| Speed (constant) | block `speed` (pitch kept) | spec |
| Speed curves (montage, hero, bullet, jump cut, flash in / out, custom) | block `speed`: a curve name or points `[[position 0–1, speed 0.1–10], …]` along the source; video by one `setpts` expression, sound by `atempo` driven by `asendcmd`; a split shares the curve between the halves (named curves: hypotheses) | spec, editor Speed › Curve, `kanton catalog curves` |
| Smooth slow motion (optical flow) | block `smooth: true`: `minterpolate` where the clip is slowed (export only) | spec, editor |
| Stabilize | block `stabilize: true` (`deshake`) | spec, editor |
| Reverse | block `reverse: true` (rendered one second at a time, memory-safe) | spec |
| Freeze frame | block `{"src", "at", "dur"}` | spec, editor F |
| Photo on the main track | block `{"src": "photo.png", "dur"}` (push zoom = Ken Burns) | spec |
| Crop, rotate, flip | block `crop: [x, y, w, h]`, `rotate` (90/180/270 exact, any angle over the background), `flip`, `vflip` | spec |
| Canvas ratio / background (letterbox, blurred background) | `output` (width, height, fps), block `fit: "contain"` + `background: "blur"` or `"#RRGGBB"`, `canvas.background` default | spec, preset |
| Zoom, pan (keyframed scale on the main clip) | block `zoom` (static or `[a, b]` push), `dx`/`dy`, `cut: "punch"` | spec |
| Transitions | block `cut`: the 58 xfade transitions (`dissolve`, `wipeleft`, `fadeblack`, `circleopen`, `zoomin`…) + `blur`, `flash`, `punch`; `cut_dur` (default `transitions.duration`); no time is consumed: the two sides are rendered past the cut and cross-faded | spec, `kanton catalog transitions` |
| Filters (looks) | `grade: "warm"` or `{"filter": "cinema", "intensity": 0.6}`; built-in: warm, cool, bw, vintage, vivid, fade, cinema, noir, pastel, golden (hypotheses); preset `filters` adds brand looks; per clip (`block.grade`) or per overlay | spec, preset, `kanton catalog filters` |
| Adjust (brightness, contrast, saturation, exposure, temperature, tint, hue, sharpen, highlights, shadows, grain, fade, vignette) | `grade` keys of the same names, `gamma`, `vibrance`, `lut` (.cube) | spec |
| Effects (glitch, VHS, shake, blur, pixelate, old film, mirror, strobe, fisheye, grain, negative, pulse, flash) | `effects: [{"type", "t", "dur", "amount"}]` on the reel, or `block.effects` on a clip | spec, `kanton catalog effects` |
| Overlay / picture-in-picture | `overlays` kind `video` with `x`, `y`, `width`, `height` (a box), `fit`, `zoom`, `volume` to hear it, `loop` | spec |
| Stickers (alpha video, PNG, GIF) | `overlays` kind `sticker` / `image`, `loop` | spec |
| Masks (rectangle, circle, rounded, linear) with feather, invert | overlay `mask: {"shape", "feather", "radius", "invert", "rect", "angle"}` (drawn by Pillow, applied by ffmpeg) | spec |
| Blend modes | overlay `blend`: screen, multiply, overlay, add, lighten, darken, difference, softlight, hardlight | spec |
| Chroma key | overlay `chroma: {"color", "similarity", "blend"}` | spec |
| Opacity, rotation, scale | overlay / text `opacity`, `rotation`, `scale` | spec |
| Keyframes (position, scale, rotation, opacity) with easing | overlay / text `keyframes: [{"t", "x", "y", "scale", "rotation", "opacity", "ease"}]` (one `perspective` filter per layer) | spec, editor K |
| Animations in / out / loop | `anim_in`, `anim_out` (fade, slide_up/down/left/right, pop, zoom, spin) and `anim_loop` (pulse, shake, wobble, float, spin), with `dur` and `amount` | spec, `kanton catalog animations` |
| Text styles (font, size, colour, stroke, alignment) | text style keys and preset `text`; `text.roles` in the preset = text templates (`role: "lower_third"`) | spec, preset |
| Text shadow, background box | text `shadow`, `box` | spec |
| Auto captions | `kanton words` / `kanton captions` from the transcription, snapped to the cuts; the editor's Auto captions runs both | CLI, editor |
| Caption styles: word highlight (karaoke), typewriter | preset or spec `captions.style`: `karaoke` / `typewriter`; cues carry `words` with reel times; `highlight`, `reveal` on any text | spec, preset |
| Text animations | same `anim_*` and `keyframes` as overlays | spec |
| Music, sound effects, voice-over | `music` (one), `sounds` (many), each with `in`, `t`, `dur`, `gain_db`, `fade_in`, `fade_out`, `pitch`, `loop` | spec |
| Audio ducking (music under the voice) | `music.duck: true` or `{"threshold", "ratio", "attack", "release"}` (sidechain compressor) | spec |
| Clip audio: volume, fades, noise reduction, pitch, mute | block `volume`, `fade_in`, `fade_out`, `denoise`, `pitch`, `mute` | spec |
| Voice changer | `voice` on a block, the music or a sound: chipmunk, deep, robot, echo, hall, telephone, megaphone, vibrato (hypotheses) | spec, editor, `kanton catalog voices` |
| Record voice-over | editor Audio › Record voiceover: the microphone while the reel plays (muted), copied to `media/`, a `sounds` entry at its start | editor |
| Extract audio from a clip | a `sounds` entry whose `src` is the clip | spec |
| Auto beat | `kanton beats montage.json` → `music.beats` (seconds of the file; onsets on a tempo grid, a hypothesis); dots on the music, edges snap to them | CLI, editor Beats |
| Loudness | 2-pass loudnorm to the preset target | render |
| Markers | `markers: [{"t", "label"}]`, shown on the ruler, printed by `kanton show` | spec, editor M |
| Undo / redo, duplicate, snapping (cuts, playhead, markers, beats) | editor | editor |
| Filmstrip and waveforms on the timeline | editor (`/strip/KEY.jpg`, `/api/peaks/KEY`): nothing in the file | editor |
| Export: resolution, fps, format, cover | `output`, `export.format` (mp4, mov, webm, gif), `export.cover` (seconds → PNG), `kanton render --format --cover`; the editor's Export renders `<spec>.<format>` next to the spec | spec, CLI, editor |
| Templates | a spec with relative sources + a preset; `kanton new clip.mp4 …` starts one | CLI |
| Background removal, text-to-speech, auto reframe (AI) | not done, out of scope (no model, no numpy) | — |
| Import media | editor Media tab or files dropped anywhere on the editor (copied to `media/` next to the spec), or any path in `sources` | editor, spec |

Names an agent can use: `kanton catalog` (`transitions`, `effects`, `filters`, `blends`, `masks`, `animations`, `grade`,
`curves`, `voices`).

## Rendering notes

- A transition never shortens the reel: the renderer carries each side past the cut (from the source when it has frames
  there, by holding the boundary frame otherwise), cross-fades them with `xfade`, and overlays the patch on the cut.
  Captions and every reel time stay where they were.
- Keyframes, animations and static scale / rotation become one `perspective` filter per layer on a fixed canvas; opacity
  over time is sent frame by frame with `sendcmd`. Masks are grey PNGs drawn by Pillow and multiplied into the layer's
  alpha. Blends happen in RGB.
- Looks and effects are ffmpeg filter chains (`src/kanton/fx.py`); their values are hypotheses until validated.
- Per-clip settings (crop, flips, rotation, fit, look, sound) are rendered in pass 1 and cached per block; `reverse` is
  rendered one second at a time so ffmpeg never holds a whole clip in memory.
