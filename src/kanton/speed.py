"""Speed ramps (curves). A block's `speed` is a number, a curve name, or points [[position, speed], ...]: position
0-1 along the block's source span, speed between them linear. Reel time is the integral of 1/speed over the source, in
closed form per segment: one `setpts` expression for the picture, `atempo` driven by `asendcmd` for the sound. The named
curves are hypotheses until judged on real footage."""
from __future__ import annotations

import math
import sys

CURVES = {
    "montage": [[0, 1], [0.2, 3], [0.4, 0.6], [0.6, 3], [0.8, 0.6], [1, 1]],
    "hero": [[0, 2], [0.35, 2], [0.5, 0.3], [0.65, 2], [1, 2]],
    "bullet": [[0, 3], [0.3, 0.2], [0.7, 0.2], [1, 3]],
    "jump_cut": [[0, 1], [0.45, 1], [0.5, 6], [0.55, 1], [1, 1]],
    "flash_in": [[0, 5], [0.4, 1], [1, 1]],
    "flash_out": [[0, 1], [0.6, 1], [1, 5]],
}
LO, HI = 0.1, 10.0
STEP = 0.05                 # seconds of source between two tempo commands


def points(v) -> list[tuple[float, float]] | None:
    """The curve of a `speed` value: None for a number (constant speed), else sorted points from position 0 to 1."""
    if v is None or isinstance(v, (int, float)):
        return None
    if isinstance(v, str):
        if v not in CURVES:
            sys.exit(f"kanton: unknown speed curve {v!r} ({', '.join(CURVES)})")
        v = CURVES[v]
    try:
        pts = sorted((float(p), float(s)) for p, s in v)
    except (TypeError, ValueError):
        sys.exit(f"kanton: a speed curve is [[position 0-1, speed], ...]: {v}")
    if len(pts) < 2 or not all(0 <= p <= 1 and LO <= s <= HI for p, s in pts):
        sys.exit(f"kanton: a speed curve needs two points or more, positions 0-1 and speeds {LO:g}-{HI:g}: {v}")
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1:
        pts.append((1.0, pts[-1][1]))
    return pts


def _seg(dx: float, v0: float, v1: float, x: float) -> float:
    """Seconds of reel for the first x source seconds of a segment dx long going from speed v0 to v1."""
    k = (v1 - v0) / dx if dx > 0 else 0.0
    return x / v0 if abs(k) < 1e-12 else math.log((v0 + k * x) / v0) / k


def reel_offset(pts, L: float, s: float) -> float:
    """Source offset s (seconds from the block's in, span L) → reel offset."""
    out = 0.0
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        a, b = p0 * L, p1 * L
        if s <= a:
            break
        out += _seg(b - a, v0, v1, min(s, b) - a)
    return out


def duration(pts, L: float) -> float:
    return reel_offset(pts, L, L)


def setpts(pts, L: float) -> str:
    """setpts expression: source time from the block's start (T-STARTT) → reel time, piecewise."""
    x, expr = "(T-STARTT)", None
    for (p0, v0), (p1, v1) in reversed(list(zip(pts, pts[1:]))):
        a, dx = p0 * L, (p1 - p0) * L
        c = reel_offset(pts, L, a)
        k = (v1 - v0) / dx if dx > 0 else 0.0
        f = f"({c:.6f}+({x}-{a:.6f})/{v0:.6f})" if abs(k) < 1e-12 else f"({c:.6f}+log(({v0:.6f}+{k:.6f}*({x}-{a:.6f}))/{v0:.6f})/{k:.6f})"
        expr = f if expr is None else f"if(lt({x},{p1 * L:.6f}),{f},{expr})"
    return f"setpts='({expr})/TB'"


def tempo_commands(pts, L: float) -> str:
    """asendcmd lines: every STEP of source, the four atempo stages each take the 4th root of that stretch's mean speed."""
    lines, n = [], max(1, math.ceil(L / STEP - 1e-9))
    for j in range(n):
        a, b = j * L / n, (j + 1) * L / n
        q = ((b - a) / (reel_offset(pts, L, b) - reel_offset(pts, L, a))) ** 0.25
        lines.append(f"{a:.4f} " + ", ".join(f"atempo@c{i} tempo {q:.5f}" for i in range(4)) + ";")
    return "\n".join(lines) + "\n"


def audio_chain(pts, L: float, cmd_file) -> str:
    """The sound of a curved block (from its source start), each filter followed by a comma; `cmd_file` gets the commands."""
    from pathlib import Path
    from .fx import _esc
    Path(cmd_file).write_text(tempo_commands(pts, L))
    return f"asetpts=PTS-STARTPTS,asendcmd=f='{_esc(cmd_file)}'," + "".join(f"atempo@c{i}=1," for i in range(4))
