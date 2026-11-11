"""Import a FilmCraft project (.fcproj JSON) into a kanton spec."""
import json

import pytest

from kanton import fcproj

T = 254016000000


def tk(s):
    return int(round(s * T))


def val(x):
    return {"value": {"Float": float(x)}}


def vec(x, y):
    return {"value": {"Vec2": {"x": float(x), "y": float(y)}}}


def clip(cid, item, name, start, dur, sin, effects, speed=1.0, gain=0.0):
    return {"id": cid, "item": item, "name": name, "start": tk(start), "duration": tk(dur), "source_in": tk(sin), "speed": speed,
            "reverse": False, "enabled": True, "effects": effects, "gain_db": gain}


def motion(scale, x=540.0, y=960.0, kf=None):
    sc = val(scale)
    if kf:
        sc["keyframes"] = [{"time": tk(t), "value": {"Float": float(v)}} for t, v in kf]
    return {"effect": "motion", "params": {"scale": sc, "position": vec(x, y), "anchor": vec(1080, 1920), "rotation": val(0)}}


def media(path, w, h, alpha=False, kind="Movie", audio=True):
    return {"kind": {"Media": {"media": {"File": {"path": path}}, "info": {"name": path.split("/")[-1], "kind": kind, "duration": tk(60),
            "video": {"width": w, "height": h, "frame_rate": {"num": 30, "den": 1}, "has_alpha": alpha, "color": {"transfer": "Bt709"}},
            "audio_streams": [{"sample_rate": 48000}] if audio else []}}}}


@pytest.fixture
def project():
    items = {
        "1": media("/rush/GX014923.MP4", 2160, 3840),
        "2": media("/rush/broll.mp4", 1080, 1920),
        "3": media("/rush/sticker.mov", 1080, 1920, alpha=True),
        "4": {"kind": {"Graphic": {"width": 1080, "height": 1920, "rate": {"num": 30, "den": 1}}}},
        "5": {"kind": {"AdjustmentLayer": {}}},
        "6": media("/rush/logo.png", 400, 300, kind="Still", audio=False),
        "7": {"kind": {"Media": {"media": {"File": {"path": "/music/break.mp4"}}, "info": {"name": "break.mp4", "kind": "Movie", "duration": tk(120), "video": None, "audio_streams": [{}]}}}},
    }
    v1 = [clip(11, 1, "GX014923.MP4", 0.0, 1.5, 10.0, [motion(50, x=558)]),
          clip(12, 1, "GX014923.MP4", 1.5, 2.0, 12.0, [motion(60, kf=[(1.5, 60.0), (3.5 - 1 / 30, 68.889)])]),
          clip(13, 1, "GX014923.MP4", 3.5, 1.0, 20.0, [motion(50), {"effect": "horizontal_flip", "params": {}}], speed=2.0)]
    a1 = [clip(21, 1, "GX014923.MP4", 0.0, 1.5, 10.0, []), clip(22, 1, "GX014923.MP4", 1.5, 2.0, 12.0, []), clip(23, 1, "GX014923.MP4", 3.5, 1.0, 20.0, [])]
    graphic = clip(31, 4, "TOUT LE MONDE", 0.2, 1.0, 0.0, [motion(100), {"effect": "graphic_text", "params": {
        "text": {"value": {"Text": "TOUT LE MONDE\nS’EN FOUT"}}, "position": vec(540, 1364.5), "size": val(59), "font": {"value": {"Text": "MyBrand"}},
        "fill_color": {"value": {"Color": [1.0, 0.83137255, 0.0, 1.0]}}, "stroke_width": val(8), "leading": val(-3.5)}}])
    seq = {"settings": {"width": 1080, "height": 1920, "frame_rate": {"num": 30, "den": 1}},
           "video_tracks": [
               {"name": "Video 1", "items": v1, "transitions": [{"effect": {"effect": "zoom_blur", "params": {}}, "start": tk(1.5) - tk(0.1), "duration": tk(0.2), "align": "CenterAtCut"}]},
               {"name": "Video 2", "items": [clip(41, 2, "broll.mp4", 2.0, 1.0, 5.0, [motion(100)]), clip(42, 6, "logo.png", 0.5, 0.8, 0.0, [motion(50, x=300, y=700)])], "transitions": []},
               {"name": "Video 3", "items": [clip(51, 5, "Adjustment Layer", 0.0, 4.5, 0.0, [{"effect": "lumetri", "params": {"contrast": val(12), "saturation": val(80), "temperature": val(-10), "vignette_amount": val(-1.2)}}])], "transitions": []},
               {"name": "Video 4", "items": [graphic], "transitions": []},
               {"name": "Video 5", "items": [clip(61, 3, "sticker.mov", 3.0, 0.5, 0.0, [motion(100)])], "transitions": []}],
           "audio_tracks": [{"name": "Audio 1", "items": a1, "transitions": []},
                            {"name": "Audio 2", "items": [clip(71, 7, "break.mp4", 0.0, 4.5, 10.0, [], gain=-31.5)], "transitions": [{"effect": {"effect": "exponential_fade"}, "start": tk(4.5 - 20 / 30), "duration": tk(20 / 30), "align": "EndAtCut"}]}],
           "caption_tracks": [{"name": "subs", "style": {"font": "Anton", "size": 34.0}, "captions": [{"start": tk(0.5), "duration": tk(1.2), "text": "ET PEU IMPORTE\nQUE TU GAGNES"}]}]}
    items["10"] = {"kind": {"Sequence": seq}}
    return {"format": "fcproj", "schema_version": 15, "generator": "FilmCraft 0.7.0", "project": {"name": "test", "items": items}}


def test_blocks_zoom_offset_flip_speed_cut(project):
    s = fcproj.convert(project)
    b = s["blocks"]
    assert len(b) == 3
    assert b[0]["src"] == "GX014923" and s["sources"]["GX014923"] == "/rush/GX014923.MP4"
    assert b[0]["in"] == pytest.approx(10.0) and b[0]["out"] == pytest.approx(11.5)
    assert b[0]["zoom"] == 100 and b[0]["dx"] == 18 and "flip" not in b[0]
    assert b[1]["zoom"] == [pytest.approx(120), pytest.approx(137.8, abs=0.1)] and b[1]["cut"] == "blur"
    assert b[2]["flip"] is True and b[2]["speed"] == 2.0 and b[2]["in"] == pytest.approx(20.0) and b[2]["out"] == pytest.approx(22.0)


def test_overlays(project):
    s = fcproj.convert(project)
    o = {x["kind"]: x for x in s["overlays"]}
    assert o["video"]["src"] == "broll" and o["video"]["t"] == pytest.approx(2.0) and o["video"]["dur"] == pytest.approx(1.0) and o["video"]["in"] == pytest.approx(5.0) and o["video"]["zoom"] == 100
    assert o["sticker"]["src"] == "sticker" and o["sticker"]["t"] == pytest.approx(3.0)
    assert o["image"]["width"] == 200 and o["image"]["x"] == pytest.approx(200) and o["image"]["y"] == pytest.approx(625)


def test_texts_graphics_and_captions(project):
    s = fcproj.convert(project)
    texts = s["texts"]
    g = [t for t in texts if t["text"].startswith("TOUT")][0]
    assert g["text"] == "TOUT LE MONDE\nS’EN FOUT" and g["t"] == pytest.approx(0.2) and g["dur"] == pytest.approx(1.0)
    assert g["size"] == 59 and g["fill"].upper() == "#FFD400" and g["stroke_width"] == 8 and "y" not in g     # last baseline at 1432 = default
    c = [t for t in texts if t["text"].startswith("ET PEU")][0]
    assert c["role"] == "caption" and c["t"] == pytest.approx(0.5) and c["dur"] == pytest.approx(1.2)


def test_text_y_kept_when_not_default(project):
    project["project"]["items"]["10"]["kind"]["Sequence"]["video_tracks"][3]["items"][0]["effects"][1]["params"]["position"] = vec(540, 700)
    g = [t for t in fcproj.convert(project)["texts"] if t["text"].startswith("TOUT")][0]
    assert g["y"] == pytest.approx(767.5) and g["role"] == "title"


def test_music_and_grade(project):
    s = fcproj.convert(project)
    m = s["music"]
    assert m["src"] == "break" and m["gain_db"] == -31.5 and m["in"] == pytest.approx(10.0) and m["fade_out"] == pytest.approx(20 / 30)
    g = s["grade"]
    assert g["contrast"] == pytest.approx(1.12) and g["saturation"] == pytest.approx(0.8) and g["temperature"] == 6750 and g["vignette"] == pytest.approx(0.24)


def test_convert_file_writes_spec(project, tmp_path):
    p = tmp_path / "x.fcproj"
    p.write_text(json.dumps(project))
    out = fcproj.convert_file(p, tmp_path / "montage.json", preset="mybrand")
    d = json.loads(out.read_text())
    assert d["preset"] == "mybrand" and len(d["blocks"]) == 3 and d["output"] == {"width": 1080, "height": 1920, "fps": 30}


def test_import_centres_the_zoom_on_the_frame(project):
    """FilmCraft positions already encode where the zoom looks: kanton must not re-centre on the preset focus."""
    assert fcproj.convert(project)["zoom"] == {"focus": [540, 960]}


def test_graphic_at_caption_baseline_is_a_caption(project):
    s = fcproj.convert(project)
    roles = {t["text"][:5]: t["role"] for t in s["texts"]}
    assert roles["TOUT "] == "caption"          # 2 lines ending on the default baseline, centred: a subtitle layer
    project["project"]["items"]["10"]["kind"]["Sequence"]["video_tracks"][3]["items"][0]["effects"][1]["params"]["position"] = vec(540, 700)
    assert [t["role"] for t in fcproj.convert(project)["texts"] if t["text"].startswith("TOUT")] == ["title"]


def test_import_merges_text_fragments(project):
    """A layer split in contiguous pieces by cuts in FilmCraft comes back as one text."""
    seq = project["project"]["items"]["10"]["kind"]["Sequence"]
    g = seq["video_tracks"][3]["items"][0]
    pieces = []
    for k, (st, du) in enumerate([(0.2, 0.4), (0.6, 0.0333333), (0.6333333, 0.5666667)]):
        c = json.loads(json.dumps(g)); c["id"] = 300 + k; c["start"] = tk(st); c["duration"] = tk(du); pieces.append(c)
    seq["video_tracks"][3]["items"] = pieces
    texts = [t for t in fcproj.convert(project)["texts"] if t["text"].startswith("TOUT")]
    assert len(texts) == 1 and texts[0]["t"] == pytest.approx(0.2) and texts[0]["dur"] == pytest.approx(1.0, abs=1e-3)
