"""The local timeline editor: a small HTTP server around the spec file, and one HTML page.

GET  /            the page
GET  /api/spec    {"spec", "timeline", "sources", "style", "safe_zone", "zoom"}
PUT  /api/spec    validate + save (400 with the message when the spec is invalid; 409 with the current version when
                  X-Version is older than the file: another process wrote it since the page loaded it)
GET  /api/version {"version"}: the file's mtime as loaded. The file is the truth: /api/spec and /api/version reload it
                  when it changed on disk (an agent at work); the page polls /api/version
POST /api/chat    {"text"}: one turn of the agent, its reply appended as it streams. Default: a token (KANTON_AGENT_TOKEN, see
                  agent.py); KANTON_AGENT=cli: `claude -p` (KANTON_CLAUDE or the PATH) or any command (KANTON_AGENT_CMD); GET /api/chat reports {"status", "messages", "session", "error"}
GET  /api/model   the app's speech model (faster-whisper): {"managed", "model", "present", "bytes", "size", "path", "status"...};
                  POST starts its download once (the page asks first), GET reports the bytes landed;
                  POST {"path"} adopts a model folder already on the disk, after checking it is the right model
GET  /media/KEY   a 540×960 proxy of a source (built on first request, cached), with range requests
GET  /font        the preset font file
POST /api/render  start a proxy render of the spec; GET /api/render reports it; GET /out serves the result
POST /api/segs    [spec, saved or not] → {"segs": [{key, start, end}]}: each block's pieces for the player, built in the
                  background; GET /seg/KEY.v.mp4 and /seg/KEY.a.mp4 serve its picture and sound (waiting for them)
POST /api/layout  [texts] → the renderer's lines and positions of each text ({"lines": [[x, top, line]], "size", "asc", "desc"})
POST /api/words   {"ops": [...], "dry"}: edit by the words (dry = preview of the words, nothing written)
GET  /api/words/KEY  cached words of a source (from `kanton captions`), or []
GET  /api/catalog the names the page can offer: transitions, effects, filters (built-in and preset), blends, masks, animations, text roles
POST /api/import  multipart file → copied to media/ next to the spec and added to `sources`; returns {"key", ...payload}
GET  /strip/KEY.jpg  a filmstrip of a video source (one row of frames, planned in the payload's sources[KEY].strip)
GET  /api/peaks/KEY  the waveform of a source: {"rate": 50, "peaks": [0-100, …]}
GET  /api/beats/KEY  the beats of a source (seconds of the file), as `kanton beats`
POST /api/export  render the saved spec to <spec>.<format> next to it (the real export); GET /api/export reports it
POST /api/captions {"lang"}: transcribe the clips with no words yet, then write the captions (as `kanton words` + `kanton
                  captions`); GET /api/captions reports it
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import agent, beats, ffmpeg, fx, transcribe, words
from .. import speed as ramp
from ..captions import apply_captions
from ..render import atempo
from ..spec import Spec
from ..text import TextStyle, find_font

HERE = Path(__file__).resolve().parent

# The editor speaks to people; the CLI keeps the precise message for agents.
_FRIENDLY = [
    (r"spec not found: (.+)", "Can't find the project file {0}."),
    (r"is not valid JSON", "This file isn't a kanton project."),
    (r"preset not found: (.+)", "This project uses a style that isn't installed ({0})."),
    (r"block (\d+) needs src, in, out", "Clip {clip} is incomplete."),
    (r"block (\d+): zoom must be", "Clip {clip}: zoom must be a number like 100, or two numbers like 100,110."),
    (r"block (\d+): out ≤ in", "Clip {clip}: the trim end must come after the trim start."),
    (r"overlay needs a positive dur", "An overlay needs a duration above 0."),
    (r"text needs 'text'", "A text has no content."),
    (r"text needs dur or end", "A text needs a duration above 0."),
    (r"float\(\)|to float", "A number field is empty or invalid."),
    (r"block (\d+) zoom .* is under 100", "Clip {clip}: zoom can't go below 100%."),
    (r"nothing to render", "Add at least one clip first."),
    (r"font not found: (\S+)", "The font {0} isn't installed."),
    (r"file not found: (.+)", "Can't find the file {0}."),
    (r"ffprobe failed", "This file can't be read as a video, an image or a sound."),
    (r"(?:ffmpeg|ffprobe) not found", "ffmpeg is missing: reinstall kanton."),
    (r"ffmpeg failed", "The video could not be rendered."),
    (r"unknown transition '([^']+)'", "Unknown transition {0}."),
    (r"unknown filter '([^']+)'", "Unknown filter {0}."),
    (r"unknown effect '([^']+)'", "Unknown effect {0}."),
    (r"unknown blend '([^']+)'", "Unknown blend mode {0}."),
    (r"unknown mask '([^']+)'", "Unknown mask {0}."),
    (r"unknown animation '([^']+)'", "Unknown animation {0}."),
    (r"unknown export format '([^']+)'", "Unknown export format {0}."),
    (r"block (\d+): fit must be", "Clip {clip}: fit must be cover or contain."),
    (r"block (\d+): crop must be", "Clip {clip}: crop needs x, y, width, height."),
    (r"block (\d+): a still needs", "Clip {clip}: a still needs a duration above 0."),
    (r"keyframe of (.+?) has unknown keys", "A keyframe of {0} has unknown fields."),
    (r"unknown reveal", "Reveal must be word or char."),
    (r"unknown speed curve '([^']+)'", "Unknown speed curve {0}."),
    (r"a speed curve", "A speed curve needs two points or more, speeds between 0.1 and 10."),
    (r"unknown voice '([^']+)'", "Unknown voice effect {0}."),
    (r"no transcription backend", "Auto captions need a transcriber (speech recognition), which is not installed: run  pip install 'kanton[mlx]'  (Apple Silicon) or  pip install 'kanton[whisper]', or set KANTON_WHISPER_PYTHON to a Python that has mlx-whisper or faster-whisper, then relaunch kanton."),
]


def friendly(msg: str) -> str:
    for pat, out in _FRIENDLY:
        if m := re.search(pat, msg):
            return out.format(*m.groups(), clip=int(m[1]) + 1 if "{clip}" in out else None)
    return "Something went wrong: " + msg.removeprefix("kanton: ")


@lru_cache(maxsize=None)
def _hw_encoder() -> str | None:
    enc = subprocess.run([ffmpeg.binary("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "h264_videotoolbox" if "h264_videotoolbox" in enc else None


def _venc() -> list[str]:
    hw = _hw_encoder()
    return ["-c:v", hw, "-b:v", "2500k"] if hw else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]


_proxy_lock = threading.Lock()


def ensure_proxy(spec: Spec, key: str) -> Path:
    """540×960 H.264 proxy of a source for the browser (short GOP for seeking), built under another name then renamed."""
    with _proxy_lock:
        return _ensure_proxy(spec, key)


def _ensure_proxy(spec: Spec, key: str) -> Path:
    src = spec.source(key)
    d = spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec")
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"proxy-{key}.mp4"
    if out.exists() and out.stat().st_mtime >= Path(src).stat().st_mtime:
        return out
    info = ffmpeg.probe(src)
    vf = "scale='if(gt(a,540/960),540,-2)':'if(gt(a,540/960),-2,960)'"
    if ffmpeg.is_hlg(info):
        vf = "colorspace=iall=bt2020:itrc=bt2020-10:all=bt709:format=yuv420p," + vf
    args = ffmpeg.decode_args(info) + ["-i", src, "-vf", vf + ",format=yuv420p", "-g", "30", "-r", f"{spec.fps:g}"] + _venc()
    args += (["-c:a", "aac", "-b:a", "96k", "-ac", "2"] if info["has_audio"] else ["-an"]) + ["-movflags", "+faststart", str(out.with_suffix(".part.mp4"))]
    ffmpeg.run(args)
    out.with_suffix(".part.mp4").replace(out)
    return out


SEG_KEYS = ("path", "in", "out", "speed", "mute", "frames", "still", "reverse", "flip", "vflip", "rotate", "crop", "fit", "background", "curve", "stabilize", "voice")
CHUNK = 20.0             # seconds of reel per player piece: one piece per block would be hundreds of MB for a long clip (WebKit's quota)


def _pieces(spec: Spec, b: dict) -> list[dict]:
    """A block as the player's pieces: whole, or cut every CHUNK seconds of reel on whole frames (stills, curves and reverse stay whole)."""
    if b["still"] or b["curve"] or b["reverse"] or b["dur"] <= CHUNK + 1e-6:
        return [b]
    fps, per, out = spec.fps, round(CHUNK * spec.fps), []
    for f0 in range(0, b["frames"], per):
        f1 = min(b["frames"], f0 + per)
        out.append({**b, "in": b["in"] + f0 / fps * b["speed"], "out": b["in"] + f1 / fps * b["speed"], "frames": f1 - f0, "dur": (f1 - f0) / fps,
                    "start": b["start"] + f0 / fps, "end": b["start"] + f1 / fps})
    return out


def seg_key(spec: Spec, b: dict) -> str:
    sig = json.dumps([spec.fps, 2] + [b[k] for k in SEG_KEYS])
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def piece_chain(spec: Spec, b: dict, info: dict, src_w: int, src_h: int) -> str:
    """The picture of a block for the player, from its proxy (`src_w`×`src_h`): still, crop, flips, rotation, fit on a
    540×960 canvas. Zoom, looks and layers stay on the page."""
    import math
    W, H = 540, 960
    f = "deshake=rx=32:ry=32," if b["stabilize"] else ""
    if b["curve"]:
        f += ramp.setpts(b["curve"], b["out"] - b["in"]) + ","
    elif not b["still"]:
        f += f"setpts=(PTS-STARTPTS)/{b['speed']},"
    if b["still"] and info["kind"] != "image":
        f += "trim=end_frame=1,loop=loop=-1:size=1:start=0,"
    if b["crop"]:
        k = src_w / max(1, info["width"])
        x, y, w, h = (v * k for v in b["crop"])
        f += f"crop={max(2, w):.0f}:{max(2, h):.0f}:{x:.0f}:{y:.0f},"
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
        fg = f"scale={W}:{H}:force_original_aspect_ratio=decrease"
        if bg == "blur":
            f += f"split[bgs][fgs];[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=luma_radius=15:luma_power=2:chroma_radius=8:chroma_power=2[bg];[fgs]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
        else:
            f += f"{fg}[fg];color=c={bg}:s={W}x{H}:r={spec.fps:g}[bg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
    else:
        f += f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    f += f",setsar=1,fps={spec.fps:g},trim=end_frame={b['frames']}"
    if b["reverse"]:
        f += ",reverse,setpts=PTS-STARTPTS"
    return f


def ensure_segment(spec: Spec, b: dict) -> Path:
    """One block for the player, from its proxy, cut to its frame count as in the render: its picture and its sound as
    two fragmented MP4 (seg-KEY.v.mp4, seg-KEY.a.mp4) that the page puts end to end in one player (Media Source), so
    nothing is loaded or sought at a cut. Zoom, looks and layers stay drawn by the page. Kept for the next edits."""
    d = spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec")
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"seg-{seg_key(spec, b)}"
    if Path(f"{out}.a.mp4").exists():
        return out
    info = ffmpeg.probe(b["path"])
    if info["kind"] == "image":
        src, src_w, src_h = b["path"], info["width"], info["height"]
        args = ["-loop", "1", "-framerate", f"{spec.fps:g}", "-t", f"{b['dur']:.4f}", "-i", str(src)]
    else:
        proxy = ensure_proxy(spec, b["src"])
        pi = ffmpeg.probe(str(proxy))
        src, src_w, src_h = proxy, pi["width"], pi["height"]
        args = ["-ss", f"{b['in']:.4f}"] + ([] if b["still"] else ["-t", f"{b['out'] - b['in']:.4f}"]) + ["-i", str(proxy)]
    sound = info["kind"] != "image" and ffmpeg.probe(str(src))["has_audio"] and not b["mute"]
    tempo = ramp.audio_chain(b["curve"], b["out"] - b["in"], f"{out}.tempo.txt") if b["curve"] else f"asetpts=PTS-STARTPTS,{atempo(b['speed'])}"
    a = (f"[0:a]aresample=48000,{tempo}{fx.voice_chain(b['voice'])}aformat=channel_layouts=stereo,apad" if sound else "anullsrc=r=48000:cl=stereo")
    fc = f"[0:v]{piece_chain(spec, b, info, src_w, src_h)}[v];{a},atrim=end_sample={round(b['dur'] * 48000)}" + (",areverse" if b["reverse"] else "") + "[a]"
    part, frag = f"{out}.{threading.get_ident()}.part", ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    ffmpeg.run(args + ["-filter_complex", fc,
                "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-bf", "0", "-g", "30", "-write_tmcd", "0", "-pix_fmt", "yuv420p", *frag, f"{part}.v.mp4",
                "-map", "[a]", "-c:a", "aac", "-b:a", "96k", *frag, f"{part}.a.mp4"])     # ffmpeg's AAC: 1024 first samples, skipped by the page
    Path(f"{part}.v.mp4").replace(f"{out}.v.mp4")
    Path(f"{part}.a.mp4").replace(f"{out}.a.mp4")   # last: the sign that the block is there
    return out


TIMELINE_KEYS = ("i", "src", "in", "out", "zoom", "dx", "dy", "flip", "vflip", "speed", "cut", "cut_dur", "mute", "frames", "start", "end", "dur", "still", "fit",
                 "background", "rotate", "crop", "reverse", "grade", "volume", "fade_in", "fade_out", "denoise", "pitch", "curve", "smooth", "stabilize", "voice")
STRIP_H, PEAK_RATE = 80, 50


def _tmp(spec: Spec) -> Path:
    d = spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec")
    d.mkdir(parents=True, exist_ok=True)
    return d


def strip_meta(key: str, info: dict) -> dict | None:
    """The filmstrip planned for a video source: `n` frames `w`×`h` in one row, `rate` frames a second of the source."""
    if info["kind"] != "video":
        return None
    d = max(info["duration"], 0.04)
    rate = min(2.0, 300 / d)
    return {"url": f"/strip/{key}.jpg", "rate": rate, "n": max(1, math.ceil(d * rate - 1e-9)), "w": 2 * math.ceil(STRIP_H * info["width"] / max(1, info["height"]) / 2), "h": STRIP_H}


def fresh_strip(spec: Spec, key: str) -> Path | None:
    """The filmstrip already on disk, if it is newer than its source."""
    src, out = spec.source(key), _tmp(spec) / f"strip-{key}.jpg"
    return out if out.exists() and out.stat().st_mtime >= Path(src).stat().st_mtime else None


def ensure_strip(spec: Spec, key: str) -> Path:
    out = fresh_strip(spec, key)
    if out:
        return out
    src, out = spec.source(key), _tmp(spec) / f"strip-{key}.jpg"
    m = strip_meta(key, ffmpeg.probe(src))
    part = out.with_suffix(f".{threading.get_ident()}.part.jpg")
    ffmpeg.run(["-i", str(ensure_proxy(spec, key)), "-vf", f"fps={m['rate']:.6f},scale={m['w']}:{m['h']},tile={m['n']}x1", "-frames:v", "1", "-q:v", "5", str(part)])
    part.replace(out)
    return out


def peaks(spec: Spec, key: str) -> list[int]:
    """The loudest sample of each 1/PEAK_RATE s of a source, 0-100 (cached)."""
    src, out = spec.source(key), _tmp(spec) / f"peaks-{key}.json"
    if out.exists() and out.stat().st_mtime >= Path(src).stat().st_mtime:
        return json.loads(out.read_text())
    p = []
    if ffmpeg.probe(src)["has_audio"]:
        pcm = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "4000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
        x = array.array("h")
        x.frombytes(pcm[: len(pcm) // 2 * 2])
        if sys.byteorder == "big":
            x.byteswap()
        hop = 4000 // PEAK_RATE
        p = [round(100 * max(map(abs, x[i:i + hop])) / 32768) for i in range(0, len(x), hop)]
    out.write_text(json.dumps(p))
    return p


def _timeline(spec: Spec) -> dict:
    return {"fps": spec.fps, "width": spec.W, "height": spec.H, "duration": spec.duration,
            "blocks": [{k: b[k] for k in TIMELINE_KEYS} for b in spec.blocks], "cuts": spec.cuts()}


def _catalog(spec: Spec | None) -> dict:
    c = fx.catalog()
    c["looks"] = {**fx.FILTERS, **((spec.preset.get("filters") or {}) if spec is not None else {})}      # the values, for the page's preview
    c["curve_points"] = ramp.CURVES
    if spec is not None:
        c["filters"] = sorted(set(c["filters"]) | set(spec.preset.get("filters") or {}))
        c["roles"] = sorted(set(((spec.preset.get("text") or {}).get("roles") or {})) | {"caption", "title"})
        c["transition_duration"] = spec.transitions.get("duration", 0.3)
    else:
        c["roles"], c["transition_duration"] = ["caption", "title"], 0.3
    return c


def _payload(spec: Spec | None) -> dict:
    if spec is None:
        return {"spec": None, "name": None, "path": None}
    sources = {}
    for key, path in spec.sources.items():
        try:
            i = ffmpeg.probe(path)
            sources[key] = {"path": path, "duration": i["duration"], "width": i["width"], "height": i["height"], "kind": i["kind"], "has_audio": i["has_audio"],
                            "has_alpha": i["has_alpha"], "url": f"/media/{key}", "rev": Path(path).stat().st_mtime_ns, "strip": strip_meta(key, i)}
        except SystemExit:
            sources[key] = {"path": path, "missing": True}
    strip = lambda d: {k: v for k, v in d.items() if k != "path"}
    return {"spec": spec.data, "timeline": _timeline(spec), "sources": sources, "style": spec.text_style(),
            "safe_zone": spec.preset.get("safe_zone", {}), "zoom": spec.zoom, "captions": spec.preset.get("captions", {}),
            "name": spec.path.name if spec.path else "spec", "path": str(spec.path) if spec.path else None,
            "words": words.view(spec) if spec.data.get("words") else [],
            "sounds": [strip(m) for m in spec.sounds], "music": strip(spec.music) if spec.music else None, "markers": spec.markers,
            "effects": spec.effects, "canvas": spec.canvas, "catalog": _catalog(spec), "transitions": spec.transitions,
            "overlays": [strip(o) for o in spec.overlays], "texts": [{k: v for k, v in t.items()} for t in spec.texts]}


def claude_binary() -> str | None:
    """The Claude Code binary: KANTON_CLAUDE, then the PATH (as ffmpeg)."""
    p = os.environ.get("KANTON_CLAUDE") or shutil.which("claude")
    return p if p and Path(p).exists() else None


def agent_mode() -> str:
    """"token" (default), or "cli": KANTON_AGENT, else "cli" when KANTON_AGENT_CMD is set."""
    return os.environ.get("KANTON_AGENT") or ("cli" if os.environ.get("KANTON_AGENT_CMD") else "token")


def _tool_line(inp: dict) -> str:
    return str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or json.dumps(inp))[:160]


_CHAT_ERR = {409: "the agent is still answering", 400: "nothing to send (open a spec and type a message)"}


def _mtime(spec: Spec | None) -> int | None:
    return spec.path.stat().st_mtime_ns if spec and spec.path and spec.path.exists() else None


class State:
    def __init__(self, spec: Spec | None):
        self.spec = spec
        self.mtime = _mtime(spec)
        self.lock = threading.Lock()
        self.render = {"status": "idle", "file": None, "error": None}
        self.export = {"status": "idle", "file": None, "error": None}
        self.captions = {"status": "idle", "error": None}
        self.chat = {"status": "idle", "messages": [], "session": None, "error": None}
        self.history: list = []              # the token agent's conversation, in its provider's format
        self.model = {"status": "idle", "error": None}
        self.model_size = "?"                           # fetched from the Hub once
        self.segs = {}                                  # key → the build of a block's pieces (a future)
        self.strips = {}                                # source key → the build of its filmstrip (a future)
        self.seg_pool = ThreadPoolExecutor(4)

    def load(self, path: str | Path) -> Spec:
        spec = Spec.load(path)
        with self.lock:
            self.adopt(spec)
            self.render = {"status": "idle", "file": None, "error": None}
        return spec

    def adopt(self, spec: Spec):
        """The spec this process just loaded or saved."""
        self.spec, self.mtime, self.strips = spec, _mtime(spec), {}

    def fresh(self):
        """Reload the file when another process wrote it; a half-written file keeps the last good spec."""
        p = self.spec.path if self.spec else None
        if p and p.exists() and p.stat().st_mtime_ns != self.mtime:
            try:
                self.adopt(Spec.load(p))
            except (SystemExit, ValueError, TypeError, OSError):
                pass

    def payload(self) -> dict:
        return {**_payload(self.spec), "version": self.version()}

    def version(self) -> str | None:
        """The mtime as a string: in ns it exceeds 2**53, and a JSON number would be rounded by the page."""
        return str(self.mtime) if self.mtime else None

    def want_segments(self, spec: Spec) -> list[dict]:
        """Each block's pieces (`spec`: the blocks on the page, saved or not), built in the background in reel order; the
        pieces no block uses any more are removed."""
        out = []
        with self.lock:
            for b in spec.blocks:
                for pc in _pieces(spec, b):
                    k = seg_key(spec, pc)
                    if k not in self.segs or self.segs[k].done() and self.segs[k].exception():
                        self.segs[k] = self.seg_pool.submit(ensure_segment, spec, pc)
                    out.append({"key": k, "start": pc["start"], "end": pc["end"]})
            keep = set(self.segs)
        d = spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec")
        for f in [*d.glob("seg-*.?.mp4"), *d.glob("seg-*.tempo.txt"), *d.glob("draft-*")]:      # the player's pieces, not the render's segments
            if f.name[4:20] not in keep and ".part" not in f.name:
                f.unlink(missing_ok=True)
        return out

    def start_render(self, final: bool = False):
        """A proxy render for the preview, or (final) the export next to the spec, in the background."""
        from ..render import render
        job = "export" if final else "render"
        with self.lock:
            if getattr(self, job)["status"] == "running" or self.spec is None:
                return
            setattr(self, job, {"status": "running", "file": None, "error": None})
            spec = Spec(json.loads(json.dumps(self.spec.data)), self.spec.path)

        def go():
            try:
                out = spec.dir / f"{spec.path.stem}.{spec.export['format']}" if final else _tmp(spec) / (spec.path.stem + "-proxy.mp4")
                render(spec, out, proxy=not final)
                with self.lock:
                    setattr(self, job, {"status": "done", "file": str(out), "error": None})
            except BaseException as e:  # SystemExit included
                with self.lock:
                    setattr(self, job, {"status": "error", "file": None, "error": friendly(str(e))[-2000:]})
        threading.Thread(target=go, daemon=True).start()

    def start_captions(self, lang: str, srt: str | None = None, src: str | None = None):
        """Transcribe the clips that have no words yet into the saved spec, then rebuild its captions."""
        with self.lock:
            if self.captions["status"] == "running" or self.spec is None:
                return
            self.captions = {"status": "running", "error": None}
            path = self.spec.path

        def go():
            try:
                spec = Spec.load(path)
                if srt is not None:               # an .srt the user brought: its cues become the words, no transcription
                    srcs = sorted({b["src"] for b in spec.blocks})
                    if src is None and len(srcs) != 1:
                        sys.exit(f"kanton: the reel uses {len(srcs)} sources; say which one the .srt belongs to ({', '.join(srcs)})")
                    target = src or srcs[0]
                    if target not in srcs:
                        sys.exit(f"kanton: no source {target!r} in the blocks")
                    ws = transcribe.words_from_srt(srt)
                    if not ws:
                        sys.exit("kanton: no cue found in that .srt")
                    spec.data.setdefault("words", {})[target] = ws
                n = apply_captions(spec, spec.data.get("words", {}) if srt is not None else words.transcribe_missing(spec, language=lang))
                spec.save()
                with self.lock:
                    self.adopt(Spec.load(path))
                    self.captions = {"status": "done", "captions": n, "error": None}
            except BaseException as e:  # SystemExit included
                with self.lock:
                    self.captions = {"status": "error", "error": friendly(str(e))[-2000:]}
        threading.Thread(target=go, daemon=True).start()

    def start_chat(self, text: str) -> int:
        """One turn of the agent: `claude -p` in the spec's folder, its reply appended as it streams. The session carries
        on while the app is open (--resume). Returns the HTTP status: 409 while a turn runs, 400 with nothing to send."""
        with self.lock:
            if self.chat["status"] == "running":
                return 409
            if self.spec is None or not text.strip():
                return 400
            mode, custom = agent_mode(), os.environ.get("KANTON_AGENT_CMD")
            exe = None if mode == "token" or custom else claude_binary()
            fail = None
            if mode not in ("token", "cli"):
                fail = f"KANTON_AGENT must be token or cli, not {mode!r}"
            elif mode == "token":
                try:
                    agent.config()
                except agent.AgentError as e:
                    fail = str(e)
            elif not exe and not custom:
                fail = "claude not found: install Claude Code, set KANTON_CLAUDE to the binary, or set KANTON_AGENT_CMD to another agent's command"
            if fail:
                self.chat = {**self.chat, "status": "error", "error": fail}
                return 200
            self.chat = {**self.chat, "status": "running", "error": None, "messages": self.chat["messages"] + [{"role": "user", "text": text}]}
            spec, session = self.spec, self.chat["session"]
        system = f"You are editing {spec.path} with the kanton CLI; the editor shows the file and reloads it after each change."
        if mode == "token":
            def say(role, t):
                with self.lock:
                    self.chat["messages"].append({"role": role, "text": t})

            def go_token():
                err = None
                try:
                    agent.turn(self.history, text, system, spec.dir, say)
                except BaseException as e:
                    err = str(e) or type(e).__name__
                with self.lock:
                    self.chat = {**self.chat, "status": "error" if err else "done", "error": err and err[-2000:]}
            threading.Thread(target=go_token, daemon=True).start()
            return 200
        if custom:                           # another agent's command: the message is its last argument, its stdout the reply
            def go_cmd():
                err = None
                try:
                    pr = subprocess.Popen(shlex.split(custom) + [text], cwd=spec.dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    for line in pr.stdout:
                        if line.strip():
                            with self.lock:
                                self.chat["messages"].append({"role": "assistant", "text": line.rstrip()})
                    if pr.wait():
                        err = f"{shlex.split(custom)[0]} exited with {pr.returncode}"
                except BaseException as e:
                    err = str(e)
                with self.lock:
                    self.chat = {**self.chat, "status": "error" if err else "done", "error": err}
            threading.Thread(target=go_cmd, daemon=True).start()
            return 200
        cmd = [exe, "-p", text, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--allowedTools", "Bash(kanton:*)",
               "--append-system-prompt", system]
        if session:
            cmd += ["--resume", session]

        def go():
            err, noise = None, []
            try:
                pr = subprocess.Popen(cmd, cwd=spec.dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in pr.stdout:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        noise.append(line.strip()); continue
                    with self.lock:
                        if ev.get("session_id"):
                            self.chat["session"] = ev["session_id"]
                        if ev.get("type") == "assistant":
                            for c in (ev.get("message") or {}).get("content") or []:
                                if c.get("type") == "text" and c.get("text"):
                                    self.chat["messages"].append({"role": "assistant", "text": c["text"]})
                                elif c.get("type") == "tool_use":
                                    self.chat["messages"].append({"role": "tool", "text": f"{c.get('name')}: {_tool_line(c.get('input') or {})}"})
                        elif ev.get("type") == "result" and (ev.get("is_error") or ev.get("subtype") != "success"):
                            err = str(ev.get("result") or ev.get("subtype"))
                if pr.wait() and not err:
                    err = "\n".join(noise[-5:]) or f"claude exited with {pr.returncode}"
            except BaseException as e:
                err = str(e)
            with self.lock:
                self.chat = {**self.chat, "status": "error" if err else "done", "error": err and err[-2000:]}
        threading.Thread(target=go, daemon=True).start()
        return 200

    def model_view(self) -> dict:
        """The speech model: here or not, bytes landed, size on the Hub (asked once), the download job."""
        m = transcribe.model_status()
        if m.get("managed") and not m["present"] and self.model_size == "?":
            self.model_size = transcribe.model_size(m["repo"])
        with self.lock:
            return {**m, **self.model, "size": None if self.model_size == "?" else self.model_size}

    def start_model(self) -> int:
        """Fetch the speech model in the background; 409 while it runs."""
        with self.lock:
            if self.model["status"] == "running":
                return 409
            self.model = {"status": "running", "error": None}

        def go():
            try:
                transcribe.download_model()
                with self.lock:
                    self.model = {"status": "done", "error": None}
            except BaseException as e:
                with self.lock:
                    self.model = {"status": "error", "error": friendly(str(e))[-2000:]}
        threading.Thread(target=go, daemon=True).start()
        return 200


_WORD_OPS = {"cut": lambda s, q: words.cut(s, q["a"], q["b"], q.get("o", 0)), "keep": lambda s, q: words.keep(s, q["a"], q["b"], q.get("o", 0)),
             "move": lambda s, q: words.move(s, q["a"], q["b"], q.get("before"), q.get("o", 0), q.get("before_o", 0)),
             "copy": lambda s, q: words.copy(s, q["a"], q["b"], q.get("before"), q.get("o", 0), q.get("before_o", 0)),
             "fix": lambda s, q: words.fix(s, q["n"], q["text"])}


def _multipart_file(ctype: str, body: bytes) -> tuple[str, bytes]:
    """The first file part of a multipart/form-data body: (filename, bytes)."""
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        raise ValueError("expected a multipart/form-data upload")
    for part in body.split(b"--" + m.group(1).encode()):
        head, _, payload = part.partition(b"\r\n\r\n")
        fn = re.search(rb'filename="([^"]*)"', head)
        if fn:
            return fn.group(1).decode("utf-8", "replace"), payload[:-2] if payload.endswith(b"\r\n") else payload
    raise ValueError("no file in the upload")


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str | None = None):
        if not path.is_file():
            return self._json(404, {"error": f"not found: {path.name}"})
        size = path.stat().st_size
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_GET(self):
        st = self.state
        p = self.path.split("?")[0]
        if p == "/":
            return self._file(HERE / "index.html", "text/html; charset=utf-8")
        if p == "/api/spec":
            with st.lock:
                st.fresh()
                return self._json(200, st.payload())
        if p == "/api/model":
            return self._json(200, st.model_view())
        if p == "/api/version":
            with st.lock:
                st.fresh()
                return self._json(200, {"version": st.version()})
        if p == "/api/catalog":
            with st.lock:
                return self._json(200, _catalog(st.spec))
        if p.startswith("/media/"):
            key = p[len("/media/"):]
            with st.lock:
                spec = st.spec
            if spec is None or key not in spec.sources:
                return self._json(404, {"error": f"unknown source {key}"})
            try:
                info = ffmpeg.probe(spec.sources[key])
            except SystemExit as e:
                return self._json(404, {"error": str(e)})
            if info["kind"] == "image":
                return self._file(Path(spec.sources[key]))
            try:
                return self._file(ensure_proxy(spec, key), "video/mp4")
            except RuntimeError as e:
                return self._json(500, {"error": str(e)[-1000:]})
        if p == "/font":
            with st.lock:
                if st.spec is None:
                    return self._file(HERE.parent / "fonts" / "Anton-Regular.ttf", "font/ttf")
                style = st.spec.text_style()
                dirs = [Path(st.spec.preset.get("_dir", st.spec.dir)), st.spec.dir]
            f = Path(find_font(style.get("font", "Anton-Regular.ttf"), dirs))
            return self._file(f, {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"}.get(f.suffix.lstrip("."), "font/ttf"))
        if p in ("/api/render", "/api/export", "/api/captions", "/api/chat"):
            with st.lock:
                return self._json(200, dict(getattr(st, p[5:])))
        m = re.fullmatch(r"/strip/(.+)\.jpg|/api/(peaks|beats)/(.+)", p)
        if m:                                 # the timeline's filmstrips and waveforms, the music's beats
            key = m[1] or m[3]
            with st.lock:
                spec = st.spec
            if spec is None or key not in spec.sources:
                return self._json(404, {"error": f"unknown source {key}"})
            try:
                if m[1]:                      # built in the background; 202 while it is not there (the page asks again)
                    done = fresh_strip(spec, key)
                    if done:                  # already on disk (the spec was only reloaded): no pool job
                        return self._file(done, "image/jpeg")
                    with st.lock:
                        job = st.strips.get(key) or st.strips.setdefault(key, st.seg_pool.submit(ensure_strip, spec, key))
                    if not job.done():
                        return self._json(202, {"status": "building"})
                    return self._file(job.result(), "image/jpeg")
                return self._json(200, {"rate": PEAK_RATE, "peaks": peaks(spec, key)} if m[2] == "peaks" else {"beats": beats.detect(spec.source(key))})
            except (SystemExit, RuntimeError, subprocess.CalledProcessError, TypeError) as e:
                return self._json(500, {"error": friendly(str(e))[-1000:]})
        m = re.fullmatch(r"/seg/([0-9a-f]{16})\.([va])\.mp4", p)
        if m:                                 # a block's picture or sound; 202 while it is being built (the page asks again:
            with st.lock:                     # a request held for minutes is dropped by the browser)
                job = st.segs.get(m[1])
            if job is None:
                return self._json(404, {"error": "no such piece"})
            if not job.done():
                return self._json(202, {"status": "building"})
            try:
                out = job.result()
            except BaseException as e:  # SystemExit included
                return self._json(500, {"error": friendly(str(e))[-2000:]})
            return self._file(Path(f"{out}.{m[2]}.mp4"), "video/mp4" if m[2] == "v" else "audio/mp4")
        if p == "/out":
            with st.lock:
                f = st.render.get("file")
            return self._file(Path(f), "video/mp4") if f else self._json(404, {"error": "no render yet"})
        if p.startswith("/api/words/"):
            key = p[len("/api/words/"):]
            with st.lock:
                if st.spec is None:
                    return self._json(200, {"words": []})
                f = st.spec.dir / ".kanton-tmp" / (st.spec.path.stem if st.spec.path else "spec") / f"words-{key}.json"
            return self._json(200, {"words": json.loads(f.read_text()) if f.exists() else []})
        return self._json(404, {"error": "no such route"})

    def do_POST(self):
        p, st = self.path.split("?")[0], self.state
        if p == "/api/import":                # a file from the page, copied next to the spec and added to its sources
            try:
                name, body = _multipart_file(self.headers.get("Content-Type", ""), self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                with st.lock:
                    if st.spec is None:
                        return self._json(400, {"error": "no spec open"})
                    spec = st.spec
                    media = spec.dir / "media"
                    media.mkdir(exist_ok=True)
                    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-.") or "file"
                    dest, n = media / safe, 2
                    while dest.exists() and dest.read_bytes() != body:
                        dest, n = media / f"{Path(safe).stem}-{n}{Path(safe).suffix}", n + 1
                    dest.write_bytes(body)
                    data = json.loads(json.dumps(spec.data))
                    sources = data.setdefault("sources", {})
                    key = next((k for k, v in sources.items() if spec.resolve(v) == str(dest)), None)
                    if key is None:
                        from ..app import source_key
                        key = source_key(str(dest), sources)
                        sources[key] = f"media/{dest.name}"
                    spec = Spec(data, spec.path)
                    spec.save()
                    st.adopt(spec)
                    return self._json(200, {"key": key, **st.payload()})
            except (SystemExit, ValueError, KeyError) as e:
                return self._json(400, {"error": friendly(str(e))})
        if p == "/api/words":                 # {"ops": [{"op", "a", "b", "before", "n", "text"}], "dry"}: same code as `kanton words`
            try:
                q = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
                with st.lock:
                    spec = Spec(json.loads(json.dumps(st.spec.data)), st.spec.path)
                    for o in q["ops"]:
                        spec = _WORD_OPS[o["op"]](spec, o)
                    if q.get("dry"):          # preview of the pending edits, nothing written
                        return self._json(200, {"words": words.view(spec)})
                    spec.save()
                    st.adopt(spec)
                    return self._json(200, st.payload())
            except (SystemExit, KeyError, TypeError, ValueError, AttributeError) as e:
                return self._json(400, {"error": friendly(str(e))})
        if p == "/api/layout":                # so the page draws the texts as the export will: same wrap, same place
            try:
                q = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
                with st.lock:
                    spec = st.spec
                dirs, out = [Path(spec.preset.get("_dir", spec.dir)), spec.dir], []
                for t in q:
                    ts = TextStyle(spec.text_style(spec._norm_text(t)), dirs)
                    asc, desc = ts.font.getmetrics()
                    out.append({"lines": ts.layout(ts.wrap(t["text"]), spec.W, spec.H), "size": ts.size, "asc": asc, "desc": desc})
                return self._json(200, out)
            except (SystemExit, KeyError, TypeError, ValueError, AttributeError) as e:
                return self._json(400, {"error": friendly(str(e))})
        if p == "/api/segs":                  # the blocks on the page, saved or not: nothing is written
            try:
                spec = Spec(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")), st.spec.path)
                return self._json(200, {"segs": st.want_segments(spec)})
            except (json.JSONDecodeError, SystemExit, KeyError, TypeError, ValueError, AttributeError) as e:
                return self._json(400, {"error": friendly(str(e))})
        if p == "/api/model":                 # {"path"}: adopt a model folder on the disk (checked); no body: fetch the model (once)
            path = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("path")
            if path:
                try:
                    m = transcribe.use_model(path)
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {**st.model_view(), **m})
            code = st.start_model()
            return self._json(code, {**st.model_view(), **({"error": "the download is running"} if code == 409 else {})})
        if p == "/api/chat":                  # {"text"}: one turn of the agent; GET /api/chat reports it
            n = int(self.headers.get("Content-Length") or 0)
            code = st.start_chat(str(json.loads(self.rfile.read(n) or b"{}").get("text") or ""))
            with st.lock:
                return self._json(code, {**st.chat, **({"error": _CHAT_ERR[code]} if code in _CHAT_ERR else {})})
        if p == "/api/captions":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            st.start_captions(body.get("lang") or "fr", srt=body.get("srt"), src=body.get("src"))
            with st.lock:
                return self._json(200, dict(st.captions))
        if p not in ("/api/render", "/api/export"):
            return self._json(404, {"error": "no such route"})
        st.start_render(final=p == "/api/export")
        with st.lock:
            return self._json(200, dict(getattr(st, p[5:])))

    def do_PUT(self):
        if self.path.split("?")[0] != "/api/spec":
            return self._json(404, {"error": "no such route"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError as e:
            return self._json(400, {"error": f"invalid JSON: {e}"})
        st = self.state
        with st.lock:
            if st.spec is None:
                return self._json(400, {"error": "no spec open"})
            st.fresh()
            v = self.headers.get("X-Version")
            if v and st.mtime and v != st.version():
                return self._json(409, {"error": f"{st.spec.path.name} changed on disk since the page loaded it", "version": st.version()})
            try:
                spec = Spec(data, st.spec.path)
            except (SystemExit, ValueError, TypeError) as e:
                return self._json(400, {"error": friendly(str(e))})
            if data.get("words") and data.get("blocks") != st.spec.data.get("blocks"):    # the cut changed: the captions follow the words
                apply_captions(spec, data["words"])
            spec.save()
            st.adopt(spec)
            return self._json(200, st.payload())


def make_server(spec: Spec | None, port: int) -> ThreadingHTTPServer:
    state = State(spec)
    handler = type("KantonHandler", (Handler,), {"state": state})
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        sys.exit(f"kanton: cannot listen on port {port} ({e.strerror}); try --port <other>")
    srv.spec = spec  # type: ignore[attr-defined]
    srv.state = state  # type: ignore[attr-defined]
    return srv


def serve(spec: Spec, port: int = 8788, open_browser: bool = True):
    srv = make_server(spec, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"kanton edit  {spec.path}  →  {url}   (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
