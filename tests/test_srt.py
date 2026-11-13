import json

from kanton.transcribe import words_from_srt, words_from_json

SRT = "﻿1\r\n00:00:01,000 --> 00:00:03,000\r\n<i>Bonjour</i> tout le monde\r\n\r\n2\r\n00:01:00,500 --> 00:01:01,000\r\nSalut\r\n"


def test_srt_words_share_cue_span():
    ws = words_from_srt(SRT)
    assert [w["word"] for w in ws] == ["Bonjour", "tout", "le", "monde", "Salut"]
    assert ws[0]["start"] == 1.0 and ws[3]["end"] == 3.0
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(ws, ws[1:]))
    assert ws[4]["start"] == 60.5 and ws[4]["end"] == 61.0


def test_srt_through_words_from_json(tmp_path):
    f = tmp_path / "a.srt"
    f.write_text(SRT, encoding="utf-8")
    assert len(words_from_json(f)) == 5


def _captions(media, spec_file, body, sources=("face",)):
    import threading
    import time
    import urllib.request
    from kanton import editor
    from kanton.spec import Spec
    from test_editor import free_port
    p = spec_file({"sources": {s: str(media["face"]) for s in sources},
                   "blocks": [{"src": s, "in": 0.0, "out": 2.0} for s in sources]})
    port = free_port()
    srv = editor.make_server(Spec.load(p), port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def call(data=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/captions", data=data, method="POST" if data else "GET")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        j = call(json.dumps(body).encode())
        for _ in range(100):
            if j["status"] != "running":
                break
            time.sleep(0.1)
            j = call()
        return j, json.loads(p.read_text())
    finally:
        srv.shutdown()


def test_editor_srt_single_source(media, spec_file):
    j, saved = _captions(media, spec_file, {"srt": SRT})
    assert j["status"] == "done", j
    assert [w["word"] for w in saved["words"]["face"]][:2] == ["Bonjour", "tout"]


def test_editor_srt_multi_source_needs_src(media, spec_file):
    j, _ = _captions(media, spec_file, {"srt": SRT}, sources=("a", "b"))
    assert j["status"] == "error" and "which one" in j["error"]
    j, saved = _captions(media, spec_file, {"srt": SRT, "src": "b"}, sources=("a", "b"))
    assert j["status"] == "done" and "b" in saved["words"] and "a" not in saved["words"]


def test_editor_srt_unknown_src_and_empty(media, spec_file):
    j, _ = _captions(media, spec_file, {"srt": SRT, "src": "nope"})
    assert j["status"] == "error" and "no source" in j["error"]
    j, _ = _captions(media, spec_file, {"srt": "nothing here"})
    assert j["status"] == "error" and "no cue" in j["error"]
