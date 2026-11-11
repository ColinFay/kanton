"""Looks, effects, transitions, blends, masks and animations: the catalogs an agent or the editor can pick from, and the
ffmpeg filter chains they compile to. Every built-in look (FILTERS) is a hypothesis until validated on real footage."""
from __future__ import annotations

import sys

from . import speed

# ── transitions: ffmpeg's xfade catalog, plus kanton's own effects on the cut ─────────────────────────────────────────
XFADE = ["fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown", "circlecrop",
         "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
         "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose", "dissolve", "pixelize", "diagtl", "diagtr",
         "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr",
         "squeezeh", "squeezev", "zoomin", "fadefast", "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright",
         "coverup", "coverdown", "revealleft", "revealright", "revealup", "revealdown"]
CUT_FX = ["blur", "flash", "punch"]
TRANSITIONS = CUT_FX + XFADE


def is_xfade(name: str | None) -> bool:
    return name in XFADE


def check_transition(name: str | None) -> str:
    name = name or "none"
    if name != "none" and name not in TRANSITIONS:
        sys.exit(f"kanton: unknown transition {name!r} (see `kanton catalog transitions`)")
    return name


# ── grade: the adjust panel; a look is a grade with a name ───────────────────────────────────────────────────────────
NEUTRAL = {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0, "gamma": 1.0, "temperature": 6500.0, "vibrance": 0.0, "vignette": 0.0,
           "exposure": 0.0, "tint": 0.0, "hue": 0.0, "sharpen": 0.0, "highlights": 0.0, "shadows": 0.0, "grain": 0.0, "fade": 0.0}
GRADE_KEYS = list(NEUTRAL) + ["lut", "temperature_mix"]

FILTERS = {
    "warm": {"temperature": 7200, "saturation": 1.05},
    "cool": {"temperature": 5600, "saturation": 0.95},
    "bw": {"saturation": 0, "contrast": 1.1},
    "vintage": {"saturation": 0.75, "contrast": 0.95, "temperature": 6900, "fade": 0.35, "vignette": 0.4, "grain": 0.2},
    "vivid": {"saturation": 1.35, "contrast": 1.08, "vibrance": 0.3},
    "fade": {"fade": 0.4, "contrast": 0.9, "saturation": 0.85},
    "cinema": {"contrast": 1.15, "saturation": 0.85, "temperature": 6200, "highlights": -0.2, "shadows": 0.1, "vignette": 0.3},
    "noir": {"saturation": 0, "contrast": 1.35, "shadows": -0.2, "vignette": 0.5},
    "pastel": {"saturation": 0.8, "brightness": 0.05, "fade": 0.25, "contrast": 0.9},
    "golden": {"temperature": 7600, "saturation": 1.1, "highlights": 0.1},
}


def resolve_grade(g, preset_filters: dict | None) -> dict:
    """A grade as plain keys: None, a look name, or a dict ({"filter": name, "intensity": 0-1, ...overrides})."""
    looks = {**FILTERS, **(preset_filters or {})}
    if not g:
        return {}
    if isinstance(g, str):
        if g not in looks:
            sys.exit(f"kanton: unknown filter {g!r} (see `kanton catalog filters`)")
        return dict(looks[g])
    g = dict(g)
    name, k = g.pop("filter", None), float(g.pop("intensity", 1.0))
    look = dict(looks[name]) if name in looks else None
    if name and look is None:
        sys.exit(f"kanton: unknown filter {name!r} (see `kanton catalog filters`)")
    base = look if look is not None else g
    mixed = {key: NEUTRAL[key] + (float(v) - NEUTRAL[key]) * k if key in NEUTRAL else v for key, v in base.items()}
    return {**mixed, **g} if look is not None else mixed


def grade_chain(g: dict | None) -> str:
    """The filters of a grade, each preceded by a comma (ready to append to a chain); '' when there is nothing to do."""
    if not g:
        return ""
    v = lambda k: float(g[k])
    parts = []
    if g.get("lut"):
        parts.append(f"lut3d=file='{_esc(g['lut'])}',format=yuv420p")
    eq = {k: g[k] for k in ("contrast", "saturation", "brightness", "gamma") if k in g}
    if eq:
        parts.append("eq=" + ":".join(f"{k}={float(x):.4f}" for k, x in eq.items()))
    if g.get("exposure"):
        parts.append(f"exposure=exposure={v('exposure'):.4f}")
    if g.get("temperature"):
        parts.append(f"colortemperature=temperature={v('temperature'):.0f}:mix={float(g.get('temperature_mix', 1.0)):.3f}")
    if g.get("tint"):
        t = -v("tint") / 100.0                                   # tint > 0: magenta (less green); < 0: green
        parts.append(f"colorbalance=gs={t:.4f}:gm={t:.4f}:gh={t:.4f}")
    if g.get("hue"):
        parts.append(f"hue=h={v('hue'):.4f}")
    if g.get("vibrance"):
        parts.append(f"vibrance=intensity={v('vibrance'):.3f}")
    if g.get("highlights") or g.get("shadows") or g.get("fade"):
        lo, sh, hl = float(g.get("fade", 0)) * 0.25, 0.25 + float(g.get("shadows", 0)) * 0.2, 0.75 + float(g.get("highlights", 0)) * 0.2
        parts.append(f"curves=all='0/{lo:.4f} 0.25/{sh:.4f} 0.75/{hl:.4f} 1/1'")
    if g.get("sharpen"):
        parts.append(f"unsharp=5:5:{v('sharpen') * 1.5:.4f}")
    if g.get("grain"):
        parts.append(f"noise=alls={int(round(v('grain') * 40))}:allf=t+u")
    if g.get("vignette"):
        parts.append(f"vignette=angle={min(max(v('vignette'), 0.0), 1.0) * 0.6:.4f}")
    return "".join("," + p for p in parts)


def _esc(s: str) -> str:
    out = str(s)
    for ch in "\\:',[];":
        out = out.replace(ch, "\\" + ch)
    return out


# ── blends: how an overlay mixes with the picture under it ──────────────────────────────────────────────────────────
BLENDS = {"screen": "screen", "multiply": "multiply", "overlay": "overlay", "add": "addition", "lighten": "lighten", "darken": "darken",
          "difference": "difference", "softlight": "softlight", "hardlight": "hardlight"}
BLEND_NEUTRAL = {"screen": "black", "add": "black", "lighten": "black", "difference": "black", "multiply": "white", "darken": "white",
                 "overlay": "0x808080", "softlight": "0x808080", "hardlight": "0x808080"}   # the colour that leaves the picture as it is

MASKS = ["rect", "circle", "rounded", "linear"]
ANIMATIONS = {"in": ["fade", "slide_up", "slide_down", "slide_left", "slide_right", "pop", "zoom", "spin"],
              "out": ["fade", "slide_up", "slide_down", "slide_left", "slide_right", "pop", "zoom", "spin"],
              "loop": ["pulse", "shake", "wobble", "float", "spin"]}


# ── voices: voice changer, an audio chain after the clip's own sound (hypotheses) ─────────────────────────────────────
VOICES = {
    "chipmunk": "asetrate=72000,aresample=48000,atempo=0.66667",
    "deep": "asetrate=36000,aresample=48000,atempo=1.33333",
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",
    "echo": "aecho=0.8:0.85:180:0.4",
    "hall": "aecho=0.8:0.8:45|85|130:0.35|0.25|0.18",
    "telephone": "highpass=f=400,highpass=f=400,lowpass=f=3400,lowpass=f=3400",
    "megaphone": "highpass=f=500,lowpass=f=4500,acrusher=bits=6:mix=0.35:mode=log,volume=3dB",
    "vibrato": "vibrato=f=6:d=0.5",
}


def check_voice(name: str | None) -> str | None:
    if name and name not in VOICES:
        sys.exit(f"kanton: unknown voice {name!r} (see `kanton catalog voices`)")
    return name or None


def voice_chain(name: str | None) -> str:
    """The filters of a voice (input at 48 kHz), followed by a comma; '' for none."""
    return VOICES[name] + "," if name else ""


# ── effects: timed on the whole picture ──────────────────────────────────────────────────────────────────────────────
def _inline(chain):            # filters that take `enable`: applied in place for the window
    return {"mode": "inline", "chain": chain}


def _branch(chain):            # the rest: a copy of the window goes through the chain and is put back over the picture
    return {"mode": "branch", "chain": chain}


EFFECTS = {
    "blur": _inline(lambda a, W, H, fps, en: f"gblur=sigma={2 + 10 * a:.2f}:enable='{en}'"),
    "rgb_split": _inline(lambda a, W, H, fps, en: f"rgbashift=rh={int(14 * a)}:bh={-int(14 * a)}:enable='{en}'"),
    "glitch": _inline(lambda a, W, H, fps, en: f"rgbashift=rh={int(18 * a)}:bh={-int(18 * a)}:enable='{en}*lt(mod(n,9),3)',"
                                               f"rgbashift=rh={-int(10 * a)}:gh={int(8 * a)}:enable='{en}*between(mod(n,13),5,7)',"
                                               f"pixelize=w={int(8 + 24 * a)}:h={int(8 + 24 * a)}:enable='{en}*lt(mod(n,11),1)',"
                                               f"noise=alls={int(10 + 30 * a)}:allf=t:enable='{en}',hue=h=180:enable='{en}*eq(mod(n,17),0)'"),
    "vhs": _inline(lambda a, W, H, fps, en: f"chromashift=cbh={int(8 * a)}:crh={-int(8 * a)}:enable='{en}',noise=alls={int(8 + 14 * a)}:allf=t:enable='{en}',"
                                            f"eq=saturation={1 + 0.3 * a:.3f}:contrast=0.95:enable='{en}',gblur=sigma={0.6 * a + 0.2:.2f}:enable='{en}'"),
    "old_film": _inline(lambda a, W, H, fps, en: f"colorbalance=rs={0.25 * a:.3f}:gs={0.1 * a:.3f}:bs={-0.25 * a:.3f}:enable='{en}',"
                                                 f"eq=saturation={1 - 0.6 * a:.3f}:brightness='{0.04 * a:.3f}*sin(n*2.7)':eval=frame:enable='{en}',"
                                                 f"noise=alls={int(10 + 20 * a)}:allf=t:enable='{en}',vignette=angle={0.3 + 0.25 * a:.3f}:enable='{en}'"),
    "pixelate": _inline(lambda a, W, H, fps, en: f"pixelize=w={int(8 + 40 * a)}:h={int(8 + 40 * a)}:enable='{en}'"),
    "negative": _inline(lambda a, W, H, fps, en: f"negate=enable='{en}'"),
    "strobe": _inline(lambda a, W, H, fps, en: f"eq=brightness='if(lt(mod(n,{max(2, int(fps / 5))}),{max(1, int(fps / 10))}),{0.6 * a:.3f},0)':eval=frame:enable='{en}'"),
    "fisheye": _inline(lambda a, W, H, fps, en: f"lenscorrection=k1={-0.35 * a:.3f}:k2={-0.1 * a:.3f}:i=bilinear:enable='{en}'"),
    "grain": _inline(lambda a, W, H, fps, en: f"noise=alls={int(10 + 50 * a)}:allf=t+u:enable='{en}'"),
    "flash": _inline(lambda a, W, H, fps, en: None),      # built from its own time: see effects_graph
    "shake": _branch(lambda a, W, H, fps: f"scale={int(W * 1.06)}:{int(H * 1.06)},crop={W}:{H}:x='(iw-{W})/2+{8 + 28 * a:.1f}*sin(37*t)':y='(ih-{H})/2+{8 + 28 * a:.1f}*cos(53*t)'"),
    "pulse": _branch(lambda a, W, H, fps: f"zoompan=z='1+{0.08 * a:.4f}*abs(sin(PI*on/{fps / 2:.3f}))':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={fps:g}"),
    "mirror": _branch(lambda a, W, H, fps: f"crop=iw/2:ih:0:0,split[ml][mr];[mr]hflip[mrf];[ml][mrf]hstack"),
}


def effects_graph(effects: list[dict], vin: str, W: int, H: int, fps: float) -> tuple[list[str], str]:
    """Filtergraph lines applying `effects` (each {type, t, dur, amount}) to label `vin`; returns (lines, output label)."""
    lines, cur = [], vin
    for k, e in enumerate(sorted(effects or [], key=lambda e: float(e.get("t", 0)))):
        name = e.get("type")
        if name not in EFFECTS:
            sys.exit(f"kanton: unknown effect {name!r} (see `kanton catalog effects`)")
        t0, dur, a = float(e.get("t", 0)), float(e.get("dur", 1) or 1), min(max(float(e.get("amount", 1.0) if e.get("amount") is not None else 1.0), 0.0), 1.0)
        t1, en = t0 + dur, f"between(t,{t0:.4f},{t0 + dur:.4f})"
        fx, nxt = EFFECTS[name], f"{vin}fx{k}"
        if name == "flash":
            lines.append(f"[{cur}]fade=t=in:st={t0:.4f}:d={max(0.05, 0.1 + 0.3 * a) if dur >= 0.4 else dur:.4f}:c=white[{nxt}]")
        elif fx["mode"] == "inline":
            lines.append(f"[{cur}]{fx['chain'](a, W, H, fps, en)}[{nxt}]")
        else:
            chain = fx["chain"](a, W, H, fps)
            lines.append(f"[{cur}]split[{nxt}a][{nxt}b]")
            lines.append(f"[{nxt}b]trim=start={t0:.4f}:end={t1:.4f},setpts=PTS-STARTPTS+{t0:.4f}/TB,{chain}[{nxt}c]")
            lines.append(f"[{nxt}a][{nxt}c]overlay=enable='{en}':eof_action=pass[{nxt}]")
        cur = nxt
    return lines, cur


def catalog() -> dict:
    return {"transitions": list(TRANSITIONS), "effects": list(EFFECTS), "filters": list(FILTERS), "blends": list(BLENDS), "masks": list(MASKS),
            "animations": {k: list(v) for k, v in ANIMATIONS.items()}, "grade": list(GRADE_KEYS), "curves": list(speed.CURVES), "voices": list(VOICES)}


# ── masks: an alpha picture drawn by Pillow, multiplied into the overlay's alpha by ffmpeg ──────────────────────────
def mask_image(m: dict, w: int, h: int, path) -> None:
    """The mask of an overlay as a grey PNG of its size: white = shown, black = hidden."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    x, y, rw, rh = m["rect"] or [0, 0, w, h]
    feather = float(m.get("feather", 0) or 0)
    if m["shape"] == "linear":                      # opaque on one side, transparent on the other, across a band of `feather` pixels
        band = max(2, int(round(feather))) if feather > 0 else max(2, int(round(rw)))
        big = Image.new("L", (3 * w, 3 * h), 255)
        cx, cy = w + x + rw / 2, h + y + rh / 2
        grad = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_270).resize((band, 3 * h))      # 255 at the left → 0 at the right
        big.paste(grad, (int(round(cx - band / 2)), 0))
        big.paste(0, (int(round(cx + band / 2)), 0, 3 * w, 3 * h))
        img = big.rotate(-float(m.get("angle", 0) or 0), resample=Image.BILINEAR, center=(cx, cy)).crop((w, h, 2 * w, 2 * h))
    else:
        img = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(img)
        if m["shape"] == "rect":
            d.rectangle((x, y, x + rw, y + rh), fill=255)
        elif m["shape"] == "rounded":
            d.rounded_rectangle((x, y, x + rw, y + rh), radius=float(m.get("radius") or min(rw, rh) * 0.2), fill=255)
        else:
            d.ellipse((x, y, x + rw, y + rh), fill=255)
        if feather > 0:
            img = img.filter(ImageFilter.GaussianBlur(feather / 2))
    if m.get("invert"):
        img = ImageOps.invert(img)
    img.save(path)
