import subprocess

import pytest

from kanton import ffmpeg
from kanton.render import Renderer, fesc, render
from kanton.spec import Spec

from conftest import probe_frame_count, probe_frame_mean


def test_fesc():
    assert fesc("a:b,c'd") == "a\\:b\\,c\\'d"


def make(media, spec_file, **over):
    d = {"sources": {"face": str(media["face"]), "broll": str(media["broll"]), "music": str(media["music"])},
         "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 116, "dx": 18}]}
    d.update(over)
    return Spec.load(spec_file(d))


def test_zoom_chain_static_and_push(media, spec_file):
    s = make(media, spec_file)
    r = Renderer(s, s.dir / "out.mp4")
    static = r.zoom_chain(s.blocks[1], 2)
    assert static.startswith("crop=w=1862.069:h=3310.345") and "scale=1080:1920" in static
    s2 = make(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 2, "zoom": [98, 106]}, {"src": "face", "in": 0, "out": 1, "cut": "punch"}])
    push = Renderer(s2, s2.dir / "o.mp4").zoom_chain(s2.blocks[0], 1)
    assert push.startswith("zoompan=z='0.98+(1.06-0.98)*on/59'") and "s=1080x1920" in push and "fps=30" in push
    punch = Renderer(s2, s2.dir / "o.mp4").zoom_chain(s2.blocks[1], 1)
    assert "zoompan" in punch and "max(0,1-on/4)" in punch


def test_scale_factor_and_hlg_chain(media, spec_file):
    s = make(media, spec_file)
    r = Renderer(s, s.dir / "o.mp4")
    assert r.scale_factor(ffmpeg.probe(str(media["face"]))) == 1
    assert r.scale_factor(ffmpeg.probe(str(media["broll"]))) == 2
    assert r.hlg_chain(ffmpeg.probe(str(media["face"]))) == ""
    assert "colorspace=iall=bt2020" in r.hlg_chain(ffmpeg.probe(str(media["hlg"])))


def test_cut_and_grade_chains(media, spec_file):
    s = make(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "in": 1, "out": 2, "cut": "blur"}, {"src": "face", "in": 2, "out": 3, "cut": "flash"}],
             grade={"contrast": 1.1, "saturation": 0.8, "temperature": 6200, "vignette": 0.5})
    r = Renderer(s, s.dir / "o.mp4")
    c = r.cut_chain()
    assert c.count("gblur") == 3 and "between(t,0.900,1.100)" in c and "fade=t=in:st=2.000:d=0.1:c=white" in c
    g = r.grade_chain()
    assert "eq=contrast=1.1000:saturation=0.8000" in g and "colortemperature=temperature=6200" in g and "vignette=angle=0.3000" in g
    assert Renderer(make(media, spec_file), s.dir / "o.mp4").grade_chain() == ""


def test_render_proxy_duration_and_size(media, spec_file, tmp_path):
    s = make(media, spec_file)
    out = render(s, tmp_path / "proxy.mp4", proxy=True)
    i = ffmpeg.probe(str(out))
    assert (i["width"], i["height"]) == (540, 960) and i["has_audio"]
    assert abs(i["duration"] - 2.5) < 0.07


def test_render_full_export_settings(media, spec_file, tmp_path):
    s = make(media, spec_file, blocks=[{"src": "face", "in": 0.5, "out": 1.5}, {"src": "face", "in": 3.0, "out": 3.5, "flip": True, "cut": "blur"}])
    out = render(s, tmp_path / "full.mp4")
    i = ffmpeg.probe(str(out))
    assert (i["width"], i["height"]) == (1080, 1920) and i["fps"] == 30 and i["codec"] == "h264"
    assert abs(i["duration"] - 1.5) < 0.07
    assert i["bitrate"] < 26e6
    cmd = (s.dir / ".kanton-tmp" / "montage" / "final.cmd").read_text()
    assert "-crf 17" in cmd and "-maxrate 23.5M" in cmd and "-ar 48000" in cmd     # 94 % of the API ceiling: the measured average stays under it


def test_block_cache_reused(media, spec_file, tmp_path):
    s = make(media, spec_file)
    r = Renderer(s, tmp_path / "a.mp4", proxy=True)
    seg = r.render_block(s.blocks[0])
    m = seg.stat().st_mtime_ns
    assert r.render_block(s.blocks[0]) == seg and seg.stat().st_mtime_ns == m


def test_render_overlays_text_music(media, spec_file, tmp_path):
    s = make(media, spec_file,
             blocks=[{"src": "face", "in": 0.0, "out": 3.0}],
             overlays=[{"kind": "video", "src": "broll", "t": 1.0, "dur": 1.0},
                       {"kind": "image", "src": str(media["still"]), "t": 0.2, "dur": 0.5, "x": 100, "y": 300, "width": 400},
                       {"kind": "sticker", "src": str(media["sticker"]), "t": 2.2, "dur": 0.6}],
             texts=[{"text": "salut", "t": 0.0, "dur": 1.0, "fill": "#FFD400"}],
             music={"src": "music", "gain_db": -20, "fade_out": 0.5})
    out = render(s, tmp_path / "ov.mp4")
    # broll (solid blue) covers the frame between 1 and 2 s, not before
    r, g, b = probe_frame_mean(out, 1.5)
    assert b > 150 and r < 80 and g < 80
    r2, g2, b2 = probe_frame_mean(out, 0.6)
    assert not (b2 > 150 and r2 < 80 and g2 < 80)
    # green still at (100,300) 400 px wide at 0.4 s
    r, g, b = probe_frame_mean(out, 0.4, (120, 320, 360, 260))
    assert g > 150 and r < 80 and b < 80
    # red sticker square at (100,1500) at 2.5 s, transparent elsewhere (the pattern shows through)
    r, g, b = probe_frame_mean(out, 2.5, (120, 1520, 260, 260))
    assert r > 150 and g < 80 and b < 80
    # yellow text pixels (default fill is white: the test spec sets the brand yellow) near the baseline at 0.5 s
    yellow = probe_frame_count(out, 0.5, (390, 1360, 300, 80), lambda p: abs(p[0] - 244) < 40 and abs(p[1] - 199) < 40 and p[2] < 90)
    assert yellow > 800
    assert probe_frame_count(out, 1.5, (390, 1360, 300, 80), lambda p: abs(p[0] - 244) < 40 and abs(p[1] - 199) < 40 and p[2] < 90) < 50
    i = ffmpeg.probe(str(out))
    assert abs(i["duration"] - 3.0) < 0.07 and i["has_audio"]


def test_render_speed_and_mute(media, spec_file, tmp_path):
    s = make(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 2, "speed": 2, "mute": True}, {"src": "face", "in": 0, "out": 1, "zoom": [100, 110]}])
    out = render(s, tmp_path / "sp.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 2.0) < 0.07


def test_render_hlg_source(media, spec_file, tmp_path):
    s = make(media, spec_file, sources={"h": str(media["hlg"])}, blocks=[{"src": "h", "in": 0, "out": 1}])
    out = render(s, tmp_path / "hlg.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 1.0) < 0.07


def test_render_empty_exits(media, spec_file, tmp_path):
    s = make(media, spec_file, blocks=[])
    with pytest.raises(SystemExit):
        render(s, tmp_path / "x.mp4")


def test_render_refuses_zoom_under_100(media, spec_file, tmp_path):
    s = make(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 90}])
    with pytest.raises(SystemExit, match="under 100"):
        render(s, tmp_path / "x.mp4", proxy=True)


def test_segment_frames_match_spec_from_23976_source(spec_file, tmp_path):
    """A 23.976 fps source converted to 30 fps: each segment has exactly the block's frames (no drift over the reel)."""
    from conftest import ff
    src = tmp_path / "gopro.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=24000/1001:duration=12",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
    cuts = [(0.21, 1.377), (1.64, 4.806), (5.03, 5.9), (6.07, 7.53), (7.81, 8.44), (8.62, 10.95), (11.0, 11.82)]
    s = Spec.load(spec_file({"sources": {"g": str(src)}, "blocks": [{"src": "g", "in": a, "out": b} for a, b in cuts]}))
    r = Renderer(s, tmp_path / "o.mp4", proxy=True)
    count = lambda p: int(subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-count_packets", "-select_streams", "v",
                                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(p)],
                                         capture_output=True, text=True).stdout.strip())
    assert [count(r.render_block(b)) for b in s.blocks] == [b["frames"] for b in s.blocks]


def test_slow_push_moves_every_frame(spec_file, tmp_path):
    """A slow push on a small source (720p, 1× cover) glides: no frame jumps while the others stand still (staircase)."""
    from conftest import ff
    src = tmp_path / "dot.mp4"
    ff("-f", "lavfi", "-i", "color=black:s=1280x720:d=2:r=30,drawbox=x=480:y=60:w=6:h=6:color=white:t=fill",
       "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", src)
    s = Spec.load(spec_file({"sources": {"d": str(src)}, "blocks": [{"src": "d", "in": 0, "out": 2, "zoom": [100, 100.85]}]}))
    out = tmp_path / "push.mp4"
    render(s, out, proxy=False)
    raw = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", str(out), "-vf", "crop=400:400:0:0", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    ys = []
    for k in range(len(raw) // 160000):
        f = raw[k * 160000:(k + 1) * 160000]
        tot = sum(f)
        ys.append(sum(y * sum(f[y * 400:(y + 1) * 400]) for y in range(400)) / tot)
    steps = [abs(b - a) for a, b in zip(ys[1:], ys[2:])]  # skip the first frame (encoder warm-up)
    mean = abs(ys[-1] - ys[1]) / len(steps)
    assert max(steps) < 3 * mean, f"staircase: max step {max(steps):.3f} px for a mean of {mean:.3f} px/frame"


def test_block_key_changes_with_segment_version(media, spec_file, monkeypatch):
    """Segments cached by an older renderer are not reused once the rendering changes."""
    import kanton.render as R
    s = make(media, spec_file)
    r = Renderer(s, s.dir / "o.mp4")
    info = ffmpeg.probe(s.blocks[0]["path"])
    k1 = r.block_key(s.blocks[0], info)
    monkeypatch.setattr(R, "SEGMENT_VERSION", R.SEGMENT_VERSION + 1)
    assert r.block_key(s.blocks[0], info) != k1
