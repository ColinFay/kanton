"""spec → video. Pass 1: one segment per block (crop, flips, rotation, fit on the canvas, zoom, speed, reverse, look, sound), plus a
patch per transition on a cut (the two sides rendered past the cut and cross-faded). Pass 2: concat. Pass 3: cut effects, patches,
grade, timed effects, overlays and texts (masks, blends, keyframes, animations, karaoke), sounds and music, loudness, export."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ffmpeg, fx, motion
from . import speed as ramp
from .spec import Spec
from .text import TextStyle, states


def fesc(s: str) -> str:
    """Escape a value inside a filtergraph (paths with : , ' or spaces)."""
    out = str(s)
    for ch in "\\:',[];":
        out = out.replace(ch, "\\" + ch)
    return out


def ms(t: float) -> str:
    return f"{t:.4f}"


def atempo(speed: float) -> str:
    """The atempo filters for a speed (one filter takes 0.5-2), each followed by a comma; '' at speed 1."""
    a, sp = "", speed
    while sp > 2.0:
        a += "atempo=2.0,"; sp /= 2.0
    while sp < 0.5:
        a += "atempo=0.5,"; sp /= 0.5
    if abs(sp - 1.0) > 1e-6:
        a += f"atempo={sp:.4f},"
    return a


def pitch_chain(semitones: float) -> str:
    """Shift the pitch, keep the duration (resample then tempo back); '' at 0."""
    if not semitones:
        return ""
    r = 2 ** (semitones / 12.0)
    return f"asetrate={48000 * r:.0f},aresample=48000," + atempo(1 / r)


def _part(out: Path) -> Path:
    """Where a thread writes `out` before renaming it: two threads making the same file, or a render stopped half-way, never
    leave a broken file in the cache."""
    return out.with_name(f"{out.stem}.{threading.get_ident()}{out.suffix}")


JOBS = max(1, min(4, (os.cpu_count() or 2) // 2))  # ffmpeg processes at once in pass 1 (4: measured best on 8 cores)
SEGMENT_VERSION = 4  # bump when a segment's rendering changes, so cached segments are rebuilt
BLOCK_SIG = ("path", "in", "out", "zoom", "dx", "dy", "flip", "vflip", "speed", "cut", "mute", "frames", "still", "fit", "background", "rotate", "crop",
             "reverse", "grade", "volume", "fade_in", "fade_out", "denoise", "pitch", "curve", "smooth", "stabilize", "voice")
ENCODE = {"mp4": {"v": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"], "a": ["-c:a", "aac", "-b:a", "192k"], "x": ["-movflags", "+faststart"]},
          "mov": {"v": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"], "a": ["-c:a", "pcm_s16le"], "x": []},
          "webm": {"v": ["-c:v", "libvpx-vp9", "-crf", "31", "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p"], "a": ["-c:a", "libopus", "-b:a", "128k"], "x": []},
          "gif": {"v": [], "a": [], "x": ["-loop", "0"]}}


def overlay_box(W: int, H: int, o: dict, info: dict) -> tuple[float, float, int, int]:
    """Where an overlay sits on the frame and how big: (x, y, w, h). A video with no width fills the frame; a sticker with no
    width fits in it; an image with no width keeps its size; a width (and a height) makes a box, centred unless x and y are given."""
    iw, ih = max(1, info["width"]), max(1, info["height"])
    if o["width"] is not None:
        w = int(round(float(o["width"])))
        h = int(round(float(o["height"]))) if o["height"] is not None else max(1, int(round(ih * w / iw)))
    elif o["kind"] == "video":
        w, h = W, H
    elif o["kind"] == "sticker":
        k = min(W / iw, H / ih, 1.0)
        w, h = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
    else:
        w, h = iw, ih
    x = float(o["x"]) if o["x"] is not None else (W - w) / 2
    y = float(o["y"]) if o["y"] is not None else (H - h) / 2
    return x, y, w, h


class Renderer:
    def __init__(self, spec: Spec, out: Path, proxy: bool = False, tmp: Path | None = None, verbose: bool = False):
        self.spec, self.out, self.proxy, self.verbose = spec, Path(out), proxy, verbose
        self.W, self.H, self.fps = spec.W, spec.H, spec.fps
        self.tmp = tmp or (spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec"))
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.log = []

    def say(self, msg: str):
        print(msg, flush=True)
        self.log.append(msg)

    # ── helpers ─────────────────────────────────────────────────────────────
    def hlg_chain(self, info: dict) -> str:
        if not ffmpeg.is_hlg(info):
            return ""
        lut = self.spec.preset.get("hlg_lut") or self.spec.data.get("hlg_lut")
        if lut:
            lut = self.spec.resolve(lut)
            return f",format=rgb48le,lut3d=file='{fesc(lut)}',scale=w=iw:h=ih:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        return ",colorspace=iall=bt2020:itrc=bt2020-10:all=bt709:format=yuv420p"

    def cover(self, info: dict, S: int) -> str:
        W, H = self.W * S, self.H * S
        return f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}"

    def zoom_chain(self, b: dict, S: int) -> str:
        """Static zoom = crop+scale on the S× cover; push = zoompan on a 4× canvas (render_block), so its whole-pixel window steps stay sub-pixel."""
        W, H = self.W, self.H
        fx_, fy = self.spec.zoom.get("focus", [W / 2, H / 2])
        za, zb = b["zoom"][0] / 100.0, b["zoom"][1] / 100.0
        dx, dy = b["dx"], b["dy"]
        punch = b["cut"] == "punch"
        if abs(za - zb) < 1e-6 and not punch:
            z = za
            cw, ch = W * S / z, H * S / z
            x = f"clip({fx_ * S}-{cw / 2}-{dx * S / z},0,{W * S - cw})"
            y = f"clip({fy * S}-{ch / 2}-{dy * S / z},0,{H * S - ch})"
            return f"crop=w={cw:.3f}:h={ch:.3f}:x='{x}':y='{y}':exact=1,scale={W}:{H}:flags=lanczos"
        n = max(b["frames"] - 1, 1)
        zexpr = f"{za}+({zb}-{za})*on/{n}"
        if punch:
            zexpr = f"({zexpr})*(1+0.08*max(0,1-on/4))"
        x = f"clip({fx_ * S}-iw/zoom/2-{dx * S}/zoom,0,iw-iw/zoom)"
        y = f"clip({fy * S}-ih/zoom/2-{dy * S}/zoom,0,ih-ih/zoom)"
        return f"zoompan=z='{zexpr}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={self.fps:g}"

    def scale_factor(self, info: dict) -> int:
        return 2 if min(info["width"] / self.W, info["height"] / self.H) >= 1.5 else 1

    # ── pass 1 ──────────────────────────────────────────────────────────────
    def block_key(self, b: dict, info: dict) -> str:
        sig = json.dumps({k: b[k] for k in BLOCK_SIG}, sort_keys=True)
        sig += f"|v{SEGMENT_VERSION}|{Path(b['path']).stat().st_mtime}|{self.W}x{self.H}@{self.fps}|{self.spec.zoom.get('focus')}|{self.spec.preset.get('hlg_lut')}"
        return hashlib.sha1(sig.encode()).hexdigest()[:16]

    def picture_chain(self, b: dict, info: dict, S: int, bg_idx: int) -> tuple[str, bool]:
        """From the source frame to the W×H canvas, before the zoom: still, crop, flips, rotation, fit (cover, or contain over a
        blurred / coloured background), HLG, fps. Returns the chain and whether it uses the colour input `bg_idx`."""
        W, H = self.W * S, self.H * S
        f, needs_bg = "", False
        if b["still"] and info["kind"] != "image":
            f += "trim=end_frame=1,loop=loop=-1:size=1:start=0,"
        f += self.time_chain(b)
        if b["crop"]:
            x, y, w, h = b["crop"]
            f += f"crop={w:.0f}:{h:.0f}:{x:.0f}:{y:.0f},"
        if b["flip"]:
            f += "hflip,"
        if b["vflip"]:
            f += "vflip,"
        rot = b["rotate"] % 360
        tilted = abs(rot - round(rot / 90) * 90) > 1e-6
        if rot and not tilted:
            f += {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}[int(round(rot / 90)) * 90 % 360]
        elif tilted:
            f += f"format=rgba,rotate=a={math.radians(rot):.6f}:ow='hypot(iw,ih)':oh=ow:c=black@0,"
        if b["fit"] == "contain" or tilted:
            bg = b["background"] or "#000000"
            fg = f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos"
            if bg == "blur":
                f += f"split[bgs][fgs];[bgs]{self.cover(info, S)},boxblur=luma_radius=30:luma_power=2:chroma_radius=15:chroma_power=2[bg];[fgs]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
            else:
                needs_bg = True
                f += f"{fg}[fg];[{bg_idx}:v][fg]overlay=(W-w)/2:(H-h)/2"
        else:
            f += self.cover(info, S)
        f += self.hlg_chain(info) + f",fps={self.fps:g}"
        return f, needs_bg

    def time_chain(self, b: dict) -> str:
        """Stabilisation, then the source's time onto the reel's (speed or curve, interpolated frames for a smooth slow
        motion), before any resize: the zoom after it counts the block's own frames."""
        f = "deshake=rx=64:ry=64," if b["stabilize"] else ""
        if b["curve"]:
            f += ramp.setpts(b["curve"], b["out"] - b["in"]) + ","
        elif b["speed"] != 1.0:
            f += f"setpts=PTS/{b['speed']},"
        if b["smooth"] and (min(s for _, s in b["curve"]) if b["curve"] else b["speed"]) < 1:
            f += f"minterpolate=fps={self.fps:g}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
        return f

    def audio_chain(self, b: dict, label: str, dur: float) -> str:
        fade = self.spec.preset.get("audio", {}).get("cut_fade_frames", 2) / self.fps
        if b["curve"]:
            cmd = self.tmp / f"tempo-{hashlib.sha1(json.dumps([b['curve'], b['in'], b['out']]).encode()).hexdigest()[:12]}.txt"
            n = round(dur * 48000)
            a = label + "aresample=48000," + ramp.audio_chain(b["curve"], b["out"] - b["in"], cmd) + f"apad=whole_len={n},atrim=end_sample={n},"
        else:
            a = label + "aresample=48000," + atempo(b["speed"])
        a += pitch_chain(b["pitch"]) + fx.voice_chain(b["voice"])
        if b["denoise"]:
            a += f"afftdn=nr={12 * b['denoise']:.1f}:nf=-25,"
        if b["volume"]:
            a += f"volume={b['volume']:.2f}dB,"
        fi, fo = max(fade, b["fade_in"]), max(fade, b["fade_out"])
        return a + f"afade=t=in:d={fi:.4f},afade=t=out:st={max(0.0, dur - fo):.4f}:d={fo:.4f},aresample=48000,aformat=channel_layouts=stereo"

    def block_args(self, b: dict, info: dict, S: int, out: Path, head_pad: int = 0, pad_tail: bool = False) -> list[str]:
        """The ffmpeg arguments rendering block `b` to `out` (a transition handle pads the frames it lacks by cloning)."""
        dur, src_dur = b["dur"], b["out"] - b["in"]
        hw = ffmpeg.decode_args(info)
        if b["still"] and info["kind"] == "image":
            args = ["-loop", "1", "-framerate", f"{self.fps:g}", "-t", ms(dur), "-i", b["path"]]
        elif b["still"]:
            args = hw + ["-ss", ms(b["in"]), "-i", b["path"]]
        else:
            args = hw + ["-ss", ms(b["in"]), "-t", ms(src_dur), "-i", b["path"]]
        n = 1
        if info["has_audio"] and not b["mute"] and not b["still"]:
            a = self.audio_chain(b, "[0:a]", dur) + "[a]"
        else:
            args += ["-f", "lavfi", "-t", ms(dur), "-i", "anullsrc=r=48000:cl=stereo"]
            a, n = f"[{n}:a]anull[a]", n + 1
        chain, needs_bg = self.picture_chain(b, info, S, n)
        if needs_bg:
            args += ["-f", "lavfi", "-t", ms(dur + 1), "-i", f"color=c={b['background'] or '#000000'}:s={self.W * S}x{self.H * S}:r={self.fps:g}"]
        v = "[0:v]" + (f"format={info['pix_fmt']}," if hw else "") + chain
        if head_pad or pad_tail:
            v += f",tpad=start={head_pad}:start_mode=clone:stop=-1:stop_mode=clone"
        v += "," + self.zoom_chain(b, S)
        v += fx.grade_chain(b["grade"]) + ",setsar=1,format=yuv420p[v]"
        args += ["-filter_complex", v + ";" + a, "-map", "[v]", "-map", "[a]", "-t", ms(dur), "-frames:v", str(b["frames"]), "-r", f"{self.fps:g}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
                 "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out)]
        return args

    def render_block(self, b: dict) -> Path:
        if min(b["zoom"]) < 100 - 1e-6:
            raise SystemExit(f"kanton: block {b['i']} zoom {b['zoom']} is under 100 and would show a border (100 = full frame)")
        info = ffmpeg.probe(b["path"])
        seg = self.tmp / f"seg-{self.block_key(b, info)}.mkv"
        if seg.exists() and seg.stat().st_size > 0:
            return seg
        S = self.scale_factor(info)
        if b["zoom"][0] != b["zoom"][1] or b["cut"] == "punch":
            S = 4  # zoompan rounds its window to whole pixels: on a 4× canvas a step is a quarter of an output pixel
        part = _part(seg)
        ffmpeg.run(self.block_args(b, info, S, part), quiet=not self.verbose)
        if b["reverse"]:
            self._reverse(part, b)
        part.replace(seg)
        return seg

    def _reverse(self, seg: Path, b: dict):
        """Play the segment backwards, one second at a time (ffmpeg's reverse keeps every frame of its input in memory)."""
        chunk, parts = max(1, int(round(self.fps))), []
        for k, start in enumerate(range(0, b["frames"], chunk)):
            end = min(b["frames"], start + chunk)
            p = seg.with_name(f"{seg.stem}-r{k}.mkv")
            s0, s1 = int(round(start / self.fps * 48000)), int(round(end / self.fps * 48000))
            ffmpeg.run(["-i", str(seg), "-filter_complex", f"[0:v]trim=start_frame={start}:end_frame={end},reverse,setpts=PTS-STARTPTS[v];"
                        f"[0:a]atrim=start_sample={s0}:end_sample={s1},areverse,asetpts=PTS-STARTPTS[a]", "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(p)], quiet=not self.verbose)
            parts.append(p)
        lst = seg.with_suffix(".rev.txt")
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in reversed(parts)))
        tmp = seg.with_suffix(".rev.mkv")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp)], quiet=not self.verbose)
        tmp.replace(seg)
        for p in parts + [lst]:
            p.unlink(missing_ok=True)

    def render_handle(self, b: dict, side: str, frames: int) -> Path:
        """The picture of a block carried past its cut (side "a": after its end, "b": before its start), `frames` long and
        centred on the cut, for a transition patch. A still or a reversed block is held on its boundary frame."""
        info = ffmpeg.probe(b["path"])
        sp = b["curve"][-1 if side == "a" else 0][1] if b["curve"] else b["speed"]           # a curve carries on at its end speed
        d, sh = frames / self.fps, frames / 2 / self.fps * sp
        held = b["still"] or b["reverse"]
        if side == "a":
            lo, hi, zoom = b["out"] - sh, (b["out"] if held else b["out"] + sh), [b["zoom"][1]] * 2
        else:
            lo, hi, zoom = (b["in"] if held else b["in"] - sh), b["in"] + sh, [b["zoom"][0]] * 2
        head = frames // 2 if (held and side == "b") else 0
        if lo < 0:
            head, lo = int(round(-lo / sp * self.fps)), 0.0
        ext = {**b, "in": lo, "out": max(hi, lo + 1 / self.fps), "zoom": zoom, "cut": "none", "frames": frames, "dur": d, "mute": True, "reverse": False, "effects": [],
               "speed": sp, "curve": None, "smooth": False}
        key = hashlib.sha1(f"{self.block_key(b, info)}|{side}|{frames}|hd1".encode()).hexdigest()[:16]
        out = self.tmp / f"hd-{key}.mkv"
        if out.exists() and out.stat().st_size > 0:
            return out
        S, part = self.scale_factor(info), _part(out)
        ffmpeg.run(self.block_args(ext, info, S, part, head_pad=head, pad_tail=True), quiet=not self.verbose)
        part.replace(out)
        return out

    def render_transition(self, a: dict, b: dict) -> Path:
        """The cross-fade from block `a` into block `b` (xfade), as a patch centred on their cut; its length is in `transition_frames`."""
        frames = self.transition_frames(a, b)
        d = frames / self.fps
        key = hashlib.sha1(f"{self.block_key(a, ffmpeg.probe(a['path']))}|{self.block_key(b, ffmpeg.probe(b['path']))}|{b['cut']}|{frames}|tr1".encode()).hexdigest()[:16]
        patch = self.tmp / f"tr-{key}.mkv"
        if patch.exists() and patch.stat().st_size > 0:
            return patch
        ea, eb, part = self.render_handle(a, "a", frames), self.render_handle(b, "b", frames), _part(patch)
        ffmpeg.run(["-i", str(ea), "-i", str(eb), "-filter_complex", f"[0:v][1:v]xfade=transition={b['cut']}:duration={d:.4f}:offset=0,format=yuv420p[v]",
                    "-map", "[v]", "-frames:v", str(frames), "-r", f"{self.fps:g}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", str(part)],
                   quiet=not self.verbose)
        part.replace(patch)
        return patch

    def segments(self) -> list[Path]:
        """Pass 1 side by side: one ffmpeg leaves cores idle (seek, start, encoder flush), so the blocks' segments and the
        transitions' patches are made JOBS at a time; returns the segments in reel order."""
        bl = self.spec.blocks
        with ThreadPoolExecutor(JOBS) as ex:
            patches = [ex.submit(self.render_transition, a, b) for a, b in zip(bl, bl[1:]) if fx.is_xfade(b["cut"])]
            segs = list(ex.map(self.render_block, bl))
            for p in patches:
                p.result()
        return segs

    def transition_frames(self, a: dict, b: dict) -> int:
        n = int(round(min(b["cut_dur"], a["dur"], b["dur"]) * self.fps))
        return max(2, n - n % 2)

    # ── pass 2 ──────────────────────────────────────────────────────────────
    def concat(self, segs: list[Path]) -> Path:
        base = self.tmp / "base.mkv"
        lst = self.tmp / "concat.txt"
        lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs))
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base)], quiet=not self.verbose)
        return base

    def measure_loudness(self, base: Path) -> dict | None:
        au = self.spec.preset.get("audio", {})
        err = ffmpeg.run_capture(["-i", str(base), "-vn", "-af", f"loudnorm=I={au.get('lufs', -16.5)}:TP={au.get('tp', -1.5)}:LRA={au.get('lra', 11)}:print_format=json", "-f", "null", "-"])
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        try:
            vals = [float(d[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")]
        except (KeyError, ValueError):
            return None
        if any(v != v or abs(v) == float("inf") for v in vals):
            return None  # silent track: no 2nd pass
        return d

    # ── pass 3 ──────────────────────────────────────────────────────────────
    def cut_chain(self) -> str:
        parts = []
        for b in self.spec.blocks[1:]:
            tc = b["start"]
            if b["cut"] == "blur":
                for sigma, half in ((4, 0.1), (6, 0.067), (8, 0.034)):
                    parts.append(f"gblur=sigma={sigma}:enable='between(t,{tc - half:.3f},{tc + half:.3f})'")
            elif b["cut"] == "flash":
                parts.append(f"fade=t=in:st={tc:.3f}:d=0.1:c=white")
        return "".join("," + p for p in parts)

    def grade_chain(self) -> str:
        return fx.grade_chain(self.spec.grade)

    def _input(self, args: list[str]) -> int:
        """Add an input; returns its index."""
        self._inputs += args
        self._idx += 1
        return self._idx - 1

    def overlay_box(self, o: dict, info: dict) -> tuple[float, float, int, int]:
        return overlay_box(self.W, self.H, o, info)

    def sizing_chain(self, o: dict, info: dict, box: tuple) -> str:
        W, H = self.W, self.H
        x, y, w, h = box
        if o["width"] is None and o["kind"] == "video":
            if o["fit"] == "contain":
                return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black," + self.hlg_chain(info).lstrip(",") + ("," if self.hlg_chain(info) else "")
            f = self.cover(info, 1) + self.hlg_chain(info) + ","
            z = o["zoom"] / 100.0
            if abs(z - 1.0) > 1e-6 or o["dx"] or o["dy"]:
                fx_, fy = self.spec.zoom.get("focus", [W / 2, H / 2])
                cw, ch = W / z, H / z
                f += f"crop=w={cw:.3f}:h={ch:.3f}:x='clip({fx_}-{cw / 2}-{o['dx'] / z},0,{W - cw})':y='clip({fy}-{ch / 2}-{o['dy'] / z},0,{H - ch})':exact=1,scale={W}:{H}:flags=lanczos,"
            return f
        if o["height"] is not None and o["width"] is not None:
            f = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
        elif w != info["width"] or h != info["height"]:
            f = f"scale={w}:{h}:flags=lanczos,"
        else:
            f = ""
        return f + (self.hlg_chain(info).lstrip(",") + "," if info["kind"] == "video" and self.hlg_chain(info) else "")

    def layer_motion(self, idx: int, chain: str, m: dict, box: tuple, tag: str) -> tuple[str, str]:
        """Opacity and motion filters of a layer whose content comes out of `chain` at `box`; returns (chain, overlay position)."""
        W, H = self.W, self.H
        x, y, w, h = box
        if m["opacity"] is not None:
            cmd = self.tmp / f"opacity-{tag}.txt"
            cmd.write_text("".join(f"{k / self.fps:.4f} colorchannelmixer@{tag} aa {v:.4f};\n" for k, v in enumerate(m["opacity"])))
            chain += f"sendcmd=f='{fesc(str(cmd))}',colorchannelmixer@{tag}=aa={m['opacity'][0]:.4f},"
        elif m["static_opacity"] < 1.0 - 1e-6:
            chain += f"colorchannelmixer=aa={m['static_opacity']:.4f},"
        if m["perspective"]:
            chain += f"pad={W + 2}:{H + 2}:{x + 1:.1f}:{y + 1:.1f}:color=black@0,{m['perspective']},crop={W}:{H}:1:1,"
            return chain, "0:0"
        return chain, f"{x:.1f}:{y:.1f}"

    def compose(self, label: str, t0: float, t1: float, pos: str, blend: str | None):
        """Put layer `label` over the picture between t0 and t1, at `pos`, as an overlay or through a blend mode."""
        nxt = f"v{self._idx}c{len(self._fc)}"
        en = f"between(t,{t0:.4f},{t1:.4f})"
        if blend:
            c = self._input(["-f", "lavfi", "-t", ms(self.spec.duration), "-i", f"color=c={fx.BLEND_NEUTRAL[blend]}:s={self.W}x{self.H}:r={self.fps:g}"])
            self._fc.append(f"[{c}:v][{label}]overlay={pos}:enable='{en}':eof_action=pass,format=gbrp[{label}l]")       # blended in RGB, not YUV
            self._fc.append(f"[{self._vcur}]format=gbrp[{label}b];[{label}b][{label}l]blend=all_mode={fx.BLENDS[blend]}:enable='{en}',format=yuv420p[{nxt}]")
        else:
            self._fc.append(f"[{self._vcur}][{label}]overlay={pos}:enable='{en}':eof_action=pass[{nxt}]")
        self._vcur = nxt

    def add_overlay(self, o: dict, k: int):
        info = ffmpeg.probe(o["path"])
        box = self.overlay_box(o, info)
        x, y, w, h = box
        loop = ["-stream_loop", "-1"] if o["loop"] and info["kind"] != "image" else []
        if info["kind"] == "image":
            idx = self._input(["-loop", "1", "-framerate", f"{self.fps:g}", "-t", ms(o["dur"]), "-i", o["path"]])
        else:
            idx = self._input(loop + ["-ss", ms(o["in"]), "-t", ms(o["dur"]), "-i", o["path"]])
        f = f"[{idx}:v]" + ("hflip," if o["flip"] else "") + self.sizing_chain(o, info, box)
        if o.get("chroma"):
            c = o["chroma"] if isinstance(o["chroma"], dict) else {"color": o["chroma"]}
            f += f"chromakey=color={c.get('color', '0x00FF00')}:similarity={c.get('similarity', 0.3)}:blend={c.get('blend', 0.1)},despill=type=green,"
        if o["grade"]:
            f += fx.grade_chain(o["grade"]).lstrip(",") + ","
        if info["kind"] == "video":
            f += f"fps={self.fps:g},"
        f += "format=rgba,"
        tag = f"ov{k}"
        if o["mask"]:
            mp = self.tmp / f"mask-{hashlib.sha1(json.dumps([o['mask'], w, h], sort_keys=True).encode()).hexdigest()[:12]}.png"
            if not mp.exists():
                fx.mask_image(o["mask"], w, h, mp)
            m = self._input(["-loop", "1", "-framerate", f"{self.fps:g}", "-t", ms(o["dur"]), "-i", str(mp)])
            self._fc.append(f"{f}split[{tag}c][{tag}a]")
            self._fc.append(f"[{tag}a]alphaextract[{tag}ae];[{m}:v]format=gray[{tag}m];[{tag}ae][{tag}m]blend=all_mode=multiply[{tag}am]")
            f = f"[{tag}c][{tag}am]alphamerge,"
        mo = motion.build(self.W, self.H, self.fps, o["dur"], box, keyframes=o["keyframes"], anim_in=o["anim_in"], anim_out=o["anim_out"], anim_loop=o["anim_loop"],
                          scale=o["scale"], rotation=o["rotation"], opacity=o["opacity"])
        f, pos = self.layer_motion(idx, f, mo, box, tag)
        self._fc.append(f + f"setpts=PTS-STARTPTS+{o['t']:.4f}/TB[{tag}]")
        self.compose(tag, o["t"], o["t"] + o["dur"], pos, o["blend"])
        if o["volume"] is not None and info["has_audio"]:
            self._sounds.append((f"[{idx}:a]", {"t": o["t"], "dur": o["dur"], "gain_db": o["volume"], "fade_in": 0.0, "fade_out": 0.0, "pitch": 0.0}))

    def add_text(self, t: dict, k: int, style_dirs: list[Path]):
        W, H = self.W, self.H
        st = TextStyle(self.spec.text_style(t), style_dirs)
        animated = bool(t["keyframes"] or t["anim_in"] or t["anim_out"] or t["anim_loop"])
        baked = {"shadow": t["shadow"], "box": t["box"], "opacity": 1.0 if animated else t["opacity"], "rotation": 0.0 if animated else t["rotation"]}
        key = hashlib.sha1(json.dumps([t["text"], self.spec.text_style(t), W, H, baked, t["words"], t["highlight"], t["reveal"], t["t"], t["dur"], 3], sort_keys=True, default=str).encode()).hexdigest()[:12]
        meta_p = self.tmp / f"text-{key}.json"
        if meta_p.exists() and all(Path(s["png"]).exists() for s in json.loads(meta_p.read_text())):
            sts = json.loads(meta_p.read_text())
        else:
            sts = states(st, t["text"], W, H, self.tmp / f"text-{key}", t0=t["t"], dur=t["dur"], words=t["words"], highlight=t["highlight"], reveal=t["reveal"], crop=True, **baked)
            meta_p.write_text(json.dumps(sts))
        boxes = [s["bbox"] for s in sts if s["bbox"]]
        bb = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else (0, 0, W, H)
        self.text_boxes.append((t, {"bbox": bb, "lines": st.wrap(t["text"])}))
        base = (float(self.spec.text_style(t)["x"]), float(self.spec.text_style(t).get("y", self.spec.text_style(t).get("y_baseline", H))))
        for j, s in enumerate(sts):
            idx = self._input(["-loop", "1", "-framerate", f"{self.fps:g}", "-t", ms(s["dur"]), "-i", s["png"]])
            tag = f"tx{k}s{j}"
            mo = motion.build(W, H, self.fps, s["dur"], (0, 0, W, H), pivot=((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), base=base, keyframes=t["keyframes"],
                              anim_in=t["anim_in"], anim_out=t["anim_out"], anim_loop=t["anim_loop"], scale=1.0, rotation=t["rotation"] if animated else 0.0,
                              opacity=t["opacity"] if animated else 1.0, offset=s["t"] - t["t"]) if animated else \
                {"perspective": None, "opacity": None, "static_opacity": 1.0}
            if animated:        # the whole layer's animation is cut to this state's window: the in / out run against the text's own clock
                mo = {**mo, "opacity": mo["opacity"]}
            f, pos = self.layer_motion(idx, f"[{idx}:v]format=rgba,", mo, (*s["origin"], W, H), tag)
            self._fc.append(f + f"setpts=PTS-STARTPTS+{s['t']:.4f}/TB[{tag}]")
            self.compose(tag, s["t"], s["t"] + s["dur"], pos, None)

    def sound_chain(self, label: str, m: dict, mdur: float) -> str:
        a = f"{label}aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS," + pitch_chain(m["pitch"]) + fx.voice_chain(m.get("voice"))
        if m["fade_in"]:
            a += f"afade=t=in:d={m['fade_in']:.3f},"
        if m["fade_out"]:
            a += f"afade=t=out:st={max(0.0, mdur - m['fade_out']):.3f}:d={m['fade_out']:.3f},"
        return a + f"volume={m['gain_db']:.2f}dB,adelay={int(round(m['t'] * 1000))}:all=1"

    def audio_graph(self, loud: dict | None):
        """Voice bus loudness (2-pass when measured), then music (ducked under the voice when asked) and sounds, mixed into [aout]."""
        spec = self.spec
        au = spec.preset.get("audio", {})
        ln = f"loudnorm=I={au.get('lufs', -16.5)}:TP={au.get('tp', -1.5)}:LRA={au.get('lra', 11)}"
        if loud:
            ln += (f":measured_I={loud['input_i']}:measured_TP={loud['input_tp']}:measured_LRA={loud['input_lra']}"
                   f":measured_thresh={loud['input_thresh']}:offset={loud['target_offset']}:linear=true")
        else:
            ln = "anull"  # silent or unmeasurable voice track: leave it alone
        self._fc.append(f"[0:a]{ln},aresample=48000,aformat=channel_layouts=stereo[va]")
        layers, duck = [], None
        for m in ([spec.music] if spec.music else []) + spec.sounds:
            mdur = m["dur"] if m["dur"] else max(0.1, spec.duration - m["t"])
            idx = self._input((["-stream_loop", "-1"] if m["loop"] else []) + ["-ss", ms(m["in"]), "-t", ms(mdur), "-i", m["path"]])
            lab = f"s{idx}"
            self._fc.append(self.sound_chain(f"[{idx}:a]", m, mdur) + f"[{lab}]")
            if m is spec.music and m.get("duck"):
                duck = (lab, m["duck"])
            layers.append(lab)
        for i, (label, m) in enumerate(self._sounds):
            lab = f"so{i}"
            self._fc.append(self.sound_chain(label, m, m["dur"]) + f"[{lab}]")
            layers.append(lab)
        acur = "va"
        if duck:
            lab, d = duck
            self._fc.append(f"[va]asplit[va0][sc];[{lab}][sc]sidechaincompress=threshold={d['threshold']}:ratio={d['ratio']}:attack={d['attack']}:release={d['release']}[{lab}d]")
            layers[layers.index(lab)], acur = f"{lab}d", "va0"
        if layers:
            self._fc.append(f"[{acur}]" + "".join(f"[{l}]" for l in layers) + f"amix=inputs={1 + len(layers)}:duration=first:normalize=0[aout]")
        else:
            self._fc.append(f"[{acur}]anull[aout]")

    def final(self, base: Path, loud: dict | None) -> Path:
        spec, W, H = self.spec, self.W, self.H
        self._inputs, self._idx, self._fc, self._sounds = [], 0, [], []
        self._input(["-i", str(base)])
        self._fc.append(f"[0:v]null{self.cut_chain()}[v0]")
        self._vcur = "v0"
        for a, b in zip(spec.blocks, spec.blocks[1:]):                       # transitions: the patches over their cut
            if fx.is_xfade(b["cut"]):
                patch, d = self.render_transition(a, b), self.transition_frames(a, b) / self.fps
                idx = self._input(["-i", str(patch)])
                t0 = b["start"] - d / 2
                self._fc.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t0:.4f}/TB[tr{idx}]")
                self.compose(f"tr{idx}", t0, t0 + d, "0:0", None)
        g = self.grade_chain()
        if g:
            self._fc.append(f"[{self._vcur}]null{g}[vg]")
            self._vcur = "vg"
        lines, self._vcur = fx.effects_graph(spec.effects, self._vcur, W, H, self.fps)
        self._fc += lines
        for k, o in enumerate(sorted(spec.overlays, key=lambda o: o["t"])):
            self.add_overlay(o, k)
        style_dirs = [Path(spec.preset.get("_dir", spec.dir)), spec.dir]
        self.text_boxes = []
        for k, t in enumerate(spec.texts):
            self.add_text(t, k, style_dirs)
        fmt = "mp4" if self.proxy else spec.export["format"]
        if self.proxy:
            self._fc.append(f"[{self._vcur}]scale={W // 2}:{H // 2}:flags=bicubic[vout]")
        elif fmt == "gif":
            self._fc.append(f"[{self._vcur}]fps=15,scale={min(W, 540)}:-2:flags=lanczos,split[g1][g2];[g1]palettegen=stats_mode=diff[gp];[g2][gp]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vout]")
        else:
            self._fc.append(f"[{self._vcur}]null[vout]")
        if fmt != "gif":
            self.audio_graph(loud)
        ex, dur = spec.export, spec.duration
        if self.proxy:
            venc, aenc, extra = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"], ["-c:a", "aac", "-b:a", "128k"], ["-movflags", "+faststart"]
        else:
            venc, aenc, extra = list(ENCODE[fmt]["v"]), list(ENCODE[fmt]["a"]), list(ENCODE[fmt]["x"])
            if fmt == "mp4":
                cap = 0.94 * min(float(ex.get("maxrate_cap_mbps", 25)), float(ex.get("mbit_seconds", 2400)) / max(dur, 1.0))
                venc[2:2] = ["-preset", ex.get("preset", "slow"), "-crf", str(ex.get("crf", 17)), "-maxrate", f"{cap:.1f}M", "-bufsize", f"{2 * cap:.1f}M"]
        args = self._inputs + ["-filter_complex", ";".join(self._fc), "-map", "[vout]"]
        if fmt != "gif":
            args += ["-map", "[aout]"]
        args += ["-t", ms(dur), "-r", f"{15 if fmt == 'gif' else self.fps:g}"] + venc
        args += (aenc + ["-ar", "48000", "-ac", "2"] if fmt != "gif" else ["-an"]) + extra + [str(self.out)]
        (self.tmp / "final.cmd").write_text(" ".join(a if " " not in a else repr(a) for a in ["ffmpeg"] + args) + "\n")
        ffmpeg.run(args, quiet=not self.verbose)
        if ex.get("cover") is not None and not self.proxy:
            cover = self.out.with_suffix(".png")
            ffmpeg.run(["-ss", ms(min(float(ex["cover"]), max(0.0, dur - 1 / self.fps))), "-i", str(self.out), "-frames:v", "1", str(cover)], quiet=not self.verbose)
            self.say(f"✓ cover {cover}")
        return self.out

    def run(self) -> Path:
        spec = self.spec
        if not spec.blocks:
            raise SystemExit("kanton: nothing to render (no blocks)")
        self.say(f"kanton render → {self.out.name}  ({len(spec.blocks)} blocks, {spec.duration:.1f} s{', proxy' if self.proxy else ''})")
        segs = self.segments()
        if self.verbose:
            for b, seg in zip(spec.blocks, segs):
                self.say(f"  block {b['i']} {b['src']} {b['in']:.2f}-{b['out']:.2f} → {seg.name}")
        base = self.concat(segs)
        loud = self.measure_loudness(base)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.final(base, loud)
        info = ffmpeg.probe(str(self.out))
        ffmpeg.probe.cache_clear()
        self.say(f"✓ {self.out}  {info['duration']:.2f} s, {info['bitrate'] / 1e6:.1f} Mbit/s, {info['size'] / 1e6:.0f} MB")
        return self.out


def render(spec: Spec, out: str | Path, proxy: bool = False, verbose: bool = False, tmp: str | Path | None = None) -> Path:
    return Renderer(spec, Path(out), proxy=proxy, tmp=Path(tmp) if tmp else None, verbose=verbose).run()


def clean(spec: Spec):
    d = spec.dir / ".kanton-tmp"
    if d.exists():
        shutil.rmtree(d)
