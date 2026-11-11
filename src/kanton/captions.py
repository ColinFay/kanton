"""Words with times → caption cues (text layers with role "caption").

Rules (preset `captions`): a new cue on a pause ≥ `gap`, when the text no longer fits `max_lines` lines of
`max_width`, or past `max_frames`. A cue starts `lead_frames` before its first word, ends when the next
cue starts if the pause is shorter than `join_gap`, otherwise `tail` seconds after its last word. A boundary
within `snap_frames` of a cut moves onto the cut. A cue stays inside its shot: a new cue at every cut, and
nothing (lead, tail, min length) crosses one. Cues last at least `min_frames` (unless the shot is shorter),
never overlap, and sit on whole frames.
"""
from __future__ import annotations

from pathlib import Path

from .spec import Spec
from .text import TextStyle

DEFAULTS = {"gap": 0.35, "min_frames": 25, "max_frames": 210, "snap_frames": 16, "lead_frames": 1, "tail": 0.3, "join_gap": 0.6}


def _fits(style: TextStyle, text: str) -> bool:
    lines = style.wrap(text)
    return len(lines) <= style.max_lines and all(style.width(l) <= style.max_width for l in lines)


def _group(words: list[dict], style: TextStyle, fps: float, cfg: dict, cuts: list[float]) -> list[list[dict]]:
    """Runs of speech split on pauses and cuts, then each run in the fewest even parts that fit (no orphan word)."""
    runs, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] >= cfg["gap"] or any(cur[-1]["start"] < x <= w["start"] for x in cuts)):
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    fits = lambda g: _fits(style, " ".join(x["word"] for x in g)) and (g[-1]["end"] - g[0]["start"]) * fps <= cfg["max_frames"]
    groups = []
    for run in runs:
        n = 0
        while True:
            n += 1
            k, r = divmod(len(run), n)
            sizes = [k + (j < r) for j in range(n)]
            parts = [run[sum(sizes[:j]):sum(sizes[:j + 1])] for j in range(n)]
            if n >= len(run) or all(fits(g) for g in parts):
                break
        groups += parts
    return groups


def build_cues(words: list[dict], style: TextStyle, cuts: list[float], fps: float, cfg: dict | None = None) -> list[dict]:
    cfg = {**DEFAULTS, **(cfg or {})}
    F = 1.0 / fps
    words = [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])} for w in words if str(w["word"]).strip()]
    words.sort(key=lambda w: w["start"])
    cues = []
    for g in _group(words, style, fps, cfg, cuts):
        cues.append({"t": max(0.0, g[0]["start"] - cfg["lead_frames"] * F), "end": g[-1]["end"] + cfg["tail"],
                     "first": g[0]["start"], "last": g[-1]["end"], "text": " ".join(w["word"] for w in g), "words": g})
    snap = cfg["snap_frames"] * F
    for c in cues:                                   # boundaries near a cut fall on the cut
        for key, ok in (("t", lambda x: x <= c["first"] + 1e-9), ("end", lambda x: x >= c["last"] - 1e-9)):
            near = [x for x in cuts if abs(x - c[key]) <= snap + 1e-9 and ok(x)]
            if near:
                c[key] = min(near, key=lambda x: abs(x - c[key]))
    for a, b in zip(cues, cues[1:]):                 # join short pauses, never overlap
        if b["first"] - a["last"] < cfg["join_gap"]:
            a["end"] = b["t"]
        a["end"] = min(a["end"], b["t"])
    for i, c in enumerate(cues):                     # minimum and maximum length
        nxt = cues[i + 1]["t"] if i + 1 < len(cues) else None
        if c["end"] - c["t"] < cfg["min_frames"] * F:
            c["end"] = c["t"] + cfg["min_frames"] * F
            if nxt is not None:
                c["end"] = min(c["end"], nxt)
        c["end"] = min(c["end"], c["t"] + cfg["max_frames"] * F)
    for c in cues:                                   # inside its shot
        c["t"] = max([c["t"]] + [x for x in cuts if x <= c["first"]])
        c["end"] = min([c["end"]] + [x for x in cuts if x > c["first"]])
    out = []
    for c in cues:                                   # whole frames
        t0 = round(c["t"] * fps) / fps
        t1 = max(round(c["end"] * fps), round(c["t"] * fps) + 1) / fps
        out.append({"t": t0, "dur": t1 - t0, "text": c["text"], "words": c["words"]})
    for a, b in zip(out, out[1:]):
        if a["t"] + a["dur"] > b["t"] + 1e-9:
            a["dur"] = max(1.0 / fps, b["t"] - a["t"])
    for c in out:                                    # the words of the cue, in reel time, inside the cue
        t0, t1 = c["t"], c["t"] + c["dur"]
        c["words"] = [{"word": w["word"], "t": round(min(max(w["start"], t0), t1), 4), "dur": round(max(0.0, min(w["end"], t1) - min(max(w["start"], t0), t1)), 4)} for w in c["words"]]
    return out


def plays(b: dict, w: dict) -> bool:
    """A block plays a word when the word is mostly inside it."""
    s, e = float(w["start"]), float(w["end"])
    return not b.get("still") and e > b["in"] and s < b["out"] and min(e, b["out"]) - max(s, b["in"]) >= (e - s) / 2


def words_to_reel(spec: Spec, src: str, words: list[dict]) -> list[dict]:
    """Source-time words → reel-time words through the blocks of `src`, once per block that plays them (a passage copied
    is captioned at each place); words cut out (or mostly) are dropped, partial ones clamped."""
    out = []
    for w in words:
        s, e = float(w["start"]), float(w["end"])
        for b in spec.blocks:
            if b["src"] == src and plays(b, w):
                cs, ce = max(s, b["in"]), min(e, b["out"])
                out.append({"word": str(w["word"]).strip(), "start": spec.at(b, cs), "end": spec.at(b, ce)})
    out.sort(key=lambda w: w["start"])
    return out


def apply_captions(spec: Spec, words_by_src: dict[str, list[dict]]) -> int:
    """Replace the role="caption" texts of the spec with cues built from `words_by_src` (source time). Returns the cue count."""
    style = TextStyle(spec.text_style(), [Path(spec.preset.get("_dir", spec.dir)), spec.dir])
    cfg = {**DEFAULTS, **(spec.preset.get("captions") or {})}
    words = []
    for src, ws in words_by_src.items():
        words += words_to_reel(spec, src, ws)
    words.sort(key=lambda w: w["start"])
    cues = build_cues(words, style, spec.cuts(), spec.fps, cfg)
    keep = [t for t in (spec.data.get("texts") or []) if t.get("role", "caption") != "caption"]
    kind = (spec.data.get("captions") or {}).get("style") or cfg.get("style") or "plain"      # plain | karaoke | typewriter
    lit = (spec.preset.get("text") or {}).get("highlight")
    out = []
    for c in cues:
        t = {"text": c["text"], "t": c["t"], "dur": c["dur"], "role": "caption"}
        if kind in ("karaoke", "typewriter"):
            t["words"] = c["words"]
        if kind == "typewriter":
            t["reveal"] = "word"
        elif kind == "karaoke" and not lit:
            t["highlight"] = {"scale": 1.12}
        out.append(t)
    spec.data["texts"] = keep + out
    spec.texts = [spec._norm_text(t) for t in spec.data["texts"]]
    return len(cues)
