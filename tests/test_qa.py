"""qa: measure a spec and an export against the rules, nothing is modified."""
import pytest

from kanton import qa, transcribe
from kanton.render import render
from kanton.spec import Spec


def mk(media, spec_file, **over):
    d = {"sources": {"face": str(media["face"]), "broll": str(media["broll"]), "music": str(media["music"])},
         "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 104}],
         "texts": [{"text": "salut", "t": 0.0, "dur": 1.0}]}
    d.update(over)
    return Spec.load(spec_file(d))


def levels(findings, rule):
    return [f["level"] for f in findings if f["rule"] == rule]


def test_clean_spec_has_no_errors(media, spec_file):
    f = qa.check_spec(mk(media, spec_file))
    assert not [x for x in f if x["level"] == "error"], f
    assert "ok" in levels(f, "fragments") and "ok" in levels(f, "safe_zone")


def test_fragments_and_short_blocks(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "in": 1, "out": 1.2}]))
    assert levels(f, "fragments") == ["error"]
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "in": 1, "out": 1.5}]))
    assert levels(f, "fragments") == ["ok"] and levels(f, "short_blocks") == ["warn"]


def test_first_frame_must_be_main_track(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, overlays=[{"kind": "video", "src": "broll", "t": 0, "dur": 1}]))
    assert levels(f, "first_frame") == ["error"]
    f = qa.check_spec(mk(media, spec_file, overlays=[{"kind": "video", "src": "broll", "t": 0.5, "dur": 1}]))
    assert levels(f, "first_frame") == ["ok"]


def test_safe_zone_and_text_shape(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, texts=[{"text": "trop haut", "t": 0, "dur": 1, "y": 200}]))
    assert levels(f, "safe_zone") == ["error"]
    f = qa.check_spec(mk(media, spec_file, texts=[{"text": "court", "t": 0, "dur": 0.2}, {"text": "long " * 3, "t": 1, "dur": 9}]))
    assert levels(f, "text_duration") == ["warn"]
    f = qa.check_spec(mk(media, spec_file, texts=[{"text": "supercalifragilisticexpialidociousandmore", "t": 0, "dur": 1}]))
    assert levels(f, "text_width") == ["warn"]
    f = qa.check_spec(mk(media, spec_file, texts=[{"text": "a", "t": 0, "dur": 2}, {"text": "b", "t": 1, "dur": 2}]))
    assert levels(f, "text_overlap") == ["warn"]


def test_zoom_levels(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 90}]))
    assert levels(f, "zoom_border") == ["error"]
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 116}]))
    assert levels(f, "zoom_range") == ["info"]
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": [100, 106]}]))
    assert levels(f, "zoom_range") == ["ok"] and levels(f, "zoom_border") == ["ok"]


def test_music_level_reported(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, music={"src": "music", "gain_db": -10}))
    assert levels(f, "music_level") == ["warn"]
    f = qa.check_spec(mk(media, spec_file, music={"src": "music", "gain_db": -30}))
    assert levels(f, "music_level") == ["info"]


def test_export_checks(media, spec_file, tmp_path):
    s = mk(media, spec_file)
    out = render(s, tmp_path / "full.mp4")
    f = qa.check_export(s, out)
    assert levels(f, "format") == ["ok"] and levels(f, "duration") == ["ok"] and levels(f, "audio_rate") == ["ok"] and levels(f, "file_size") == ["ok"]
    assert levels(f, "loudness") == ["ok"], [x for x in f if x["rule"] == "loudness"]
    assert levels(f, "bitrate") in (["ok"], ["warn"])             # synthetic pattern may sit under the quality floor
    p = render(s, tmp_path / "proxy.mp4", proxy=True)
    assert levels(qa.check_export(s, p), "format") == ["error"]


def test_text_check_against_reference(media, spec_file, monkeypatch, tmp_path):
    s = mk(media, spec_file)
    out = render(s, tmp_path / "t.mp4", proxy=True)
    monkeypatch.setitem(transcribe.BACKENDS, "fake", lambda wav, language, model: [
        {"word": "salut", "start": 0.0, "end": 0.3}, {"word": "tout", "start": 0.4, "end": 0.6}, {"word": "le", "start": 0.7, "end": 0.8}, {"word": "monde", "start": 0.9, "end": 1.2}])
    f = qa.check_text(out, "Salut, tout le monde !", backend="fake")
    assert levels(f, "words") == ["ok"]
    f = qa.check_text(out, "Salut tout le monde entier", backend="fake")
    assert levels(f, "words") == ["error"] and "entier" in [x for x in f if x["rule"] == "words"][0]["msg"]
    monkeypatch.setitem(transcribe.BACKENDS, "fake", lambda wav, language, model: [{"word": "a", "start": 0.0, "end": 0.3}, {"word": "b", "start": 1.0, "end": 1.2}])
    f = qa.check_text(out, "a b", backend="fake")
    assert levels(f, "word_gaps") == ["error"]


def test_report_and_exit_code(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1, "zoom": 90}]))
    txt = qa.report(f)
    assert "✗" in txt and "zoom_border" in txt and qa.has_errors(f)
    assert not qa.has_errors(qa.check_spec(mk(media, spec_file)))


def test_overlays_off_frame_transitions_too_long_and_looks_are_reported(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, overlays=[{"kind": "image", "src": str(media["still"]), "t": 0.5, "dur": 1, "x": 900, "y": 300, "width": 400}]))
    assert levels(f, "overlay_frame") == ["warn"]
    f = qa.check_spec(mk(media, spec_file, overlays=[{"kind": "image", "src": str(media["still"]), "t": 0.5, "dur": 1, "x": 100, "y": 300, "width": 400}]))
    assert levels(f, "overlay_frame") == ["ok"]
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 1}, {"src": "face", "in": 1, "out": 1.4, "cut": "dissolve", "cut_dur": 1.0}]))
    assert levels(f, "transition_length") == ["info"]
    f = qa.check_spec(mk(media, spec_file, grade="cinema", blocks=[{"src": "face", "in": 0, "out": 1, "grade": {"saturation": 0.5}}]))
    assert levels(f, "look") == ["info"]
    assert levels(qa.check_spec(mk(media, spec_file)), "look") == []


def test_curves_voices_and_beats_are_reported_as_hypotheses(media, spec_file):
    f = qa.check_spec(mk(media, spec_file, blocks=[{"src": "face", "in": 0, "out": 2, "speed": "hero", "voice": "robot"}],
                         music={"src": "music", "beats": [0.5, 1.0]}))
    m = " ".join(x["msg"] for x in f if x["rule"] == "hypothesis")
    assert levels(f, "hypothesis") == ["info"] and all(w in m for w in ("speed curve", "voice", "beats"))
    assert levels(qa.check_spec(mk(media, spec_file)), "hypothesis") == []
