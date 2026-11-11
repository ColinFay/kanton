"""Motion of a layer over time: keyframes and in / out / loop animations become one `perspective` filter (position, scale and
rotation on a fixed canvas, evaluated per frame by ffmpeg) and the opacity of every frame (sent with `sendcmd`).

The same formulas serve twice: with floats they give the value at a time (editor, tests), with `X` they build the ffmpeg
expression in `on`, the frame number of the layer."""
from __future__ import annotations

import math


def fmt(v) -> str:
    return v.s if isinstance(v, X) else f"{float(v):.5f}".rstrip("0").rstrip(".")


class X:
    """An ffmpeg expression under construction: arithmetic on it builds the string."""

    def __init__(self, s):
        self.s = str(s)

    def __str__(self):
        return self.s

    def _b(self, op, o, rev=False):
        a, b = (fmt(o), self.s) if rev else (self.s, fmt(o))
        return X(f"({a}{op}{b})")

    def __add__(self, o): return self._b("+", o)
    def __radd__(self, o): return self._b("+", o, True)
    def __sub__(self, o): return self._b("-", o)
    def __rsub__(self, o): return self._b("-", o, True)
    def __mul__(self, o): return self._b("*", o)
    def __rmul__(self, o): return self._b("*", o, True)
    def __truediv__(self, o): return self._b("/", o)
    def __rtruediv__(self, o): return self._b("/", o, True)
    def __neg__(self): return X(f"(-{self.s})")


def sin(v):
    return X(f"sin({v})") if isinstance(v, X) else math.sin(v)


def cos(v):
    return X(f"cos({v})") if isinstance(v, X) else math.cos(v)


def clip(v, lo, hi):
    return X(f"clip({fmt(v)},{fmt(lo)},{fmt(hi)})") if isinstance(v, X) else min(max(v, lo), hi)


def before(t, thr, a, b):
    """a when t < thr, else b."""
    if isinstance(t, X) or isinstance(a, X) or isinstance(b, X):
        return X(f"if(lt({fmt(t)},{fmt(thr)}),{fmt(a)},{fmt(b)})")
    return a if t < thr else b


EASE = {"linear": lambda u: u, "ease": lambda u: u * u * (3 - 2 * u), "in": lambda u: u * u, "out": lambda u: 1 - (1 - u) * (1 - u)}


class Track:
    """One property over time: `base` without keys; held before the first key and after the last, interpolated between
    two keys with the ease of the key being reached."""

    def __init__(self, base: float, keys: list[tuple[float, float, str]]):
        self.base, self.keys = float(base), sorted(keys)

    def at(self, t):
        if not self.keys:
            return self.base
        out = self.keys[-1][1]
        for (t0, v0, _), (t1, v1, ease) in reversed(list(zip(self.keys, self.keys[1:]))):
            u = clip((t - t0) / max(t1 - t0, 1e-6), 0, 1)
            out = before(t, t1, v0 + (v1 - v0) * EASE.get(ease, EASE["linear"])(u), out)
        return before(t, self.keys[0][0], self.keys[0][1], out)


# in / out animations: u goes 0 → 1 as the layer appears (or disappears, played backwards); x y r add, s o multiply
PI = math.pi
ANIM = {
    "fade": lambda u, W, H: {"o": u},
    "slide_up": lambda u, W, H: {"y": (1 - u) * 0.08 * H, "o": u},
    "slide_down": lambda u, W, H: {"y": (u - 1) * 0.08 * H, "o": u},
    "slide_left": lambda u, W, H: {"x": (1 - u) * 0.18 * W, "o": u},
    "slide_right": lambda u, W, H: {"x": (u - 1) * 0.18 * W, "o": u},
    "pop": lambda u, W, H: {"s": 1 - 0.5 * (1 - u) * (1 - u) + 0.15 * sin(PI * u) * u, "o": clip(u * 3, 0, 1)},
    "zoom": lambda u, W, H: {"s": 1.4 - 0.4 * u, "o": u},
    "spin": lambda u, W, H: {"r": -25 * (1 - u), "o": u},
}
LOOP = {
    "pulse": lambda t, P, A, W, H: {"s": 1 + 0.06 * A * sin(2 * PI * t / P)},
    "shake": lambda t, P, A, W, H: {"x": 0.01 * W * A * sin(2 * PI * 7 * t / P), "y": 0.007 * W * A * cos(2 * PI * 9 * t / P)},
    "wobble": lambda t, P, A, W, H: {"r": 4 * A * sin(2 * PI * t / P)},
    "float": lambda t, P, A, W, H: {"y": 0.013 * W * A * sin(2 * PI * t / P)},
    "spin": lambda t, P, A, W, H: {"r": 360 * t / P},
}


def build(W: int, H: int, fps: float, dur: float, box: tuple[float, float, float, float], pivot: tuple[float, float] | None = None,
          base: tuple[float, float] | None = None, keyframes: list[dict] | None = None, anim_in: dict | None = None, anim_out: dict | None = None,
          anim_loop: dict | None = None, scale: float = 1.0, rotation: float = 0.0, opacity: float = 1.0, offset: float = 0.0) -> dict:
    """The motion of a layer whose content sits in `box` (x, y, w, h) on the frame, padded by one transparent pixel on each side:
    `perspective` = the filter (None when the layer does not move), `opacity` = one value per frame (None when constant),
    `at(t)` = the values at a time, `corners(t)` = where the padded canvas corners land."""
    x0, y0, w0, h0 = box
    px, py = pivot or (x0 + w0 / 2, y0 + h0 / 2)
    bx, by = base or (x0, y0)
    kf = keyframes or []
    tracks = {k: Track(v, [(f["t"], f[k], f.get("ease", "linear")) for f in kf if k in f])
              for k, v in (("x", bx), ("y", by), ("scale", scale), ("rotation", rotation), ("opacity", opacity))}
    animated = bool(kf) or anim_in or anim_out or anim_loop
    moving = bool(animated)

    def values(t):
        v = {k: tr.at(t) for k, tr in tracks.items()}
        v = {"x": v["x"] - bx, "y": v["y"] - by, "s": v["scale"], "r": v["rotation"], "o": v["opacity"]}       # x y: the translation
        parts = []
        if anim_in:
            parts.append(ANIM[anim_in["type"]](clip(t / max(anim_in["dur"], 1e-6), 0, 1), W, H))
        if anim_out:
            parts.append(ANIM[anim_out["type"]](clip((dur - t) / max(anim_out["dur"], 1e-6), 0, 1), W, H))
        if anim_loop:
            parts.append(LOOP[anim_loop["type"]](t, max(anim_loop["dur"], 1e-6), anim_loop.get("amount", 1.0), W, H))
        for p in parts:
            for k, val in p.items():
                v[k] = v[k] * val if k in ("s", "o") else v[k] + val
        return v

    def corners(t):
        v = values(t)
        s, r = v["s"], v["r"] * PI / 180
        cx, cy = px + 1 + v["x"], py + 1 + v["y"]
        out = []
        for qx, qy in ((0, 0), (W + 2, 0), (0, H + 2), (W + 2, H + 2)):
            dx, dy = qx - (px + 1), qy - (py + 1)
            out.append((cx + s * (cos(r) * dx - sin(r) * dy), cy + s * (sin(r) * dx + cos(r) * dy)))
        return out

    def at(t):
        v = values(t)
        return {"x": bx + v["x"], "y": by + v["y"], "scale": v["s"], "rotation": v["r"], "opacity": v["o"]}

    perspective = None
    if moving or abs(scale - 1.0) > 1e-9 or abs(rotation) > 1e-9:
        T = X(f"(on/{fps:g}+{offset:.5f})")
        cs = corners(T)
        perspective = "perspective=" + ":".join(f"x{i}='{fmt(c[0])}':y{i}='{fmt(c[1])}'" for i, c in enumerate(cs)) + f":sense=destination:eval={'frame' if moving else 'init'}"
    n = max(1, int(round(dur * fps)))
    op = [float(values(k / fps + offset)["o"]) for k in range(n)]
    varies = any(abs(o - op[0]) > 1e-6 for o in op)
    return {"perspective": perspective, "opacity": op if varies else None, "moving": moving, "at": at, "corners": corners, "static_opacity": op[0]}
