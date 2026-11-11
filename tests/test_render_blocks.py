"""Pass 1 per block (stills, freeze, fit, rotate, crop, vflip, reverse, grade, audio), transitions on the cut, timed effects."""
import pytest

from kanton import ffmpeg
from kanton.render import Renderer, render
from kanton.spec import Spec

from conftest import probe_frame_mean, rms_db

RED = lambda c: c[0] > 150 and c[1] < 90 and c[2] < 90
BLUE = lambda c: c[2] > 150 and c[0] < 90 and c[1] < 90
GREEN = lambda c: c[1] > 150 and c[0] < 90 and c[2] < 90
GREY = lambda c: max(c) - min(c) < 25


def mk(media, spec_file, **over):
    d = {"sources": {k: str(v) for k, v in media.items()}, "blocks": [{"src": "face", "in": 0.5, "out": 2.0}]}
    d.update(over)
    return Spec.load(spec_file(d))


def half(x, y, w, h):          # a box in full-frame coordinates on a proxy render
    return (x // 2, y // 2, w // 2, h // 2)


def test_still_image_and_freeze_frame_on_the_main_track(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 0.5}, {"src": "still", "dur": 1, "zoom": [100, 108]}, {"src": "redblue", "at": 0.9, "dur": 1}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 2.5) < 0.07
    assert GREEN(probe_frame_mean(out, 1.0, half(300, 800, 400, 300)))              # the still fills the frame (cover) for its second
    a, b = probe_frame_mean(out, 1.6), probe_frame_mean(out, 2.4)                    # the frozen frame does not change
    assert RED(a) and RED(b) and max(abs(x - y) for x, y in zip(a, b)) < 6


def test_fit_contain_with_colour_or_blurred_background(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "wide", "in": 0, "out": 0.5, "fit": "contain", "background": "#00FF00"}, {"src": "wide", "in": 0, "out": 0.5, "fit": "contain", "background": "blur"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    from test_editor import video_frames
    r = Renderer(s, tmp_path / "o.mp4", proxy=True)
    assert [video_frames(r.render_block(b)) for b in s.blocks] == [b["frames"] for b in s.blocks]       # a colour background never costs a frame
    assert GREEN(probe_frame_mean(out, 0.25, half(100, 100, 880, 400)))             # above the letterboxed picture: the colour
    assert RED(probe_frame_mean(out, 0.25, half(100, 800, 300, 300))) and BLUE(probe_frame_mean(out, 0.25, half(700, 800, 300, 300)))
    top = probe_frame_mean(out, 0.75, half(100, 100, 300, 300))                     # blurred copy of the picture: reddish on the left, never black
    assert top[0] > 90 and sum(top) > 150
    assert RED(probe_frame_mean(out, 0.75, half(100, 800, 300, 300)))


def test_rotate_crop_and_vflip(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "wide", "in": 0, "out": 0.4, "rotate": 90}, {"src": "wide", "in": 0, "out": 0.4, "crop": [0, 0, 640, 720]},
                                     {"src": "tall", "in": 0, "out": 0.4, "vflip": True}, {"src": "wide", "in": 0, "out": 0.4, "rotate": 45, "fit": "contain", "background": "#00FF00"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert RED(probe_frame_mean(out, 0.2, half(300, 100, 400, 400))) and BLUE(probe_frame_mean(out, 0.2, half(300, 1400, 400, 400)))   # 90° clockwise: left → top
    assert RED(probe_frame_mean(out, 0.6, half(100, 800, 300, 300))) and RED(probe_frame_mean(out, 0.6, half(700, 800, 300, 300)))     # left half only
    assert BLUE(probe_frame_mean(out, 1.0, half(300, 100, 400, 400))) and RED(probe_frame_mean(out, 1.0, half(300, 1400, 400, 400)))   # upside down
    assert GREEN(probe_frame_mean(out, 1.4, half(40, 40, 120, 120)))                                                                   # 45°: a corner shows the background


def test_reverse_plays_the_block_backwards(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 2, "reverse": True}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert BLUE(probe_frame_mean(out, 0.2)) and RED(probe_frame_mean(out, 1.8))
    assert abs(ffmpeg.probe(str(out))["duration"] - 2.0) < 0.07 and ffmpeg.probe(str(out))["has_audio"]


def test_block_grade_and_timed_effects(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "red", "in": 0, "out": 0.5, "grade": {"saturation": 0}}, {"src": "red", "in": 0, "out": 0.5, "effects": [{"type": "negative"}]},
                                     {"src": "red", "in": 0, "out": 0.5}], effects=[{"type": "pixelate", "t": 1.0, "dur": 0.2}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert GREY(probe_frame_mean(out, 0.25))
    c = probe_frame_mean(out, 0.75)
    assert c[0] < 90 and c[1] > 150 and c[2] > 150                                  # negative of red: cyan, for the block only
    assert RED(probe_frame_mean(out, 1.4))


def test_block_audio_volume_fades_denoise_pitch(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 1}, {"src": "redblue", "in": 0, "out": 1, "volume": -30},
                                     {"src": "redblue", "in": 0, "out": 1, "fade_in": 0.4, "fade_out": 0.4, "denoise": True, "pitch": 3}])
    r = Renderer(s, tmp_path / "o.mp4", proxy=True)
    segs = [r.render_block(b) for b in s.blocks]
    assert rms_db(segs[0], 0.2, 0.8) - rms_db(segs[1], 0.2, 0.8) == pytest.approx(30, abs=1.5)
    assert rms_db(segs[2], 0.0, 0.1) < rms_db(segs[2], 0.45, 0.55) - 10                # fade in
    assert all(abs(ffmpeg.probe(str(x))["duration"] - 1.0) < 0.05 for x in segs)


def test_xfade_transitions_keep_the_timeline_and_mix_the_two_sides(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "red", "in": 0, "out": 1}, {"src": "broll", "in": 0, "out": 1, "cut": "dissolve", "cut_dur": 0.4},
                                     {"src": "red", "in": 0.5, "out": 1.5, "cut": {"type": "wipeleft", "dur": 0.4}}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 3.0) < 0.07 and s.duration == pytest.approx(3.0)
    assert RED(probe_frame_mean(out, 0.7)) and BLUE(probe_frame_mean(out, 1.3))      # untouched outside the window
    r, g, b = probe_frame_mean(out, 1.0)                                              # mid-dissolve: both
    assert 60 < r < 200 and 60 < b < 200
    left, right = probe_frame_mean(out, 2.0, half(0, 0, 500, 1920)), probe_frame_mean(out, 2.0, half(580, 0, 500, 1920))
    assert {RED(left) or BLUE(left), RED(right) or BLUE(right)} == {True} and RED(left) != RED(right)   # mid-wipe: one side each
    cmd = (s.dir / ".kanton-tmp" / "montage" / "final.cmd").read_text()
    assert "xfade=" not in cmd and cmd.count("tr-") == 2                              # the transitions are patches rendered apart, overlaid on the cut


def test_transition_with_a_push_zoom_and_speed_renders(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": [100, 110], "speed": 2}, {"src": "broll", "in": 0.2, "out": 1, "cut": "circleopen"},
                                     {"src": "still", "dur": 0.5, "cut": "fade"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 1.8) < 0.07


def test_first_block_transition_is_ignored_and_patch_cached(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "red", "in": 0, "out": 0.5, "cut": "dissolve"}, {"src": "broll", "in": 0, "out": 0.5, "cut": "dissolve"}])
    r = Renderer(s, tmp_path / "o.mp4", proxy=True)
    p = r.render_transition(s.blocks[0], s.blocks[1])
    m = p.stat().st_mtime_ns
    assert r.render_transition(s.blocks[0], s.blocks[1]) == p and p.stat().st_mtime_ns == m
    assert abs(ffmpeg.probe(str(p))["duration"] - 0.3) < 0.04


def test_cut_effects_still_work(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 0.5}, {"src": "face", "in": 1, "out": 1.5, "cut": "blur"}, {"src": "face", "in": 2, "out": 2.5, "cut": "flash"},
                                     {"src": "face", "in": 3, "out": 3.5, "cut": "punch"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert abs(ffmpeg.probe(str(out))["duration"] - 2.0) < 0.07
