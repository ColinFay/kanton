"""Voice effects (voice changer) on clips, music and sounds; stabilisation of a shaky clip."""
import subprocess

import pytest

from kanton import ffmpeg, fx
from kanton.render import Renderer, render
from kanton.spec import Spec

from conftest import ff, rms_db
from test_editor import video_frames


def mk(media, spec_file, **over):
    d = {"sources": {k: str(v) for k, v in media.items()}, "blocks": [{"src": "face", "in": 0, "out": 1}]}
    d.update(over)
    return Spec.load(spec_file(d))


def test_voice_catalog_and_unknown_voice(media, spec_file):
    assert {"chipmunk", "deep", "robot", "echo", "telephone", "megaphone"} <= set(fx.VOICES) and fx.catalog()["voices"] == list(fx.VOICES)
    with pytest.raises(SystemExit, match="unknown voice"):
        mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "voice": "dalek"}])
    with pytest.raises(SystemExit, match="unknown voice"):
        mk(media, spec_file, music={"src": "music", "voice": "dalek"})


def test_every_voice_renders_a_clip_of_the_same_length(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "voice": v} for v in fx.VOICES])
    r = Renderer(s, tmp_path / "o.mp4")
    for b in s.blocks:
        seg = r.render_block(b)
        assert video_frames(seg) == b["frames"] and rms_db(seg, 0.2, 0.8) > -40, b["voice"]


def test_voice_on_music_and_sounds(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 2, "mute": True}], music={"src": "music", "voice": "telephone"}, sounds=[{"src": "music", "t": 1, "dur": 0.5, "voice": "robot"}])
    assert s.music["voice"] == "telephone" and s.sounds[0]["voice"] == "robot"
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert rms_db(out, 0.2, 0.8) < rms_db(render(mk(media, spec_file, blocks=s.data["blocks"], music={"src": "music"}), tmp_path / "p.mp4", proxy=True), 0.2, 0.8) - 6   # the 220 Hz tone is under the telephone band


def test_stabilize_steadies_a_shaky_clip(media, spec_file, tmp_path):
    still = tmp_path / "scene.png"
    ff("-f", "lavfi", "-i", "testsrc2=size=1180x2020:rate=1", "-frames:v", "1", still)
    shaky = tmp_path / "shaky.mp4"
    ff("-loop", "1", "-i", still, "-vf", "crop=1080:1920:x='50+40*sin(n*2.1)':y='50+40*cos(n*1.3)'", "-t", "2", "-r", "30",
       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", shaky)

    def jitter(seg):                                    # mean luma difference between consecutive frames, past the first second
        err = subprocess.run([ffmpeg.binary("ffmpeg"), "-hide_banner", "-ss", "1", "-i", str(seg), "-vf", "tblend=all_mode=difference,crop=iw/2:ih/2,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                              "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
        v = [float(l.split("=")[-1]) for l in err.splitlines() if "YAVG" in l]
        return sum(v) / len(v)
    s = Spec.load(spec_file({"sources": {"s": str(shaky)}, "blocks": [{"src": "s", "in": 0, "out": 2}, {"src": "s", "in": 0, "out": 2, "stabilize": True}]}))
    r = Renderer(s, tmp_path / "o.mp4")
    raw, steady = (r.render_block(b) for b in s.blocks)
    assert video_frames(steady) == s.blocks[1]["frames"]
    assert jitter(steady) < 0.6 * jitter(raw)
