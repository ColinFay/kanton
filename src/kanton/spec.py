"""The cut list: load, merge with a preset, normalise, compute the timeline.

A spec is a JSON file. Times are seconds. `blocks` are the main track, played back to back in order.
`overlays`, `texts` and `music` are placed in reel time (seconds from the start of the rendered video).
Zoom 100 = the source fills the frame with no border; 116 = 16 % closer. A list [a, b] is a linear push
over the block.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from . import fx
from . import speed as ramp

HERE = Path(__file__).resolve().parent
PRESET_DIRS = [Path.cwd(), Path.home() / ".config" / "kanton" / "presets", HERE / "presets"]
EXPORT_FORMATS = ("mp4", "mov", "webm", "gif")
STYLE_KEYS = ("y", "x", "size", "fill", "stroke", "stroke_width", "font", "max_width", "uppercase", "align", "y_baseline", "line_step", "max_lines", "leading")
TEXT_KEYS = ("shadow", "box", "opacity", "rotation", "highlight", "reveal", "anim_in", "anim_out", "anim_loop", "keyframes")
KEYFRAME_KEYS = ("t", "x", "y", "scale", "rotation", "opacity", "ease")
SHADOW = {"color": "#000000", "dx": 4, "dy": 4, "blur": 6, "opacity": 0.6}
BOX = {"color": "#000000", "opacity": 0.6, "padding": 20, "radius": 16}
DUCK = {"threshold": 0.02, "ratio": 6, "attack": 80, "release": 500}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def find_preset(ref: str | dict | None, spec_dir: Path) -> dict:
    """ref = None | name | path | inline dict. A preset may itself say "extends": <ref>."""
    default = json.loads((HERE / "presets" / "default.json").read_text())
    if ref is None:
        return default
    if isinstance(ref, dict):
        parent = find_preset(ref.get("extends"), spec_dir)
        p = _deep_merge(parent, {k: v for k, v in ref.items() if k != "extends"})
        p.setdefault("_dir", str(spec_dir))
        return p
    cands = [spec_dir / ref, Path(ref).expanduser()] + [d / ref for d in PRESET_DIRS] + [d / f"{ref}.json" for d in [spec_dir] + PRESET_DIRS]
    for c in cands:
        if c.is_file():
            data = json.loads(c.read_text())
            parent = find_preset(data.get("extends"), c.parent)
            p = _deep_merge(parent, {k: v for k, v in data.items() if k != "extends"})
            p["_dir"] = str(c.parent)
            p["_path"] = str(c)
            return p
    sys.exit(f"kanton: preset not found: {ref}")


class Spec:
    def __init__(self, data: dict, path: str | os.PathLike | None = None):
        self.path = Path(path).resolve() if path else None
        self.dir = self.path.parent if self.path else Path.cwd()
        self.data = data
        self.preset = find_preset(data.get("preset"), self.dir)
        self.output = _deep_merge(self.preset.get("output", {}), data.get("output", {}))
        self.fps = float(self.output.get("fps", 30))
        self.W = int(self.output.get("width", 1080))
        self.H = int(self.output.get("height", 1920))
        self.sources = {k: self.resolve(v) for k, v in (data.get("sources") or {}).items()}
        self.transitions = _deep_merge(self.preset.get("transitions", {}), data.get("transitions", {}))
        self.canvas = {"background": (data.get("canvas") or {}).get("background", (self.preset.get("canvas") or {}).get("background"))}
        self.blocks = [self._norm_block(b, i) for i, b in enumerate(data.get("blocks") or [])]
        self._timeline()
        self.effects = [self._norm_effect(e) for e in (data.get("effects") or [])]
        for b in self.blocks:
            self.effects += [self._norm_effect({**e, "t": b["start"], "dur": b["dur"]}) for e in b.pop("effects")]
        self.overlays = [self._norm_overlay(o) for o in (data.get("overlays") or [])]
        self.texts = [self._norm_text(t) for t in (data.get("texts") or [])]
        self.music = self._norm_music(data.get("music"))
        self.sounds = [self._norm_music(m, sound=True) for m in (data.get("sounds") or [])]
        self.markers = sorted(({"t": float(m.get("t", 0)), "label": str(m.get("label", ""))} for m in (data.get("markers") or [])), key=lambda m: m["t"])
        self.grade = fx.resolve_grade(data.get("grade"), self.preset.get("filters"))
        self.zoom = _deep_merge(self.preset.get("zoom", {}), data.get("zoom", {}))
        self.export = _deep_merge(self.preset.get("export", {}), data.get("export", {}))
        self.export.setdefault("format", "mp4")
        if self.export["format"] not in EXPORT_FORMATS:
            sys.exit(f"kanton: unknown export format {self.export['format']!r} ({', '.join(EXPORT_FORMATS)})")

    # ── loading / saving ────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | os.PathLike) -> "Spec":
        p = Path(path)
        if not p.is_file():
            sys.exit(f"kanton: spec not found: {path}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.exit(f"kanton: {path} is not valid JSON: {e}")
        return cls(data, p)

    def save(self, path: str | os.PathLike | None = None):
        p = Path(path) if path else self.path
        p.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def resolve(self, ref: str) -> str:
        """A source path: absolute, or relative to the spec file, then to the preset directory."""
        p = Path(str(ref)).expanduser()
        if p.is_absolute():
            return str(p)
        for base in (self.dir, Path(self.preset.get("_dir", self.dir))):
            if (base / p).exists():
                return str((base / p).resolve())
        return str((self.dir / p).resolve())

    def source(self, key: str) -> str:
        """A source key from `sources`, or a path."""
        if key in self.sources:
            return self.sources[key]
        return self.resolve(key)

    # ── normalisation ───────────────────────────────────────────────────────
    def frames(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def _norm_block(self, b: dict, i: int) -> dict:
        still = "dur" in b and not ("in" in b and "out" in b)       # a frozen frame of a video (`at`), or an image on the main track
        if "src" not in b or not (still or ("in" in b and "out" in b)):
            sys.exit(f"kanton: block {i} needs src, in, out (or src, dur [, at] for a still): {b}")
        z = b.get("zoom", 100)
        if isinstance(z, (int, float)):
            z = [float(z), float(z)]
        elif isinstance(z, list) and len(z) == 2:
            z = [float(z[0]), float(z[1])]
        else:
            sys.exit(f"kanton: block {i}: zoom must be a number or [start, end]: {z}")
        curve = None if still else ramp.points(b.get("speed"))
        speed = 1.0 if still or curve else float(b.get("speed", 1.0) or 1.0)
        cut, cut_dur = b.get("cut"), b.get("cut_dur")
        if isinstance(cut, dict):
            cut, cut_dur = cut.get("type"), cut.get("dur", cut_dur)
        fit = b.get("fit", "cover")
        if fit not in ("cover", "contain"):
            sys.exit(f"kanton: block {i}: fit must be cover or contain: {fit!r}")
        crop = b.get("crop")
        if crop is not None and not (isinstance(crop, list) and len(crop) == 4):
            sys.exit(f"kanton: block {i}: crop must be [x, y, w, h]: {crop}")
        dn = b.get("denoise", 0)
        n = {"i": i, "src": b["src"], "path": self.source(b["src"]), "zoom": z, "still": still,
             "dx": float(b.get("dx", 0) or 0), "dy": float(b.get("dy", 0) or 0), "flip": bool(b.get("flip", False)), "vflip": bool(b.get("vflip", False)),
             "speed": speed, "cut": fx.check_transition(cut), "cut_dur": float(cut_dur if cut_dur is not None else self.transitions.get("duration", 0.3)),
             "mute": bool(b.get("mute", False)) or still, "fit": fit, "background": b.get("background") or self.canvas["background"] if fit == "contain" else b.get("background"),
             "rotate": float(b.get("rotate", 0) or 0), "crop": [float(x) for x in crop] if crop else None, "reverse": bool(b.get("reverse", False)),
             "grade": fx.resolve_grade(b.get("grade"), self.preset.get("filters")), "volume": float(b.get("volume", 0) or 0),
             "fade_in": float(b.get("fade_in", 0) or 0), "fade_out": float(b.get("fade_out", 0) or 0),
             "denoise": 1.0 if dn is True else float(dn or 0), "pitch": float(b.get("pitch", 0) or 0), "effects": list(b.get("effects") or []),
             "curve": curve, "smooth": bool(b.get("smooth", False)), "stabilize": bool(b.get("stabilize", False)) and not still, "voice": fx.check_voice(b.get("voice"))}
        if still:
            n["in"] = float(b.get("at", 0) or 0)
            n["out"] = n["in"] + float(b["dur"])
            if float(b["dur"]) <= 0:
                sys.exit(f"kanton: block {i}: a still needs a positive dur")
        else:
            n["in"], n["out"] = float(b["in"]), float(b["out"])
            if n["out"] <= n["in"]:
                sys.exit(f"kanton: block {i}: out ≤ in ({n['in']} → {n['out']})")
        L = n["out"] - n["in"]
        n["frames"] = max(1, self.frames(ramp.duration(curve, L) if curve else L / speed))
        n["dur"] = n["frames"] / self.fps
        if curve:
            n["speed"] = L / n["dur"]                       # the mean speed, for what only needs one
        return n

    def _norm_effect(self, e: dict) -> dict:
        if e.get("type") not in fx.EFFECTS:
            sys.exit(f"kanton: unknown effect {e.get('type')!r} (see `kanton catalog effects`)")
        return {"type": e["type"], "t": float(e.get("t", 0) or 0), "dur": float(e.get("dur", 1) or 1), "amount": float(1.0 if e.get("amount") is None else e["amount"])}

    def _anim(self, a, kind: str, what: str) -> dict | None:
        if not a:
            return None
        a = {"type": a} if isinstance(a, str) else dict(a)
        if a.get("type") not in fx.ANIMATIONS[kind]:
            sys.exit(f"kanton: unknown animation {a.get('type')!r} for {what} ({', '.join(fx.ANIMATIONS[kind])})")
        out = {"type": a["type"], "dur": float(a.get("dur", 1.0 if kind == "loop" else 0.3))}
        if kind == "loop":
            out["amount"] = float(a.get("amount", 1.0))
        return out

    def _keyframes(self, kfs, what: str) -> list[dict]:
        out = []
        for k in kfs or []:
            bad = set(k) - set(KEYFRAME_KEYS)
            if bad:
                sys.exit(f"kanton: keyframe of {what} has unknown keys {sorted(bad)} (allowed: {', '.join(KEYFRAME_KEYS)})")
            n = {"t": float(k.get("t", 0) or 0)}
            for key in ("x", "y", "scale", "rotation", "opacity"):
                if k.get(key) is not None:
                    n[key] = float(k[key])
            n["ease"] = k.get("ease", "linear")
            out.append(n)
        return sorted(out, key=lambda k: k["t"])

    def _motion(self, o: dict, what: str) -> dict:
        return {"keyframes": self._keyframes(o.get("keyframes"), what), "anim_in": self._anim(o.get("anim_in"), "in", what),
                "anim_out": self._anim(o.get("anim_out"), "out", what), "anim_loop": self._anim(o.get("anim_loop"), "loop", what),
                "rotation": float(o.get("rotation", 0) or 0), "opacity": float(1.0 if o.get("opacity") is None else o["opacity"])}

    def _timeline(self):
        t = 0
        for b in self.blocks:
            b["start_frame"] = t
            b["start"] = t / self.fps
            t += b["frames"]
            b["end"] = t / self.fps
        self.total_frames = t
        self.duration = t / self.fps

    def _norm_overlay(self, o: dict) -> dict:
        kind = o.get("kind") or "video"
        what = f"overlay {o.get('src')}"
        n = {"kind": kind, "src": o.get("src"), "path": self.source(o["src"]) if o.get("src") else None,
             "t": float(o.get("t", 0)), "dur": float(o.get("dur", 0) or 0), "in": float(o.get("in", 0) or 0),
             "zoom": float(o.get("zoom", 100) or 100), "dx": float(o.get("dx", 0) or 0), "dy": float(o.get("dy", 0) or 0),
             "x": o.get("x"), "y": o.get("y"), "width": o.get("width"), "height": o.get("height"),
             "flip": bool(o.get("flip", False)), "fit": o.get("fit", "cover"), "chroma": o.get("chroma"), "loop": bool(o.get("loop", False)),
             "volume": None if o.get("volume") is None else float(o["volume"]), "scale": float(o.get("scale", 1) or 1),
             "blend": o.get("blend"), "grade": fx.resolve_grade(o.get("grade"), self.preset.get("filters")), "mask": None, **self._motion(o, what)}
        if n["dur"] <= 0:
            sys.exit(f"kanton: overlay needs a positive dur: {o}")
        if n["blend"] is not None and n["blend"] not in fx.BLENDS:
            sys.exit(f"kanton: unknown blend {n['blend']!r} for {what} ({', '.join(fx.BLENDS)})")
        m = o.get("mask")
        if m:
            m = {"shape": m} if isinstance(m, str) else m
            if m.get("shape") not in fx.MASKS:
                sys.exit(f"kanton: unknown mask {m.get('shape')!r} for {what} ({', '.join(fx.MASKS)})")
            n["mask"] = {"shape": m["shape"], "feather": float(m.get("feather", 0) or 0), "radius": float(m.get("radius", 0) or 0), "invert": bool(m.get("invert", False)),
                         "rect": [float(x) for x in m["rect"]] if m.get("rect") else None, "angle": float(m.get("angle", 0) or 0)}
        return n

    def _norm_text(self, t: dict) -> dict:
        if "text" not in t:
            sys.exit(f"kanton: text needs 'text': {t}")
        role = t.get("role", "caption")
        rd = ((self.preset.get("text") or {}).get("roles") or {}).get(role) or {}
        t = {**{k: v for k, v in rd.items() if k in TEXT_KEYS}, **t}                       # the role's text settings, then the text's own
        n = {"text": str(t["text"]), "t": float(t.get("t", 0)), "role": role, "style": t.get("style") or {}}
        if "end" in t:
            n["dur"] = float(t["end"]) - n["t"]
        else:
            n["dur"] = float(t.get("dur", 0) or 0)
        if n["dur"] <= 0:
            sys.exit(f"kanton: text needs dur or end > t: {t}")
        for k in STYLE_KEYS:
            if k in t:
                n["style"][k] = t[k]
        what = f"text « {n['text'][:20]} »"
        sh, bx = t.get("shadow"), t.get("box")
        n["shadow"] = {**SHADOW, **(sh if isinstance(sh, dict) else {})} if sh else None
        n["box"] = {**BOX, **(bx if isinstance(bx, dict) else {})} if bx else None
        n["words"] = [{"word": str(w["word"]), "t": float(w["t"]), "dur": float(w["dur"])} for w in (t.get("words") or [])]
        hl = t.get("highlight", (self.preset.get("text") or {}).get("highlight"))
        n["highlight"] = dict(hl) if isinstance(hl, dict) else ({} if hl else None)
        n["reveal"] = t.get("reveal")
        if n["reveal"] not in (None, "word", "char"):
            sys.exit(f"kanton: unknown reveal {n['reveal']!r} for {what} (word, char)")
        n.update(self._motion(t, what))
        return n

    def _norm_music(self, m: dict | None, sound: bool = False) -> dict | None:
        if not m:
            return None
        n = {"src": m["src"], "path": self.source(m["src"]), "in": float(m.get("in", 0) or 0), "t": float(m.get("t", 0) or 0),
             "dur": float(m["dur"]) if m.get("dur") else None, "gain_db": float(m.get("gain_db", 0) or 0),
             "fade_in": float(m.get("fade_in", 0) or 0), "fade_out": float(m.get("fade_out", 0) or 0),
             "pitch": float(m.get("pitch", 0) or 0), "loop": bool(m.get("loop", False)), "voice": fx.check_voice(m.get("voice")),
             "beats": sorted(float(x) for x in m.get("beats") or [])}
        if not sound:
            d = m.get("duck")
            n["duck"] = {**DUCK, **(d if isinstance(d, dict) else {})} if d else None
        return n

    # ── time mapping ────────────────────────────────────────────────────────
    def reel_time(self, src: str, t_src: float) -> float | None:
        """Source time → reel time, through the blocks of that source. None if cut out."""
        for b in self.blocks:
            if b["src"] == src and not b["still"] and b["in"] - 1e-6 <= t_src <= b["out"] + 1e-6:
                return self.at(b, t_src)
        return None

    def at(self, b: dict, t_src: float) -> float:
        """Source time → reel time inside block b (through its speed or its curve)."""
        x = min(max(t_src, b["in"]), b["out"]) - b["in"]
        return b["start"] + (ramp.reel_offset(b["curve"], b["out"] - b["in"], x) if b["curve"] else x / b["speed"])

    def cuts(self) -> list[float]:
        """Reel times of the cuts between blocks (not counting 0 and the end)."""
        return [b["start"] for b in self.blocks[1:]]

    def text_style(self, t: dict | None = None) -> dict:
        s = {k: v for k, v in self.preset.get("text", {}).items() if k not in ("roles", "highlight")}
        if t:
            s.update({k: v for k, v in ((self.preset.get("text") or {}).get("roles") or {}).get(t.get("role"), {}).items() if k in STYLE_KEYS})
            s.update(t.get("style", {}))
        sz = self.preset.get("safe_zone")
        if sz:                              # a text never leaves the safe zone: width, horizontal place and baseline held inside
            mw = s["max_width"] = min(float(s["max_width"]), sz["right"] - sz["left"])
            off = {"left": 0, "right": mw}.get(s.get("align"), mw / 2)          # from x to the left edge of the text box
            s["x"] = min(max(float(s["x"]), sz["left"] + off), sz["right"] - mw + off)
            k = "y" if "y" in s else "y_baseline"
            s[k] = min(float(s[k]), sz["bottom"])
        return s

    def describe(self) -> str:
        lines = [f"{len(self.blocks)} blocks, {self.duration:.2f} s, {len(self.texts)} texts, {len(self.overlays)} overlays"
                 + (", music" if self.music else "")]
        for b in self.blocks:
            z = f"{b['zoom'][0]:g}" if b["zoom"][0] == b["zoom"][1] else f"{b['zoom'][0]:g}→{b['zoom'][1]:g}"
            lines.append(f"  {b['i']:3d} {b['start']:7.2f}s +{b['dur']:5.2f}  {b['src']} {b['in']:.2f}-{b['out']:.2f}  zoom {z}"
                         + (" still" if b["still"] else "") + (f" dx {b['dx']:+g}" if b["dx"] else "") + (" flip" if b["flip"] else "")
                         + (f" cut:{b['cut']}" if b["cut"] != "none" else ""))
        for m in self.markers:
            lines.append(f"  marker {m['t']:7.2f}s {m['label']}")
        return "\n".join(lines)
