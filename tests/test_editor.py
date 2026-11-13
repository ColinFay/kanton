"""The local editor server: the page, the spec API, media proxies with range requests, proxy render."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kanton import editor
from kanton.spec import Spec


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def server(media, spec_file):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 104}],
                   "texts": [{"text": "salut", "t": 0.0, "dur": 1.0}]})
    port = free_port()
    srv = editor.make_server(Spec.load(p), port)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    yield {"url": f"http://127.0.0.1:{port}", "spec": p, "srv": srv}
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, dict(r.headers), r.read()


def put(url, data, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="PUT", headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def ready(url):
    """What the page does: ask again while the server answers 202 (still being built)."""
    for _ in range(300):
        r = get(url)
        if r[0] != 202:
            return r
        time.sleep(0.1)
    raise AssertionError("never built")


def test_index_page(server):
    st, h, body = get(server["url"] + "/")
    assert st == 200 and b"kanton" in body and b"<video" in body
    assert b'id="wait"' in body                                                   # message shown until the video can play
    for tab in (b"Media", b"Audio", b"Text", b"Stickers", b"Effects", b"Transitions", b"Filters", b"Adjust"):
        assert tab in body
    for lane in (b"Video", b"Overlays", b"Text", b"Effects", b"Audio"):
        assert lane in body


def test_get_spec_with_timeline(server):
    st, _, body = get(server["url"] + "/api/spec")
    d = json.loads(body)
    assert st == 200 and d["spec"]["blocks"][1]["zoom"] == 104
    assert d["timeline"]["duration"] == pytest.approx(2.5) and d["timeline"]["fps"] == 30
    assert d["timeline"]["blocks"][1]["start"] == pytest.approx(1.5)
    assert d["sources"]["face"]["duration"] > 5 and d["sources"]["face"]["width"] == 1080 and d["sources"]["face"]["has_alpha"] is False
    assert d["style"]["size"] == 60 and d["style"]["fill"] == "#FFFFFF"


def test_put_spec_saves_and_validates(server):
    d = json.loads(get(server["url"] + "/api/spec")[2])["spec"]
    d["blocks"][0]["out"] = 1.5
    st, r = put(server["url"] + "/api/spec", d)
    assert st == 200 and r["timeline"]["duration"] == pytest.approx(2.0)
    assert json.loads(server["spec"].read_text())["blocks"][0]["out"] == 1.5
    d["blocks"][0]["out"] = 0.1
    st, r = put(server["url"] + "/api/spec", d)
    assert st == 400 and "Clip 1" in r["error"]
    assert json.loads(server["spec"].read_text())["blocks"][0]["out"] == 1.5     # the bad spec was not written


def test_media_proxy_with_range(server):
    st, h, body = get(server["url"] + "/media/face")
    assert st == 200 and h["Content-Type"] == "video/mp4" and len(body) > 1000
    st, h, part = get(server["url"] + "/media/face", {"Range": "bytes=0-99"})
    assert st == 206 and len(part) == 100 and h["Content-Range"].startswith("bytes 0-99/")
    st, _, _ = get(server["url"] + "/media/face")
    assert (server["srv"].spec.dir / ".kanton-tmp" / "montage" / "proxy-face.mp4").exists()


def test_unknown_media_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server["url"] + "/media/nope")
    assert e.value.code == 404


def test_font_served(server):
    st, h, body = get(server["url"] + "/font")
    assert st == 200 and len(body) > 10000 and "font" in h["Content-Type"]


def post(url, data=None):
    req = urllib.request.Request(url, data=b"" if data is None else json.dumps(data).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_render_status_is_idle_until_started(server):
    st, _, body = get(server["url"] + "/api/render")
    assert st == 200 and json.loads(body)["status"] == "idle"


def test_render_proxy_endpoint(server):
    st, j = post(server["url"] + "/api/render")
    assert st == 200 and j["status"] == "running"
    st, j = post(server["url"] + "/api/render")
    assert j["status"] == "running"                       # a second start while running is a no-op
    for _ in range(600):
        d = json.loads(get(server["url"] + "/api/render")[2])
        if d["status"] == "done":
            break
        time.sleep(0.2)
    assert d["status"] == "done" and d["file"].endswith("-proxy.mp4")
    st, h, body = get(server["url"] + "/out", {"Range": "bytes=0-9"})
    assert st == 206 and len(body) == 10
    assert json.loads(get(server["url"] + "/api/render")[2])["status"] == "done"   # polling never restarts a render


def test_proxy_appears_only_when_complete(media, spec_file, monkeypatch):
    """Built under another name and renamed: a request during the build (or after a crash) never gets half a file."""
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 1}]}))
    final, written = s.dir / ".kanton-tmp" / "montage" / "proxy-face.mp4", []
    real = editor.ffmpeg.run
    monkeypatch.setattr(editor.ffmpeg, "run", lambda args: (written.append(args[-1]), real(args))[1])
    assert editor.ensure_proxy(s, "face") == final and final.exists()
    assert written and written[0] != str(final) and not Path(written[0]).exists()


def video_frames(path) -> int:
    import subprocess
    from kanton import ffmpeg
    r = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(r.stdout.strip())


def audio_seconds(path) -> float:
    import subprocess
    from kanton import ffmpeg
    r = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def test_segment_is_one_block_for_the_player(media, spec_file):
    """Each block becomes a picture and a sound in fragmented MP4, cut to the block's frames, that the page puts end to end
    in one player (Media Source): nothing is loaded or sought at a cut while playing."""
    from kanton import blanks
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.5, "out": 2.5},
                                                                                {"src": "face", "in": 1.01, "out": 1.06}]}))  # 1 source frame for 2 in the spec
    for b, frames in zip(s.blocks, (30, 2)):
        out = editor.ensure_segment(s, b)
        v, a = Path(f"{out}.v.mp4"), Path(f"{out}.a.mp4")
        assert video_frames(v) == b["frames"] == frames
        assert b"moof" in v.read_bytes() and b"moof" in a.read_bytes()                # fragmented, for Media Source
        assert abs(audio_seconds(a) - (b["dur"] + 1024 / 48000)) < 1024 / 48000        # the block's sound after the encoder's first 1024 samples
    sil = blanks.silences_energy(f"{editor.ensure_segment(s, s.blocks[0])}.a.mp4", 0.0, 1.0)   # the source is silent from 2.0: the second half
    assert len(sil) == 1 and abs(sil[0][0] - (0.5 + 1024 / 48000)) < 0.07


def test_segment_cached_by_its_block(media, spec_file, monkeypatch):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 1}]}))
    a = editor.ensure_segment(s, s.blocks[0])
    monkeypatch.setattr(editor.ffmpeg, "run", lambda args, **kw: pytest.fail("built again"))
    s2 = Spec(s.data, s.path)
    assert editor.ensure_segment(s2, s2.blocks[0]) == a


def test_segments_endpoint_lists_the_blocks_and_serves_them(server):
    """The page sends its blocks, saved or not, and gets each block's pieces; after an edit only the changed block is new."""
    saved = server["spec"].read_text()
    data = json.loads(saved)
    st, j = post(server["url"] + "/api/segs", data)
    assert st == 200 and [round(x["start"], 3) for x in j["segs"]] == [0.0, 1.5] and j["segs"][-1]["end"] == pytest.approx(2.5)
    st, h, body = ready(server["url"] + f"/seg/{j['segs'][1]['key']}.v.mp4")
    assert st == 200 and h["Content-Type"] == "video/mp4" and b"moof" in body
    st, h, body = ready(server["url"] + f"/seg/{j['segs'][1]['key']}.a.mp4")
    assert st == 200 and h["Content-Type"] == "audio/mp4" and b"moof" in body
    data["blocks"][0]["out"] = 1.5
    st, j2 = post(server["url"] + "/api/segs", data)
    assert j2["segs"][0]["key"] != j["segs"][0]["key"] and j2["segs"][1]["key"] == j["segs"][1]["key"]
    assert server["spec"].read_text() == saved                                         # nothing written


def test_layout_endpoint_places_texts_as_the_render(server):
    """The page draws each text from the renderer's own lines and positions (wrapped, inside the safe zone)."""
    long = "un sous-titre bien trop long pour tenir sur une seule ligne de la zone"
    req = urllib.request.Request(server["url"] + "/api/layout", data=json.dumps([{"text": long, "t": 0, "dur": 1}, {"text": "salut", "t": 0, "dur": 1}]).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        lay = json.loads(r.read())
    assert len(lay[0]["lines"]) >= 2 and len(lay[1]["lines"]) == 1
    assert all(x >= 150 for x, top, line in lay[0]["lines"]) and lay[1]["lines"][0][2] == "SALUT"
    assert lay[0]["lines"][-1][1] + lay[0]["asc"] == pytest.approx(1432)        # the last baseline on y_baseline


def test_catalog_and_payload_carry_the_new_tracks(server):
    d = json.loads(get(server["url"] + "/api/catalog")[2])
    assert "dissolve" in d["transitions"] and "shake" in d["effects"] and "warm" in d["filters"] and d["animations"]["in"] and d["blends"] and d["masks"]
    assert d["grade"] and "roles" in d and "transition_duration" in d and d["looks"]["warm"]["temperature"] == 7200
    p = json.loads(get(server["url"] + "/api/spec")[2])
    assert p["sounds"] == [] and p["markers"] == [] and p["effects"] == [] and p["canvas"] == {"background": None} and "catalog" in p
    b = p["timeline"]["blocks"][0]
    assert b["still"] is False and b["cut_dur"] == 0.3 and b["fit"] == "cover" and b["rotate"] == 0


def test_import_copies_the_file_next_to_the_spec_and_adds_a_source(server, media):
    body = Path(media["still"]).read_bytes()
    boundary = b"----kanton"
    data = (b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"logo.png\"\r\nContent-Type: image/png\r\n\r\n" + body
            + b"\r\n--" + boundary + b"--\r\n")
    req = urllib.request.Request(server["url"] + "/api/import", data=data, method="POST", headers={"Content-Type": "multipart/form-data; boundary=" + boundary.decode()})
    with urllib.request.urlopen(req) as r:
        j = json.loads(r.read())
    assert j["key"] == "logo" and j["spec"]["sources"]["logo"] == "media/logo.png"
    assert (server["spec"].parent / "media" / "logo.png").read_bytes() == body
    assert json.loads(server["spec"].read_text())["sources"]["logo"] == "media/logo.png"
    assert j["sources"]["logo"]["kind"] == "image"


def test_import_accepts_an_audio_only_file(server, media):
    body = Path(media["music"]).read_bytes()
    boundary = b"----kanton"
    data = (b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"my song.wav\"\r\nContent-Type: audio/wav\r\n\r\n" + body
            + b"\r\n--" + boundary + b"--\r\n")
    req = urllib.request.Request(server["url"] + "/api/import", data=data, method="POST", headers={"Content-Type": "multipart/form-data; boundary=" + boundary.decode()})
    with urllib.request.urlopen(req) as r:
        j = json.loads(r.read())
    s = j["sources"][j["key"]]
    assert s["kind"] == "audio" and s["has_audio"] and s["duration"] > 9


def test_audio_tab_has_an_import_button():
    html = (Path(editor.__file__).parent / "index.html").read_text()
    assert 'id="bImportAudio"' in html and 'id="audIn"' in html and 'accept="audio/*' in html


def test_player_pieces_follow_stills_reverse_rotation_and_fit(media, spec_file):
    """The page plays what the server cut: a frozen frame stays put, a reversed block runs backwards, a rotated or letterboxed
    block keeps its geometry (the page only adds zoom, flip and the layers)."""
    from conftest import probe_frame_mean
    s = Spec.load(spec_file({"sources": {"redblue": str(media["redblue"]), "wide": str(media["wide"]), "still": str(media["still"])},
                             "blocks": [{"src": "redblue", "in": 0, "out": 2, "reverse": True}, {"src": "redblue", "at": 0.5, "dur": 1},
                                        {"src": "wide", "in": 0, "out": 0.5, "rotate": 90}, {"src": "wide", "in": 0, "out": 0.5, "fit": "contain", "background": "#00FF00"},
                                        {"src": "still", "dur": 0.5}]}))
    pieces = [editor.ensure_segment(s, b) for b in s.blocks]
    v = lambda k: Path(f"{pieces[k]}.v.mp4")
    assert all(video_frames(v(k)) == b["frames"] for k, b in enumerate(s.blocks))
    assert probe_frame_mean(v(0), 0.2)[2] > 150 and probe_frame_mean(v(0), 1.8)[0] > 150                                  # backwards: blue first
    assert probe_frame_mean(v(1), 0.1)[0] > 150 and probe_frame_mean(v(1), 0.9)[0] > 150                                  # frozen on a red frame
    assert probe_frame_mean(v(2), 0.2, (150, 50, 200, 200))[0] > 150 and probe_frame_mean(v(2), 0.2, (150, 700, 200, 200))[2] > 150   # 90°: red on top
    assert probe_frame_mean(v(3), 0.2, (50, 50, 440, 200))[1] > 150                                                       # the background above the letterbox
    assert probe_frame_mean(v(4), 0.2)[1] > 150                                                                           # the still
    s2 = Spec({**s.data, "blocks": [{**s.data["blocks"][0], "reverse": False}]}, s.path)
    assert editor.seg_key(s, s.blocks[0]) != editor.seg_key(s2, s2.blocks[0])


def test_friendly_names_the_new_mistakes():
    assert editor.friendly("kanton: unknown transition 'swoosh' (see `kanton catalog transitions`)") == "Unknown transition swoosh."
    assert editor.friendly("kanton: unknown filter 'nope' (see `kanton catalog filters`)") == "Unknown filter nope."
    assert editor.friendly("kanton: unknown effect 'nope' (see `kanton catalog effects`)") == "Unknown effect nope."
    assert editor.friendly("kanton: unknown blend 'burn' for overlay x (screen, multiply)") == "Unknown blend mode burn."
    assert editor.friendly("kanton: block 1: fit must be cover or contain: 'stretch'") == "Clip 2: fit must be cover or contain."
    assert editor.friendly("kanton: unknown export format 'avi' (mp4, mov, webm, gif)") == "Unknown export format avi."


def _write_outside(server, zoom):
    """Another process (an agent) rewrites the spec on disk."""
    d = json.loads(Path(server["spec"]).read_text()); d["blocks"][1]["zoom"] = zoom
    Path(server["spec"]).write_text(json.dumps(d))


def test_spec_reloads_when_the_file_changes_on_disk(server):
    """The file is the truth: the server serves what is on disk, with a version marker the page can poll."""
    d0 = json.loads(get(server["url"] + "/api/spec")[2])
    v0 = json.loads(get(server["url"] + "/api/version")[2])["version"]
    assert d0["version"] == v0 and v0
    _write_outside(server, 110)
    v1 = json.loads(get(server["url"] + "/api/version")[2])["version"]
    d1 = json.loads(get(server["url"] + "/api/spec")[2])
    assert v1 != v0 and d1["version"] == v1 and d1["spec"]["blocks"][1]["zoom"] == 110


def test_version_is_a_string_so_javascript_cannot_round_it(server):
    """mtime_ns is above 2**53: as a JSON number the page rounds it, sends back another value, and every save is a 409."""
    d0 = json.loads(get(server["url"] + "/api/spec")[2])
    assert isinstance(d0["version"], str)
    assert isinstance(json.loads(get(server["url"] + "/api/version")[2])["version"], str)
    _write_outside(server, 110)
    st, j = put(server["url"] + "/api/spec", d0["spec"], headers={"X-Version": d0["version"]})
    assert st == 409 and isinstance(j["version"], str)


def test_half_written_file_keeps_the_last_good_spec(server):
    d0 = json.loads(get(server["url"] + "/api/spec")[2])
    Path(server["spec"]).write_text('{"kanton": 1, "blocks": [')
    d1 = json.loads(get(server["url"] + "/api/spec")[2])
    assert d1["version"] == d0["version"] and d1["spec"]["blocks"][1]["zoom"] == 104


def test_put_refuses_to_overwrite_a_file_changed_since_the_page_loaded_it(server):
    d0 = json.loads(get(server["url"] + "/api/spec")[2])
    _write_outside(server, 110)
    st, j = put(server["url"] + "/api/spec", d0["spec"], headers={"X-Version": str(d0["version"])})
    assert st == 409 and "changed on disk" in j["error"] and j["version"] != d0["version"]
    assert json.loads(Path(server["spec"]).read_text())["blocks"][1]["zoom"] == 110        # untouched
    st, j = put(server["url"] + "/api/spec", d0["spec"], headers={"X-Version": str(j["version"])})
    assert st == 200 and json.loads(Path(server["spec"]).read_text())["blocks"][1]["zoom"] == 104
    assert b"/api/version" in get(server["url"] + "/")[2]                                   # the page polls it


def test_pieces_and_strips_answer_202_while_they_are_built(server, monkeypatch):
    """A long build (a 4K source) must not hold the request: the browser drops it after a minute ("Load failed").
    The server answers 202 at once and the page asks again."""
    import kanton.editor as ed
    slow_seg, slow_strip = ed.ensure_segment, ed.ensure_strip
    monkeypatch.setattr(ed, "ensure_segment", lambda spec, b: (time.sleep(0.8), slow_seg(spec, b))[1])
    monkeypatch.setattr(ed, "ensure_strip", lambda spec, key: (time.sleep(0.8), slow_strip(spec, key))[1])
    spec = json.loads(get(server["url"] + "/api/spec")[2])["spec"]
    req = urllib.request.Request(server["url"] + "/api/segs", data=json.dumps(spec).encode(), method="POST")
    key = json.loads(urllib.request.urlopen(req).read())["segs"][0]["key"]
    def status(path):
        try:
            return get(server["url"] + path)[0]
        except urllib.error.HTTPError as e:
            return e.code
    assert status(f"/seg/{key}.v.mp4") == 202 and status("/strip/face.jpg") == 202
    for path in (f"/seg/{key}.v.mp4", "/strip/face.jpg"):
        for _ in range(100):
            if status(path) == 200:
                break
            time.sleep(0.1)
        assert status(path) == 200
    assert b"202" in get(server["url"] + "/")[2]                                              # the page knows to ask again


def test_long_blocks_are_cut_into_pieces_for_the_player(server, monkeypatch, tmp_path):
    """One piece per block would be hundreds of MB for a long clip: WebKit's player refuses it ("quota exceeded").
    Blocks are cut every CHUNK seconds of reel; a speed keeps the cut in reel time."""
    import kanton.editor as ed
    from kanton import ffmpeg
    monkeypatch.setattr(ed, "CHUNK", 2.0)
    st, j = post(server["url"] + "/api/segs", {"sources": {"face": str(json.loads(server["spec"].read_text())["sources"]["face"])},
                                              "blocks": [{"src": "face", "in": 0, "out": 5}, {"src": "face", "in": 0, "out": 5, "speed": 2}]})
    segs = j["segs"]
    assert [(round(s["start"], 3), round(s["end"], 3)) for s in segs] == [(0, 2), (2, 4), (4, 5), (5, 7), (7, 7.5)]
    assert len({s["key"] for s in segs}) == 5
    code, h, body = ready(server["url"] + f"/seg/{segs[1]['key']}.v.mp4")
    assert code == 200 and b"moof" in body
    (tmp_path / "p.mp4").write_bytes(body)
    assert ffmpeg.probe(str(tmp_path / "p.mp4"))["duration"] == pytest.approx(2.0, abs=0.05)
    assert b"AHEAD" in get(server["url"] + "/")[2]                                                   # the page keeps a window
