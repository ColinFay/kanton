# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: python -m PyInstaller packaging/kanton.spec   (run packaging/build.sh instead)
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src" / "kanton"
FFBIN = ROOT / "build" / "ffmpeg-bin"
VERSION = next(l.split('"')[1] for l in (ROOT / "pyproject.toml").read_text().splitlines() if l.startswith("version"))

datas = [(str(SRC / "presets"), "kanton/presets"), (str(SRC / "fonts"), "kanton/fonts"), (str(SRC / "editor" / "index.html"), "kanton/editor"),
         (str(ROOT / "packaging" / "THIRD_PARTY.md"), "."), (str(ROOT / "LICENSE"), ".")]
binaries = [(str(FFBIN / "ffmpeg"), "bin"), (str(FFBIN / "ffprobe"), "bin")] + [(str(p), "bin/lib") for p in (FFBIN / "lib").glob("*.dylib")]
# the transcriber (faster-whisper) and what it loads at run time; its model is fetched on first use, not shipped
whisper = [collect_all(m) for m in ("faster_whisper", "ctranslate2", "onnxruntime", "huggingface_hub", "tokenizers", "av")]
datas += [d for w in whisper for d in w[0]]; binaries += [b for w in whisper for b in w[1]]; hidden = [h for w in whisper for h in w[2]]

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT / "src")], binaries=binaries, datas=datas,
             hiddenimports=["kanton.cli", "kanton.app", "kanton.editor", "kanton.render", "kanton.captions", "kanton.blanks", "kanton.qa", "kanton.fcproj", "kanton.transcribe"] + hidden,
             hookspath=[], excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="kanton", debug=False, strip=False, upx=False, console=False, argv_emulation=False, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="kanton")
app = BUNDLE(coll, name="kanton.app", icon=str(ROOT / "packaging" / "kanton.icns"), bundle_identifier="fr.colinfay.kanton",
             info_plist={"CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION, "NSHighResolutionCapable": True,
                         "CFBundleDocumentTypes": [{"CFBundleTypeName": "kanton montage", "CFBundleTypeExtensions": ["json"], "CFBundleTypeRole": "Editor"}],
                         "LSMinimumSystemVersion": "12.0"})
