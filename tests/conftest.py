import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kanton import ffmpeg


def ff(*args):
    subprocess.run([ffmpeg.binary("ffmpeg"), "-hide_banner", "-v", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict:
    """Small synthetic sources, generated once per session with ffmpeg (no external files)."""
    d = tmp_path_factory.mktemp("media")
    m = {}
    # face: 1080x1920, 6 s, moving test pattern; tone with two silences (2.0-3.0 s and 4.0-4.6 s)
    m["face"] = d / "face.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=6",
       "-f", "lavfi", "-i", "aevalsrc=exprs='0.4*sin(440*2*PI*t)*(1-between(t\\,2\\,3))*(1-between(t\\,4\\,4.6))':s=48000:c=stereo:d=6",
       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", m["face"])
    # broll: 1620x2880 (≥1.5× → 2× cover path), solid blue, 3 s, no audio
    m["broll"] = d / "broll.mp4"
    ff("-f", "lavfi", "-i", "color=c=blue:size=1620x2880:rate=30:duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", m["broll"])
    # still: 400x300 solid green png
    m["still"] = d / "still.png"
    ff("-f", "lavfi", "-i", "color=c=lime:size=400x300:rate=1:duration=1", "-frames:v", "1", m["still"])
    # sticker: 1080x1920 ProRes 4444 with alpha: red square 300x300 at (100,1500), transparent elsewhere, 1 s
    m["sticker"] = d / "sticker.mov"
    ff("-f", "lavfi", "-i", "color=c=red:size=300x300:rate=30:duration=1,format=rgba,pad=1080:1920:100:1500:black@0.0,format=yuva444p10le",
       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", m["sticker"])
    # music: 10 s tone at 220 Hz
    m["music"] = d / "music.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=10", "-ac", "2", m["music"])
    # hlg: 1080x1920 2 s tagged HLG / bt2020
    m["hlg"] = d / "hlg.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=2", "-pix_fmt", "yuv420p10le",
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-c:v", "libx264", "-preset", "ultrafast",
       "-x264-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", m["hlg"])
    # wide: 1280x720, left half red, right half blue, 2 s, silent (fit, rotate, crop)
    m["wide"] = d / "wide.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:size=1280x720:rate=30:duration=2,drawbox=x=640:y=0:w=640:h=720:color=blue:t=fill",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", m["wide"])
    # tall: 1080x1920, top half red, bottom half blue, 2 s, silent (vflip)
    m["tall"] = d / "tall.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:size=1080x1920:rate=30:duration=2,drawbox=x=0:y=960:w=1080:h=960:color=blue:t=fill",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", m["tall"])
    # redblue: 1080x1920, red for 1 s then blue for 1 s, with a tone (reverse, freeze)
    m["redblue"] = d / "redblue.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:size=1080x1920:rate=30:duration=1", "-f", "lavfi", "-i", "color=c=blue:size=1080x1920:rate=30:duration=1",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
       "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", m["redblue"])
    # red: 1080x1920 solid red, 2 s, silent (transitions)
    m["red"] = d / "red.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:size=1080x1920:rate=30:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", m["red"])
    # music1: 1 s tone at 220 Hz (loop)
    m["music1"] = d / "music1.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1", "-ac", "2", m["music1"])
    # black / white stills 300x300 (blend modes)
    m["black"] = d / "black.png"
    ff("-f", "lavfi", "-i", "color=c=black:size=300x300:rate=1:duration=1", "-frames:v", "1", m["black"])
    m["white"] = d / "white.png"
    ff("-f", "lavfi", "-i", "color=c=white:size=300x300:rate=1:duration=1", "-frames:v", "1", m["white"])
    return m


@pytest.fixture
def spec_file(tmp_path):
    def make(data: dict, name: str = "montage.json") -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p
    return make


def probe_frame_mean(video: Path, t: float, box: tuple[int, int, int, int] | None = None) -> tuple[float, float, float]:
    """Mean RGB of a frame (or a crop box x,y,w,h) at time t."""
    from PIL import Image
    import io
    args = [ffmpeg.binary("ffmpeg"), "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    png = subprocess.run(args, capture_output=True, check=True).stdout
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if box:
        x, y, w, h = box
        im = im.crop((x, y, x + w, y + h))
    px = list(im.get_flattened_data()) if hasattr(im, 'get_flattened_data') else list(im.getdata())
    n = len(px)
    return tuple(sum(p[i] for p in px) / n for i in range(3))


def probe_frame_count(video: Path, t: float, box: tuple[int, int, int, int], pred) -> int:
    """Number of pixels in a crop box at time t for which pred((r, g, b)) is true."""
    from PIL import Image
    import io
    args = [ffmpeg.binary("ffmpeg"), "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    png = subprocess.run(args, capture_output=True, check=True).stdout
    im = Image.open(io.BytesIO(png)).convert("RGB")
    x, y, w, h = box
    im = im.crop((x, y, x + w, y + h))
    px = list(im.get_flattened_data()) if hasattr(im, "get_flattened_data") else list(im.getdata())
    return sum(1 for p in px if pred(p))


def rms_db(path, start: float, end: float) -> float:
    """RMS level in dBFS of a slice of a file's audio."""
    import re
    args = [ffmpeg.binary("ffmpeg"), "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path), "-vn", "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"]
    err = subprocess.run(args, capture_output=True, text=True).stderr
    m = re.findall(r"RMS level dB: (-?[\d.]+|-inf)", err)
    return float("-inf") if not m or m[-1] == "-inf" else float(m[-1])


@pytest.fixture(autouse=True)
def _kanton_home(tmp_path_factory, monkeypatch):
    """Settings and the recent list never touch the real user config."""
    monkeypatch.setenv("KANTON_HOME", str(tmp_path_factory.mktemp("kanton-home")))
