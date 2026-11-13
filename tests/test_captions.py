"""Words (with times) → caption cues: grouping on pauses and width, timing rules, snapping to cuts."""
import pytest

from kanton.captions import apply_captions, build_cues, words_to_reel
from kanton.spec import Spec
from kanton.text import TextStyle

STYLE = TextStyle({"font": "Anton-Regular.ttf", "size": 60, "stroke_width": 8, "max_width": 780, "uppercase": True, "max_lines": 2})
CFG = {"gap": 0.35, "min_frames": 25, "max_frames": 210, "snap_frames": 16, "lead_frames": 1, "tail": 0.3, "join_gap": 0.6}


def W(text, t, dur=0.25):
    out, x = [], t
    for w in text.split():
        out.append({"word": w, "start": x, "end": x + dur})
        x += dur + 0.05
    return out


def test_pause_splits_cues():
    words = W("salut tout le monde", 0.0) + W("ça va", 3.0)
    cues = build_cues(words, STYLE, cuts=[], fps=30, cfg=CFG)
    assert [c["text"] for c in cues] == ["salut tout le monde", "ça va"]
    assert cues[0]["t"] == pytest.approx(0.0)
    assert cues[1]["t"] == pytest.approx(3.0 - 1 / 30)            # lead of one frame


def test_long_sentence_splits_by_width_two_lines_max():
    words = W("tout le monde s'en fout de ton allure du dimanche et de ton dénivelé du samedi matin", 0.0)
    cues = build_cues(words, STYLE, cuts=[], fps=30, cfg=CFG)
    assert len(cues) >= 2
    for c in cues:
        lines = STYLE.wrap(c["text"])
        assert len(lines) <= 2 and all(STYLE.width(l) <= 780 for l in lines)
    assert " ".join(c["text"] for c in cues) == "tout le monde s'en fout de ton allure du dimanche et de ton dénivelé du samedi matin"


def test_contiguous_when_pause_is_short_tail_when_long():
    words = W("une phrase", 0.0) + W("la suite", 1.0) + W("bien plus tard", 5.0)
    cues = build_cues(words, STYLE, cuts=[], fps=30, cfg=CFG)
    assert len(cues) == 3
    assert cues[0]["t"] + cues[0]["dur"] == pytest.approx(cues[1]["t"])            # joined: ends when the next starts
    last_word_end = 1.0 + 0.25 + 0.05 + 0.25
    assert abs(cues[1]["t"] + cues[1]["dur"] - (last_word_end + 0.3)) <= 1 / 60      # tail after a long pause, to the frame


def test_min_duration_extended():
    words = W("oui", 0.0, dur=0.2)
    cues = build_cues(words, STYLE, cuts=[], fps=30, cfg=CFG)
    assert cues[0]["dur"] * 30 >= 25


def test_max_duration_splits():
    words = W(" ".join(["mot"] * 30), 0.0, dur=0.25)      # 9 s of speech without pause
    cues = build_cues(words, STYLE, cuts=[], fps=30, cfg=CFG)
    assert all(c["dur"] * 30 <= 210 + 1 for c in cues)


def test_snap_to_cuts():
    words = W("avant la coupe", 0.0) + W("après la coupe", 1.55)
    cues = build_cues(words, STYLE, cuts=[1.5], fps=30, cfg=CFG)
    assert cues[1]["t"] == pytest.approx(1.5)
    assert cues[0]["t"] + cues[0]["dur"] == pytest.approx(1.5)
    far = build_cues(W("avant", 0.0) + W("après", 3.0), STYLE, cuts=[1.5], fps=30, cfg=CFG)
    assert far[1]["t"] == pytest.approx(3.0 - 1 / 30)


def test_times_rounded_to_frames():
    cues = build_cues(W("salut toi", 0.123), STYLE, cuts=[], fps=30, cfg=CFG)
    for c in cues:
        assert abs(c["t"] * 30 - round(c["t"] * 30)) < 1e-6 and abs(c["dur"] * 30 - round(c["dur"] * 30)) < 1e-6


def test_words_to_reel_through_blocks(media, spec_file):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.0, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "speed": 2}]}))
    words = [{"word": "a", "start": 0.5, "end": 0.7}, {"word": "b", "start": 1.2, "end": 1.4}, {"word": "c", "start": 1.9, "end": 2.05}, {"word": "d", "start": 3.5, "end": 3.7}]
    out = words_to_reel(s, "face", words)
    assert [w["word"] for w in out] == ["b", "c", "d"]
    assert out[0]["start"] == pytest.approx(0.2) and out[1]["end"] == pytest.approx(1.0)   # clamped to the block end
    assert out[2]["start"] == pytest.approx(1.25) and out[2]["end"] == pytest.approx(1.35)


def test_apply_replaces_captions_keeps_titles(media, spec_file):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 3.0}],
                   "texts": [{"text": "TITRE", "t": 0, "dur": 1, "role": "title"}, {"text": "old", "t": 0, "dur": 1, "role": "caption"}]})
    s = Spec.load(p)
    n = apply_captions(s, {"face": W("salut tout le monde", 0.5)})
    s.save()
    s2 = Spec.load(p)
    assert n == 1
    roles = [t["role"] for t in s2.texts]
    assert roles.count("title") == 1 and roles.count("caption") == 1
    assert [t["text"] for t in s2.texts if t["role"] == "caption"] == ["salut tout le monde"]


def test_cue_stays_inside_its_shot():
    """A cue never crosses a cut: speech running over a cut gives two cues, and tail / min length stop at the cut."""
    words = W("un deux trois", 0.0) + W("quatre cinq", 0.9)          # no pause at the cut (0.88)
    cues = build_cues(words, STYLE, cuts=[0.88, 2.0], fps=30, cfg=CFG)
    assert [c["text"] for c in cues] == ["un deux trois", "quatre cinq"]
    assert cues[0]["t"] + cues[0]["dur"] <= 0.88 + 1 / 60 and cues[1]["t"] >= 0.88 - 1 / 60
    assert cues[1]["t"] + cues[1]["dur"] <= 2.0 + 1 / 60
    short = build_cues(W("oui", 0.0, dur=0.2) + W("non", 2.0), STYLE, cuts=[0.5], fps=30, cfg=CFG)
    assert short[0]["t"] + short[0]["dur"] <= 0.5 + 1 / 60                # min length does not spill into the next shot


def test_split_for_width_is_balanced_no_orphan():
    """Text too long for one cue is split into even parts, never a full cue plus one leftover word."""
    n = 1
    while build_cues(W(" ".join(["mot"] * (n + 1)), 0.0, dur=0.1), STYLE, cuts=[], fps=30, cfg=CFG).__len__() == 1:
        n += 1
    cues = build_cues(W(" ".join(["mot"] * (n + 1)), 0.0, dur=0.1), STYLE, cuts=[], fps=30, cfg=CFG)
    sizes = [len(c["text"].split()) for c in cues]
    assert len(sizes) == 2 and max(sizes) - min(sizes) <= 1


def test_word_mostly_outside_the_block_is_dropped(media, spec_file):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.0, "out": 2.0}]}))
    words = [{"word": "avant", "start": 0.6, "end": 1.05}, {"word": "dedans", "start": 1.2, "end": 1.5}, {"word": "après", "start": 1.9, "end": 2.4}]
    assert [w["word"] for w in words_to_reel(s, "face", words)] == ["dedans"]


def test_karaoke_and_typewriter_styles_write_the_words_into_the_cues(media, spec_file):
    """captions.style karaoke / typewriter: each cue carries its words in reel time (clamped to the cue), so the renderer can
    light the word being said or type them; plain (default) writes no words."""
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.0, "out": 4.0}], "preset": {"captions": {"style": "karaoke"}, "text": {"highlight": {"fill": "#FFD400"}}}})
    s = Spec.load(p)
    apply_captions(s, {"face": W("salut tout le monde", 1.5)})
    cue = s.data["texts"][0]
    assert [w["word"] for w in cue["words"]] == ["salut", "tout", "le", "monde"] and cue["words"][0]["t"] == pytest.approx(0.5)
    assert cue["words"][1]["dur"] == pytest.approx(0.25) and "reveal" not in cue and "highlight" not in cue      # the preset lights the word
    assert all(cue["t"] - 1e-9 <= w["t"] and w["t"] + w["dur"] <= cue["t"] + cue["dur"] + 1e-9 for w in cue["words"])
    s.data["captions"] = {"style": "typewriter"}
    s = Spec(s.data, s.path)
    apply_captions(s, {"face": W("salut tout le monde", 1.5)})
    assert s.data["texts"][0]["reveal"] == "word" and len(s.data["texts"][0]["words"]) == 4
    s.data["captions"] = {"style": "plain"}
    s = Spec(s.data, s.path)
    apply_captions(s, {"face": W("salut tout le monde", 1.5)})
    assert "words" not in s.data["texts"][0] and "reveal" not in s.data["texts"][0]
    s2 = Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 1.0, "out": 4.0}], "preset": {"captions": {"style": "karaoke"}}}, "k.json"))
    apply_captions(s2, {"face": W("salut", 1.5)})
    assert s2.data["texts"][0]["highlight"] == {"scale": 1.12}                                                   # no preset highlight: the word pops


def test_no_one_frame_junk_cue_from_snapping_across_a_neighbour():
    """A cut far before a cue used to drag its start onto the cut, over the previous cue: two cues at the same t, the first one frame long."""
    words = W("un", 0.0) + W("deux", 2.621) + W("trois quatre", 3.6)
    cues = build_cues(words, STYLE, cuts=[2.6], fps=30, cfg=CFG)
    assert [c["text"] for c in cues] == ["un", "deux", "trois quatre"]
    assert len({round(c["t"], 4) for c in cues}) == len(cues)
    assert all(c["dur"] > 5 / 30 for c in cues)


def test_word_a_frame_before_a_cut_belongs_to_the_next_shot():
    cues = build_cues(W("un", 0.0) + W("deux", 4.86), STYLE, cuts=[4.8667], fps=30, cfg=CFG)
    assert cues[-1]["dur"] > 0.5
