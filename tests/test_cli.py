"""The command line, run as `python -m kanton`."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run(*args, env=None, cwd=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "kanton", *map(str, args)], capture_output=True, text=True, env=e, cwd=cwd)


def spec_for(media, tmp_path, name="montage.json", **over):
    d = {"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 104}],
         "texts": [{"text": "salut", "t": 0.0, "dur": 1.0}]}
    d.update(over)
    p = tmp_path / name
    p.write_text(json.dumps(d))
    return p


def test_help_lists_commands():
    r = run("--help")
    assert r.returncode == 0
    for cmd in ("render", "preview", "show", "transcribe", "captions", "blanks", "qa", "probe", "clean", "edit"):
        assert cmd in r.stdout
    assert "import-fcproj" not in r.stdout                      # easter egg: works, not advertised


def test_show(media, tmp_path):
    r = run("show", spec_for(media, tmp_path))
    assert r.returncode == 0 and "2 blocks" in r.stdout and "zoom 104" in r.stdout


def test_render_and_preview_default_names(media, tmp_path):
    p = spec_for(media, tmp_path)
    r = run("preview", p)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "montage-proxy.mp4").exists()
    r = run("render", p, "-o", tmp_path / "out" / "final.mp4")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "final.mp4").exists()


def test_probe(media):
    r = run("probe", media["face"])
    assert r.returncode == 0 and "1080" in r.stdout and "1920" in r.stdout


def test_transcribe_with_python_backend(media, tmp_path):
    stub = tmp_path / "mlx_whisper.py"
    stub.write_text("def transcribe(path, **kw):\n    return {'segments': [{'words': [{'word': ' salut', 'start': 0.1, 'end': 0.4}, {'word': ' toi', 'start': 0.5, 'end': 0.7}]}]}\n")
    out = tmp_path / "words.json"
    r = run("transcribe", media["face"], "--from", "1", "--to", "2", "--backend", "python", "--python", sys.executable, "-o", out, env={"PYTHONPATH": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    w = json.loads(out.read_text())
    assert [x["word"] for x in w] == ["salut", "toi"] and w[0]["start"] == pytest.approx(1.1)


def test_captions_from_words_file(media, tmp_path):
    p = spec_for(media, tmp_path, texts=[{"text": "TITRE", "t": 0, "dur": 1, "role": "title"}])
    words = tmp_path / "w.json"
    words.write_text(json.dumps([{"word": "salut", "start": 0.6, "end": 0.9}, {"word": "toi", "start": 1.0, "end": 1.3}, {"word": "là", "start": 3.2, "end": 3.5}]))
    r = run("captions", p, "--words", f"face={words}")
    assert r.returncode == 0, r.stderr
    d = json.loads(p.read_text())
    caps = [t for t in d["texts"] if t.get("role") == "caption"]
    assert [c["text"] for c in caps] == ["salut toi", "là"] and any(t["text"] == "TITRE" for t in d["texts"])
    assert "2 captions" in r.stdout


def test_captions_refresh_from_srt_and_force(media, tmp_path):
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.5, "out": 4.0}])
    words = tmp_path / "w.json"
    words.write_text(json.dumps([{"word": "salut", "start": 0.6, "end": 0.9}, {"word": "toi", "start": 1.0, "end": 1.3}]))
    assert run("captions", p, "--words", f"face={words}").returncode == 0
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nbonjour tout le monde\n")
    r = run("captions", p, "--words", f"face={srt}")                         # an .srt replaces the captions and is kept in the spec
    assert r.returncode == 0, r.stderr
    d = json.loads(p.read_text())
    caps = [t["text"] for t in d["texts"] if t.get("role") == "caption"]
    assert caps == ["bonjour tout le monde"] and [w["word"] for w in d["words"]["face"]] == ["bonjour", "tout", "le", "monde"]
    stub = tmp_path / "mlx_whisper.py"                                       # --force transcribes again, whatever the spec holds
    stub.write_text("def transcribe(path, **kw):\n    return {'segments': [{'words': [{'word': ' encore', 'start': 1.0, 'end': 1.4}]}]}\n")
    r = run("captions", p, "--force", "--backend", "python", "--python", sys.executable, env={"PYTHONPATH": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    d = json.loads(p.read_text())
    assert [t["text"] for t in d["texts"] if t.get("role") == "caption"] == ["encore"]


def test_blanks_dry_and_apply(media, tmp_path):
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.0, "out": 6.0}])
    before = p.read_text()
    r = run("blanks", p, "--mode", "energy", "--db", "-35", "--dry")
    assert r.returncode == 0 and p.read_text() == before and "removed" in r.stdout
    r = run("blanks", p, "--mode", "energy", "--db", "-35")
    assert r.returncode == 0, r.stderr
    d = json.loads(p.read_text())
    assert "blocks_raw" in d and len(d["blocks"]) == 3


def test_blanks_words_mode_from_file(media, tmp_path):
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 1.0, "out": 5.0}])
    words = tmp_path / "w.json"
    words.write_text(json.dumps([{"word": "a", "start": 1.2, "end": 1.9}, {"word": "b", "start": 3.1, "end": 3.9}]))
    r = run("blanks", p, "--mode", "words", "--words", f"face={words}")
    assert r.returncode == 0, r.stderr
    assert len(json.loads(p.read_text())["blocks"]) == 2


def test_qa_exit_codes(media, tmp_path):
    p = spec_for(media, tmp_path)
    r = run("qa", p)
    assert r.returncode == 0 and "fragments" in r.stdout
    bad = spec_for(media, tmp_path, name="bad.json", blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 90}])
    r = run("qa", bad)
    assert r.returncode == 1 and "zoom_border" in r.stdout
    out = tmp_path / "x.mp4"
    assert run("render", p, "-o", out).returncode == 0
    r = run("qa", p, "--export", out)
    assert r.returncode == 0 and "loudness" in r.stdout


def test_import_fcproj_minimal(tmp_path):
    T = 254016000000
    proj = {"project": {"items": {
        "1": {"kind": {"Media": {"media": {"File": {"path": "/rush/a.mp4"}}, "info": {"kind": "Movie", "video": {"width": 1080, "height": 1920, "has_alpha": False}, "audio_streams": [{}]}}}},
        "2": {"kind": {"Sequence": {"settings": {"width": 1080, "height": 1920, "frame_rate": {"num": 30, "den": 1}},
                                    "video_tracks": [{"name": "V1", "items": [{"id": 5, "item": 1, "name": "a.mp4", "start": 0, "duration": 2 * T, "source_in": 3 * T, "speed": 1.0, "enabled": True, "effects": [{"effect": "motion", "params": {"scale": {"value": {"Float": 100.0}}, "position": {"value": {"Vec2": {"x": 540.0, "y": 960.0}}}}}]}], "transitions": []}],
                                    "audio_tracks": [{"name": "A1", "items": [], "transitions": []}], "caption_tracks": []}}}}}}
    f = tmp_path / "p.fcproj"
    f.write_text(json.dumps(proj))
    r = run("import-fcproj", f, "-o", tmp_path / "s.json", "--preset", "default")
    assert r.returncode == 0, r.stderr
    d = json.loads((tmp_path / "s.json").read_text())
    assert d["blocks"] == [{"src": "a", "in": 3.0, "out": 5.0, "zoom": 100}] and d["preset"] == "default"


def test_clean_removes_tmp(media, tmp_path):
    p = spec_for(media, tmp_path)
    assert run("preview", p).returncode == 0
    assert (tmp_path / ".kanton-tmp").exists()
    assert run("clean", p).returncode == 0
    assert not (tmp_path / ".kanton-tmp").exists()


def test_frame(media, tmp_path):
    from PIL import Image
    out = tmp_path / "f.png"
    r = run("frame", media["broll"], "--at", "1.5", "-o", out)
    assert r.returncode == 0, r.stderr
    im = Image.open(out).convert("RGB")
    assert im.size == (1620, 2880) and im.getpixel((10, 10))[2] > 200


def test_new_writes_one_block_per_clip(media, tmp_path):
    out = tmp_path / "m.json"
    r = run("new", media["face"], media["broll"], "-o", out, "--preset", "default")
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert list(d["sources"]) == ["face", "broll"] and d["preset"] == "default"
    assert [b["src"] for b in d["blocks"]] == ["face", "broll"] and d["blocks"][0]["in"] == 0 and 5.9 < d["blocks"][0]["out"] < 6.1 and d["blocks"][1]["out"] == pytest.approx(3.0, abs=0.1)
    r = run("new", media["face"], cwd=tmp_path)
    assert r.returncode == 0 and (tmp_path / "montage.json").exists()


def test_catalog_lists_names_for_agents():
    r = run("catalog")
    assert r.returncode == 0 and "dissolve" in r.stdout and "shake" in r.stdout and "vintage" in r.stdout and "slide_up" in r.stdout
    r = run("catalog", "transitions")
    assert r.returncode == 0 and "wipeleft" in r.stdout.split() and "shake" not in r.stdout
    r = run("catalog", "--json")
    assert "circle" in json.loads(r.stdout)["masks"]
    assert run("catalog", "nope").returncode != 0


def test_render_format_and_cover_flags(media, tmp_path):
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.5, "out": 1.0}])
    r = run("render", p, "--format", "gif", "--cover", "0.2")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "montage.gif").exists() and (tmp_path / "montage.png").exists()
    r = run("show", spec_for(media, tmp_path, name="mk.json", markers=[{"t": 0.5, "label": "hook"}], blocks=[{"src": "face", "dur": 1}]))
    assert r.returncode == 0 and "marker    0.50s hook" in r.stdout and "still" in r.stdout


def test_help_lists_new_commands():
    r = run("--help")
    assert "new" in r.stdout and "catalog" in r.stdout
