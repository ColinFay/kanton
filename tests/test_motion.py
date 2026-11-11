"""Motion of a layer: keyframes and animations become one perspective filter (position, scale, rotation) and per-frame opacity."""
import math

import pytest

from kanton import motion


def test_track_holds_outside_and_interpolates_with_the_ease_of_the_key_reached():
    tr = motion.Track(5.0, [(1.0, 10.0, "linear"), (2.0, 20.0, "ease")])
    assert tr.at(0.0) == 10.0 and tr.at(3.0) == 20.0                          # held before the first key and after the last
    assert tr.at(1.5) == pytest.approx(15.0)                                  # smoothstep at its middle = linear middle
    assert tr.at(1.25) == pytest.approx(10 + 10 * (0.25 * 0.25 * (3 - 0.5)))
    assert motion.Track(5.0, []).at(9.0) == 5.0
    e = tr.at(motion.X("T"))
    assert "if(lt(T,2" in str(e) and "if(lt(T,1" in str(e)


def test_x_builds_ffmpeg_expressions_with_the_same_formulas():
    x = motion.X("T")
    assert str(x * 2 + 1) == "((T*2)+1)" and str(motion.sin(x / 3)) == "sin((T/3))" and str(motion.clip(x, 0, 1)) == "clip(T,0,1)"
    assert motion.sin(math.pi / 2) == pytest.approx(1.0) and motion.clip(4, 0, 1) == 1


def test_build_static_layer_needs_no_motion():
    m = motion.build(1080, 1920, 30, 2.0, box=(100, 300, 400, 300))
    assert m["perspective"] is None and m["opacity"] is None and not m["moving"]
    m = motion.build(1080, 1920, 30, 2.0, box=(100, 300, 400, 300), scale=0.5)
    assert m["perspective"].startswith("perspective=x0=") and "sense=destination" in m["perspective"] and not m["moving"]


def test_build_keyframes_and_animations():
    m = motion.build(1080, 1920, 30, 1.0, box=(100, 300, 400, 300), keyframes=[{"t": 0, "x": 100}, {"t": 1, "x": 600, "ease": "linear"}],
                     anim_out={"type": "fade", "dur": 0.5})
    assert m["moving"] and "on/30" in m["perspective"] and m["opacity"] is not None and len(m["opacity"]) == 30
    assert m["opacity"][0] == pytest.approx(1.0) and m["opacity"][22] == pytest.approx(0.533, abs=0.03) and m["opacity"][29] < 0.1
    at = m["at"]
    assert at(0.0)["x"] == pytest.approx(100) and at(0.5)["x"] == pytest.approx(350) and at(1.0)["x"] == pytest.approx(600)
    m = motion.build(1080, 1920, 30, 1.0, box=(100, 300, 400, 300), anim_in={"type": "slide_up", "dur": 0.4}, anim_loop={"type": "pulse", "dur": 1.0, "amount": 1.0})
    assert m["at"](0.0)["y"] > 300 + 50 and m["at"](0.6)["y"] == pytest.approx(300) and m["at"](0.25)["scale"] > 1.0
    assert m["opacity"][0] < 0.2 and m["opacity"][-1] == pytest.approx(1.0)


def test_corners_follow_scale_about_the_pivot():
    m = motion.build(1080, 1920, 30, 1.0, box=(100, 300, 400, 300), keyframes=[{"t": 0, "scale": 1}, {"t": 1, "scale": 0.5}])
    c0, c1 = m["corners"](0.0), m["corners"](1.0)
    assert c0[0] == pytest.approx((0, 0), abs=1e-6)                             # the padded canvas corner stays put at scale 1
    cx, cy = 100 + 200 + 1, 300 + 150 + 1                                        # the box centre, in the padded canvas
    assert c1[0] == pytest.approx((cx / 2, cy / 2), abs=1e-6)                   # half way to the pivot at scale 0.5
