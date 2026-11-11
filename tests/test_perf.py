"""Speed without a change of output: hardware decoding, pass 1 side by side, text pictures cut to what they draw."""
import hashlib
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from kanton import ffmpeg
from kanton import render as R
from kanton.spec import Spec
from kanton.text import TextStyle, states


def frames_md5(p: Path) -> str:
    out = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", str(p), "-map", "0:v", "-f", "framemd5", "-"], capture_output=True, text=True, check=True).stdout
    return hashlib.md5("\n".join(l for l in out.splitlines() if not l.startswith("#")).encode()).hexdigest()


def test_camera_video_decodes_on_the_media_engine(media, tmp_path, monkeypatch):
    spec = Spec({"sources": {"f": str(media["face"]), "s": str(media["still"])}, "blocks": [{"src": "f", "in": 0, "out": 1}, {"src": "s", "dur": 1}]}, tmp_path / "m.json")
    r = R.Renderer(spec, tmp_path / "o.mp4")
    hevc = {**ffmpeg.probe(spec.blocks[0]["path"]), "codec": "hevc"}
    monkeypatch.setattr(ffmpeg, "hwaccel", lambda: True)
    vid = r.block_args(spec.blocks[0], hevc, 1, tmp_path / "x.mkv")
    assert vid[:2] == ["-hwaccel", "videotoolbox"] and vid.index("-hwaccel") < vid.index("-i")
    assert vid[vid.index("-filter_complex") + 1].startswith("[0:v]format=yuv420p,")      # the frames back in the decoder's own format
    assert "-hwaccel" not in r.block_args(spec.blocks[0], ffmpeg.probe(spec.blocks[0]["path"]), 1, tmp_path / "x.mkv")   # H.264: in software
    assert "-hwaccel" not in r.block_args(spec.blocks[1], ffmpeg.probe(spec.blocks[1]["path"]), 1, tmp_path / "x.mkv")   # a picture
    monkeypatch.setattr(ffmpeg, "hwaccel", lambda: False)
    assert "-hwaccel" not in r.block_args(spec.blocks[0], hevc, 1, tmp_path / "x.mkv")


@pytest.mark.skipif(not ffmpeg.hwaccel(), reason="this ffmpeg has no VideoToolbox")
def test_hardware_decoding_gives_the_same_frames(tmp_path, monkeypatch):
    src = tmp_path / "hevc.mp4"                       # an iPhone-like HEVC clip
    subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3", "-c:v", "hevc_videotoolbox",
                    "-b:v", "10M", "-tag:v", "hvc1", str(src)], check=True)
    spec = Spec({"sources": {"f": str(src)}, "blocks": [{"src": "f", "in": 1, "out": 2, "zoom": [100, 112]}, {"src": "f", "in": 1, "out": 2, "fit": "contain", "background": "blur"}]}, tmp_path / "m.json")
    sums = []
    for hw in (False, True):
        monkeypatch.setattr(ffmpeg, "hwaccel", lambda hw=hw: hw)
        r = R.Renderer(spec, tmp_path / "o.mp4", tmp=tmp_path / f"t{hw}")
        sums.append([frames_md5(r.render_block(b)) for b in spec.blocks])
    assert sums[0] == sums[1]


def test_pass_one_runs_blocks_side_by_side_and_keeps_their_order(media, tmp_path, monkeypatch):
    spec = Spec({"sources": {"f": str(media["face"])}, "blocks": [{"src": "f", "in": i * 0.5, "out": i * 0.5 + 0.5} for i in range(4)]}, tmp_path / "m.json")
    r = R.Renderer(spec, tmp_path / "o.mp4")
    seen = set()

    def slow(self, b):
        seen.add(threading.get_ident())
        time.sleep(0.3)
        return Path(f"seg{b['i']}")
    monkeypatch.setattr(R.Renderer, "render_block", slow)
    t0 = time.perf_counter()
    assert r.segments() == [Path(f"seg{i}") for i in range(4)]
    assert len(seen) > 1 and time.perf_counter() - t0 < 4 * 0.3


def test_text_picture_is_cut_to_what_it_draws(tmp_path):
    st = TextStyle({"size": 60, "x": 540, "y": 1400})
    s = states(st, "un deux trois", 1080, 1920, tmp_path / "t", t0=0, dur=1, crop=True)[0]
    w, h = Image.open(s["png"]).size
    x0, y0, x1, y1 = s["bbox"]
    ox, oy = s["origin"]
    assert w * h < 1080 * 1920 / 10                                 # not a full frame of transparent pixels
    assert ox % 2 == 0 and oy % 2 == 0                              # on the chroma grid of yuv420p
    assert ox <= x0 and oy <= y0 and ox + w >= x1 and oy + h >= y1    # every drawn pixel kept, at its place


def test_player_pieces_leave_the_render_cache_alone(media, tmp_path, monkeypatch):
    from kanton import editor
    p = tmp_path / "m.json"
    spec = Spec({"sources": {"f": str(media["face"])}, "blocks": [{"src": "f", "in": 0, "out": 1}]}, p)
    d = tmp_path / ".kanton-tmp" / "m"
    d.mkdir(parents=True)
    seg, old = d / "seg-0123456789abcdef.mkv", d / "seg-fedcba9876543210.v.mp4"
    seg.write_bytes(b"x"); old.write_bytes(b"x")
    monkeypatch.setattr(editor, "ensure_segment", lambda spec, b: None)
    editor.State(spec).want_segments(spec)
    assert seg.exists()          # pass 1 of the render: kept for the next export
    assert not old.exists()      # a player piece no block uses: removed


def test_font_lookup_walks_the_folders_once(tmp_path, monkeypatch):
    from kanton import text
    walks = []
    rglob = Path.rglob
    monkeypatch.setattr(Path, "rglob", lambda self, pat: walks.append(self) or rglob(self, pat))
    for _ in range(3):                                   # one TextStyle per text: the project folder is walked once, not per text
        assert text.find_font("Anton-Regular.ttf", [tmp_path]).endswith("Anton-Regular.ttf")
    assert walks.count(tmp_path) == 1
