"""Speech → words with times. Backends: mlx-whisper (Apple Silicon), faster-whisper, another Python
interpreter that has one of them (KANTON_WHISPER_PYTHON), or a words JSON produced by anything else."""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from . import ffmpeg

MODELS = {"mlx": "mlx-community/whisper-large-v3-turbo", "faster": "large-v3-turbo"}
MODEL_FILE = Path.home() / ".config" / "kanton" / "speech-model"      # folder of a model picked on the disk (the app's "Pick on disk…")
SHAPES = {(32, 4, 128): "large-v3-turbo", (32, 32, 128): "large-v3", (32, 32, 80): "large-v2", (24, 24, 80): "medium", (12, 12, 80): "small",
          (6, 6, 80): "base", (4, 4, 80): "tiny", (32, 2, 128): "distil-large-v3"}      # (encoder layers, decoder layers, mel bands)

SUBPROCESS_SCRIPT = r'''
import json, sys
wav, lang, model_mlx, model_faster = sys.argv[1:5]
try:
    import mlx_whisper
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=model_mlx, language=lang, word_timestamps=True, condition_on_previous_text=False)
    words = [w for s in r["segments"] for w in s.get("words", [])]
except ImportError:
    from faster_whisper import WhisperModel
    segs, _ = WhisperModel(model_faster).transcribe(wav, language=lang, word_timestamps=True, condition_on_previous_text=False)
    words = [{"word": w.word, "start": w.start, "end": w.end} for s in segs for w in (s.words or [])]
print(json.dumps([{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words]))
'''


def chosen_model() -> Path | None:
    """The folder picked on the disk, as long as its model.bin is still there."""
    p = Path(MODEL_FILE.read_text().strip()) if MODEL_FILE.exists() else None
    return p if p and (p / "model.bin").exists() else None


def model_for(backend: str) -> str:
    env = os.environ.get("KANTON_WHISPER_MODEL")
    if env:
        return env
    if backend == "faster" and chosen_model():
        return str(chosen_model())
    return MODELS.get(backend, MODELS["faster"])


def _shape(model_bin: Path) -> tuple[str, int, int, int]:
    """(spec, encoder layers, decoder layers, mel bands) from the header of a CTranslate2 model.bin (variable names and dims; data skipped)."""
    with open(model_bin, "rb") as f:
        rd = lambda fmt: struct.unpack(fmt, f.read(struct.calcsize(fmt)))
        rs = lambda: f.read(rd("<H")[0]).rstrip(b"\0").decode("utf-8", "replace")
        version = rd("<I")[0]
        spec = rs() if version >= 2 else ""
        rd("<I")                                            # spec revision
        enc = dec = mels = 0
        for _ in range(rd("<I")[0]):
            name = rs(); dims = rd("<" + "I" * rd("<B")[0]); rd("<B"); f.seek(rd("<I")[0], 1)
            if name == "encoder/conv1/weight":
                mels = dims[1]
            elif name.startswith("encoder/layer_"):
                enc = max(enc, int(name.split("/")[1][6:]) + 1)
            elif name.startswith("decoder/layer_"):
                dec = max(dec, int(name.split("/")[1][6:]) + 1)
    return spec, enc, dec, mels


def check_model(path: str | Path) -> str:
    """The name of the Whisper model in that folder, if it is the one kanton's transcriber needs; ValueError saying what it is otherwise."""
    d = Path(path).expanduser(); d = d.parent if d.is_file() else d
    want = MODELS["faster"]
    if any((d / f).exists() for f in ("weights.safetensors", "weights.npz")):
        raise ValueError(f"{d} holds an MLX Whisper model; kanton's transcriber needs the CTranslate2 version of {want} (model.bin, config.json, tokenizer.json)")
    missing = [f for f in ("model.bin", "config.json", "tokenizer.json") if not (d / f).exists()]
    if missing:
        raise ValueError(f"{d} is not a faster-whisper model folder: missing {', '.join(missing)}")
    try:
        spec, enc, dec, mels = _shape(d / "model.bin")
    except (struct.error, ValueError, OSError) as e:
        raise ValueError(f"{d}/model.bin is not a CTranslate2 model ({e})")
    if spec != "WhisperSpec":
        raise ValueError(f"{d}/model.bin is not a Whisper model ({spec or 'unknown spec'})")
    name = SHAPES.get((enc, dec, mels), f"an unknown Whisper model ({enc} encoder / {dec} decoder layers, {mels} mel bands)")
    if name != want:
        raise ValueError(f"{d} holds Whisper {name}; kanton needs {want}")
    return name


def use_model(path: str | Path) -> dict:
    """Check the folder, remember it (MODEL_FILE), report the model status."""
    d = Path(path).expanduser(); d = d.parent if d.is_file() else d
    check_model(d)
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_FILE.write_text(str(d) + "\n")
    return model_status()


def _mlx(wav: str, language: str, model: str) -> list[dict]:
    import mlx_whisper  # type: ignore
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=model, language=language, word_timestamps=True, condition_on_previous_text=False)
    return [{"word": w["word"], "start": w["start"], "end": w["end"]} for s in r["segments"] for w in s.get("words", [])]


def _faster(wav: str, language: str, model: str) -> list[dict]:
    from faster_whisper import WhisperModel  # type: ignore
    segs, _ = WhisperModel(model, download_root=os.environ.get("KANTON_MODEL_DIR"), compute_type="int8").transcribe(wav, language=language, word_timestamps=True, condition_on_previous_text=False)
    return [{"word": w.word, "start": w.start, "end": w.end} for s in segs for w in (s.words or [])]


BACKENDS = {"mlx": _mlx, "faster": _faster}


def model_status() -> dict:
    """The speech model the app's own transcriber (faster-whisper) uses: is it on this Mac, how many bytes of it are here.
    {"managed": False} when another backend answers first (mlx-whisper, or none at all)."""
    if available_backends()[:1] != ["faster"]:
        return {"managed": False}
    from faster_whisper.utils import _MODELS, download_model as fetch  # type: ignore
    from huggingface_hub import constants  # type: ignore
    name = MODELS["faster"]
    repo = _MODELS.get(name, name)
    chosen = chosen_model()
    if chosen:
        return {"managed": True, "model": name, "repo": repo, "present": True, "bytes": 0, "path": str(chosen)}
    cache = os.environ.get("KANTON_MODEL_DIR") or constants.HF_HUB_CACHE
    d = Path(cache) / ("models--" + repo.replace("/", "--"))
    have = sum(f.stat().st_size for f in d.glob("blobs/*") if f.is_file()) if d.exists() else 0
    try:
        fetch(name, local_files_only=True, cache_dir=os.environ.get("KANTON_MODEL_DIR"))
        present = True
    except Exception:
        present = False
    return {"managed": True, "model": name, "repo": repo, "present": present, "bytes": have, "path": None}


def model_size(repo: str) -> int | None:
    """Bytes of the model on the Hub (None when offline)."""
    try:
        from huggingface_hub import HfApi  # type: ignore
        return sum(s.size or 0 for s in HfApi().model_info(repo, files_metadata=True, timeout=5).siblings)
    except Exception:
        return None


def download_model() -> str:
    """Fetch the speech model once (into the Hugging Face cache, or KANTON_MODEL_DIR)."""
    from faster_whisper.utils import download_model as fetch  # type: ignore
    return fetch(model_for("faster"), cache_dir=os.environ.get("KANTON_MODEL_DIR"))


def available_backends() -> list[str]:
    out = []
    for name, mod in (("mlx", "mlx_whisper"), ("faster", "faster_whisper")):
        try:
            __import__(mod)
            out.append(name)
        except ImportError:
            pass
    return out


def extract_wav(path: str, start: float, end: float | None, out: str | Path) -> Path:
    args = ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-t", f"{max(0.01, end - start):.3f}"]
    args += ["-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    ffmpeg.run(args)
    return Path(out)


def _run_python(python: str, wav: str, language: str) -> list[dict]:
    res = subprocess.run([python, "-c", SUBPROCESS_SCRIPT, wav, language, model_for("mlx"), model_for("faster")], capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"kanton: transcription in {python} failed:\n{res.stderr[-2000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


def transcribe(path: str, start: float = 0.0, end: float | None = None, backend: str = "auto", language: str = "fr", python: str | None = None) -> list[dict]:
    """Words with times in SOURCE time (the extract offset is added back)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = str(extract_wav(path, start, end, Path(tmp) / "x.wav"))
        if backend == "python" or (backend == "auto" and python):
            words = _run_python(python or os.environ.get("KANTON_WHISPER_PYTHON") or sys.executable, wav, language)
        elif backend == "auto":
            avail = available_backends()
            env_py = os.environ.get("KANTON_WHISPER_PYTHON")
            if avail:
                words = BACKENDS[avail[0]](wav, language, model_for(avail[0]))
            elif env_py:
                words = _run_python(env_py, wav, language)
            else:
                sys.exit("kanton: no transcription backend. Options: pip install 'kanton[whisper]' (faster-whisper), "
                         "pip install 'kanton[mlx]' (Apple Silicon), set KANTON_WHISPER_PYTHON=/path/to/python that has one, "
                         "or pass a words JSON with --words.")
        elif backend in BACKENDS:
            words = BACKENDS[backend](wav, language, model_for(backend))
        else:
            sys.exit(f"kanton: unknown backend {backend!r} (mlx, faster, python, auto)")
    out = [{"word": str(w["word"]).strip(), "start": float(w["start"]) + start, "end": float(w["end"]) + start} for w in words if str(w["word"]).strip()]
    out.sort(key=lambda w: w["start"])
    return out


_SRT_TIME = re.compile(r"(?:(\d+):)?(\d+):(\d+)[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d+):(\d+)[,.](\d{1,3})")


def words_from_srt(text: str) -> list[dict]:
    """Words with times from SubRip text. A cue's words share its time span in proportion to their length
    (an .srt carries no word times); the cue's start and end are exact."""
    def secs(h, m, s, ms):
        return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000
    out: list[dict] = []
    for block in re.split(r"\n\s*\n", text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip().split("\n")
        i = next((k for k, ln in enumerate(lines) if _SRT_TIME.search(ln)), None)
        if i is None:
            continue
        g = _SRT_TIME.search(lines[i]).groups()
        t0, t1 = secs(*g[:4]), secs(*g[4:])
        toks = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", " ".join(lines[i + 1:])).split()
        if not toks or t1 <= t0:
            continue
        total = sum(len(w) + 1 for w in toks)
        t = t0
        for w in toks:
            e = t + (t1 - t0) * (len(w) + 1) / total
            out.append({"word": w, "start": t, "end": e})
            t = e
        out[-1]["end"] = t1
    out.sort(key=lambda w: w["start"])
    return out


def words_from_json(path: str | Path) -> list[dict]:
    if str(path).lower().endswith(".srt"):
        return words_from_srt(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "segments" in d:
        words = [w for s in d["segments"] for w in s.get("words", [])]
    elif isinstance(d, dict) and "words" in d:
        words = d["words"]
    elif isinstance(d, list):
        words = d
    else:
        sys.exit(f"kanton: {path}: expected a list of words, {{'words': [...]}} or whisper segments")
    out = [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])} for w in words if str(w.get("word", "")).strip()]
    out.sort(key=lambda w: w["start"])
    return out


def save_words(words: list[dict], path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(words, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return p
