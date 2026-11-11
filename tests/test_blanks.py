"""Cutting the blanks: silences (energy) or gaps between words, margins, tiny leftovers, re-timing of texts."""
import json

import pytest

from kanton import blanks
from kanton.spec import Spec

CFG = {"min": 0.30, "pre": 0.08, "post": 0.12, "min_cut": 0.20, "min_keep_frames": 9}


def test_silences_energy_finds_the_two_gaps(media):
    s = blanks.silences_energy(str(media["face"]), 0.0, 6.0, db=-35, min_len=0.3)
    assert len(s) == 2
    assert abs(s[0][0] - 2.0) < 0.06 and abs(s[0][1] - 3.0) < 0.06
    assert abs(s[1][0] - 4.0) < 0.06 and abs(s[1][1] - 4.6) < 0.06


def test_silences_energy_respects_range(media):
    s = blanks.silences_energy(str(media["face"]), 3.5, 6.0, db=-35, min_len=0.3)
    assert len(s) == 1 and abs(s[0][0] - 4.0) < 0.06


def test_gaps_from_words():
    words = [{"word": "a", "start": 0.0, "end": 0.5}, {"word": "b", "start": 0.6, "end": 1.0}, {"word": "c", "start": 1.5, "end": 2.0}]
    assert blanks.gaps_from_words(words, 0.0, 3.0, 0.3) == [(1.0, 1.5)]
    assert blanks.gaps_from_words(words, 0.0, 3.0, 0.05) == [(0.5, 0.6), (1.0, 1.5)]


def test_cut_block_margins():
    raw = {"src": "face", "in": 0.0, "out": 6.0, "zoom": 106}
    out = blanks.cut_block(raw, [(2.0, 3.0), (4.0, 4.6)], CFG, fps=30)
    assert [(round(b["in"], 2), round(b["out"], 2)) for b in out] == [(0.0, 2.12), (2.92, 4.12), (4.52, 6.0)]
    assert all(b["zoom"] == 106 for b in out)


def test_cut_block_ignores_small_removals_and_merges_tiny_leftovers():
    raw = {"src": "face", "in": 0.0, "out": 3.0}
    assert len(blanks.cut_block(raw, [(1.0, 1.25)], CFG, fps=30)) == 1            # 0.25 − 0.2 margins = 0.05 < min_cut
    out = blanks.cut_block(raw, [(1.0, 1.5), (1.55, 2.2)], CFG, fps=30)            # 0.05 s left between the two cuts (1.5 frames): merged back
    assert len(out) == 2 and all((b["out"] - b["in"]) * 30 >= 9 for b in out)


def test_apply_energy_rebuilds_blocks_and_retimes_texts(media, spec_file):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 6.0, "zoom": 106}],
                   "texts": [{"text": "T", "t": 3.5, "dur": 1.0, "role": "title"}, {"text": "S", "t": 2.5, "dur": 0.3, "role": "title"}],
                   "overlays": [{"kind": "video", "src": str(media["broll"]), "t": 1.0, "dur": 0.5}]})
    s = Spec.load(p)
    rep = blanks.apply_blanks(s, mode="energy", db=-35)
    s.save()
    d = json.loads(p.read_text())
    assert d["blocks_raw"] == [{"src": "face", "in": 0.0, "out": 6.0, "zoom": 106}]
    assert len(d["blocks"]) == 3 and rep["removed"] > 1.0
    s2 = Spec.load(p)
    assert abs(s2.duration - (6.0 - 0.8 - 0.4)) < 0.1
    titles = {t["text"]: t for t in s2.texts}
    assert abs(titles["T"]["t"] - (2.12 + (3.5 - 2.92))) < 0.05           # moved with its words
    assert abs(titles["S"]["t"] - 2.12) < 0.05                             # was in the cut silence: lands on the next kept frame
    assert abs(s2.overlays[0]["t"] - 1.0) < 1e-6


def test_retime_leaves_the_texts_of_an_untouched_block_in_place(media, spec_file):
    # the opening block comes from later in the source; its 2.52 s play as 76 whole frames, a little past its `out`
    hook = {"src": "face", "in": 2.0, "out": 4.52}
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [hook, {"src": "face", "in": 0.0, "out": 1.5}, {"src": "face", "in": 5.0, "out": 6.0}],
                   "texts": [{"text": "A", "t": 1.0, "dur": 76 / 30 - 1.0, "role": "title"}, {"text": "B", "t": 76 / 30, "dur": 0.5, "role": "title"}]})
    s = Spec.load(p)
    s.data["blocks"] = [hook, {"src": "face", "in": 0.0, "out": 0.5}, {"src": "face", "in": 1.0, "out": 1.5}, {"src": "face", "in": 5.0, "out": 6.0}]   # a blank cut further on
    s2 = Spec(s.data, s.path)
    blanks.retime(s2, s.blocks)
    assert [v for t in s2.data["texts"] for v in (t["t"], t["dur"])] == pytest.approx([1.0, 76 / 30 - 1.0, 76 / 30, 0.5], abs=1e-4)


def test_retime_glues_the_texts_to_a_cut_in_a_block_from_later_in_the_source(media, spec_file):
    # what is cut out of the opening block must not send its texts to the next passage of the source (5.0 s, far in the reel)
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 2.0, "out": 4.52}, {"src": "face", "in": 0.0, "out": 1.5}, {"src": "face", "in": 5.0, "out": 6.0}],
                   "texts": [{"text": "A", "t": 1.0, "dur": 76 / 30 - 1.0, "role": "title"}, {"text": "B", "t": 76 / 30, "dur": 0.5, "role": "title"}]})
    s = Spec.load(p)
    s.data["blocks"][0]["out"] = 3.5
    s2 = Spec(s.data, s.path)
    blanks.retime(s2, s.blocks)
    assert [v for t in s2.data["texts"] for v in (t["t"], t["dur"])] == pytest.approx([1.0, 0.5, 1.5, 0.5], abs=1e-4)


def test_retime_texts_follow_their_block_when_blocks_swap(media, spec_file):
    # A ends on the cut, B starts on it: each stays with its own block
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 1.0}, {"src": "face", "in": 3.0, "out": 4.0}],
                   "texts": [{"text": "A", "t": 0.5, "dur": 0.5, "role": "title"}, {"text": "B", "t": 1.0, "dur": 0.5, "role": "title"}]})
    s = Spec.load(p)
    s.data["blocks"].reverse()
    s2 = Spec(s.data, s.path)
    blanks.retime(s2, s.blocks)
    assert [v for t in s2.data["texts"] for v in (t["t"], t["dur"])] == pytest.approx([1.5, 0.5, 0.0, 0.5], abs=1e-4)


def test_apply_is_replayable(media, spec_file):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 6.0}]})
    s = Spec.load(p); blanks.apply_blanks(s, mode="energy", db=-35); s.save()
    first = json.loads(p.read_text())["blocks"]
    s = Spec.load(p); blanks.apply_blanks(s, mode="energy", db=-35); s.save()
    assert json.loads(p.read_text())["blocks"] == first


def test_apply_words_mode(media, spec_file):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.0, "out": 5.0}]})
    s = Spec.load(p)
    words = [{"word": "a", "start": 1.2, "end": 1.9}, {"word": "b", "start": 3.1, "end": 3.9}, {"word": "c", "start": 4.7, "end": 4.9}]
    rep = blanks.apply_blanks(s, mode="words", words_by_src={"face": words})
    assert len(s.blocks) == 3 and [round(b["in"], 2) for b in s.blocks] == [1.0, 3.02, 4.62]
    assert rep["ranges"][0] == (pytest.approx(2.02), pytest.approx(3.02))


def test_simulate_audio_concatenates_kept_blocks(media, spec_file, tmp_path):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 1.0}, {"src": "face", "in": 3.0, "out": 4.0}]}))
    wav = blanks.simulate_audio(s, tmp_path / "sim.wav")
    from kanton import ffmpeg
    assert abs(ffmpeg.probe(str(wav))["duration"] - 2.0) < 0.05
