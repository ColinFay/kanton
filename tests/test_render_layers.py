"""Pass 3: picture-in-picture, masks, blends, keyframes and animations, karaoke and typewriter texts, sounds, ducking, export formats."""
import pytest
from PIL import Image

from kanton import ffmpeg
from kanton.render import render
from kanton.spec import Spec

from conftest import probe_frame_count, probe_frame_mean, rms_db

RED = lambda c: c[0] > 150 and c[1] < 90 and c[2] < 90
BLUE = lambda c: c[2] > 150 and c[0] < 90 and c[1] < 90
LIME = lambda c: c[1] > 150 and c[0] < 90 and c[2] < 90
GREY = lambda c: max(c) - min(c) < 25


def mk(media, spec_file, **over):
    d = {"sources": {k: str(v) for k, v in media.items()}, "blocks": [{"src": "red", "in": 0, "out": 2}]}
    d.update(over)
    return Spec.load(spec_file(d))


def half(x, y, w, h):
    return (x // 2, y // 2, w // 2, h // 2)


def lime_box(out, t):
    """Bounding box of the lime pixels of a proxy frame, in full-frame coordinates."""
    import io, subprocess
    png = subprocess.run([ffmpeg.binary("ffmpeg"), "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    im = Image.open(io.BytesIO(png)).convert("RGB")
    xs, ys = [], []
    for y in range(0, im.height, 2):
        for x in range(0, im.width, 2):
            if LIME(im.getpixel((x, y))):
                xs.append(x); ys.append(y)
    return (min(xs) * 2, min(ys) * 2, max(xs) * 2, max(ys) * 2) if xs else None


def test_picture_in_picture_video_with_a_box(media, spec_file, tmp_path):
    s = mk(media, spec_file, overlays=[{"kind": "video", "src": "broll", "t": 0.5, "dur": 1, "x": 100, "y": 300, "width": 400, "height": 300}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert BLUE(probe_frame_mean(out, 1.0, half(120, 320, 360, 260)))
    assert RED(probe_frame_mean(out, 1.0, half(100, 620, 400, 200))) and RED(probe_frame_mean(out, 1.0, half(520, 300, 400, 300)))   # the box ends where asked
    assert RED(probe_frame_mean(out, 0.2, half(120, 320, 360, 260)))


def test_masks_cut_the_overlay(media, spec_file, tmp_path):
    s = mk(media, spec_file, overlays=[{"kind": "image", "src": "still", "t": 0, "dur": 1, "x": 100, "y": 300, "width": 400, "mask": {"shape": "circle", "feather": 4}},
                                       {"kind": "image", "src": "still", "t": 1, "dur": 1, "x": 100, "y": 300, "width": 400, "mask": {"shape": "rounded", "radius": 80}},
                                       {"kind": "image", "src": "still", "t": 0, "dur": 1, "x": 100, "y": 900, "width": 400, "mask": {"shape": "linear", "feather": 100, "angle": 0}},
                                       {"kind": "image", "src": "still", "t": 1, "dur": 1, "x": 100, "y": 900, "width": 400, "mask": {"shape": "rect", "rect": [0, 0, 200, 300], "invert": True}}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert LIME(probe_frame_mean(out, 0.5, half(260, 410, 80, 80))) and RED(probe_frame_mean(out, 0.5, half(102, 302, 30, 30)))       # circle: centre in, corner out
    assert LIME(probe_frame_mean(out, 1.5, half(260, 410, 80, 80))) and RED(probe_frame_mean(out, 1.5, half(102, 302, 12, 12)))       # rounded: the corner is gone
    assert LIME(probe_frame_mean(out, 0.5, half(110, 1100, 60, 60))) and RED(probe_frame_mean(out, 0.5, half(440, 1100, 50, 60)))     # linear: fades out to the right
    assert RED(probe_frame_mean(out, 1.5, half(110, 910, 160, 260))) and LIME(probe_frame_mean(out, 1.5, half(320, 910, 160, 260)))   # inverted rect: the left half is cut


def test_blend_modes(media, spec_file, tmp_path):
    s = mk(media, spec_file, overlays=[{"kind": "image", "src": "black", "t": 0, "dur": 1, "x": 100, "y": 300, "blend": "screen"},
                                       {"kind": "image", "src": "white", "t": 0, "dur": 1, "x": 600, "y": 300, "blend": "screen"},
                                       {"kind": "image", "src": "white", "t": 1, "dur": 1, "x": 100, "y": 300, "blend": "multiply"},
                                       {"kind": "image", "src": "black", "t": 1, "dur": 1, "x": 600, "y": 300, "blend": "multiply"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert RED(probe_frame_mean(out, 0.5, half(120, 320, 260, 260)))                       # screen with black: nothing
    assert min(probe_frame_mean(out, 0.5, half(620, 320, 260, 260))) > 200                 # screen with white: white
    assert RED(probe_frame_mean(out, 1.5, half(120, 320, 260, 260)))                       # multiply with white: nothing
    assert max(probe_frame_mean(out, 1.5, half(620, 320, 260, 260))) < 40                  # multiply with black: black


def test_keyframes_move_scale_rotate_and_fade(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "red", "in": 0, "out": 2}, {"src": "red", "in": 0, "out": 2}], overlays=[
        {"kind": "image", "src": "still", "t": 0, "dur": 1, "x": 100, "y": 300, "width": 400, "keyframes": [{"t": 0, "x": 100}, {"t": 1, "x": 600}]},
        {"kind": "image", "src": "still", "t": 1, "dur": 1, "x": 100, "y": 300, "width": 400, "keyframes": [{"t": 0, "scale": 1}, {"t": 1, "scale": 0.5}]},
        {"kind": "image", "src": "still", "t": 2, "dur": 1, "x": 300, "y": 1000, "width": 400, "keyframes": [{"t": 0, "rotation": 0}, {"t": 1, "rotation": 90}]},
        {"kind": "image", "src": "still", "t": 3, "dur": 1, "x": 300, "y": 1000, "width": 400, "keyframes": [{"t": 0, "opacity": 1}, {"t": 1, "opacity": 0}]}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    b0, b1 = lime_box(out, 0.1), lime_box(out, 0.9)
    assert b0 and b1 and abs(b0[0] - 150) < 40 and abs(b1[0] - 550) < 40 and abs((b1[2] - b1[0]) - (b0[2] - b0[0])) < 20     # slides right, same size
    b2, b3 = lime_box(out, 1.1), lime_box(out, 1.9)
    assert b2 and b3 and abs((b2[2] - b2[0]) - 380) < 40 and abs((b3[2] - b3[0]) - 220) < 40                               # halves, around its centre
    assert abs((b3[0] + b3[2]) / 2 - (b2[0] + b2[2]) / 2) < 30
    b4, b5 = lime_box(out, 2.1), lime_box(out, 2.9)
    assert b4 and b5 and (b4[2] - b4[0]) > (b4[3] - b4[1]) + 50 and (b5[3] - b5[1]) > (b5[2] - b5[0]) + 50                  # wide, then tall (turned)
    c = probe_frame_mean(out, 3.5, half(380, 1080, 240, 140))                                                              # half faded: lime over red
    assert 60 < c[0] < 200 and 60 < c[1] < 200


def test_animations_in_out_loop_and_static_transform(media, spec_file, tmp_path):
    greenish = lambda c: c[1] > c[0] + 20 and c[2] < 90
    s = mk(media, spec_file, overlays=[
        {"kind": "image", "src": "still", "t": 0, "dur": 1, "x": 100, "y": 300, "width": 400, "anim_in": "slide_up", "anim_out": {"type": "fade", "dur": 0.3}},
        {"kind": "image", "src": "still", "t": 1, "dur": 1, "x": 100, "y": 300, "width": 400, "anim_in": "pop", "anim_loop": "pulse", "rotation": 15, "scale": 0.8},
        {"kind": "sticker", "src": "sticker", "t": 0, "dur": 2, "loop": True, "anim_loop": {"type": "shake", "amount": 0.5}}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    b = lime_box(out, 0.6)
    assert b and abs(b[0] - 100) < 20 and abs(b[1] - 300) < 20                              # at rest between in and out
    assert probe_frame_count(out, 0.2, (50, 165, 200, 20), greenish) > 500                   # still sliding up: the box top is lower than 300
    assert probe_frame_count(out, 0.2, (50, 150, 200, 10), greenish) < 50
    assert probe_frame_count(out, 1.5, (0, 0, 540, 960), LIME) > 2000
    assert RED(probe_frame_mean(out, 1.8, half(120, 1520, 260, 260)))                       # the looped sticker (1 s long) is still there at 1.8 s


def test_overlay_grade_and_audio(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "red", "in": 0, "out": 2}],
           overlays=[{"kind": "video", "src": "broll", "t": 0, "dur": 1, "x": 100, "y": 300, "width": 400, "grade": "bw"},
                     {"kind": "video", "src": "redblue", "t": 1, "dur": 1, "x": 100, "y": 300, "width": 400, "volume": 0}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert GREY(probe_frame_mean(out, 0.5, half(120, 320, 360, 200)))
    assert rms_db(out, 1.2, 1.8) > rms_db(out, 0.2, 0.8) + 20                               # the main track is silent: the overlay's sound plays at its time


def test_karaoke_and_typewriter_texts(media, spec_file, tmp_path):
    words = [{"word": "gauche", "t": 0.0, "dur": 0.5}, {"word": "droite", "t": 0.5, "dur": 0.5}]
    s = mk(media, spec_file, texts=[{"text": "gauche droite", "t": 0, "dur": 1, "y": 1000, "fill": "#FFFFFF", "words": words, "highlight": {"fill": "#00FF00"}},
                                    {"text": "gauche droite", "t": 1, "dur": 1, "y": 1000, "fill": "#00FF00", "words": [{**w, "t": w["t"] + 1} for w in words], "reveal": "word"},
                                    {"text": "ABCDEFGH", "t": 0, "dur": 1, "y": 600, "fill": "#00FF00", "reveal": "char"}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    band = lambda t, side: probe_frame_count(out, t, (0 if side == "l" else 270, 440, 270, 80), LIME)
    assert band(0.25, "l") > 300 and band(0.25, "r") < 30                                   # first word highlighted
    assert band(0.75, "r") > 300 and band(0.75, "l") < 30                                   # then the second
    assert band(1.25, "l") > 300 and band(1.25, "r") < 30                                   # typewriter by words: the second not yet shown
    assert band(1.75, "r") > 300
    top = lambda t: probe_frame_count(out, t, (0, 240, 540, 80), LIME)
    assert 0 < top(0.3) < top(0.95) * 0.6                                                   # by characters: it grows


def test_text_shadow_box_animation_and_keyframes_render(media, spec_file, tmp_path):
    s = mk(media, spec_file, texts=[{"text": "salut", "t": 0, "dur": 1, "shadow": True, "box": {"color": "#0000FF", "opacity": 1}, "anim_in": "pop", "anim_out": "fade"},
                                    {"text": "haut", "t": 1, "dur": 1, "y": 1000, "rotation": 10, "opacity": 0.7, "keyframes": [{"t": 0, "y": 1200}, {"t": 0.5, "y": 1000}]}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    from kanton.text import TextStyle
    left = 540 - TextStyle(s.text_style(s.texts[0])).width("SALUT") / 2
    assert BLUE(probe_frame_mean(out, 0.5, half(int(left) - 16, 1390, 10, 20)))              # the box's padding left of the text, at full strength after the pop
    assert abs(ffmpeg.probe(str(out))["duration"] - 2.0) < 0.07


def test_sounds_music_loop_and_ducking(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 1, "mute": True}, {"src": "redblue", "in": 0, "out": 1, "mute": True}, {"src": "redblue", "in": 0, "out": 1}],
           music={"src": "music1", "gain_db": -6, "loop": True, "duck": True}, sounds=[{"src": "music", "t": 0.2, "dur": 0.3, "gain_db": 0}])
    out = render(s, tmp_path / "o.mp4", proxy=True)
    assert rms_db(out, 1.3, 1.9) > -33                                                       # the 1 s music still plays in the second second
    assert rms_db(out, 0.2, 0.5) > rms_db(out, 0.6, 0.9) + 3                                 # the sound effect on top of the music
    cmd = (s.dir / ".kanton-tmp" / "montage" / "final.cmd").read_text()
    assert "sidechaincompress" in cmd and "-stream_loop" in cmd
    assert abs(ffmpeg.probe(str(out))["duration"] - 3.0) < 0.07


def test_export_formats_and_cover(media, spec_file, tmp_path):
    s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 0.6}], export={"cover": 0.3})
    out = render(s, tmp_path / "o.mp4")
    cover = tmp_path / "o.png"
    assert cover.exists() and Image.open(cover).size == (1080, 1920) and RED(Image.open(cover).convert("RGB").getpixel((500, 900)))
    for fmt, codec in (("gif", "gif"), ("webm", "vp9"), ("mov", "prores")):
        s = mk(media, spec_file, blocks=[{"src": "redblue", "in": 0, "out": 0.6}], export={"format": fmt})
        out = render(s, tmp_path / f"o.{fmt}")
        i = ffmpeg.probe(str(out))
        assert out.suffix == f".{fmt}" and i["codec"] == codec, (fmt, i["codec"])
        assert abs(i["duration"] - 0.6) < 0.15
