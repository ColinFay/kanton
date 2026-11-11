import json
from pathlib import Path

import pytest

from kanton.spec import Spec


def base(media, **over):
    d = {"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 116, "dx": 18}]}
    d.update(over)
    return d


def test_timeline_rounds_to_frames(media, spec_file):
    s = Spec.load(spec_file(base(media)))
    assert s.fps == 30 and (s.W, s.H) == (1080, 1920)
    assert [b["frames"] for b in s.blocks] == [45, 30]
    assert [b["start"] for b in s.blocks] == [0.0, 1.5]
    assert s.blocks[1]["end"] == pytest.approx(2.5)
    assert s.duration == pytest.approx(2.5) and s.total_frames == 75


def test_zoom_forms(media, spec_file):
    s = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 106}, {"src": "face", "in": 0, "out": 1, "zoom": [98, 106]}, {"src": "face", "in": 0, "out": 1}])))
    assert s.blocks[0]["zoom"] == [106.0, 106.0]
    assert s.blocks[1]["zoom"] == [98.0, 106.0]
    assert s.blocks[2]["zoom"] == [100.0, 100.0]


def test_bad_block_exits(media, spec_file):
    with pytest.raises(SystemExit):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 2, "out": 1}])))
    with pytest.raises(SystemExit):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0}])))
    with pytest.raises(SystemExit):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": "big"}])))


def test_speed_shortens_block(media, spec_file):
    s = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 2, "speed": 2}])))
    assert s.blocks[0]["frames"] == 30 and s.duration == pytest.approx(1.0)


def test_reel_time_maps_through_blocks(media, spec_file):
    s = Spec.load(spec_file(base(media)))
    assert s.reel_time("face", 0.5) == pytest.approx(0.0)
    assert s.reel_time("face", 1.0) == pytest.approx(0.5)
    assert s.reel_time("face", 3.5) == pytest.approx(2.0)
    assert s.reel_time("face", 2.5) is None          # cut out
    assert s.reel_time("other", 1.0) is None
    assert s.cuts() == [pytest.approx(1.5)]


def test_text_end_and_style_override(media, spec_file):
    s = Spec.load(spec_file(base(media, texts=[{"text": "hello", "t": 0.2, "end": 1.0, "y": 1300, "size": 40}, {"text": "x", "t": 0, "dur": 0.5}])))
    assert s.texts[0]["dur"] == pytest.approx(0.8)
    st = s.text_style(s.texts[0])
    assert st["y"] == 1300 and st["size"] == 40 and st["font"] == "Anton-Regular.ttf"
    assert s.text_style(s.texts[1])["size"] == 60
    with pytest.raises(SystemExit):
        Spec.load(spec_file(base(media, texts=[{"text": "no time"}])))


def test_overlay_and_music_normalised(media, spec_file):
    s = Spec.load(spec_file(base(media, overlays=[{"kind": "video", "src": str(media["broll"]), "t": 0.5, "dur": 1.0, "in": 0.2}],
                                 music={"src": str(media["music"]), "gain_db": -20, "fade_out": 0.5})))
    o = s.overlays[0]
    assert o["path"] == str(media["broll"]) and o["zoom"] == 100 and o["t"] == 0.5
    assert s.music["gain_db"] == -20 and s.music["dur"] is None and s.music["fade_out"] == 0.5
    with pytest.raises(SystemExit):
        Spec.load(spec_file(base(media, overlays=[{"kind": "video", "src": str(media["broll"]), "t": 0}])))


def test_inline_preset_extends_default(media, spec_file):
    s = Spec.load(spec_file(base(media, preset={"text": {"size": 64, "fill": "#FFD400"}, "zoom": {"focus": [540, 640]}})))
    assert s.preset["text"]["size"] == 64 and s.preset["text"]["fill"] == "#FFD400"
    assert s.preset["text"]["stroke_width"] == 7                # inherited
    assert s.preset["safe_zone"]["top"] == 270


def test_preset_from_file_and_relative_sources(media, spec_file, tmp_path):
    (tmp_path / "brand.json").write_text(json.dumps({"extends": "default", "text": {"uppercase": False}}))
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "face.mp4").write_bytes(Path(media["face"]).read_bytes())
    s = Spec.load(spec_file({"preset": "brand.json", "sources": {"face": "clips/face.mp4"}, "blocks": [{"src": "face", "in": 0, "out": 1}]}))
    assert s.preset["text"]["uppercase"] is False and s.preset["_path"].endswith("brand.json")
    assert s.blocks[0]["path"] == str(tmp_path / "clips" / "face.mp4")
    with pytest.raises(SystemExit):
        Spec.load(spec_file({"preset": "nope-nope", "blocks": []}, "x.json"))


def test_describe_and_save(media, spec_file, tmp_path):
    p = spec_file(base(media))
    s = Spec.load(p)
    assert "2 blocks" in s.describe() and "zoom 116" in s.describe()
    s.data["blocks"].append({"src": "face", "in": 4, "out": 5})
    s.save()
    assert len(json.loads(p.read_text())["blocks"]) == 3


def test_spec_level_zoom_overrides_preset(media, spec_file):
    s = Spec.load(spec_file(base(media, zoom={"focus": [540, 960]})))
    assert s.zoom["focus"] == [540, 960] and s.zoom["validated"] == [100, 106]
    assert Spec.load(spec_file(base(media))).zoom["focus"] == [540, 600] or Spec.load(spec_file(base(media))).zoom["focus"] == [540, 960]


def test_texts_are_held_inside_the_safe_zone(media, spec_file, tmp_path):
    """Width, horizontal place and baseline come back inside the preset safe zone (150-930, bottom 1432), whatever a text asks."""
    from kanton.text import TextStyle
    long = "un sous-titre bien trop large qui sortirait de la zone sur les deux côtés"
    s = Spec.load(spec_file(base(media, texts=[{"text": long, "t": 0, "dur": 1, "x": 1000, "y": 1800, "max_width": 1000}])))
    st = s.text_style(s.texts[0])
    assert st["max_width"] == 780 and st["y"] == 1432
    x0, y0, x1, y1 = TextStyle(st).render(long, s.W, s.H, tmp_path / "t.png")["bbox"]
    assert x0 >= 150 and x1 <= 930 and y1 <= 1432 + 30                          # +30: stroke and descenders under the baseline, as qa allows
    d = s.text_style()
    assert (d["x"], d["max_width"], d["y_baseline"]) == (540, 780, 1432)       # the default preset is already inside: unchanged
