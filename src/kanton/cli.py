"""kanton — a JSON cut list rendered by ffmpeg."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _spec(path: str):
    from .spec import Spec
    return Spec.load(path)


def _parse_words_args(items: list[str] | None) -> dict:
    """--words src=file.json [src=file.json …] → {src: words}"""
    from .transcribe import words_from_json
    out = {}
    for it in items or []:
        if "=" not in it:
            sys.exit(f"kanton: --words expects src=file.json, got {it!r}")
        src, f = it.split("=", 1)
        out[src] = words_from_json(f)
    return out


def words_for(spec, src: str, backend: str, python: str | None, language: str, force: bool = False) -> list[dict]:
    """Transcribe one source over the union of its blocks, cached in .kanton-tmp/<spec>/words-<src>.json."""
    from .transcribe import save_words, transcribe, words_from_json
    cache = spec.dir / ".kanton-tmp" / (spec.path.stem if spec.path else "spec") / f"words-{src}.json"
    bl = [b for b in spec.blocks for _ in [0] if b["src"] == src] + [b for b in spec.data.get("blocks_raw", []) if b.get("src") == src]
    if not bl:
        sys.exit(f"kanton: no block uses source {src!r}")
    lo, hi = min(float(b["in"]) for b in bl), max(float(b["out"]) for b in bl)
    meta = cache.with_suffix(".range.json")
    if cache.exists() and meta.exists() and not force:
        m = json.loads(meta.read_text())
        if m["lo"] <= lo + 1e-6 and m["hi"] >= hi - 1e-6:
            return words_from_json(cache)
    print(f"transcribing {src} {lo:.1f}-{hi:.1f} s…", flush=True)
    words = transcribe(spec.source(src), lo, hi, backend=backend, language=language, python=python)
    cache.parent.mkdir(parents=True, exist_ok=True)
    save_words(words, cache)
    meta.write_text(json.dumps({"lo": lo, "hi": hi}))
    return words


def cmd_render(a):
    from .render import render
    spec = _spec(a.spec)
    if getattr(a, "format", None):
        spec.export["format"] = a.format
    if getattr(a, "cover", None) is not None:
        spec.export["cover"] = a.cover
    fmt = "mp4" if a.proxy else spec.export["format"]
    out = Path(a.out) if a.out else spec.dir / (spec.path.stem + ("-proxy" if a.proxy else "") + "." + fmt)
    render(spec, out, proxy=a.proxy, verbose=a.verbose, tmp=a.tmp)


def cmd_new(a):
    from .app import new_spec
    out = new_spec(a.media, a.out or "montage.json", preset=a.preset)
    d = json.loads(Path(out).read_text())
    print(f"✓ {len(d['blocks'])} blocks → {out}")


def cmd_catalog(a):
    from . import fx
    c = fx.catalog()
    if a.group and a.group not in c:
        sys.exit(f"kanton: unknown catalog {a.group!r} ({', '.join(c)})")
    if a.json:
        print(json.dumps(c[a.group] if a.group else c, indent=1))
    elif a.group:
        v = c[a.group]
        print("\n".join(f"{k}: {' '.join(x)}" for k, x in v.items()) if isinstance(v, dict) else " ".join(v))
    else:
        for k, v in c.items():
            print(f"{k}: " + (" | ".join(f"{kk}: {' '.join(x)}" for kk, x in v.items()) if isinstance(v, dict) else " ".join(v)))


def cmd_preview(a):
    a.proxy = True
    cmd_render(a)


def cmd_show(a):
    print(_spec(a.spec).describe())


def cmd_probe(a):
    from .ffmpeg import probe
    print(json.dumps(probe(a.media), indent=1))


def cmd_frame(a):
    from .ffmpeg import run
    run(["-ss", str(a.at), "-i", a.media, "-frames:v", "1", a.out])
    print(f"✓ {a.out}")


def cmd_transcribe(a):
    from .transcribe import save_words, transcribe
    words = transcribe(a.media, a.start, a.end, backend=a.backend, language=a.lang, python=a.python)
    out = Path(a.out) if a.out else Path(a.media).with_suffix(".words.json")
    save_words(words, out)
    print(f"✓ {len(words)} words → {out}")
    if a.text:
        print(" ".join(w["word"] for w in words))


def cmd_captions(a):
    from .captions import apply_captions
    spec = _spec(a.spec)
    given = _parse_words_args(a.words)                           # .srt / words JSON brought by the user: they replace the transcript
    stored_words = spec.data.get("words") or {}
    spec.data["words"] = stored_words
    if given:
        stored_words.update(given)
    words = dict(stored_words)
    for src in sorted({b["src"] for b in spec.blocks}):
        if src not in given and (a.force or src not in words):   # --force: transcribe again even if the spec already holds words
            stored_words.pop(src, None)                          # the old transcript no longer matches the captions
            words[src] = words_for(spec, src, a.backend, a.python, a.lang, force=a.force)
    n = apply_captions(spec, words)
    spec.save()
    print(f"✓ {n} captions → {spec.path}")


def cmd_words(a):
    from . import words as W
    spec = _spec(a.spec)
    spec.data.setdefault("words", {}).update(_parse_words_args(a.words))
    W.transcribe_missing(spec, a.backend, a.lang, a.python)
    def rng(s):                                    # A-B@K: words A..B, the K-th time they are played (from 1)
        r, _, k = s.partition("@")
        x, _, y = r.partition("-")
        return int(x), int(y or x), int(k or 1) - 1
    if a.cut:
        spec = W.cut(spec, *rng(a.cut))
    elif a.keep:
        spec = W.keep(spec, *rng(a.keep))
    elif a.move or a.copy:
        x, y, o = rng(a.move or a.copy)
        n, _, bo = rng(a.before) if a.before else (None, None, 0)
        spec = (W.move if a.move else W.copy)(spec, x, y, n, o, bo)
    elif a.fix:
        n, _, text = a.fix.partition("=")
        spec = W.fix(spec, int(n), text)
    spec.save()
    print(" ".join(f"[{e['n']}:{e['word']}]" if e["t"] is None else f"{e['n']}{'@' + str(e['o'] + 1) if e['o'] else ''}:{e['word']}" for e in W.view(spec)))
    print(f"{len(spec.blocks)} blocks, {spec.duration:.2f} s")


def cmd_beats(a):
    from .beats import bpm, detect
    if not a.target.endswith(".json"):
        print(json.dumps(detect(a.target)))
        return
    spec = _spec(a.target)
    if not spec.music:
        sys.exit("kanton: this spec has no music")
    b = detect(spec.music["path"])
    spec.data["music"]["beats"] = b
    spec.save()
    print(f"✓ {len(b)} beats" + (f", {bpm(b):.0f} bpm" if bpm(b) else "") + " → music.beats")


def cmd_blanks(a):
    from .blanks import apply_blanks, simulate_audio
    spec = _spec(a.spec)
    words = None
    if a.mode == "words":
        words = _parse_words_args(a.words)
        for src in sorted({b["src"] for b in spec.data.get("blocks_raw", spec.data["blocks"])}):
            if src not in words:
                words[src] = words_for(spec, src, a.backend, a.python, a.lang)
    cfg = {k: getattr(a, k) for k in ("min", "pre", "post", "min_cut") if getattr(a, k) is not None}
    rep = apply_blanks(spec, mode=a.mode, words_by_src=words, db=a.db, cfg=cfg)
    for s, e in rep["ranges"]:
        print(f"  cut {s:8.2f} → {e:8.2f}  ({e - s:.2f} s)")
    print(f"{rep['blocks']} blocks, {rep['duration']:.2f} s, removed {rep['removed']:.2f} s")
    if a.sim:
        from .transcribe import transcribe
        wav = simulate_audio(spec, spec.dir / ".kanton-tmp" / "sim.wav")
        ws = transcribe(str(wav), 0.0, None, backend=a.backend, language=a.lang, python=a.python)
        print("heard: " + " ".join(w["word"] for w in ws))
    if a.dry:
        print("(dry run: spec not written)")
    else:
        spec.save()
        print(f"✓ {spec.path}")


def cmd_qa(a):
    from .qa import check_export, check_spec, check_text, has_errors, report
    spec = _spec(a.spec)
    findings = check_spec(spec)
    if a.export:
        findings += check_export(spec, a.export)
        if a.text:
            ref = Path(a.text).read_text(encoding="utf-8") if Path(a.text).is_file() else a.text
            findings += check_text(a.export, ref, backend=a.backend, language=a.lang)
    print(report(findings))
    sys.exit(1 if has_errors(findings) else 0)


def cmd_import_fcproj(a):
    from .fcproj import convert_file
    out = Path(a.out) if a.out else Path(a.project).with_suffix(".json")
    p = convert_file(a.project, out, preset=a.preset)
    d = json.loads(p.read_text())
    print(f"✓ {len(d['blocks'])} blocks, {len(d.get('overlays', []))} overlays, {len(d.get('texts', []))} texts → {p}")
    for n in d.get("notes", []):
        print("  note: " + n)


def cmd_clean(a):
    from .render import clean
    clean(_spec(a.spec))
    print("✓ cleaned")


def cmd_edit(a):
    from .editor import serve
    serve(_spec(a.spec), port=a.port, open_browser=not a.no_open)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="kanton", description=__doc__)
    ap.add_argument("--version", action="version", version=f"kanton {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, fn, help=None, **kw):
        p = sub.add_parser(name, **({"help": help} if help else {}), **kw)   # no help = not listed
        p.set_defaults(fn=fn)
        return p

    def whisper_opts(p):
        p.add_argument("--backend", default="auto", help="auto | mlx | faster | python")
        p.add_argument("--python", help="interpreter that has mlx-whisper or faster-whisper (or KANTON_WHISPER_PYTHON)")
        p.add_argument("--lang", default="fr")

    p = add("render", cmd_render, "spec → video (x264 mp4 by default; mov, webm, gif), loudness, overlays, texts, music")
    p.add_argument("spec"); p.add_argument("-o", "--out"); p.add_argument("--proxy", action="store_true", help="half size, fast encode")
    p.add_argument("--format", choices=["mp4", "mov", "webm", "gif"], help="container / codec (default: the spec's export.format, mp4)")
    p.add_argument("--cover", type=float, metavar="T", help="also write the frame at T seconds as <out>.png")
    p.add_argument("--verbose", action="store_true"); p.add_argument("--tmp", help="working directory (default .kanton-tmp next to the spec)")
    p = add("new", cmd_new, "start a montage.json: one block per clip, whole clips")
    p.add_argument("media", nargs="+"); p.add_argument("-o", "--out"); p.add_argument("--preset")
    p = add("catalog", cmd_catalog, "the names an agent can use: transitions, effects, filters, blends, masks, animations, grade keys")
    p.add_argument("group", nargs="?"); p.add_argument("--json", action="store_true")
    p = add("preview", cmd_preview, "quick half-size render (<spec>-proxy.mp4)")
    p.add_argument("spec"); p.add_argument("-o", "--out"); p.add_argument("--verbose", action="store_true"); p.add_argument("--tmp")
    p = add("show", cmd_show, "print the timeline"); p.add_argument("spec")
    p = add("probe", cmd_probe, "media facts (size, fps, duration, HLG, alpha)"); p.add_argument("media")
    p = add("frame", cmd_frame, "one image of a media at a time (s)")
    p.add_argument("media"); p.add_argument("--at", type=float, required=True); p.add_argument("-o", "--out", required=True)
    p = add("transcribe", cmd_transcribe, "media → words with times (JSON)")
    p.add_argument("media"); p.add_argument("--from", dest="start", type=float, default=0.0); p.add_argument("--to", dest="end", type=float)
    p.add_argument("-o", "--out"); p.add_argument("--text", action="store_true", help="also print the text"); whisper_opts(p)
    p = add("captions", cmd_captions, "write the caption texts of a spec from the transcription of its sources")
    p.add_argument("spec"); p.add_argument("--words", nargs="*", metavar="SRC=FILE", help="words JSON or .srt per source (skips transcription)")
    p.add_argument("--force", action="store_true", help="transcribe again even if cached or already in the spec"); whisper_opts(p)
    p = add("words", cmd_words, "edit by the words: print them numbered ([n:cut], n@2: played a second time), cut / keep / move / copy / fix "
                                "(transcribes once into the spec)")
    p.add_argument("spec"); p.add_argument("--words", nargs="*", metavar="SRC=FILE", help="words JSON or .srt per source (skips transcription)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--cut", metavar="A-B[@K]", help="@K: the K-th time the words are played (default 1)")
    g.add_argument("--keep", metavar="A-B[@K]", help="put cut words back where the reel skips them")
    g.add_argument("--move", metavar="A-B[@K]"); g.add_argument("--copy", metavar="A-B[@K]", help="play the words once more (see --before)")
    g.add_argument("--fix", metavar="N=TEXT", help="correct the text of a word")
    p.add_argument("--before", metavar="N[@K]", help="with --move / --copy: play the words before word N (default: at the end)"); whisper_opts(p)
    p = add("blanks", cmd_blanks, "cut the blanks inside the blocks (replayable from blocks_raw)")
    p.add_argument("spec"); p.add_argument("--mode", choices=["energy", "words"], default="energy")
    p.add_argument("--db", type=float, help="silence threshold in dBFS (energy mode, default from preset)")
    p.add_argument("--min", type=float, help="shortest blank to cut, s"); p.add_argument("--pre", type=float); p.add_argument("--post", type=float); p.add_argument("--min-cut", dest="min_cut", type=float)
    p.add_argument("--words", nargs="*", metavar="SRC=FILE"); p.add_argument("--dry", action="store_true"); p.add_argument("--sim", action="store_true", help="transcribe the result and print what is heard")
    whisper_opts(p)
    p = add("qa", cmd_qa, "measure a spec (and an export) against the rules; exit 1 on errors")
    p.add_argument("spec"); p.add_argument("--export"); p.add_argument("--text", help="reference text or .srt to compare with the export's transcription"); whisper_opts(p)
    p = add("import-fcproj", cmd_import_fcproj)                          # a FilmCraft project → spec; undocumented on purpose
    p.add_argument("project"); p.add_argument("-o", "--out"); p.add_argument("--preset")
    p = add("beats", cmd_beats, "beats of the music (seconds of the file) → music.beats, or printed for an audio file"); p.add_argument("target", metavar="spec|audio")
    p = add("clean", cmd_clean, "remove the .kanton-tmp cache of a spec"); p.add_argument("spec")
    p = add("edit", cmd_edit, "open the timeline editor in the browser")
    p.add_argument("spec"); p.add_argument("--port", type=int, default=8788); p.add_argument("--no-open", action="store_true")

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
