"""Import a FilmCraft project (.fcproj, JSON) into a kanton spec. Zoom becomes relative to the full frame
(100 = no border), text layers become texts, V2+ media become overlays, the second audio track becomes music."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .spec import find_preset

T = 254016000000


def _val(v):
    if isinstance(v, dict) and "value" in v:
        return list(v["value"].values())[0]
    return v


def _param(clip: dict, effect: str, name: str):
    for e in clip.get("effects", []):
        if e.get("effect") == effect:
            return e.get("params", {}).get(name)
    return None


def _has_effect(clip: dict, effect: str) -> bool:
    return any(e.get("effect") == effect for e in clip.get("effects", []))


def _hex(color) -> str:
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        r, g, b = color[:3]
        if max(r, g, b) <= 1.0:
            r, g, b = (round(x * 255) for x in (r, g, b))
        return "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))
    return "#FFFFFF"


def convert(project: dict, preset: str | None = None) -> dict:
    items = project["project"]["items"]
    seqs = [it for it in items.values() if "Sequence" in it["kind"]]
    if not seqs:
        raise SystemExit("kanton: no sequence in this project")
    S = max(seqs, key=lambda it: sum(len(t["items"]) for t in it["kind"]["Sequence"]["video_tracks"]))["kind"]["Sequence"]
    fr = S["settings"]["frame_rate"]
    fps = fr["num"] / fr["den"]
    W, H = int(S["settings"]["width"]), int(S["settings"]["height"])
    sec = lambda ticks: ticks / T
    notes = []
    try:
        pre = find_preset(preset, Path.cwd()) if preset else find_preset(None, Path.cwd())
    except SystemExit:
        pre = find_preset(None, Path.cwd())          # unknown preset name: still written to the spec, default values for comparison
    ptext = pre.get("text", {})

    sources, keys = {}, {}

    def source_key(item_id) -> str | None:
        it = items[str(item_id)]
        if "Media" not in it["kind"]:
            return None
        if str(item_id) in keys:
            return keys[str(item_id)]
        path = it["kind"]["Media"]["media"].get("File", {}).get("path") or it.get("name")
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(path).stem).strip("-") or "src"
        key, n = base, 2
        while key in sources and sources[key] != path:
            key, n = f"{base}-{n}", n + 1
        sources[key] = path
        keys[str(item_id)] = key
        return key

    def media_info(item_id) -> dict:
        return items[str(item_id)]["kind"]["Media"]["info"]

    def zoom_of(clip: dict, info: dict) -> float | list[float]:
        v = info.get("video") or {}
        w, h = v.get("width") or W, v.get("height") or H
        fill = max(W / w, H / h) * 100.0
        sc = _param(clip, "motion", "scale")
        if sc is None:
            return 100
        kf = sc.get("keyframes") if isinstance(sc, dict) else None
        if kf:
            vals = [list(k["value"].values())[0] for k in sorted(kf, key=lambda k: k["time"])]
            return [round(vals[0] / fill * 100, 2), round(vals[-1] / fill * 100, 2)]
        return round(float(_val(sc)) / fill * 100, 2)

    def offset(clip: dict) -> tuple[float, float]:
        pos = _val(_param(clip, "motion", "position")) or {}
        if isinstance(pos, dict):
            return float(pos.get("x", W / 2)) - W / 2, float(pos.get("y", H / 2)) - H / 2
        return 0.0, 0.0

    VT = S["video_tracks"]
    v1 = sorted([c for c in VT[0]["items"] if c.get("enabled", True)], key=lambda c: c["start"])
    cut_at = {}
    for tr in VT[0].get("transitions", []):
        eff = tr["effect"]["effect"]
        centre = sec(tr["start"] + tr["duration"] / 2)
        cut_at[round(centre * fps)] = "blur" if "blur" in eff else ("flash" if eff in ("dip_to_white", "flash") else None)
        if cut_at[round(centre * fps)] is None:
            notes.append(f"transition {eff} at {centre:.2f}s not converted (cut kept)")
    blocks = []
    for c in v1:
        key = source_key(c["item"])
        if key is None:
            notes.append(f"V1 clip {c.get('name')} is not media (nested sequence?): skipped")
            continue
        speed = float(c.get("speed") or 1.0)
        b = {"src": key, "in": round(sec(c["source_in"]), 6), "out": round(sec(c["source_in"]) + sec(c["duration"]) * speed, 6), "zoom": zoom_of(c, media_info(c["item"]))}
        dx, dy = offset(c)
        if abs(dx) >= 0.5:
            b["dx"] = round(dx, 1)
        if abs(dy) >= 0.5:
            b["dy"] = round(dy, 1)
        if _has_effect(c, "horizontal_flip"):
            b["flip"] = True
        if abs(speed - 1.0) > 1e-6:
            b["speed"] = speed
        cut = cut_at.get(round(sec(c["start"]) * fps))
        if cut and blocks:
            b["cut"] = cut
        blocks.append(b)

    overlays, texts, grade = [], [], {}
    for tr in VT[1:]:
        for c in sorted(tr["items"], key=lambda c: c["start"]):
            if not c.get("enabled", True):
                notes.append(f"disabled clip {c.get('name')} on {tr['name']} skipped")
                continue
            it = items[str(c["item"])]
            kind = list(it["kind"].keys())[0]
            t0, dur = round(sec(c["start"]), 6), round(sec(c["duration"]), 6)
            if kind == "Media":
                info = it["kind"]["Media"]["info"]
                key = source_key(c["item"])
                v = info.get("video") or {}
                if info.get("kind") == "Still":
                    scale = float(_val(_param(c, "motion", "scale")) or 100.0)
                    w, h = (v.get("width") or W) * scale / 100.0, (v.get("height") or H) * scale / 100.0
                    pos = _val(_param(c, "motion", "position")) or {"x": W / 2, "y": H / 2}
                    overlays.append({"kind": "image", "src": key, "t": t0, "dur": dur, "width": round(w), "x": round(pos["x"] - w / 2, 1), "y": round(pos["y"] - h / 2, 1)})
                elif v.get("has_alpha"):
                    overlays.append({"kind": "sticker", "src": key, "t": t0, "dur": dur, "in": round(sec(c["source_in"]), 6)})
                elif v:
                    o = {"kind": "video", "src": key, "t": t0, "dur": dur, "in": round(sec(c["source_in"]), 6), "zoom": zoom_of(c, info)}
                    if _has_effect(c, "horizontal_flip"):
                        o["flip"] = True
                    if _has_effect(c, "ultra_key"):
                        o["chroma"] = {"color": "0x00FF00"}
                        notes.append(f"chroma key on {c.get('name')}: colour set to green, adjust")
                    overlays.append(o)
                else:
                    notes.append(f"audio-only media {c.get('name')} on {tr['name']} skipped")
            elif kind == "Graphic":
                p = next((e["params"] for e in c["effects"] if e["effect"] == "graphic_text"), None)
                if not p:
                    notes.append(f"graphic {c.get('name')} without text skipped")
                    continue
                text = str(_val(p.get("text")) or "")
                size = float(_val(p.get("size")) or ptext.get("size", 60))
                pos = _val(p.get("position")) or {"x": W / 2, "y": ptext.get("y_baseline", 1432)}
                n = text.count("\n") + 1
                step = 67.5 * size / 59.0
                y_last = float(pos["y"]) + step * (n - 1)
                centred = abs(float(pos["x"]) - W / 2) <= 1 and abs(y_last - float(ptext.get("y_baseline", 1432))) <= 1
                t = {"text": text, "t": t0, "dur": dur, "role": "caption" if centred else "title"}
                fill = _hex(_val(p.get("fill_color")))
                sw = float(_val(p.get("stroke_width")) or 0)
                if abs(size - float(ptext.get("size", 60))) > 0.01:
                    t["size"] = size
                if fill.upper() != str(ptext.get("fill", "#FFFFFF")).upper():
                    t["fill"] = fill
                if abs(sw - float(ptext.get("stroke_width", 7))) > 0.01:
                    t["stroke_width"] = sw
                if abs(y_last - float(ptext.get("y_baseline", 1432))) > 1:
                    t["y"] = round(y_last, 1)
                if abs(float(pos["x"]) - W / 2) > 1:
                    t["x"] = float(pos["x"])
                if _param(c, "motion", "scale") and (_param(c, "motion", "scale") or {}).get("keyframes"):
                    notes.append(f"pop animation on text « {text[:20]} » not converted")
                texts.append(t)
            elif kind == "AdjustmentLayer":
                lum = next((e["params"] for e in c["effects"] if e["effect"] == "lumetri"), None)
                if lum:
                    g = {}
                    ct = float(_val(lum.get("contrast")) or 0)
                    if ct:
                        g["contrast"] = round(1 + ct / 100, 3)
                    sat = _val(lum.get("saturation"))
                    if sat is not None and abs(float(sat) - 100) > 0.01:
                        g["saturation"] = round(float(sat) / 100, 3)
                    temp = float(_val(lum.get("temperature")) or 0)
                    if temp:
                        g["temperature"] = int(round(6500 - temp * 25))
                    vig = float(_val(lum.get("vignette_amount")) or 0)
                    if vig < 0:
                        g["vignette"] = round(min(1.0, -vig / 5), 3)
                    tint = float(_val(lum.get("tint")) or 0)
                    if tint:
                        notes.append(f"lumetri tint {tint:+g} not converted")
                    grade.update(g)
            else:
                notes.append(f"{kind} clip {c.get('name')} on {tr['name']} skipped")

    for ct in S.get("caption_tracks", []):
        for cue in ct.get("captions", []):
            texts.append({"text": cue["text"], "t": round(sec(cue["start"]), 6), "dur": round(sec(cue["duration"]), 6), "role": "caption"})

    music = None
    for tr in S.get("audio_tracks", [])[1:]:
        for c in sorted(tr["items"], key=lambda c: c["start"]):
            key = source_key(c["item"])
            if key is None:
                continue
            if music:
                notes.append(f"extra music clip {c.get('name')} on {tr['name']} not converted (one music per spec)")
                continue
            music = {"src": key, "in": round(sec(c["source_in"]), 6), "t": round(sec(c["start"]), 6), "dur": round(sec(c["duration"]), 6), "gain_db": float(c.get("gain_db") or 0)}
            fades = [sec(x["duration"]) for x in tr.get("transitions", []) if "fade" in x["effect"]["effect"]]
            if fades:
                music["fade_out"] = round(max(fades), 6)

    texts = _merge_fragments(sorted(texts, key=lambda t: t["t"]), fps)
    spec = {"kanton": 1}
    if preset:
        spec["preset"] = preset
    spec.update({"output": {"width": W, "height": H, "fps": int(fps) if fps == int(fps) else fps}, "sources": sources, "blocks": blocks,
                 "zoom": {"focus": [W / 2, H / 2]}, "overlays": overlays, "texts": texts})
    if music:
        spec["music"] = music
    if grade:
        spec["grade"] = grade
    if notes:
        spec["notes"] = notes
    return spec


def _merge_fragments(texts: list[dict], fps: float) -> list[dict]:
    """Contiguous pieces of the same layer (left by cuts in FilmCraft) become one text."""
    out: list[dict] = []
    ident = lambda t: (t["text"], t["role"], t.get("size"), t.get("fill"), t.get("stroke_width"), t.get("y"), t.get("x"))
    for t in texts:
        prev = next((o for o in reversed(out[-8:]) if ident(o) == ident(t) and abs(o["t"] + o["dur"] - t["t"]) <= 1.5 / fps), None)
        if prev:
            prev["dur"] = round(t["t"] + t["dur"] - prev["t"], 6)
        else:
            out.append(dict(t))
    return sorted(out, key=lambda t: t["t"])


def convert_file(path: str | Path, out: str | Path, preset: str | None = None) -> Path:
    project = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = convert(project, preset=preset)
    out = Path(out)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out
