"""Text layers rendered with Pillow: exact font file, stroke, line step, wrap by measured width."""
from __future__ import annotations

import itertools
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONT_DIRS = [HERE / "fonts", Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path.home() / ".fonts",
             Path.home() / ".local" / "share" / "fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def find_font(ref: str, extra_dirs: list[Path] = ()) -> str:
    return _find_font(ref, tuple(str(d) for d in extra_dirs))


@lru_cache(maxsize=None)
def _find_font(ref: str, extra_dirs: tuple[str, ...]) -> str:
    """Once per font and folders: a TextStyle per text would otherwise walk the project folder (rglob) for each one."""
    p = Path(ref).expanduser()
    if p.is_file():
        return str(p)
    for d in list(extra_dirs) + FONT_DIRS:
        d = Path(d)
        if (d / ref).is_file():
            return str(d / ref)
        if d.is_dir():
            for f in d.rglob(ref):
                return str(f)
    sys.exit(f"kanton: font not found: {ref} (put the file next to the preset, or give an absolute path)")


@lru_cache(maxsize=None)
def load_font(path: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, int(round(size)))


@lru_cache(maxsize=1 << 16)
def _width(font_path: str, size: float, stroke_width: int, line: str) -> float:
    """Measured width of a line, stroke included (the wrap measures the same pieces of line many times)."""
    box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), line, font=load_font(font_path, size), stroke_width=stroke_width)
    return box[2] - box[0]


class TextStyle:
    def __init__(self, s: dict, search_dirs: list[Path] = ()):
        self.font_path = find_font(s.get("font", "Anton-Regular.ttf"), search_dirs)
        self.size = float(s.get("size", 60))
        self.fill = hex_rgb(s.get("fill", "#FFFFFF"))
        self.stroke = hex_rgb(s.get("stroke", "#000000"))
        self.stroke_width = int(round(float(s.get("stroke_width", 7))))
        self.uppercase = bool(s.get("uppercase", True))
        self.align = s.get("align", "center")
        self.max_width = int(s.get("max_width", 780))
        self.max_lines = int(s.get("max_lines", 2))
        self.x = float(s.get("x", 540))
        self.y_baseline = float(s.get("y", s.get("y_baseline", 1432)))
        self.font = load_font(self.font_path, self.size)
        asc, desc = self.font.getmetrics()
        ls = s.get("line_step")
        self.line_step = float(ls) if ls else float(asc + desc) + float(s.get("leading", 0) or 0)

    def width(self, line: str) -> float:
        return _width(self.font_path, self.size, self.stroke_width, line)

    def prepare(self, text: str) -> str:
        return text.upper() if self.uppercase else text

    def wrap(self, text: str) -> list[str]:
        """Keep given line breaks when every line fits; otherwise rebalance into the fewest lines ≤ max_width."""
        text = self.prepare(text)
        lines = [l for l in text.split("\n")]
        if all(self.width(l) <= self.max_width for l in lines):
            return lines
        words = text.replace("\n", " ").split()
        if not words:
            return [""]
        for n in range(1, max(self.max_lines, 1) + 2):
            best = None
            if n > len(words):
                break
            for cuts in itertools.combinations(range(1, len(words)), n - 1):
                ls = [" ".join(words[a:b]) for a, b in zip((0,) + cuts, cuts + (len(words),))]
                m = max(self.width(l) for l in ls)
                if best is None or m < best[0]:
                    best = (m, ls)
            if best and best[0] <= self.max_width:
                return best[1]
        # greedy fallback, lines may exceed max_width (reported by qa)
        out, line = [], ""
        for w in words:
            cand = (line + " " + w).strip()
            if self.width(cand) <= self.max_width or not line:
                line = cand
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
        return out

    def layout(self, lines: list[str], W: int, H: int) -> list[tuple[float, float, str]]:
        """(x, y_top_for_draw, line) per line: the last line's baseline sits at y_baseline."""
        asc, _ = self.font.getmetrics()
        out = []
        n = len(lines)
        for k, line in enumerate(lines):
            baseline = self.y_baseline - self.line_step * (n - 1 - k)
            w = self.width(line)
            if self.align == "left":
                x = self.x
            elif self.align == "right":
                x = self.x - w
            else:
                x = self.x - w / 2
            out.append((x, baseline - asc, line))
        return out

    def tokens(self, lines: list[str]) -> list[tuple[int, int, str]]:
        """(line index, x offset in the line, word) for every word, in reading order."""
        out = []
        for k, line in enumerate(lines):
            pos = 0
            for w in line.split(" "):
                if w:
                    out.append((k, self.font.getlength(line[:pos]), w))
                pos += len(w) + 1
        return out

    def _draw(self, d: ImageDraw.ImageDraw, lay: list[tuple[float, float, str]], fill, stroke, visible_words: int | None, visible_chars: int | None):
        """The lines, whole or cut to the first `visible_words` words / `visible_chars` characters."""
        nw = nc = 0
        for x, y, line in lay:
            if visible_chars is not None:
                line = line[:max(0, visible_chars - nc)]
                nc += len(line) + 1
            elif visible_words is not None:
                ws = line.split(" ")
                line = " ".join(ws[:max(0, visible_words - nw)])
                nw += len(ws)
            if line:
                d.text((x, y), line, font=self.font, fill=fill, stroke_width=self.stroke_width, stroke_fill=stroke)

    def render(self, text: str, W: int, H: int, out_path: str | Path, shadow: dict | None = None, box: dict | None = None, opacity: float = 1.0,
               rotation: float = 0.0, highlight: dict | None = None, highlight_word: int | None = None, visible_words: int | None = None,
               visible_chars: int | None = None, crop: bool = False) -> dict:
        """Full-frame RGBA PNG (`crop`: cut to what it draws, its corner on the frame at `origin`); returns the bounding box of
        drawn pixels, the glyphs' own box and the lines."""
        lines = self.wrap(text)
        lay = self.layout(lines, W, H)
        fill, stroke = self.fill + (255,), self.stroke + (255,)
        glyphs = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(glyphs)
        self._draw(d, lay, fill, stroke, visible_words, visible_chars)
        if highlight is not None and highlight_word is not None:                 # the word being said, drawn again over itself
            toks = self.tokens(lines)
            if 0 <= highlight_word < len(toks):
                k, off, w = toks[highlight_word]
                x, y, _ = lay[k]
                hfill, sc = hex_rgb(highlight.get("fill", "#FFFFFF")) + (255,), float(highlight.get("scale", 1.0) or 1.0)
                hstroke = hex_rgb(highlight["stroke"]) + (255,) if highlight.get("stroke") else stroke
                if abs(sc - 1.0) < 1e-6:
                    d.text((x + off, y), w, font=self.font, fill=hfill, stroke_width=self.stroke_width, stroke_fill=hstroke)
                else:                                                          # bigger, on the same baseline, centred on the word's place
                    big = load_font(self.font_path, self.size * sc)
                    asc, _ = self.font.getmetrics()
                    basc, _ = big.getmetrics()
                    adv, badv = self.font.getlength(w), big.getlength(w)
                    d.text((x + off + (adv - badv) / 2, y + asc - basc), w, font=big, fill=hfill, stroke_width=int(round(self.stroke_width * sc)), stroke_fill=hstroke)
        text_bbox = glyphs.getbbox()
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if box and text_bbox:
            pad, r = float(box.get("padding", 20)), float(box.get("radius", 16))
            x0, y0, x1, y1 = text_bbox
            bd = ImageDraw.Draw(img)
            bd.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=r, fill=hex_rgb(box.get("color", "#000000")) + (int(round(255 * float(box.get("opacity", 0.6)))),))
        if shadow and text_bbox:
            sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sc = hex_rgb(shadow.get("color", "#000000")) + (255,)
            self._draw(ImageDraw.Draw(sh), lay, sc, sc, visible_words, visible_chars)
            if shadow.get("blur"):
                sh = sh.filter(ImageFilter.GaussianBlur(float(shadow["blur"])))
            a = sh.getchannel("A").point(lambda v: int(v * float(shadow.get("opacity", 0.6))))
            sh.putalpha(a)
            moved = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            moved.paste(sh, (int(round(float(shadow.get("dx", 4)))), int(round(float(shadow.get("dy", 4))))))
            img.alpha_composite(moved)
        img.alpha_composite(glyphs)
        if opacity < 1.0:
            img.putalpha(img.getchannel("A").point(lambda v: int(v * max(0.0, opacity))))
        if rotation and text_bbox:
            cx, cy = (text_bbox[0] + text_bbox[2]) / 2, (text_bbox[1] + text_bbox[3]) / 2
            img = img.rotate(-rotation, resample=Image.BICUBIC, center=(cx, cy))
        bbox = img.getbbox()
        origin = (max(0, bbox[0] - 8) // 2 * 2, max(0, bbox[1] - 8) // 2 * 2) if bbox and crop else (0, 0)
        if bbox and crop:   # with a transparent margin, on the chroma grid: the same frames, far fewer pixels per frame
            img = img.crop((*origin, min(W, bbox[2] + 8), min(H, bbox[3] + 8)))
        img.save(out_path)
        return {"lines": lines, "bbox": bbox, "origin": origin, "text_bbox": text_bbox, "widths": [self.width(l) for l in lines]}


def states(style: TextStyle, text: str, W: int, H: int, out_stem: str | Path, t0: float, dur: float, words: list[dict] | None = None,
           highlight: dict | None = None, reveal: str | None = None, **extra) -> list[dict]:
    """The pictures of a text over time: one per word lit (karaoke, from `words` in reel time) or per word / character shown
    (typewriter; evenly spread when the words have no times). Each state: {png, t, dur, bbox}."""
    prepared = style.prepare(text)
    toks = prepared.split()
    timed = [w for w in (words or []) if w["dur"] >= 0] if words and len(words) == len(toks) else None
    end, out = t0 + dur, []

    def add(k: int, t: float, **kw):
        png = Path(f"{out_stem}-{k:03d}.png")
        meta = style.render(text, W, H, png, **extra, **kw)
        out.append({"png": str(png), "t": t, "bbox": meta["bbox"], "origin": meta["origin"]})

    if reveal == "char":
        n = max(1, len("".join(prepared.split("\n"))) if "\n" in prepared else len(prepared))
        n = max(1, len(prepared))
        for i in range(n):
            add(i, t0 + dur * i / n, visible_chars=i + 1)
    elif reveal == "word":
        for i in range(len(toks)):
            add(i, max(t0, timed[i]["t"]) if timed else t0 + dur * i / len(toks), visible_words=i + 1)
    elif highlight is not None and timed:
        if timed[0]["t"] > t0 + 1e-6:
            add(0, t0)
        for i, w in enumerate(timed):
            add(i + 1, max(t0, w["t"]), highlight=highlight, highlight_word=i)
    else:
        add(0, t0)
    out = [s for s in out if s["t"] < end - 1e-9]
    for a, b in zip(out, out[1:] + [{"t": end}]):
        a["dur"] = b["t"] - a["t"]
    return [s for s in out if s["dur"] > 1e-9]
