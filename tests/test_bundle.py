"""Relocating ffmpeg + ffprobe with their dylibs into a self-contained folder (macOS, Homebrew build)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import bundle_ffmpeg  # noqa: E402

HB = Path("/opt/homebrew/bin/ffmpeg")
pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not HB.exists(), reason="macOS + Homebrew ffmpeg only")


def test_dependencies_are_resolved_recursively():
    deps = bundle_ffmpeg.dependencies(HB)
    assert deps and all(p.exists() for p in deps)
    assert any("libavcodec" in p.name for p in deps) and any("libx264" in p.name for p in deps)


def test_bundle_runs_without_homebrew(tmp_path):
    out = bundle_ffmpeg.bundle(tmp_path / "bin", ["ffmpeg", "ffprobe"])
    assert (out / "ffmpeg").exists() and (out / "ffprobe").exists() and (out / "lib").is_dir()
    moved = tmp_path / "elsewhere"
    shutil.move(str(out), str(moved))                       # must not depend on its build location
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD")}
    env["PATH"] = "/usr/bin:/bin"                             # no Homebrew on the PATH
    r = subprocess.run([str(moved / "ffmpeg"), "-version"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "ffmpeg version" in r.stdout
    r = subprocess.run([str(moved / "ffprobe"), "-version"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    links = subprocess.run(["/usr/bin/otool", "-L", str(moved / "ffmpeg")], capture_output=True, text=True).stdout
    assert "/opt/homebrew" not in links
    for lib in (moved / "lib").iterdir():
        assert "/opt/homebrew" not in subprocess.run(["/usr/bin/otool", "-L", str(lib)], capture_output=True, text=True).stdout, lib.name
