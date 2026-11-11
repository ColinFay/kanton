"""Measure a spec and its export against the rules. Read-only."""
from __future__ import annotations

import difflib
import re
import tempfile
import unicodedata
from pathlib import Path

from . import ffmpeg, fx, transcribe
from .render import overlay_box
from .spec import Spec
from .text import TextStyle


def F(level: str, rule: str, msg: str) -> dict:
    return {"level": level, "rule": rule, "msg": msg}


def check_spec(spec: Spec) -> list[dict]:
    out = []
    pre = spec.preset
    fps = spec.fps
    min_keep = int((pre.get("blanks") or {}).get("min_keep_frames", 9))
    frag = [(b["i"], b["frames"]) for b in spec.blocks if b["frames"] < min_keep]
    out.append(F("error" if frag else "ok", "fragments", f"blocks under {min_keep} frames: {frag}" if frag else f"no block under {min_keep} frames"))
    short = [(b["i"], b["frames"]) for b in spec.blocks if min_keep <= b["frames"] < 20]
    out.append(F("warn" if short else "ok", "short_blocks", f"blocks of {min_keep}-19 frames (a face needs ~20 to read): {short}" if short else "no block under 20 frames"))
    first = [o["src"] for o in spec.overlays if o["kind"] in ("video", "image") and o["t"] <= 1e-9]
    out.append(F("error" if first else "ok", "first_frame", f"an overlay covers the first frame: {first}" if first else "first frame = main track"))
    # zooms
    zs = [z for b in spec.blocks for z in b["zoom"]]
    border = [(b["i"], b["zoom"]) for b in spec.blocks if min(b["zoom"]) < 100 - 1e-6]
    out.append(F("error" if border else "ok", "zoom_border", f"zoom under 100 shows a border: {border}" if border else "no zoom under 100"))
    zcfg = spec.zoom or {}
    v = zcfg.get("validated") or [100, 106]
    s = zcfg.get("sourced") or [110, 120]
    hors = sorted({round(z, 1) for z in zs if not (v[0] - 1e-6 <= z <= v[1] + 1e-6)})
    if not hors:
        out.append(F("ok", "zoom_range", f"zoom levels within the validated range {v}"))
    else:
        tag = "sourced" if all(s[0] <= z <= s[1] for z in hors) else "unsourced"
        out.append(F("info", "zoom_range", f"zoom levels outside the validated range {v}: {hors} ({tag}, hypothesis to judge)"))
    # texts
    cap = pre.get("captions") or {}
    mn, mx = int(cap.get("min_frames", 25)), int(cap.get("max_frames", 210))
    sz = pre.get("safe_zone") or {}
    dirs = [Path(pre.get("_dir", spec.dir)), spec.dir]
    bad_dur, bad_width, bad_zone = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for k, t in enumerate(spec.texts):
            fr = round(t["dur"] * fps)
            if not mn <= fr <= mx:
                bad_dur.append((k, fr))
            st = TextStyle(spec.text_style(t), dirs)
            meta = st.render(t["text"], spec.W, spec.H, Path(tmp) / f"{k}.png")
            if len(meta["lines"]) > st.max_lines or any(w > st.max_width for w in meta["widths"]):
                bad_width.append((k, len(meta["lines"]), [round(w) for w in meta["widths"]]))
            if meta["bbox"] and sz:
                x0, y0, x1, y1 = meta["bbox"]
                if y0 < sz.get("top", 0) or y1 > sz.get("bottom", spec.H) + 30 or x0 < sz.get("left", 0) or x1 > sz.get("right", spec.W):
                    bad_zone.append((k, f"y {y0}-{y1} x {x0}-{x1}"))
    if spec.texts:
        out.append(F("warn" if bad_dur else "ok", "text_duration", f"texts outside {mn}-{mx} frames: {bad_dur}" if bad_dur else f"all texts within {mn}-{mx} frames"))
        out.append(F("warn" if bad_width else "ok", "text_width", f"texts over max_width or max_lines: {bad_width}" if bad_width else "all text lines fit"))
        out.append(F("error" if bad_zone else "ok", "safe_zone", f"texts outside the safe zone (top ≥ {sz.get('top')}, bottom ≤ {sz.get('bottom')}, x {sz.get('left')}-{sz.get('right')}): {bad_zone}" if bad_zone else "all texts inside the safe zone"))
        ts = sorted(spec.texts, key=lambda t: t["t"])
        ov = [(round(a["t"], 2), round(b["t"], 2)) for a, b in zip(ts, ts[1:]) if a["t"] + a["dur"] > b["t"] + 1e-6]
        out.append(F("warn" if ov else "ok", "text_overlap", f"texts overlapping in time: {ov}" if ov else "no text overlap"))
    if spec.music:
        g = spec.music["gain_db"]
        out.append(F("warn" if g > -20 else "info", "music_level", f"music gain {g:+.1f} dB: check it sits ~27 dB under the voice on a phone speaker"))
    off = []
    for k, o in enumerate(spec.overlays):
        x, y, w, h = overlay_box(spec.W, spec.H, o, ffmpeg.probe(o["path"]))
        if x < -1 or y < -1 or x + w > spec.W + 1 or y + h > spec.H + 1:
            off.append((k, o["src"], f"{x:.0f},{y:.0f} {w}×{h}"))
    if spec.overlays:
        out.append(F("warn" if off else "ok", "overlay_frame", f"overlays partly outside the frame: {off}" if off else "all overlays inside the frame"))
    long = [(b["i"], b["cut"], b["cut_dur"]) for a, b in zip(spec.blocks, spec.blocks[1:]) if fx.is_xfade(b["cut"]) and b["cut_dur"] > min(a["dur"], b["dur"]) + 1e-9]
    if long:
        out.append(F("info", "transition_length", f"transitions longer than a block beside them, shortened to fit: {long}"))
    looks = bool(spec.grade) or any(b["grade"] for b in spec.blocks) or any(o["grade"] for o in spec.overlays)
    if looks:
        out.append(F("info", "look", "grade / filter values are a hypothesis: judge the look on the export"))
    audio = ([spec.music] if spec.music else []) + spec.sounds
    guess = [w for w, on in (("named speed curve", any(isinstance(spec.data["blocks"][b["i"]].get("speed"), str) for b in spec.blocks)),
                             ("voice effect", any(x.get("voice") for x in spec.blocks + audio)), ("beats", any(m["beats"] for m in audio))) if on]
    if guess:
        out.append(F("info", "hypothesis", f"{', '.join(guess)}: values not validated on real footage yet, judge them on the export"))
    hlg = sorted({b["src"] for b in spec.blocks if ffmpeg.is_hlg(ffmpeg.probe(b["path"]))} | {o["src"] for o in spec.overlays if o["path"] and ffmpeg.is_hlg(ffmpeg.probe(o["path"]))})
    if hlg and not (pre.get("hlg_lut") or spec.data.get("hlg_lut")):
        out.append(F("info", "hlg", f"HLG sources converted with the generic matrix (no hlg_lut in the preset): {hlg}"))
    return out


def check_export(spec: Spec, path: str | Path) -> list[dict]:
    out = []
    i = ffmpeg.probe(str(path))
    ex = spec.export
    ok_fmt = (i["width"], i["height"]) == (spec.W, spec.H) and abs(i["fps"] - spec.fps) < 0.01 and i["codec"] == "h264"
    out.append(F("ok" if ok_fmt else "error", "format", f"{i['width']}×{i['height']} {i['fps']:g} fps {i['codec']}" + ("" if ok_fmt else f" (expected {spec.W}×{spec.H} {spec.fps:g} fps h264)")))
    dd = abs(i["duration"] - spec.duration)
    out.append(F("ok" if dd <= 2.5 / spec.fps else "error", "duration", f"{i['duration']:.2f} s (spec {spec.duration:.2f} s)"))
    mbps = i["bitrate"] / 1e6
    cap = min(float(ex.get("maxrate_cap_mbps", 25)), float(ex.get("mbit_seconds", 2400)) / max(i["duration"], 1.0))
    mn = float(ex.get("min_mbps", 12))
    lvl = "error" if mbps > cap + 0.5 else ("warn" if mbps < mn else "ok")
    out.append(F(lvl, "bitrate", f"{mbps:.1f} Mbit/s (quality floor {mn:g}, API ceiling {cap:.1f})"))
    out.append(F("ok" if i["has_audio"] else "error", "audio", "audio track present" if i["has_audio"] else "no audio track"))
    rate = None
    err = ffmpeg.run_capture(["-i", str(path), "-filter_complex", "ebur128", "-f", "null", "-"])
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    try:
        import json, subprocess
        pr = json.loads(subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate", "-of", "json", str(path)], capture_output=True, text=True).stdout)
        rate = int(pr["streams"][0]["sample_rate"])
    except Exception:
        pass
    out.append(F("ok" if rate == 48000 else "error", "audio_rate", f"audio {rate} Hz" if rate else "audio rate unknown"))
    mb = i["size"] / 1e6
    out.append(F("ok" if mb <= float(ex.get("max_mb", 300)) else "error", "file_size", f"{mb:.0f} MB (max {ex.get('max_mb', 300)})"))
    if m:
        lufs = float(m[-1])
        target = float((spec.preset.get("audio") or {}).get("lufs", -16.5))
        out.append(F("ok" if abs(lufs - target) <= 0.5 else "error", "loudness", f"{lufs:.1f} LUFS (target {target:g} ± 0.5)"))
    else:
        out.append(F("error", "loudness", "could not measure loudness"))
    return out


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"-->|\d+:\d+:\d+[,.]\d+", " ", t)
    return re.sub(r"[^a-z0-9 ]", " ", t).split()


def check_text(path: str | Path, reference: str, backend: str = "auto", words: list[dict] | None = None, language: str = "fr") -> list[dict]:
    """Transcribe an export and compare with the reference text (plain text or .srt content); list gaps ≥ 0.5 s between words."""
    if words is None:
        words = transcribe.transcribe(str(path), 0.0, None, backend=backend, language=language)
    ref = _norm(reference)
    heard = _norm(" ".join(w["word"] for w in words))
    sm = difflib.SequenceMatcher(a=ref, b=heard, autojunk=False)
    diffs = [f"« {' '.join(ref[a0:a1])} » heard « {' '.join(heard[b0:b1])} »" for op, a0, a1, b0, b1 in sm.get_opcodes() if op != "equal"]
    ok = sm.ratio() > 0.95 and not diffs
    out = [F("ok" if ok else "error", "words", f"{len(ref)} expected, {len(heard)} heard, ratio {sm.ratio():.3f}" + ("" if ok else "; " + " | ".join(diffs)))]
    gaps = [(round(a["end"], 2), round(b["start"] - a["end"], 2), a["word"], b["word"]) for a, b in zip(words, words[1:]) if b["start"] - a["end"] >= 0.5]
    out.append(F("error" if gaps else "ok", "word_gaps", f"gaps ≥ 0.5 s between words: {gaps}" if gaps else "no gap ≥ 0.5 s between words"))
    return out


def has_errors(findings: list[dict]) -> bool:
    return any(f["level"] == "error" for f in findings)


def report(findings: list[dict]) -> str:
    tag = {"ok": "✓", "warn": "!", "error": "✗", "info": "H"}
    lines = [f"[{tag[f['level']]}] {f['rule']}: {f['msg']}" for f in findings]
    n = {k: sum(1 for f in findings if f["level"] == k) for k in tag}
    lines.append(f"{n['error']} errors, {n['warn']} warnings, {n['info']} hypotheses, {n['ok']} ok")
    return "\n".join(lines)
