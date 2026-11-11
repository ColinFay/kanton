"""Looks, effects and transitions: the catalogs and the filter chains they compile to (fx.py)."""
import pytest

from kanton import fx


def test_catalog_lists_every_name_an_agent_can_use():
    c = fx.catalog()
    assert {"dissolve", "wipeleft", "fadeblack", "zoomin", "blur", "flash", "punch"} <= set(c["transitions"]) and "custom" not in c["transitions"]
    assert {"blur", "shake", "rgb_split", "glitch", "vhs", "old_film", "pulse", "pixelate", "mirror", "negative", "strobe", "fisheye", "grain", "flash"} <= set(c["effects"])
    assert {"screen", "multiply", "overlay", "add", "lighten", "darken", "difference", "softlight", "hardlight"} <= set(c["blends"])
    assert {"fade", "slide_up", "slide_down", "slide_left", "slide_right", "pop", "zoom", "spin"} <= set(c["animations"]["in"])
    assert set(c["animations"]["out"]) == set(c["animations"]["in"])
    assert {"pulse", "shake", "wobble", "float", "spin"} <= set(c["animations"]["loop"])
    assert {"rect", "circle", "rounded", "linear"} <= set(c["masks"])
    assert {"warm", "cool", "bw", "vintage", "vivid", "fade", "cinema", "noir"} <= set(c["filters"])
    assert set(c["grade"]) >= {"contrast", "saturation", "brightness", "gamma", "temperature", "vibrance", "vignette", "exposure", "tint", "hue", "sharpen", "highlights", "shadows", "grain", "fade", "lut"}


def test_grade_chain_covers_the_adjust_panel():
    g = fx.grade_chain({"contrast": 1.1, "saturation": 0.8, "temperature": 6200, "vignette": 0.5})
    assert "eq=contrast=1.1000:saturation=0.8000" in g and "colortemperature=temperature=6200" in g and "vignette=angle=0.3000" in g
    g = fx.grade_chain({"exposure": 0.5, "tint": -20, "hue": 10, "sharpen": 0.5, "highlights": -0.3, "shadows": 0.2, "grain": 0.3, "fade": 0.2, "lut": "/x/look.cube"})
    assert "exposure=exposure=0.5000" in g and "colorbalance=gs=0.2000:gm=0.2000:gh=0.2000" in g and "hue=h=10.0000" in g
    assert "unsharp=" in g and "curves=all='0/0.0500 0.25/0.2900 0.75/0.6900 1/1'" in g and "noise=alls=12" in g and "lut3d=file='/x/look.cube'" in g
    assert g.startswith(",")                                   # ready to append to a chain
    assert fx.grade_chain({}) == "" and fx.grade_chain(None) == ""


def test_named_filters_resolve_with_intensity_and_overrides():
    assert fx.resolve_grade("warm", {}) == fx.FILTERS["warm"]
    g = fx.resolve_grade({"filter": "bw", "contrast": 1.2}, {})
    assert g["saturation"] == 0 and g["contrast"] == 1.2
    g = fx.resolve_grade({"filter": "warm", "intensity": 0.5}, {})          # half way back to neutral
    assert g["temperature"] == pytest.approx((fx.FILTERS["warm"]["temperature"] + 6500) / 2)
    assert "intensity" not in g and "filter" not in g
    assert fx.resolve_grade("brand", {"brand": {"saturation": 1.3}})["saturation"] == 1.3      # a preset look wins over a built-in name
    assert fx.resolve_grade(None, {}) == {} and fx.resolve_grade({"contrast": 1.1}, {}) == {"contrast": 1.1}
    with pytest.raises(SystemExit, match="unknown filter"):
        fx.resolve_grade("nope", {})


def test_effects_graph_times_each_effect():
    lines, out = fx.effects_graph([{"type": "blur", "t": 1.0, "dur": 0.5, "amount": 1.0}, {"type": "negative", "t": 2, "dur": 1}], "v0", 1080, 1920, 30)
    g = ";".join(lines)
    assert g.startswith("[v0]") and g.endswith(f"[{out}]") and "gblur" in g and "between(t,1.0000,1.5000)" in g and "negate" in g
    lines, out = fx.effects_graph([{"type": "shake", "t": 0, "dur": 1, "amount": 0.5}], "v0", 1080, 1920, 30)
    g = ";".join(lines)
    assert "split" in g and "crop=" in g and "overlay=" in g and "between(t,0.0000,1.0000)" in g        # not a timeline filter: a copy is shaken and put back for the window
    assert fx.effects_graph([], "v0", 1080, 1920, 30) == ([], "v0")
    with pytest.raises(SystemExit, match="unknown effect"):
        fx.effects_graph([{"type": "nope", "t": 0, "dur": 1}], "v0", 1080, 1920, 30)


def test_every_effect_runs_in_ffmpeg():
    from kanton import ffmpeg
    for name in fx.catalog()["effects"]:
        lines, out = fx.effects_graph([{"type": name, "t": 0.1, "dur": 0.2, "amount": 0.7}], "v0", 1080, 1920, 30)
        assert lines and out != "v0", name
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=0.4", "-filter_complex", "[0:v]null[v0];" + ";".join(lines) + f";[{out}]null[vout]",
                    "-map", "[vout]", "-frames:v", "12", "-f", "null", "-"])


def test_transition_kinds():
    assert fx.is_xfade("dissolve") and fx.is_xfade("wipeleft") and fx.is_xfade("fade")
    assert not fx.is_xfade("blur") and not fx.is_xfade("none") and not fx.is_xfade("punch")
    with pytest.raises(SystemExit, match="unknown transition"):
        fx.check_transition("swoosh")
    assert fx.check_transition("dissolve") == "dissolve" and fx.check_transition(None) == "none"
