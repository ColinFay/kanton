"""Beats of a music (auto beat), without numpy: the onset strength (rise of the log energy, 100 frames a
second), the tempo that best repeats it (autocorrelation weighted towards 120 bpm), a grid on that tempo, each beat
pulled to the strongest onset near it and dropped where the music has none. A hypothesis until judged on real music."""
from __future__ import annotations

import array
import math
import statistics
import subprocess
import sys

from . import ffmpeg

RATE, HOP = 8000, 80                    # 100 envelope frames a second


def _onsets(path: str) -> list[float]:
    pcm = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = array.array("h")
    x.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        x.byteswap()
    e = [math.log(1e-6 + sum(v * v for v in x[i:i + HOP]) / HOP / 32768 ** 2) for i in range(0, len(x) - HOP + 1, HOP)]
    return [0.0] + [max(0.0, b - a) for a, b in zip(e, e[1:])]


def detect(path: str) -> list[float]:
    """Beat times in seconds of the file."""
    o = _onsets(path)
    n = len(o)
    if n < 100 or max(o) < 0.3:
        return []
    r = {L: sum(o[k] * o[k + L] for k in range(n - L)) * math.exp(-0.5 * math.log2(L / 50) ** 2) for L in range(30, 101)}
    L = max(r, key=r.get)
    a, b, c = r.get(L - 1, r[L]), r[L], r.get(L + 1, r[L])
    P = L + (0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c < 0 else 0.0)        # the period, between two lags
    phase = max(range(L), key=lambda f: sum(o[round(f + k * P)] for k in range(int((n - 1 - f) / P) + 1)))
    w, top, out = max(1, round(P / 8)), max(o), []
    for k in range(int((n - 1 - phase) / P) + 1):
        g = round(phase + k * P)
        near = range(max(0, g - w), min(n, g + w + 1))
        best = max(near, key=lambda i: o[i])
        if o[best] >= 0.2 * top:
            out.append(round(best * HOP / RATE, 3))
    return out


def bpm(beats: list[float]) -> float | None:
    return 60 / statistics.median(b - a for a, b in zip(beats, beats[1:])) if len(beats) > 2 else None
