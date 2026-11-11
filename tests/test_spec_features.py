"""The spec carries every feature as plain JSON: stills and freeze frames, per-block looks and audio, transitions
with a duration, picture-in-picture, masks, blends, keyframes, animations, sounds, markers, text roles."""
import pytest

from kanton import fx
from kanton.captions import plays
from kanton.spec import Spec


def base(media, **over):
    d = {"sources": {"face": str(media["face"]), "broll": str(media["broll"]), "still": str(media["still"]), "music": str(media["music"])},
         "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0}]}
    d.update(over)
    return d


def test_freeze_frame_and_still_blocks(media, spec_file):
    s = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "at": 1.0, "dur": 1.5}, {"src": "still", "dur": 2, "zoom": [100, 110]}])))
    b0, fz, st = s.blocks
    assert not b0["still"] and fz["still"] and st["still"]
    assert (fz["in"], fz["out"], fz["frames"]) == (1.0, 2.5, 45) and (st["in"], st["out"], st["frames"]) == (0.0, 2.0, 60)
    assert s.duration == pytest.approx(4.5) and st["zoom"] == [100.0, 110.0]
    assert s.reel_time("face", 1.5) is None                                   # a frozen frame plays no words
    assert not plays(fz, {"start": 1.2, "end": 1.4}) and plays(b0, {"start": 0.2, "end": 0.4})
    with pytest.raises(SystemExit, match="needs src, in, out"):
        Spec.load(spec_file(base(media, blocks=[{"src": "face"}])))
    assert "still" in s.describe()


def test_block_fields_for_the_clip_panel(media, spec_file):
    s = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "fit": "contain", "background": "#112233", "rotate": 90, "crop": [10, 20, 500, 900],
                                                 "vflip": True, "reverse": True, "grade": "warm", "volume": -6, "fade_in": 0.2, "fade_out": 0.3, "denoise": True, "pitch": 2,
                                                 "cut": "dissolve", "cut_dur": 0.5, "effects": [{"type": "shake", "amount": 0.5}]},
                                                {"src": "face", "in": 1, "out": 2, "cut": {"type": "wipeleft", "dur": 0.4}},
                                                {"src": "face", "in": 2, "out": 3, "cut": "blur"}])))
    b = s.blocks[0]
    assert b["fit"] == "contain" and b["background"] == "#112233" and b["rotate"] == 90 and b["crop"] == [10, 20, 500, 900] and b["vflip"] and b["reverse"]
    assert b["grade"] == fx.FILTERS["warm"] and b["volume"] == -6 and b["fade_in"] == 0.2 and b["fade_out"] == 0.3 and b["denoise"] == 1.0 and b["pitch"] == 2
    assert b["cut"] == "dissolve" and b["cut_dur"] == 0.5
    assert s.blocks[1]["cut"] == "wipeleft" and s.blocks[1]["cut_dur"] == 0.4
    assert s.blocks[2]["cut"] == "blur" and s.blocks[2]["cut_dur"] == 0.3                        # preset default (transitions.duration)
    d = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1}], transitions={"duration": 0.6}))).blocks[0]
    assert d["cut_dur"] == 0.6 and d["fit"] == "cover" and d["background"] is None and d["rotate"] == 0 and d["crop"] is None and d["grade"] == {} and d["volume"] == 0
    with pytest.raises(SystemExit, match="unknown transition"):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "cut": "swoosh"}])))
    with pytest.raises(SystemExit, match="unknown filter"):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "grade": "nope"}])))
    with pytest.raises(SystemExit, match="fit"):
        Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1, "fit": "stretch"}])))


def test_block_effects_are_timed_on_the_reel_and_global_effects_kept(media, spec_file):
    s = Spec.load(spec_file(base(media, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "in": 1, "out": 2, "effects": [{"type": "pixelate"}]}],
                                 effects=[{"type": "blur", "t": 0.2, "dur": 0.5, "amount": 0.4}])))
    assert s.effects == [{"type": "blur", "t": 0.2, "dur": 0.5, "amount": 0.4}, {"type": "pixelate", "t": 1.0, "dur": 1.0, "amount": 1.0}]
    with pytest.raises(SystemExit, match="unknown effect"):
        Spec.load(spec_file(base(media, effects=[{"type": "nope", "t": 0, "dur": 1}])))


def test_overlay_fields_pip_mask_blend_keyframes_animations(media, spec_file):
    s = Spec.load(spec_file(base(media, overlays=[
        {"kind": "video", "src": "broll", "t": 0.5, "dur": 1, "x": 100, "y": 200, "width": 400, "height": 300, "loop": True, "volume": -3, "rotation": 10, "scale": 0.8,
         "mask": {"shape": "circle", "feather": 12}, "blend": "screen", "grade": {"saturation": 0},
         "keyframes": [{"t": 0.5, "x": 300, "y": 200, "scale": 1.2, "ease": "ease"}, {"t": 0, "x": 100, "y": 200, "opacity": 0.5}],
         "anim_in": "pop", "anim_out": {"type": "fade", "dur": 0.4}, "anim_loop": {"type": "pulse", "dur": 1.2, "amount": 0.5}}])))
    o = s.overlays[0]
    assert (o["x"], o["y"], o["width"], o["height"]) == (100, 200, 400, 300) and o["loop"] and o["volume"] == -3 and o["rotation"] == 10 and o["scale"] == 0.8
    assert o["mask"] == {"shape": "circle", "feather": 12, "radius": 0, "invert": False, "rect": None, "angle": 0} and o["blend"] == "screen" and o["grade"] == {"saturation": 0}
    assert [k["t"] for k in o["keyframes"]] == [0, 0.5] and o["keyframes"][1]["ease"] == "ease" and o["keyframes"][0]["opacity"] == 0.5     # sorted
    assert o["anim_in"] == {"type": "pop", "dur": 0.3} and o["anim_out"] == {"type": "fade", "dur": 0.4} and o["anim_loop"] == {"type": "pulse", "dur": 1.2, "amount": 0.5}
    d = Spec.load(spec_file(base(media, overlays=[{"kind": "image", "src": "still", "t": 0, "dur": 1}]))).overlays[0]
    assert d["mask"] is None and d["blend"] is None and d["keyframes"] == [] and d["anim_in"] is None and not d["loop"] and d["volume"] is None and d["height"] is None
    for bad, msg in (({"blend": "burn"}, "unknown blend"), ({"mask": {"shape": "star"}}, "unknown mask"), ({"anim_in": "explode"}, "unknown animation"),
                     ({"keyframes": [{"t": 0, "colour": 1}]}, "keyframe"), ({"anim_loop": "fade"}, "unknown animation")):
        with pytest.raises(SystemExit, match=msg):
            Spec.load(spec_file(base(media, overlays=[{"kind": "image", "src": "still", "t": 0, "dur": 1, **bad}])))


def test_text_fields_and_roles_from_the_preset(media, spec_file):
    pre = {"text": {"roles": {"title": {"size": 80, "y": 900, "anim_in": "pop"}, "lower_third": {"align": "left", "x": 160, "y": 1300, "max_width": 700}}, "highlight": {"fill": "#FFD400"}}}
    s = Spec.load(spec_file(base(media, preset=pre, texts=[
        {"text": "TITRE", "t": 0, "dur": 1, "role": "title", "shadow": True, "box": {"color": "#000000"}, "opacity": 0.8, "rotation": -5},
        {"text": "un deux", "t": 1, "dur": 1, "role": "caption", "words": [{"word": "un", "t": 1.0, "dur": 0.4}, {"word": "deux", "t": 1.4, "dur": 0.5}], "reveal": "word"},
        {"text": "nom", "t": 2, "dur": 1, "role": "lower_third", "size": 40},
        {"text": "x", "t": 2, "dur": 1, "role": "title", "anim_in": {"type": "fade", "dur": 0.2}}])))
    t0, t1, t2, t3 = s.texts
    assert t0["shadow"] == {"color": "#000000", "dx": 4, "dy": 4, "blur": 6, "opacity": 0.6} and t0["box"] == {"color": "#000000", "opacity": 0.6, "padding": 20, "radius": 16}
    assert t0["opacity"] == 0.8 and t0["rotation"] == -5 and t0["anim_in"] == {"type": "pop", "dur": 0.3}        # from the role
    assert s.text_style(t0)["size"] == 80 and s.text_style(t0)["y"] == 900
    assert t1["words"][1] == {"word": "deux", "t": 1.4, "dur": 0.5} and t1["reveal"] == "word" and t1["highlight"] == {"fill": "#FFD400"} and t1["anim_in"] is None
    assert s.text_style(t2)["size"] == 40 and s.text_style(t2)["align"] == "left" and s.text_style(t2)["x"] == 160
    assert t3["anim_in"] == {"type": "fade", "dur": 0.2}                                                          # the text wins over the role
    assert s.text_style(t1)["size"] == 60 and "roles" not in s.text_style(t1)
    with pytest.raises(SystemExit, match="unknown reveal"):
        Spec.load(spec_file(base(media, texts=[{"text": "x", "t": 0, "dur": 1, "reveal": "letter"}])))


def test_sounds_music_markers_canvas_export(media, spec_file):
    s = Spec.load(spec_file(base(media, music={"src": "music", "gain_db": -20, "duck": True, "loop": True},
                                 sounds=[{"src": "music", "t": 1.0, "in": 2.0, "dur": 0.5, "gain_db": -3, "fade_out": 0.1, "pitch": -2}, {"src": str(media["music"]), "t": 2.0}],
                                 markers=[{"t": 1.2, "label": "hook"}, {"t": 0.4}], canvas={"background": "blur"},
                                 export={"format": "gif", "cover": 1.5})))
    assert s.music["duck"] == {"threshold": 0.02, "ratio": 6, "attack": 80, "release": 500} and s.music["loop"]
    assert s.sounds[0]["path"] == str(media["music"]) and s.sounds[0]["t"] == 1.0 and s.sounds[0]["dur"] == 0.5 and s.sounds[0]["pitch"] == -2 and s.sounds[0]["fade_out"] == 0.1
    assert s.sounds[1]["dur"] is None and s.sounds[1]["gain_db"] == 0 and s.sounds[1]["path"] == str(media["music"])
    assert s.markers == [{"t": 0.4, "label": ""}, {"t": 1.2, "label": "hook"}]
    assert s.canvas["background"] == "blur" and s.export["format"] == "gif" and s.export["cover"] == 1.5
    m = Spec.load(spec_file(base(media, music={"src": "music", "duck": {"ratio": 4}}))).music
    assert m["duck"]["ratio"] == 4 and m["duck"]["threshold"] == 0.02 and not m["loop"]
    d = Spec.load(spec_file(base(media)))
    assert d.sounds == [] and d.markers == [] and d.canvas == {"background": None} and d.export["format"] == "mp4" and d.music is None
    with pytest.raises(SystemExit, match="unknown export format"):
        Spec.load(spec_file(base(media, export={"format": "avi"})))


def test_global_grade_accepts_a_look_name(media, spec_file):
    assert Spec.load(spec_file(base(media, grade="cinema"))).grade == fx.FILTERS["cinema"]
    s = Spec.load(spec_file(base(media, preset={"filters": {"brand": {"saturation": 1.2, "temperature": 7000}}}, grade={"filter": "brand", "intensity": 0.5})))
    assert s.grade["saturation"] == pytest.approx(1.1) and s.grade["temperature"] == pytest.approx(6750)
