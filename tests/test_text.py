from PIL import Image

from kanton.text import TextStyle, hex_rgb

STYLE = {"font": "Anton-Regular.ttf", "size": 60, "fill": "#FFD400", "stroke": "#000000", "stroke_width": 8, "uppercase": True, "max_width": 780, "y_baseline": 1432, "x": 540}


def test_hex_rgb():
    assert hex_rgb("#FFD400") == (255, 212, 0) and hex_rgb("000000") == (0, 0, 0)


def test_wrap_keeps_short_text_on_one_line():
    st = TextStyle(STYLE)
    assert st.wrap("salut") == ["SALUT"]
    assert st.width("SALUT") < 780


def test_wrap_balances_long_text_under_max_width():
    st = TextStyle(STYLE)
    lines = st.wrap("tout le monde s'en fout de ton allure du dimanche")
    assert 2 <= len(lines) <= 3
    assert all(st.width(l) <= 780 for l in lines)
    assert " ".join(lines) == "TOUT LE MONDE S'EN FOUT DE TON ALLURE DU DIMANCHE"


def test_wrap_respects_given_breaks_when_they_fit():
    st = TextStyle(STYLE)
    assert st.wrap("une ligne\ndeux lignes") == ["UNE LIGNE", "DEUX LIGNES"]


def test_wrap_rebalances_given_breaks_that_overflow():
    st = TextStyle(STYLE)
    lines = st.wrap("un mot\nune très longue deuxième ligne qui dépasse largement la largeur maximale")
    assert all(st.width(l) <= 780 for l in lines)


def test_no_uppercase_option():
    st = TextStyle(dict(STYLE, uppercase=False))
    assert st.wrap("Salut") == ["Salut"]


def test_layout_puts_last_baseline_at_y_baseline():
    st = TextStyle(STYLE)
    asc, _ = st.font.getmetrics()
    lay = st.layout(["A", "B"], 1080, 1920)
    assert lay[1][1] + asc == 1432
    assert lay[0][1] + asc == 1432 - st.line_step
    assert st.line_step > 50


def test_line_step_override_and_leading():
    assert TextStyle(dict(STYLE, line_step=67.5)).line_step == 67.5
    a = TextStyle(STYLE).line_step
    assert TextStyle(dict(STYLE, leading=-3.5)).line_step == a - 3.5


def test_render_png_full_frame_with_bbox(tmp_path):
    st = TextStyle(STYLE)
    meta = st.render("salut", 1080, 1920, tmp_path / "t.png")
    im = Image.open(tmp_path / "t.png")
    assert im.size == (1080, 1920) and im.mode == "RGBA"
    x0, y0, x1, y1 = meta["bbox"]
    assert 150 < x0 < x1 < 930                     # centred, inside the safe zone
    assert 1432 - 80 < y0 < 1432 and y1 <= 1432 + 20   # glyphs above the baseline (Anton has no descenders in caps)
    assert meta["lines"] == ["SALUT"]
    px = im.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))
    assert px[3] == 255


def test_fill_colour_present_in_render(tmp_path):
    st = TextStyle(STYLE)
    st.render("OOO", 1080, 1920, tmp_path / "t.png")
    im = Image.open(tmp_path / "t.png").convert("RGBA")
    yellow = sum(1 for p in im.get_flattened_data() if p[3] > 200 and abs(p[0] - 255) < 10 and abs(p[1] - 212) < 10)
    black = sum(1 for p in im.get_flattened_data() if p[3] > 200 and p[0] < 20 and p[1] < 20)
    assert yellow > 500 and black > 500


def test_missing_font_exits(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        TextStyle(dict(STYLE, font="NoSuchFont-Regular.ttf"))
