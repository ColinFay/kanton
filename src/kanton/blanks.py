"""Cut the blanks inside the blocks: from word gaps (transcription) or from the energy (silencedetect).

Replayable: the hand-picked ranges are kept in `blocks_raw`, `blocks` is always rebuilt from them.
Texts and overlays are re-timed through the source so that they stay on their words."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from . import ffmpeg
from .spec import Spec

DEFAULTS = {"min": 0.30, "pre": 0.08, "post": 0.12, "min_cut": 0.20, "min_keep_frames": 9, "db": -35}


def silences_energy(path: str, start: float, end: float, db: float = -35, min_len: float = 0.3) -> list[tuple[float, float]]:
    """Silences (absolute source time) in [start, end] at `db` dBFS after a 250 Hz high-pass, ≥ min_len long."""
    err = ffmpeg.run_capture(["-ss", f"{start:.3f}", "-t", f"{max(0.01, end - start):.3f}", "-i", path, "-vn",
                              "-af", f"highpass=f=250,silencedetect=n={db}dB:d={min_len}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", err)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else end - start
        out.append((start + max(0.0, s), start + min(e, end - start)))
    return out


def gaps_from_words(words: list[dict], start: float, end: float, min_len: float) -> list[tuple[float, float]]:
    ws = sorted((w for w in words if w["end"] > start and w["start"] < end), key=lambda w: w["start"])
    return [(a["end"], b["start"]) for a, b in zip(ws, ws[1:]) if b["start"] - a["end"] >= min_len]


def cut_block(raw: dict, gaps: list[tuple[float, float]], cfg: dict, fps: float) -> list[dict]:
    cfg = {**DEFAULTS, **(cfg or {})}
    b_in, b_out = float(raw["in"]), float(raw["out"])
    cuts = []
    for gs, ge in sorted(gaps):
        gs, ge = max(gs, b_in), min(ge, b_out)
        if ge <= gs:
            continue
        a = b_in if gs <= b_in + 1e-6 else gs + cfg["post"]      # silence at the head: trim up to the speech
        b = b_out if ge >= b_out - 1e-6 else ge - cfg["pre"]
        if b - a >= cfg["min_cut"]:
            cuts.append((a, b))
    pieces, cur = [], b_in
    for a, b in cuts:
        if a > cur:
            pieces.append([cur, a])
        cur = max(cur, b)
    if b_out > cur:
        pieces.append([cur, b_out])
    if not pieces:
        pieces = [[b_in, b_out]]
    min_keep = cfg["min_keep_frames"] / fps
    merged = [pieces[0]]
    for p in pieces[1:]:                                           # a leftover too short to see goes back with its neighbour
        if p[1] - p[0] < min_keep or merged[-1][1] - merged[-1][0] < min_keep:
            merged[-1][1] = p[1]
        else:
            merged.append(p)
    out = []
    for a, b in merged:
        nb = copy.deepcopy(raw)
        nb["in"], nb["out"] = round(a, 3), round(b, 3)
        out.append(nb)
    return out


def _kept(new: list[dict], b: dict, t: float, last: bool, fps: float) -> float | None:
    """Reel time on `new` of the first frame of old block `b` from t on (last: the last one before t) still played."""
    x = b["in"] + (min(max(t, b["start"]), b["end"]) - b["start"]) * b["speed"]
    a, z = (b["in"], x) if last else (x, b["in"] + b["dur"] * b["speed"])
    best = None
    for n in new:
        lo, hi = max(a, n["in"]), min(z, n["in"] + n["dur"] * n["speed"])    # whole frames play a little past `out`
        if n["src"] == b["src"] and hi - lo >= 0.5 / fps * n["speed"] and (best is None or (hi > best[0] if last else lo < best[0])):
            best = (hi if last else lo, n)
    return None if best is None else best[1]["start"] + (best[0] - best[1]["in"]) / best[1]["speed"]


def apply_blanks(spec: Spec, mode: str = "energy", words_by_src: dict | None = None, db: float | None = None, cfg: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(spec.preset.get("blanks") or {}), **(cfg or {})}
    if db is not None:
        cfg["db"] = db
    data = spec.data
    if "blocks_raw" not in data:
        data["blocks_raw"] = copy.deepcopy(data["blocks"])
    old_blocks = copy.deepcopy(spec.blocks)
    new_blocks, ranges, removed = [], [], 0.0
    for raw in data["blocks_raw"]:
        path = spec.source(raw["src"])
        if mode == "words":
            words = (words_by_src or {}).get(raw["src"])
            if words is None:
                raise SystemExit(f"kanton: no words for source {raw['src']!r} (transcribe it first or pass --words)")
            gaps = gaps_from_words(words, float(raw["in"]), float(raw["out"]), cfg["min"])
        else:
            gaps = silences_energy(path, float(raw["in"]), float(raw["out"]), db=cfg["db"], min_len=cfg["min"])
        pieces = cut_block(raw, gaps, cfg, spec.fps)
        for a, b in zip(pieces, pieces[1:]):
            ranges.append((a["out"], b["in"]))
            removed += b["in"] - a["out"]
        if pieces and pieces[0]["in"] > float(raw["in"]) + 1e-6:
            removed += pieces[0]["in"] - float(raw["in"])
        if pieces and pieces[-1]["out"] < float(raw["out"]) - 1e-6:
            removed += float(raw["out"]) - pieces[-1]["out"]
        new_blocks += pieces
    data["blocks"] = new_blocks
    spec.blocks = [spec._norm_block(b, i) for i, b in enumerate(new_blocks)]
    spec._timeline()
    retime(spec, old_blocks)
    return {"ranges": ranges, "removed": removed, "blocks": len(new_blocks), "duration": spec.duration}


def retime(spec: Spec, old_blocks: list[dict]):
    """Texts and overlays placed on `old_blocks` keep their frames on the blocks of `spec`, wherever those went. A start
    goes to the first frame from it on that is still in the cut, an end to the last one before it."""
    data = spec.data

    def remap(t: float, last: bool) -> float:
        for b in (reversed(old_blocks) if last else old_blocks):
            r = _kept(spec.blocks, b, t, last, spec.fps) if (t > b["start"] + 1e-9 if last else t < b["end"] - 1e-9) else None
            if r is not None:
                return r
        return 0.0 if last else spec.duration
    for coll in ("texts", "overlays"):
        for item in data.get(coll) or []:
            t0 = float(item.get("t", 0))
            t1 = t0 + (float(item["dur"]) if item.get("dur") else float(item.get("end", t0)) - t0)
            n0, n1 = remap(t0, False), remap(t1, True)
            item["t"] = round(n0, 4)
            if "end" in item:
                item["end"] = round(max(n1, n0 + 1 / spec.fps), 4)
            else:
                item["dur"] = round(max(n1 - n0, 1 / spec.fps), 4)
    spec.texts = [spec._norm_text(t) for t in data.get("texts") or []]
    spec.overlays = [spec._norm_overlay(o) for o in data.get("overlays") or []]


def simulate_audio(spec: Spec, out: str | Path) -> Path:
    """The voice of the kept blocks, back to back, as a 16 kHz mono wav (to transcribe and read the text)."""
    import tempfile
    from pathlib import Path as _P
    out = _P(out)
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for b in spec.blocks:
            p = _P(tmp) / f"{b['i']:04d}.wav"
            ffmpeg.run(["-ss", f"{b['in']:.3f}", "-t", f"{b['out'] - b['in']:.3f}", "-i", b["path"], "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(p)])
            parts.append(p)
        lst = _P(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out
