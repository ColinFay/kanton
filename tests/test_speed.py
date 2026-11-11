"""Speed ramps (curves), smooth slow motion, and a push zoom that ends where it says whatever the speed."""
import math

import pytest

from kanton import speed
from kanton.render import Renderer, render
from kanton.spec import Spec

from conftest import probe_frame_mean
from test_editor import audio_seconds, video_frames


def sound_seconds(seg) -> float:                       # decoded length of a segment's sound (its container gives none)
    import subprocess
    from kanton import ffmpeg
    pcm = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", str(seg), "-map", "0:a", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, check=True).stdout
    return len(pcm) / 2 / 48000

RED = lambda c: c[0] > 150 and c[1] < 90 and c[2] < 90
BLUE = lambda c: c[2] > 150 and c[0] < 90 and c[1] < 90


def mk(media, spec_file, blocks):
    return Spec.load(spec_file({"sources": {k: str(v) for k, v in media.items()}, "blocks": blocks}))


def test_curve_points_named_or_written():
    assert speed.points(2) is None and speed.points(None) is None                          # a number is a constant speed
    assert speed.points("hero") == speed.points(speed.CURVES["hero"])
    assert speed.points([[0.5, 4], [0, 1]]) == [(0.0, 1.0), (0.5, 4.0), (1.0, 4.0)]       # sorted, held flat to the end
    for bad in ("nope", [[0, 1]], [[0, 0.01], [1, 1]], [[0, 1], [1.5, 2]]):
        with pytest.raises(SystemExit):
            speed.points(bad)


def test_curve_duration_is_the_integral_of_one_over_speed():
    assert speed.duration([(0, 2), (1, 2)], 4.0) == pytest.approx(2.0)
    assert speed.duration([(0, 1), (1, 3)], 2.0) == pytest.approx(2.0 * math.log(3) / 2)        # ∫0^1 2 dp / (1 + 2p)
    pts = speed.points("bullet")
    s = [speed.reel_offset(pts, 3.0, x / 10 * 3.0) for x in range(11)]
    assert s[0] == 0 and s[-1] == pytest.approx(speed.duration(pts, 3.0)) and all(a < b for a, b in zip(s, s[1:]))


def test_spec_block_with_a_curve_takes_its_duration(media, spec_file):
    s = mk(media, spec_file, [{"src": "face", "in": 0, "out": 3, "speed": [[0, 1], [1, 3]]}, {"src": "face", "in": 0, "out": 1}])
    b = s.blocks[0]
    assert b["curve"] == [(0.0, 1.0), (1.0, 3.0)]
    assert b["frames"] == round(3 * math.log(3) / 2 * 30) and s.blocks[1]["start"] == pytest.approx(b["dur"])
    assert b["speed"] == pytest.approx(3 / b["dur"])                                        # the mean speed, for what only needs it
    assert s.reel_time("face", 3.0) == pytest.approx(b["dur"], abs=0.5 / 30) and s.reel_time("face", 1.5) == pytest.approx(3 * math.log(2) / 2, abs=1e-6)
    assert mk(media, spec_file, [{"src": "face", "in": 0, "out": 3, "speed": 1.5}]).blocks[0]["curve"] is None


def test_curve_renders_its_frames_and_its_sound(media, spec_file, tmp_path):
    s = mk(media, spec_file, [{"src": "face", "in": 0, "out": 3, "speed": "hero"}, {"src": "face", "in": 0, "out": 1, "speed": [[0, 0.5], [1, 2]]}])
    r = Renderer(s, tmp_path / "o.mp4", proxy=True)
    for b in s.blocks:
        seg = r.render_block(b)
        assert video_frames(seg) == b["frames"]
        assert sound_seconds(seg) == pytest.approx(b["dur"], abs=0.03)                    # the sound keeps the picture's length
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert audio_seconds(out) == pytest.approx(s.duration, abs=0.07)


def test_curve_plays_the_source_through_the_ramp(media, spec_file, tmp_path):
    """redblue is red for 1 s then blue: at 4× over the first half the red is gone sooner than at a constant speed."""
    s = mk(media, spec_file, [{"src": "redblue", "in": 0, "out": 2, "speed": [[0, 4], [0.5, 4], [0.5001, 0.5], [1, 0.5]]}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert s.duration == pytest.approx(0.25 + 2.0, abs=0.04)
    assert RED(probe_frame_mean(out, 0.1)) and BLUE(probe_frame_mean(out, 0.5))


def test_push_zoom_ends_on_its_value_at_any_speed(media, spec_file, tmp_path):
    """tall: red top, blue bottom (y ≥ 960); focus y 600. At zoom 200 the last frame shows blue at y 1800; past it, red."""
    for sp in (2, 0.5):
        s = mk(media, spec_file, [{"src": "tall", "in": 0, "out": 2 * min(sp, 1), "speed": sp, "zoom": [100, 200]}])
        out = render(s, tmp_path / f"z{sp}.mp4", proxy=True)
        assert BLUE(probe_frame_mean(out, s.duration - 0.1, (200, 880, 140, 40))), sp


def test_smooth_slow_motion_makes_new_frames(media, spec_file, tmp_path):
    import subprocess
    from kanton import ffmpeg
    def repeats(seg):                                  # frames (of the first 20) that do not move from the one before
        err = subprocess.run([ffmpeg.binary("ffmpeg"), "-hide_banner", "-i", str(seg), "-frames:v", "20", "-vf", "tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                              "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
        return sum(float(l.split("=")[-1]) < 0.1 for l in err.splitlines() if "YAVG" in l)
    plain = mk(media, spec_file, [{"src": "face", "in": 0, "out": 0.5, "speed": 0.5}])
    smooth = mk(media, spec_file, [{"src": "face", "in": 0, "out": 0.5, "speed": 0.5, "smooth": True}])
    a, b = Renderer(plain, tmp_path / "a.mp4").render_block(plain.blocks[0]), Renderer(smooth, tmp_path / "b.mp4").render_block(smooth.blocks[0])
    assert video_frames(b) == smooth.blocks[0]["frames"]
    assert repeats(a) >= 8 and repeats(b) == 0


def test_catalog_lists_the_curves():
    from kanton import fx
    assert set(speed.CURVES) <= set(fx.catalog()["curves"]) and "hero" in speed.CURVES
