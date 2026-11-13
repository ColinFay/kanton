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
