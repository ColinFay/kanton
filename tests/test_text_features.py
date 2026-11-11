"""Text layers beyond fill and stroke: shadow, box, opacity, rotation, the word being said (karaoke), typewriter states."""
from PIL import Image

from kanton.text import TextStyle, states

STYLE = {"font": "Anton-Regular.ttf", "size": 60, "fill": "#FFFFFF", "stroke": "#000000", "stroke_width": 6, "uppercase": True, "max_width": 780, "y_baseline": 1000, "x": 540}


def render(tmp_path, name, text="salut", **extra):
    st = TextStyle(STYLE)
    meta = st.render(text, 1080, 1920, tmp_path / f"{name}.png", **extra)
    return Image.open(tmp_path / f"{name}.png").convert("RGBA"), meta


def test_shadow_extends_the_box_down_and_right(tmp_path):
    plain, mp = render(tmp_path, "plain", text="OOO")
    shadowed, ms = render(tmp_path, "shadow", text="OOO", shadow={"color": "#000000", "dx": 10, "dy": 10, "blur": 4, "opacity": 0.6})
    assert ms["bbox"][2] >= mp["bbox"][2] + 8 and ms["bbox"][3] >= mp["bbox"][3] + 8 and ms["bbox"][0] <= mp["bbox"][0] + 2
    x, y = mp["bbox"][2] + 6, (mp["bbox"][1] + mp["bbox"][3]) // 2    # just right of the last O, mid-height: the shadow, translucent
    assert 20 < shadowed.getpixel((x, y))[3] < 200 and plain.getpixel((x, y))[3] == 0


def test_box_behind_the_text(tmp_path):
    im, meta = render(tmp_path, "box", box={"color": "#0000FF", "opacity": 0.5, "padding": 20, "radius": 10})
    x0, y0, x1, y1 = meta["text_bbox"]
    px = im.getpixel((x0 - 10, (y0 + y1) // 2))                     # inside the padding, outside the glyphs
    assert px[2] > 200 and px[0] < 30 and abs(px[3] - 128) < 8
    assert im.getpixel((x0 - 19, y0 - 19))[3] == 0                   # the rounded corner is cut
    assert meta["bbox"][0] <= x0 - 18 and meta["bbox"][2] >= x1 + 18


def test_opacity_and_rotation(tmp_path):
    im, _ = render(tmp_path, "half", opacity=0.5)
    assert max(p[3] for p in im.get_flattened_data()) <= 130
    _, flat = render(tmp_path, "flat", text="une ligne assez longue")
    _, tilt = render(tmp_path, "tilt", text="une ligne assez longue", rotation=12)
    assert tilt["bbox"][3] - tilt["bbox"][1] > (flat["bbox"][3] - flat["bbox"][1]) + 60     # the line now spans more height
    cx = lambda b: (b[0] + b[2]) / 2
    assert abs(cx(tilt["bbox"]) - cx(flat["bbox"])) < 12                                   # turned about its centre


def lime_and_white(im, x0, x1):
    lime = white = 0
    for y in range(900, 1020):
        for x in range(x0, x1, 2):
            r, g, b, a = im.getpixel((x, y))
            if a > 200 and g > 180 and r < 80:
                lime += 1
            elif a > 200 and r > 200 and g > 200 and b > 200:
                white += 1
    return lime, white


def test_highlight_states_follow_the_words(tmp_path):
    words = [{"word": "gauche", "t": 2.0, "dur": 0.5}, {"word": "droite", "t": 2.5, "dur": 0.4}]
    st = states(TextStyle(STYLE), "gauche droite", 1080, 1920, tmp_path / "k", t0=1.9, dur=1.2, words=words, highlight={"fill": "#00FF00"})
    assert [(round(s["t"], 2), round(s["dur"], 2)) for s in st] == [(1.9, 0.1), (2.0, 0.5), (2.5, 0.6)]      # nothing lit, first word, second word until the end
    im1, im2 = Image.open(st[1]["png"]).convert("RGBA"), Image.open(st[2]["png"]).convert("RGBA")
    l1, r1 = lime_and_white(im1, 150, 540), lime_and_white(im1, 540, 930)
    assert l1[0] > 200 and l1[1] < 30 and r1[1] > 200 and r1[0] < 30
    l2, r2 = lime_and_white(im2, 150, 540), lime_and_white(im2, 540, 930)
    assert r2[0] > 200 and r2[1] < 30 and l2[1] > 200 and l2[0] < 30
    assert lime_and_white(Image.open(st[0]["png"]).convert("RGBA"), 150, 930)[0] < 30


def test_reveal_states_by_word_and_by_char(tmp_path):
    words = [{"word": "gauche", "t": 2.0, "dur": 0.5}, {"word": "droite", "t": 2.5, "dur": 0.4}]
    st = states(TextStyle(STYLE), "gauche droite", 1080, 1920, tmp_path / "w", t0=2.0, dur=1.0, words=words, reveal="word")
    assert [round(s["t"], 2) for s in st] == [2.0, 2.5]
    im = Image.open(st[0]["png"]).convert("RGBA")
    assert lime_and_white(im, 150, 540)[1] > 200 and lime_and_white(im, 560, 930)[1] < 30     # "gauche" alone, in place (the line is laid out whole)
    st = states(TextStyle(STYLE), "ABCDEFGH", 1080, 1920, tmp_path / "c", t0=0.0, dur=0.8, reveal="char")
    assert len(st) == 8 and st[0]["t"] == 0.0 and abs(st[-1]["t"] - 0.7) < 1e-6 and abs(sum(s["dur"] for s in st) - 0.8) < 1e-6
    a, b = Image.open(st[0]["png"]).convert("RGBA").getbbox(), Image.open(st[-1]["png"]).convert("RGBA").getbbox()
    assert b[2] - b[0] > 3 * (a[2] - a[0])


def test_highlight_scale_pops_the_word(tmp_path):
    words = [{"word": "un", "t": 0.0, "dur": 0.5}, {"word": "deux", "t": 0.5, "dur": 0.5}]
    big = states(TextStyle(STYLE), "un deux", 1080, 1920, tmp_path / "b", t0=0, dur=1, words=words, highlight={"fill": "#00FF00", "scale": 1.3})
    flat = states(TextStyle(STYLE), "un deux", 1080, 1920, tmp_path / "f", t0=0, dur=1, words=words, highlight={"fill": "#00FF00"})
    hb, hf = (Image.open(s[1]["png"]).convert("RGBA").getbbox() for s in (big, flat))
    assert hb[3] - hb[1] > hf[3] - hf[1] + 8
