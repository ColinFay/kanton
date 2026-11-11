"""Editing by the words: the transcript in the spec, cut / keep / move / copy / fix rebuild the blocks and the captions."""
import copy
import json

import pytest

from kanton import words as W
from kanton.spec import Spec

WORDS = [{"word": "un", "start": 0.5, "end": 0.8}, {"word": "deux", "start": 0.9, "end": 1.2}, {"word": "trois", "start": 1.5, "end": 1.8},
         {"word": "quatre", "start": 2.0, "end": 2.3}, {"word": "cinq", "start": 3.0, "end": 3.3}]


@pytest.fixture
def spec(media, spec_file):
    return Spec.load(spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 4.0, "zoom": 104, "cut": "flash"}],
                                "blocks_raw": [{"src": "face", "in": 0.0, "out": 4.0}], "words": {"face": WORDS},
                                "texts": [{"text": "TITRE", "t": 2.0, "dur": 0.5, "role": "title"}]}))


def kept(s):
    return [e["word"] for e in W.view(s) if e["t"] is not None]


def ranges(s):
    return [(b["in"], b["out"]) for b in s.data["blocks"]]


def captions(s):
    return " ".join(t["text"] for t in s.data["texts"] if t.get("role") == "caption")


def test_view_numbers_the_words_with_their_reel_time(spec):
    v = W.view(spec)
    assert [e["n"] for e in v] == [0, 1, 2, 3, 4] and [e["word"] for e in v] == ["un", "deux", "trois", "quatre", "cinq"]
    assert v[0]["t"] == pytest.approx(0.5)


def test_cut_at_the_middle_of_the_pauses(spec):
    s = W.cut(spec, 1, 1)
    assert ranges(s) == [(0.0, 0.85), (1.35, 4.0)]
    assert kept(s) == ["un", "trois", "quatre", "cinq"] and [e["word"] for e in W.view(s)][1] == "deux"   # still shown, as cut
    assert s.data["blocks"][0]["cut"] == "flash" and "cut" not in s.data["blocks"][1]                    # the transition stays on the first piece
    assert "deux" not in captions(s) and "trois" in captions(s)
    assert "blocks_raw" not in s.data                                                                     # the edit is the new base for blanks


def test_cut_the_last_words_drops_the_tail(spec):
    assert ranges(W.cut(spec, 3, 4)) == [(0.0, 1.9)]


def test_cut_then_keep_gives_the_block_back(spec):
    s = W.keep(W.cut(spec, 1, 2), 1, 2)
    assert ranges(s) == [(0.0, 4.0)] and s.data["blocks"][0]["zoom"] == 104
    assert kept(s) == ["un", "deux", "trois", "quatre", "cinq"]


def test_keep_on_kept_words_changes_nothing(spec):
    assert ranges(W.keep(spec, 0, 4)) == [(0.0, 4.0)]


def test_move_before_a_word(spec):
    s = W.move(spec, 3, 4, before=1)
    assert kept(s) == ["un", "quatre", "cinq", "deux", "trois"]
    assert ranges(s) == [(0.0, 0.85), (1.9, 3.42), (0.85, 1.9)]                # the last word keeps the blanks `post` margin
    assert all(b["zoom"] == 104 for b in s.data["blocks"])
    assert captions(s).split()[:3] == ["un", "quatre", "cinq"]


def test_move_to_the_end(spec):
    s = W.move(spec, 0, 1)
    assert kept(s) == ["trois", "quatre", "cinq", "un", "deux"]


def test_move_refuses_a_target_inside_the_moved_words(spec):
    with pytest.raises(SystemExit, match="inside"):
        W.move(spec, 1, 3, before=2)


def test_a_cut_between_long_pauses_recuts_the_blank_it_creates(spec):
    s = W.cut(spec, 3, 3)                                                    # "quatre": 0.1 s left after "trois", 0.35 s before "cinq"
    assert ranges(s) == [(0.0, 1.9), (2.92, 4.0)]                            # 0.45 s ≥ min: the long side shrinks to `pre`
    assert s.data["blocks"][-1]["out"] == 4.0                                # the 0.7 s after "cinq" was already there: left alone


def test_words_put_back_come_without_their_blanks(spec):
    s = W.keep(W.cut(spec, 3, 4), 3, 4)                                      # the 0.7 s pause between "quatre" and "cinq" comes back
    assert ranges(s) == [(0.0, 2.42), (2.92, 3.42)]                          # ...cut as blanks would, with its margins


def test_fix_changes_the_text_not_the_cut(spec):
    s = W.fix(spec, 1, "DEUX!")
    assert s.data["words"]["face"][1]["word"] == "DEUX!" and ranges(s) == [(0.0, 4.0)]
    assert "DEUX!" in captions(s)


def test_titles_follow_their_words(spec):
    s = W.cut(spec, 1, 1)                                                     # 0.5 s removed before the title
    title = next(t for t in s.data["texts"] if t["role"] == "title")
    assert title["t"] == pytest.approx(1.5, abs=1 / 30)                       # block lengths are whole frames


def fork(s):
    return Spec(copy.deepcopy(s.data), s.path)


def test_copy_plays_the_words_once_more(spec):
    s = W.copy(spec, 3, 4, before=0)                                          # "quatre cinq" also at the start
    assert kept(s) == ["quatre", "cinq", "un", "deux", "trois", "quatre", "cinq"]
    assert [e["o"] for e in W.view(s) if e["n"] == 3] == [0, 1]               # each time a word is played, in reel order
    assert captions(s).split().count("quatre") == 2
    assert all(b["zoom"] == 104 for b in s.data["blocks"])                    # with the settings of the block it comes from


def test_each_copy_is_edited_on_its_own(spec):
    s = W.copy(spec, 3, 4, before=0)
    assert kept(W.cut(fork(s), 3, 3, o=0)) == ["cinq", "un", "deux", "trois", "quatre", "cinq"]
    assert kept(W.cut(fork(s), 3, 3, o=1)) == ["quatre", "cinq", "un", "deux", "trois", "cinq"]
    assert kept(W.move(fork(s), 0, 0, before=4, before_o=1)) == ["quatre", "cinq", "deux", "trois", "quatre", "un", "cinq"]
    t = W.cut(fork(s), 2, 2)                                                  # "trois" is shown, then put back, in the original
    assert [e["word"] for e in W.view(t)][3:6] == ["deux", "trois", "quatre"]
    assert kept(W.keep(t, 2, 2)) == kept(s)


def test_editor_knows_copy(spec):
    from kanton import editor
    assert kept(editor._WORD_OPS["copy"](spec, {"op": "copy", "a": 3, "b": 4, "before": 0, "o": 0}))[:3] == ["quatre", "cinq", "un"]


def test_cli_copy_and_the_times_a_word_is_played(media, tmp_path):
    from test_cli import run, spec_for
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.0, "out": 4.0}], words={"face": WORDS})
    r = run("words", p, "--copy", "3-4", "--before", "0")
    assert r.returncode == 0, r.stderr
    assert "3:quatre 4:cinq 0:un 1:deux 2:trois 3@2:quatre 4@2:cinq" in r.stdout
    r = run("words", p, "--cut", "3@2")                                       # the second time "quatre" is played
    assert r.returncode == 0, r.stderr
    assert "3:quatre 4:cinq 0:un 1:deux 2:trois 4@2:cinq" in r.stdout


def test_bad_ranges(spec):
    with pytest.raises(SystemExit, match="no word"):
        W.cut(spec, 3, 9)
    with pytest.raises(SystemExit, match="kanton words"):
        W.cut(Spec({"sources": spec.data["sources"], "blocks": spec.data["blocks"]}, spec.path), 0, 0)


def test_cli_words_import_print_and_cut(media, tmp_path):
    from test_cli import run, spec_for
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.0, "out": 4.0}])
    wf = tmp_path / "w.json"
    wf.write_text(json.dumps(WORDS))
    r = run("words", p, "--words", f"face={wf}")
    assert r.returncode == 0, r.stderr
    assert "0:un 1:deux" in r.stdout and json.loads(p.read_text())["words"]["face"][0]["word"] == "un"
    r = run("words", p, "--cut", "1-2")
    assert r.returncode == 0, r.stderr
    assert "[1:deux] [2:trois]" in r.stdout
    r = run("words", p, "--fix", "0=Un", "--move", "4", "--before", "0")
    assert r.returncode != 0                                                  # one operation per call
    r = run("words", p, "--move", "4", "--before", "0")
    assert r.returncode == 0, r.stderr
    assert r.stdout.index("4:cinq") < r.stdout.index("0:un")


def test_captions_use_the_corrected_words(media, tmp_path):
    from test_cli import run, spec_for
    fixed = [dict(w) for w in WORDS]
    fixed[0]["word"] = "UNO"
    p = spec_for(media, tmp_path, blocks=[{"src": "face", "in": 0.0, "out": 4.0}], words={"face": fixed})
    r = run("captions", p)
    assert r.returncode == 0, r.stderr
    assert "UNO" in " ".join(t["text"] for t in json.loads(p.read_text())["texts"])


def test_editor_words_endpoint(media, spec_file):
    import threading
    import urllib.request
    from kanton import editor
    from test_editor import free_port
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 4.0}], "words": {"face": WORDS}})
    port = free_port()
    srv = editor.make_server(Spec.load(p), port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/spec") as r:
            assert [e["word"] for e in json.loads(r.read())["words"]][:2] == ["un", "deux"]
        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/words", data=json.dumps(body).encode(), method="POST")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        ops = [{"op": "cut", "a": 1, "b": 1}, {"op": "fix", "n": 0, "text": "UN"}]
        d = post({"ops": ops, "dry": True})                                   # preview of the pending edits: nothing written
        assert [(e["word"], e["t"] is None) for e in d["words"]][:2] == [("UN", False), ("deux", True)]
        assert len(json.loads(p.read_text())["blocks"]) == 1
        d = post({"ops": ops})                                                # Apply
        assert [e["t"] is None for e in d["words"]][:2] == [False, True] and d["timeline"]["blocks"][1]["in"] == 1.35
        assert len(json.loads(p.read_text())["blocks"]) == 2
    finally:
        srv.shutdown()


def test_saving_new_blocks_rebuilds_the_captions_from_the_words(media, spec_file):
    """A cut changed in the timeline (a block trimmed or deleted): on Save, the captions follow the words again."""
    import threading
    import urllib.request
    from kanton import editor
    from test_editor import free_port
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.0, "out": 4.0}], "words": {"face": WORDS},
                   "texts": [{"text": "À LA MAIN", "t": 0.0, "dur": 1.0, "role": "caption"}]})
    port = free_port()
    srv = editor.make_server(Spec.load(p), port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def put(d):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/spec", data=json.dumps(d).encode(), method="PUT")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["spec"]
    try:
        d = json.loads(p.read_text())
        assert captions(Spec(put(d), p)) == "À LA MAIN"                      # same blocks: the hand-made caption stays
        d["blocks"] = [{"src": "face", "in": 0.0, "out": 0.85}, {"src": "face", "in": 1.35, "out": 4.0}]
        c = captions(Spec(put(d), p))
        assert "deux" not in c and "trois" in c and "À LA MAIN" not in c
    finally:
        srv.shutdown()
