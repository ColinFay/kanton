import json
import os
import sys

import pytest

from kanton import ffmpeg, transcribe


def test_words_from_json_shapes(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps([{"word": " salut", "start": 1.0, "end": 1.2}, {"word": "toi", "start": 0.0, "end": 0.3}]))
    w = transcribe.words_from_json(p)
    assert [x["word"] for x in w] == ["toi", "salut"] and w[1]["start"] == 1.0
    p.write_text(json.dumps({"segments": [{"words": [{"word": "a", "start": 0, "end": 1}]}, {"words": [{"word": "b", "start": 1, "end": 2}]}]}))
    assert [x["word"] for x in transcribe.words_from_json(p)] == ["a", "b"]
    p.write_text(json.dumps({"words": [{"word": "c", "start": 0, "end": 1}]}))
    assert [x["word"] for x in transcribe.words_from_json(p)] == ["c"]


def test_extract_wav_range(media, tmp_path):
    wav = transcribe.extract_wav(str(media["face"]), 1.0, 3.5, tmp_path / "x.wav")
    i = ffmpeg.probe(str(wav))
    assert abs(i["duration"] - 2.5) < 0.05


def test_fake_backend_offsets_by_start(media, tmp_path, monkeypatch):
    seen = {}

    def fake(wav, language, model):
        seen["wav"] = wav
        return [{"word": "un", "start": 0.0, "end": 0.3}, {"word": "deux", "start": 0.5, "end": 0.8}]
    monkeypatch.setitem(transcribe.BACKENDS, "fake", fake)
    w = transcribe.transcribe(str(media["face"]), 2.0, 4.0, backend="fake")
    assert [x["word"] for x in w] == ["un", "deux"] and w[0]["start"] == pytest.approx(2.0) and w[1]["end"] == pytest.approx(2.8)
    assert str(seen["wav"]).endswith(".wav")


def test_auto_without_backend_exits(media, monkeypatch):
    monkeypatch.setattr(transcribe, "available_backends", lambda: [])
    monkeypatch.delenv("KANTON_WHISPER_PYTHON", raising=False)
    with pytest.raises(SystemExit) as e:
        transcribe.transcribe(str(media["face"]), 0, 1, backend="auto")
    assert "faster-whisper" in str(e.value)


def test_python_backend_runs_in_another_interpreter(media, tmp_path, monkeypatch):
    """The subprocess backend imports mlx_whisper in the given interpreter: stub it on PYTHONPATH."""
    stub = tmp_path / "mlx_whisper.py"
    stub.write_text("def transcribe(path, **kw):\n    return {'segments': [{'words': [{'word': ' ok', 'start': 0.1, 'end': 0.4}]}]}\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    w = transcribe.transcribe(str(media["face"]), 1.0, 2.0, backend="python", python=sys.executable)
    assert w == [{"word": "ok", "start": pytest.approx(1.1), "end": pytest.approx(1.4)}]


def test_save_and_default_model_names(tmp_path):
    p = transcribe.save_words([{"word": "a", "start": 0, "end": 1}], tmp_path / "w.json")
    assert json.loads(p.read_text())[0]["word"] == "a"
    assert transcribe.model_for("mlx").startswith("mlx-community/")
    assert transcribe.model_for("faster") == "large-v3-turbo"


def test_model_status_reports_the_bundled_models_presence(monkeypatch, tmp_path):
    """The app's own transcriber (faster-whisper) needs a model fetched once: the editor asks before, and shows progress."""
    import pytest
    pytest.importorskip("faster_whisper")
    from kanton import transcribe as T
    monkeypatch.setenv("KANTON_MODEL_DIR", str(tmp_path)); monkeypatch.delenv("KANTON_WHISPER_MODEL", raising=False); monkeypatch.delenv("KANTON_WHISPER_PYTHON", raising=False)
    monkeypatch.setattr(T, "available_backends", lambda: ["faster"])
    m = T.model_status()
    assert m["managed"] and m["model"] == "large-v3-turbo" and m["repo"] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo" and not m["present"] and m["bytes"] == 0
    blobs = tmp_path / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "blobs"; blobs.mkdir(parents=True)
    (blobs / "abc.incomplete").write_bytes(b"x" * 10)
    m = T.model_status()
    assert m["bytes"] == 10 and not m["present"]
    monkeypatch.setattr(T, "available_backends", lambda: ["mlx", "faster"])
    assert T.model_status() == {"managed": False}
    monkeypatch.setattr(T, "available_backends", lambda: [])
    assert T.model_status() == {"managed": False}


def ct2_model(folder, enc, dec, mels, spec="WhisperSpec"):
    """A CTranslate2 model.bin with the real header layout and tiny variables: what faster-whisper reads."""
    import struct
    s = lambda t: struct.pack("<H", len(t) + 1) + t.encode() + b"\0"
    var = lambda name, dims: s(name) + struct.pack("<B", len(dims)) + b"".join(struct.pack("<I", d) for d in dims) + struct.pack("<BI", 1, 4) + b"\0\0\0\0"
    names = [("encoder/conv1/weight", (8, mels, 3))] + [(f"encoder/layer_{i}/w", (2,)) for i in range(enc)] + [(f"decoder/layer_{i}/w", (2,)) for i in range(dec)]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bin").write_bytes(struct.pack("<I", 6) + s(spec) + struct.pack("<II", 3, len(names)) + b"".join(var(n, d) for n, d in names) + struct.pack("<I", 0))
    (folder / "config.json").write_text("{}"); (folder / "tokenizer.json").write_text("{}")


def test_a_model_folder_on_disk_is_checked_before_it_is_used(monkeypatch, tmp_path):
    """Instead of downloading, the user can point at a model already on the disk: kanton checks it is the right one."""
    import pytest
    from kanton import transcribe as T
    monkeypatch.setattr(T, "MODEL_FILE", tmp_path / "cfg" / "speech-model")
    monkeypatch.setenv("KANTON_MODEL_DIR", str(tmp_path / "hub")); monkeypatch.delenv("KANTON_WHISPER_MODEL", raising=False)
    monkeypatch.setattr(T, "available_backends", lambda: ["faster"])
    good = tmp_path / "turbo"; ct2_model(good, 32, 4, 128)
    assert T.check_model(good) == "large-v3-turbo" and T.check_model(good / "model.bin") == "large-v3-turbo"
    v3 = tmp_path / "v3"; ct2_model(v3, 32, 32, 128)
    with pytest.raises(ValueError, match="large-v3;.*large-v3-turbo"):
        T.check_model(v3)
    other = tmp_path / "t5"; ct2_model(other, 4, 4, 80, spec="TransformerSpec")
    with pytest.raises(ValueError, match="not a Whisper"):
        T.check_model(other)
    mlx = tmp_path / "mlx"; mlx.mkdir(); (mlx / "weights.safetensors").write_bytes(b"x"); (mlx / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="MLX"):
        T.check_model(mlx)
    with pytest.raises(ValueError, match="model.bin"):
        T.check_model(tmp_path / "empty")
    with pytest.raises(ValueError):
        T.use_model(v3)
    assert not T.MODEL_FILE.exists() and T.model_for("faster") == "large-v3-turbo" and not T.model_status()["present"]
    m = T.use_model(good)
    assert m["present"] and m["path"] == str(good) and m["model"] == "large-v3-turbo"
    assert T.MODEL_FILE.read_text().strip() == str(good) and T.model_for("faster") == str(good)
    assert T.model_status()["present"] and T.model_status()["path"] == str(good)
    (good / "model.bin").unlink()                                   # gone since: back to "not here"
    assert not T.model_status()["present"] and T.model_for("faster") == "large-v3-turbo"
