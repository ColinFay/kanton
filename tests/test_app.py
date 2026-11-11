"""The desktop app: bundled binaries lookup, spec creation from a video, the JS API that the window calls, CLI passthrough."""
import json
import sys
from pathlib import Path

import pytest

from kanton import app, ffmpeg
from kanton.spec import Spec


def test_bundled_ffmpeg_lookup(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin" / "ffprobe"
    fake.write_text("#!/bin/sh\necho fake\n"); fake.chmod(0o755)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("KANTON_FFPROBE", raising=False)
    ffmpeg.binary.cache_clear()
    try:
        assert ffmpeg.binary("ffprobe") == str(fake)
    finally:
        ffmpeg.binary.cache_clear()


def test_new_spec_from_video(media, tmp_path):
    out = app.new_spec_from_video(str(media["face"]), tmp_path / "x.json", preset=None)
    d = json.loads(out.read_text())
    assert d["sources"] == {"face": str(media["face"])}
    assert d["blocks"] == [{"src": "face", "in": 0.0, "out": pytest.approx(6.0, abs=0.1)}]
    assert "preset" not in d
    out2 = app.new_spec_from_video(str(media["face"]), tmp_path / "y.json", preset="default")
    assert json.loads(out2.read_text())["preset"] == "default"


def test_api_open_and_new(media, spec_file, tmp_path):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 1}]})
    srv = app.start_server(Spec.load(p), port=0)
    try:
        api = app.Api(srv, window=None)
        assert api.current()["name"] == "montage.json" and api.current()["path"] == str(p)
        q = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1, "out": 2}]}, "other.json")
        r = api.open_spec(str(q))
        assert r["ok"] and r["name"] == "other.json" and srv.state.spec.path == q
        r = api.open_spec(str(tmp_path / "missing.json"))
        assert not r["ok"] and "missing.json" in r["error"]
        r = api.new_from_video(str(media["face"]))
        assert r["ok"] and r["path"].endswith("face.kanton.json") and srv.state.spec.blocks[0]["out"] > 5
        assert api.url().startswith("http://127.0.0.1:") and srv.server_address[1] > 0
    finally:
        srv.shutdown()


def test_main_passes_cli_subcommands_through(capsys):
    with pytest.raises(SystemExit) as e:
        app.main(["--version"])
    assert e.value.code == 0 and "kanton" in capsys.readouterr().out


def test_main_show_subcommand(media, spec_file, capsys):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0, "out": 1}]})
    app.main(["show", str(p)])
    assert "1 blocks" in capsys.readouterr().out


def test_new_from_video_asks_where_to_save_the_project(media, tmp_path):
    """New montage: pick the video, then a save dialog for the project file (where a folder can be created)."""
    import webview
    calls = []

    class Window:
        def create_file_dialog(self, kind, **kw):
            calls.append((kind, kw))
            return [str(media["face"])] if kind == webview.OPEN_DIALOG else str(tmp_path / "reel" / "montage.json")

    srv = app.start_server(None, port=0)
    try:
        api = app.Api(srv, window=Window())
        r = api.new_from_video_dialog()
        assert r["ok"] and r["path"] == str(tmp_path / "reel" / "montage.json") and (tmp_path / "reel" / "montage.json").exists()
        assert calls[1][0] == webview.SAVE_DIALOG and calls[1][1]["save_filename"] == "montage.json" and calls[1][1]["directory"] == str(Path(media["face"]).parent)
        assert srv.state.spec.blocks[0]["out"] > 5
    finally:
        srv.shutdown()


def test_pick_model_dialog_returns_the_folder(tmp_path):
    import webview

    class Window:
        def __init__(self, answer): self.answer = answer
        def create_file_dialog(self, kind, **kw):
            assert kind == webview.FOLDER_DIALOG
            return self.answer

    srv = app.start_server(None, port=0)
    try:
        assert app.Api(srv, window=Window((str(tmp_path),))).pick_model_dialog() == {"ok": True, "path": str(tmp_path)}
        assert app.Api(srv, window=Window(None)).pick_model_dialog() == {"ok": False, "error": "cancelled"}
    finally:
        srv.shutdown()
