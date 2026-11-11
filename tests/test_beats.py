"""Beats of a music (auto beat): onsets on a tempo grid, stored in the spec as `music.beats` (seconds of the file)."""
import json

import pytest

from kanton import beats, cli
from kanton.spec import Spec

from conftest import ff


@pytest.fixture(scope="module")
def kicks(tmp_path_factory):
    """A kick every 0.5 s (120 bpm) from 0.25 s, over a quiet tone, 12 s."""
    p = tmp_path_factory.mktemp("beats") / "kicks.wav"
    ff("-f", "lavfi", "-i", "aevalsrc=exprs='0.8*sin(2*PI*70*t)*exp(-25*mod(t+0.25\\,0.5))+0.05*sin(2*PI*440*t)':s=48000:c=stereo:d=12", p)
    return p


def test_detect_finds_the_kicks(kicks):
    b = beats.detect(str(kicks))
    want = [0.25 + 0.5 * k for k in range(24)]
    assert len(b) >= 22 and all(min(abs(x - w) for w in want) < 0.03 for x in b)
    assert max(min(abs(x - w) for x in b) for w in want[1:-1]) < 0.03          # none missed inside the file


def test_detect_on_silence_finds_nothing(tmp_path):
    p = tmp_path / "quiet.wav"
    ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", p)
    assert beats.detect(str(p)) == []


def test_spec_keeps_the_beats_of_the_music(media, spec_file):
    s = Spec.load(spec_file({"sources": {"face": str(media["face"]), "m": str(media["music"])}, "blocks": [{"src": "face", "in": 0, "out": 1}],
                             "music": {"src": "m", "beats": [1.0, 0.5]}}))
    assert s.music["beats"] == [0.5, 1.0]


def test_cli_beats_writes_them_into_the_spec(media, spec_file, kicks, capsys):
    p = spec_file({"sources": {"face": str(media["face"]), "m": str(kicks)}, "blocks": [{"src": "face", "in": 0, "out": 1}], "music": {"src": "m"}})
    cli.main(["beats", str(p)])
    d = json.loads(p.read_text())
    assert len(d["music"]["beats"]) >= 22 and "120 bpm" in capsys.readouterr().out
    cli.main(["beats", str(kicks)])
    assert len(json.loads(capsys.readouterr().out)) >= 22
