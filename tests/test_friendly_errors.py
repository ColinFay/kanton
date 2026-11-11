"""The editor shows errors to people; the CLI keeps the precise message for agents."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from kanton.app import Api
from kanton.editor import friendly, make_server
from kanton.spec import Spec


def test_friendly_names_the_clip_counting_from_one():
    assert friendly("kanton: block 0: out ≤ in (2.0 → 1.0)") == "Clip 1: the trim end must come after the trim start."


def test_friendly_keeps_unknown_messages_readable():
    assert friendly("kanton: something odd") == "Something went wrong: something odd"


@pytest.mark.parametrize("msg, out", [
    ("kanton: block 2 zoom [90.0, 90.0] is under 100 and would show a border (100 = full frame)", "Clip 3: zoom can't go below 100%."),
    ("kanton: ffprobe failed on /x/a.mov: moov atom not found", "This file can't be read as a video, an image or a sound."),
    ("ffmpeg failed (1):\nffmpeg -i a.mov\nInvalid data", "The video could not be rendered."),
    ("kanton: font not found: MyBrand.otf (put the file next to the preset, or give an absolute path)", "The font MyBrand.otf isn't installed."),
])
def test_friendly_covers_media_and_render_errors(msg, out):
    assert friendly(msg) == out


def test_preview_errors_are_friendly(tmp_path):
    srv = make_server(Spec({"kanton": 1, "blocks": []}, tmp_path / "m.json"), 0)
    try:
        srv.state.start_render()
        for _ in range(200):
            if srv.state.render["status"] != "running":
                break
            time.sleep(0.05)
    finally:
        srv.server_close()
    assert srv.state.render == {"status": "error", "file": None, "error": "Add at least one clip first."}


def test_open_spec_returns_a_friendly_error(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("not json")
    srv = make_server(None, 0)
    try:
        r = Api(srv).open_spec(str(f))
    finally:
        srv.server_close()
    assert r == {"ok": False, "error": "This file isn't a kanton project."}


def _put(srv, data):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/spec", data=json.dumps(data).encode(), method="PUT")
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_save_errors_are_friendly_even_for_a_non_number(tmp_path):
    data = {"kanton": 1, "sources": {"a": str(tmp_path / "a.mp4")}, "blocks": [{"src": "a", "in": 0, "out": 1}]}
    srv = make_server(Spec(data, tmp_path / "m.json"), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        bad_order = {**data, "blocks": [{"src": "a", "in": 2, "out": 1}]}
        not_a_number = {**data, "blocks": [{"src": "a", "in": None, "out": 1}]}
        assert _put(srv, bad_order) == (400, {"error": "Clip 1: the trim end must come after the trim start."})
        assert _put(srv, not_a_number) == (400, {"error": "A number field is empty or invalid."})
    finally:
        srv.shutdown()
        srv.server_close()
