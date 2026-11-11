"""Editing by the words. The transcript lives in the spec (`words`: {src: [{word, start, end}]}, source time) and
words are numbered across sources in that order. A word is played by each block it is (mostly) inside: o = 0, 1… in
reel order (a copied passage is played twice, and each time is edited on its own).

cut / keep / move / copy rewrite the blocks: a boundary falls in the middle of the pause around the words; at the ends
of a source a cut takes everything, a kept, moved or copied run gets the blanks `pre` / `post` margins. They act on
one time the words are played (o: the block that plays the first of them, then the blocks after it as long as they go
on in the source). fix corrects the text only. The blanks the edit creates are cut as `blanks` would (words mode); the
rest of the montage is left as it was. Each rebuilds the captions and moves the other texts and overlays with their
source time; the edit becomes the new base for `blanks` (blocks_raw is dropped)."""
from __future__ import annotations

import sys

from .blanks import DEFAULTS, cut_block, gaps_from_words, retime
from .captions import apply_captions, plays
from .spec import Spec


def transcribe_missing(spec: Spec, backend: str = "auto", language: str = "fr", python: str | None = None) -> dict:
    """Transcribe into `words` (whole files) the sources of the blocks that have sound and no words yet; returns them."""
    from . import ffmpeg, transcribe
    ws = spec.data.setdefault("words", {})
    for src in sorted({b["src"] for b in spec.blocks} - set(ws)):
        if ffmpeg.probe(spec.source(src))["has_audio"]:
            print(f"transcribing {src}…", flush=True)
            ws[src] = transcribe.transcribe(spec.source(src), 0.0, None, backend=backend, language=language, python=python)
    return ws


def _flat(spec: Spec) -> list[tuple[str, int]]:
    if not spec.data.get("words"):
        sys.exit("kanton: no words in the spec: run `kanton words` first")
    return [(src, i) for src, ws in spec.data["words"].items() for i in range(len(ws))]


def _span(spec: Spec, a: int, b: int) -> tuple[str, int, int]:
    flat = _flat(spec)
    for n in (a, b):
        if not 0 <= n < len(flat):
            sys.exit(f"kanton: no word {n} (0-{len(flat) - 1})")
    if a > b or flat[a][0] != flat[b][0]:
        sys.exit(f"kanton: words {a}-{b} are not one run of one source")
    return flat[a][0], flat[a][1], flat[b][1]


def _bounds(spec: Spec, ws: list[dict], i: int, j: int, open_ends: bool) -> tuple[float, float]:
    cfg = {**DEFAULTS, **(spec.preset.get("blanks") or {})}
    a = (ws[i - 1]["end"] + ws[i]["start"]) / 2 if i else (0.0 if open_ends else max(0.0, ws[i]["start"] - cfg["pre"]))
    b = (ws[j]["end"] + ws[j + 1]["start"]) / 2 if j + 1 < len(ws) else (float("inf") if open_ends else ws[j]["end"] + cfg["post"])
    return round(a, 3), round(b, 3)


def _props(bl: dict) -> dict:
    return {k: v for k, v in bl.items() if k not in ("in", "out", "cut")}


def _occ(blocks: list[dict], src: str, w: dict) -> list[int]:
    """The blocks that play a word, in reel order."""
    return [k for k, b in enumerate(blocks) if b["src"] == src and plays(b, w)]


def _chain(spec: Spec, blocks: list[dict], src: str, i: int, j: int, o: int) -> list[int]:
    """The blocks of the o-th time words i..j are played: the o-th block that plays the first of them played at all,
    then the blocks after it in the reel as long as they go on in the source, up to the last word. None played: []."""
    ws = spec.data["words"][src]
    first = next((ks for ks in (_occ(blocks, src, ws[x]) for x in range(i, j + 1)) if ks), [])
    if first and o >= len(first):
        sys.exit(f"kanton: these words are played {len(first)} time(s), not {o + 1}")
    out = first[o:o + 1]
    while out and out[-1] + 1 < len(blocks):
        p, q = blocks[out[-1]], blocks[out[-1] + 1]
        if q["src"] != src or q["in"] < p["out"] - 0.5 / spec.fps or q["in"] >= ws[j]["end"]:
            break
        out.append(out[-1] + 1)
    return out


def _remove(blocks: list[dict], src: str, a: float, b: float, fps: float, only: set[int]) -> list[dict]:
    """Source [a, b] taken out of the blocks `only` (indexes)."""
    out = []
    for k, bl in enumerate(blocks):
        if bl["src"] != src or b <= bl["in"] or a >= bl["out"] or k not in only:
            out.append(bl)
            continue
        first = True
        for lo, hi in ((bl["in"], min(a, bl["out"])), (max(b, bl["in"]), bl["out"])):
            if hi - lo >= 1 / fps:
                nb = {**bl, "in": lo, "out": hi}
                if not first:
                    nb.pop("cut", None)                     # the transition stays on the way into the block
                out.append(nb)
                first = False
    return out


def _insert(blocks: list[dict], k: int, nb: dict, fps: float) -> list[dict]:
    """Insert at k, then join with a neighbour it continues (same source and settings, no transition between)."""
    blocks.insert(k, nb)
    for x in (k, k - 1):
        if 0 <= x < len(blocks) - 1:
            p, q = blocks[x], blocks[x + 1]
            if abs(p["out"] - q["in"]) < 0.5 / fps and _props(p) == _props(q) and not q.get("cut"):
                p["out"] = q["out"]
                del blocks[x + 1]
    return blocks


def _recut(spec: Spec, old: list[dict], new: list[dict]) -> list[dict]:
    """Cut the blanks the edit created: a pause ≥ `min` between two words that the old reel did not have, and a new
    junction (a cut, the start or the end of the reel) where the end of one side plus the start of the other reach
    `min` (each side then keeps its `post` / `pre` margin)."""
    cfg = {**DEFAULTS, **(spec.preset.get("blanks") or {})}
    words = spec.data["words"]

    def covered(src: str, a: float, b: float) -> bool:      # [a, b] was in the old reel
        t = a
        for o in sorted((o for o in old if o["src"] == src), key=lambda o: o["in"]):
            if o["in"] <= t + 1e-6:
                t = max(t, o["out"])
        return t >= b - 1e-6

    def speech(b: dict | None) -> tuple[float, float] | None:
        ws = [w for w in words.get(b["src"], []) if w["end"] > b["in"] and w["start"] < b["out"]] if b else []
        return (min(w["start"] for w in ws), max(w["end"] for w in ws)) if ws else None

    out = []
    for b in new:
        gaps = [g for g in gaps_from_words(words.get(b["src"], []), b["in"], b["out"], cfg["min"]) if not covered(b["src"], *g)]
        out += cut_block(b, gaps, cfg, spec.fps) if gaps else [b]
    edge = lambda x, y: (x and (x["src"], x["out"]), y and (y["src"], y["in"]))
    seen = {edge(x, y) for x, y in zip([None] + old, old + [None])}
    for x, y in zip([None] + out, out + [None]):
        if edge(x, y) in seen:
            continue
        sx, sy = speech(x), speech(y)
        tail, head = max(0.0, x["out"] - sx[1]) if sx else 0.0, max(0.0, sy[0] - y["in"]) if sy else 0.0
        if tail + head >= cfg["min"]:
            if tail > cfg["post"]:
                x["out"] = round(sx[1] + cfg["post"], 3)
            if head > cfg["pre"]:
                y["in"] = round(sy[0] - cfg["pre"], 3)
    return out


def _apply(spec: Spec, blocks: list[dict]) -> Spec:
    spec.data["blocks"] = _recut(spec, spec.blocks, blocks)
    spec.data.pop("blocks_raw", None)
    s = Spec(spec.data, spec.path)
    retime(s, spec.blocks)
    apply_captions(s, s.data["words"])
    return s


def _blocks(spec: Spec) -> list[dict]:
    return [{**b, "in": float(b["in"]), "out": float(b["out"])} for b in spec.data["blocks"]]


def view(spec: Spec) -> list[dict]:
    """The reel as words: each time a word is played (o = 0, 1… in reel order, t = its reel time), and each word played
    nowhere (o = t = None) after the first time the word before it in its source is played."""
    out, keys, last, blocks = [], [], {}, spec.blocks
    for n, (src, i) in enumerate(_flat(spec)):
        w = spec.data["words"][src][i]
        e = {"n": n, "src": src, "i": i, "word": w["word"], "start": w["start"], "end": w["end"]}
        ts = [blocks[k]["start"] + (max(w["start"], blocks[k]["in"]) - blocks[k]["in"]) / blocks[k]["speed"] for k in _occ(blocks, src, w)]
        if ts:
            last[src] = ts[0]
        out += [{**e, "o": o, "t": t} for o, t in enumerate(ts)] or [{**e, "o": None, "t": None}]
        keys += [(t, n) for t in ts] or [(last.get(src, -1.0), n)]
    return [e for _, e in sorted(zip(keys, out), key=lambda p: p[0])]


def cut(spec: Spec, a: int, b: int, o: int = 0) -> Spec:
    """Words a..b out of their o-th time played."""
    src, i, j = _span(spec, a, b)
    blocks = _blocks(spec)
    only = set(_chain(spec, blocks, src, i, j, o))
    return _apply(spec, _remove(blocks, src, *_bounds(spec, spec.data["words"][src], i, j, True), spec.fps, only))


def keep(spec: Spec, a: int, b: int, o: int = 0) -> Spec:
    """Words a..b played in a row again, at their place in the source (after the block that ends before them): the cut
    ones, with their o-th time played (if any). Blocks that only touch their edge (playing none) are trimmed."""
    src, i, j = _span(spec, a, b)
    ws = spec.data["words"][src]
    lo, hi = _bounds(spec, ws, i, j, False)
    blocks, tol = _blocks(spec), 0.5 / spec.fps
    edge = {k for k, x in enumerate(blocks) if x["src"] == src and x["in"] < hi and x["out"] > lo and not any(plays(x, w) for w in ws[i:j + 1])}
    blocks = _remove(blocks, src, lo, hi, spec.fps, edge | set(_chain(spec, blocks, src, i, j, o)))
    before = [k for k, bl in enumerate(blocks) if bl["src"] == src and bl["out"] <= lo + tol]
    after = [k for k, bl in enumerate(blocks) if bl["src"] == src and bl["in"] >= hi - tol]
    if before:
        k = max(before, key=lambda k: blocks[k]["out"])
        nb, k = blocks[k], k + 1
    elif after:
        k = min(after, key=lambda k: blocks[k]["in"])
        nb = blocks[k]
    else:
        k, nb = len(blocks), {"src": src}
    return _apply(spec, _insert(blocks, k, {**_props(nb), "in": lo, "out": hi}, spec.fps))


def move(spec: Spec, a: int, b: int, before: int | None = None, o: int = 0, before_o: int = 0) -> Spec:
    """Words a..b (their o-th time played) cut from where they are and played just before word `before` (its
    before_o-th time played; None = at the end), with the settings of the block they came from."""
    src, i, j = _span(spec, a, b)
    if before is not None and a <= before <= b:
        sys.exit(f"kanton: word {before} is inside the moved words {a}-{b}")
    blocks = _blocks(spec)
    chain = _chain(spec, blocks, src, i, j, o)
    props = _props(blocks[chain[0]]) if chain else {"src": src}
    blocks = _remove(blocks, src, *_bounds(spec, spec.data["words"][src], i, j, True), spec.fps, set(chain))
    return _paste(spec, blocks, src, i, j, props, before, before_o)


def copy(spec: Spec, a: int, b: int, before: int | None = None, o: int = 0, before_o: int = 0) -> Spec:
    """Words a..b played once more just before word `before` (its before_o-th time played; None = at the end), with the
    settings of their o-th time played."""
    src, i, j = _span(spec, a, b)
    blocks = _blocks(spec)
    chain = _chain(spec, blocks, src, i, j, o)
    return _paste(spec, blocks, src, i, j, _props(blocks[chain[0]]) if chain else {"src": src}, before, before_o)


def _paste(spec: Spec, blocks: list[dict], src: str, i: int, j: int, props: dict, before: int | None, before_o: int) -> Spec:
    """Words i..j of `src` as a block just before word `before` (its before_o-th time played; None = at the end)."""
    k = len(blocks)
    if before is not None:
        tsrc, ti, _ = _span(spec, before, before)
        tw = spec.data["words"][tsrc]
        ks = _occ(blocks, tsrc, tw[ti])
        if not ks:
            sys.exit(f"kanton: word {before} is cut: keep it first or move before another word")
        if before_o >= len(ks):
            sys.exit(f"kanton: word {before} is played {len(ks)} time(s), not {before_o + 1}")
        k = ks[before_o]
        at, bl = _bounds(spec, tw, ti, ti, True)[0], blocks[k]
        if at - bl["in"] >= 1 / spec.fps:                     # split the block in front of the word
            tail = {**bl, "in": at}
            tail.pop("cut", None)
            bl["out"] = at
            blocks.insert(k + 1, tail)
            k += 1
    lo, hi = _bounds(spec, spec.data["words"][src], i, j, False)
    return _apply(spec, _insert(blocks, k, {**props, "in": lo, "out": hi}, spec.fps))


def fix(spec: Spec, n: int, text: str) -> Spec:
    src, i, _ = _span(spec, n, n)
    if not text.strip():
        sys.exit("kanton: a word can't be empty (cut it instead)")
    spec.data["words"][src][i]["word"] = text.strip()
    apply_captions(spec, spec.data["words"])
    return spec
