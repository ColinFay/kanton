"""The editor's studio pieces: filmstrips and waveforms for the timeline, beats, a real export, auto captions, player
pieces of curved / stabilised / voiced clips."""
import json
import time
import urllib.request
from pathlib import Path

import pytest

from kanton import editor, words
from kanton.spec import Spec

from test_editor import get, post, server, video_frames  # noqa: F401  (server is a fixture)
from test_speed import sound_seconds


def wait_job(url):
    for _ in range(900):
        d = json.loads(get(url)[2])
        if d["status"] != "running":
            return d
        time.sleep(0.2)
    raise AssertionError("job never ended")


def ready(url):
    """What the page does: ask again while the server answers 202 (still being built)."""
    for _ in range(300):
        r = get(url)
        if r[0] != 202:
            return r
        time.sleep(0.1)
    raise AssertionError("never built")


def test_payload_plans_a_filmstrip_per_video_source(server):
    s = json.loads(get(server["url"] + "/api/spec")[2])["sources"]["face"]
    st = s["strip"]
    assert st["url"] == "/strip/face.jpg" and st["rate"] == 2 and st["n"] == 12 and (st["w"], st["h"]) == (46, 80)
    code, h, body = ready(server["url"] + st["url"])
    assert code == 200 and h["Content-Type"] == "image/jpeg"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(body)).size == (st["n"] * st["w"], st["h"])


def test_peaks_show_the_silences(server):
    d = json.loads(get(server["url"] + "/api/peaks/face")[2])
    p, r = d["peaks"], d["rate"]
    assert r == 50 and len(p) == pytest.approx(6 * r, abs=2)
    assert max(p[int(2.2 * r):int(2.8 * r)]) < 5 and min(p[int(0.5 * r):int(1.5 * r)]) > 30     # face is silent 2-3 s


def test_beats_endpoint(server, tmp_path):
    from conftest import ff
    k = tmp_path / "k.wav"
    ff("-f", "lavfi", "-i", "aevalsrc=exprs='0.8*sin(2*PI*70*t)*exp(-25*mod(t\\,0.5))':s=48000:c=stereo:d=6", k)
    st = server["srv"].state
    st.spec = Spec({**st.spec.data, "sources": {**st.spec.data["sources"], "k": str(k)}}, st.spec.path)
    b = json.loads(get(server["url"] + "/api/beats/k")[2])["beats"]
    assert len(b) >= 10 and all(abs(x - round(x * 2) / 2) < 0.03 for x in b)


def test_export_renders_the_file_next_to_the_spec(server):
    assert json.loads(get(server["url"] + "/api/export")[2])["status"] == "idle"
    st, j = post(server["url"] + "/api/export")
    assert st == 200 and j["status"] == "running"
    d = wait_job(server["url"] + "/api/export")
    assert d["status"] == "done" and d["file"] == str(server["spec"].with_suffix(".mp4"))
    from kanton import ffmpeg
    i = ffmpeg.probe(d["file"])
    assert (i["width"], i["height"]) == (1080, 1920) and abs(i["duration"] - 2.5) < 0.07          # the full export, not a proxy


def test_auto_captions_transcribe_then_write_the_captions(server, monkeypatch):
    said = [{"word": " salut", "start": 0.6, "end": 0.9}, {"word": " toi", "start": 1.0, "end": 1.3}]
    monkeypatch.setattr("kanton.transcribe.transcribe", lambda path, start=0.0, end=None, backend="auto", language="fr", python=None: said)
    st, j = post(server["url"] + "/api/captions", {"lang": "fr"})
    assert st == 200 and j["status"] == "running"
    d = wait_job(server["url"] + "/api/captions")
    assert d["status"] == "done" and d["captions"] >= 1
    spec = json.loads(Path(server["spec"]).read_text())
    assert spec["words"]["face"] == said and any(t.get("role", "caption") == "caption" and "SALUT" in t["text"].upper() for t in spec["texts"])
    assert json.loads(get(server["url"] + "/api/spec")[2])["words"]                               # the page reloads the words


def test_auto_captions_report_a_missing_transcriber(server, monkeypatch):
    def none(*a, **k):
        raise SystemExit("kanton: no transcription backend")
    monkeypatch.setattr("kanton.transcribe.transcribe", none)
    post(server["url"] + "/api/captions", {"lang": "fr"})
    d = wait_job(server["url"] + "/api/captions")
    assert d["status"] == "error" and "transcri" in d["error"].lower()


def test_player_piece_of_a_curved_stabilised_voiced_clip(media, spec_file):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 3, "speed": "hero", "stabilize": True, "voice": "robot"}]}))
    b = s.blocks[0]
    out = editor.ensure_segment(s, b)
    assert video_frames(Path(f"{out}.v.mp4")) == b["frames"]
    assert sound_seconds(Path(f"{out}.a.mp4")) == pytest.approx(b["dur"], abs=0.05)
    other = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 3, "speed": "hero"}]}, "b.json"))
    assert editor.seg_key(s, b) != editor.seg_key(other, other.blocks[0])


def test_catalog_offers_curves_and_voices(server):
    c = json.loads(get(server["url"] + "/api/catalog")[2])
    assert "hero" in c["curves"] and "robot" in c["voices"] and c["curve_points"]["hero"][0] == [0, 2]


def test_words_transcribe_missing_is_shared_with_the_cli(media, spec_file, monkeypatch):
    monkeypatch.setattr("kanton.transcribe.transcribe", lambda *a, **k: [{"word": "a", "start": 0.1, "end": 0.2}])
    s = Spec.load(spec_file({"sources": {"face": str(media["face"]), "b": str(media["broll"])}, "blocks": [{"src": "face", "in": 0, "out": 1}, {"src": "b", "in": 0, "out": 1}]}))
    ws = words.transcribe_missing(s)
    assert set(ws) == {"face"} and s.data["words"] is ws                                          # a clip with no sound is skipped


def test_page_carries_the_studio_controls(server):
    body = get(server["url"] + "/")[2].decode()
    for needle in ('id="drop"', "function curveEditor", "Record voiceover", "Auto captions", "/api/export", "/api/peaks/", "/api/beats/", "Stabilize", "Voice effect"):
        assert needle in body, needle


def test_model_download_runs_in_the_background_and_the_page_asks_first(server, monkeypatch):
    from kanton import transcribe as T
    state = {"present": False}
    monkeypatch.setattr(T, "model_status", lambda: {"managed": True, "model": "large-v3-turbo", "repo": "x/y", "present": state["present"], "bytes": 0})
    def fake_download():
        time.sleep(0.3); state["present"] = True
    monkeypatch.setattr(T, "download_model", fake_download)
    m = json.loads(get(server["url"] + "/api/model")[2])
    assert m["managed"] and not m["present"] and m["status"] == "idle"
    st, j = post(server["url"] + "/api/model", {})
    assert st == 200 and j["status"] == "running"
    try:
        post(server["url"] + "/api/model", {}); assert False, "a second download must be refused"
    except urllib.error.HTTPError as e:
        assert e.code == 409
    d = wait_job(server["url"] + "/api/model")
    assert d["status"] == "done" and d["present"]
    body = get(server["url"] + "/")[2]
    assert b'id="dlgModel"' in body and b"/api/model" in body


def test_a_model_picked_on_disk_goes_through_the_check(server, monkeypatch):
    from kanton import transcribe as T
    def fake_use(path):
        if "bad" in str(path):
            raise ValueError("this is Whisper large-v3; kanton needs large-v3-turbo")
        return {"managed": True, "model": "large-v3-turbo", "repo": "x/y", "present": True, "bytes": 0, "path": str(path)}
    monkeypatch.setattr(T, "use_model", fake_use)
    try:
        post(server["url"] + "/api/model", {"path": "/models/bad"}); assert False, "a wrong model must be refused"
    except urllib.error.HTTPError as e:
        assert e.code == 400 and "large-v3-turbo" in json.loads(e.read())["error"]
    st, j = post(server["url"] + "/api/model", {"path": "/models/turbo"})
    assert st == 200 and j["present"] and j["path"] == "/models/turbo" and j["status"] == "idle"
    body = get(server["url"] + "/")[2]
    assert b'value="pick"' in body and b"pick_model_dialog" in body
