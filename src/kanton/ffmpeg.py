"""Locate and run ffmpeg/ffprobe; probe media files."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

SEARCH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]


@lru_cache(maxsize=None)
def binary(name: str) -> str:
    env = os.environ.get("KANTON_" + name.upper())
    if env:
        return env
    bundled = [Path(d) / "bin" / name for d in (getattr(sys, "_MEIPASS", None), Path(sys.executable).parent) if d]
    for p in bundled:                       # inside the desktop bundle
        if p.is_file():
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    for d in SEARCH:
        p = Path(d) / name
        if p.is_file():
            return str(p)
    sys.exit(f"kanton: {name} not found. Install ffmpeg or set KANTON_{name.upper()}=/path/to/{name}")


@lru_cache(maxsize=None)
def hwaccel() -> bool:
    """This ffmpeg can decode on the Mac's media engine (VideoToolbox)."""
    out = subprocess.run([binary("ffmpeg"), "-hide_banner", "-hwaccels"], capture_output=True, text=True).stdout
    return "videotoolbox" in out.split()


def decode_args(info: dict) -> list[str]:
    """Input options decoding an HEVC video on the media engine, the CPU left to the filters and the encoder. Set back in the
    source's pixel format (`format=` first in the chain), its frames are the software decoder's, bit for bit. HEVC only:
    measured faster from 1080p to 4K; H.264 decodes cheaply in software and was sometimes slower on the media engine."""
    return ["-hwaccel", "videotoolbox"] if info.get("codec") == "hevc" and hwaccel() else []


def _nice():
    try:
        os.nice(19)
    except Exception:
        pass


def run(args: list[str], quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given arguments (after the binary), at low priority."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-y"] + ([] if not quiet else ["-v", "error"]) + args
    res = subprocess.run(cmd, capture_output=quiet, text=True, preexec_fn=_nice)
    if check and res.returncode != 0:
        err = (res.stderr or "")[-3000:]
        raise RuntimeError(f"ffmpeg failed ({res.returncode}):\n{' '.join(cmd)[:2000]}\n{err}")
    return res


def run_capture(args: list[str]) -> str:
    """Run ffmpeg and return stderr (for filters that print their report there)."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-nostats"] + args
    res = subprocess.run(cmd, capture_output=True, text=True, preexec_fn=_nice)
    return res.stderr


@lru_cache(maxsize=None)
def probe(path: str) -> dict:
    """Media facts: duration, width/height (after rotation), fps, has_audio, transfer, has_alpha, kind."""
    p = Path(path)
    if not p.is_file():
        sys.exit(f"kanton: file not found: {path}")
    cmd = [binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"kanton: ffprobe failed on {path}: {res.stderr[-500:]}")
    d = json.loads(res.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    out = {"path": str(p), "duration": float(d.get("format", {}).get("duration") or 0), "has_audio": a is not None,
           "bitrate": int(d.get("format", {}).get("bit_rate") or 0), "size": int(d.get("format", {}).get("size") or 0),
           "created": d.get("format", {}).get("tags", {}).get("creation_time")}
    if v:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:
            w, h = h, w
        num, den = (v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den or 1)
        still = v.get("codec_name") in ("png", "mjpeg", "bmp", "tiff", "webp", "gif") or (v.get("nb_frames") in ("1",) and not a)
        pix = v.get("pix_fmt") or ""
        out.update({"width": w, "height": h, "fps": fps, "rotation": rot, "codec": v.get("codec_name"),
                    "transfer": v.get("color_transfer"), "pix_fmt": pix,
                    "has_alpha": bool(re.match(r"^(yuva|rgba|bgra|argb|abgr|gbrap|ya\d|pal8)", pix)),
                    "kind": "image" if still else "video"})
        if out["duration"] == 0 and v.get("duration"):
            out["duration"] = float(v["duration"])
    else:
        out.update({"width": 0, "height": 0, "fps": 0, "rotation": 0, "codec": None, "transfer": None, "pix_fmt": "", "has_alpha": False, "kind": "audio"})
    return out


def is_hlg(info: dict) -> bool:
    return info.get("transfer") in ("arib-std-b67", "smpte2084")
