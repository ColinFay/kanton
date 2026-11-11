"""Copy ffmpeg/ffprobe and every dylib they pull from Homebrew into one folder, rewrite the load commands so the
folder is self-contained (@executable_path / @loader_path), re-sign ad hoc. macOS only.

    python packaging/bundle_ffmpeg.py build/ffmpeg-bin
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOMEBREW = Path(os.environ.get("HOMEBREW_PREFIX", "/opt/homebrew"))
SYSTEM = ("/usr/lib/", "/System/")


def _otool_L(path: Path) -> list[str]:
    out = subprocess.run(["/usr/bin/otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout
    return [line.strip().split(" (")[0] for line in out.splitlines()[1:] if line.strip()]


def _rpaths(path: Path) -> list[str]:
    out = subprocess.run(["/usr/bin/otool", "-l", str(path)], capture_output=True, text=True, check=True).stdout
    return re.findall(r"LC_RPATH\n\s+cmdsize \d+\n\s+path (\S+)", out)


def resolve(ref: str, from_file: Path) -> Path | None:
    """A load-command path → the real file, or None for system libraries."""
    if ref.startswith(SYSTEM):
        return None
    name = Path(ref).name
    cands = []
    if ref.startswith("@rpath/"):
        cands += [Path(rp.replace("@loader_path", str(from_file.parent)).replace("@executable_path", str(from_file.parent))) / ref[len("@rpath/"):] for rp in _rpaths(from_file)]
    elif ref.startswith("@loader_path/"):
        cands.append(from_file.parent / ref[len("@loader_path/"):])
    elif ref.startswith("@executable_path/"):
        cands.append(from_file.parent / ref[len("@executable_path/"):])
    else:
        cands.append(Path(ref))
    cands += [Path(p) for p in glob.glob(str(HOMEBREW / "opt" / "*" / "lib" / name))] + [HOMEBREW / "lib" / name]
    for c in cands:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(f"cannot resolve {ref} (from {from_file})")


def dependency_map(binary: Path) -> dict[str, Path]:
    """Every non-system dylib reachable from `binary`: referenced name (what the load commands say, e.g.
    libavutil.61.dylib) → real file (often a versioned name behind a symlink, e.g. libavutil.61.1.101.dylib)."""
    seen: dict[str, Path] = {}
    todo = [(Path(binary).resolve(), Path(binary).name)]
    while todo:
        f, own = todo.pop()
        for ref in _otool_L(f):
            if ref.startswith(SYSTEM):
                continue
            name = Path(ref).name
            if name == own or name in seen:
                continue                     # own id line, or already collected
            real = resolve(ref, f)
            if real is None:
                continue
            seen[name] = real
            todo.append((real, name))
    return seen


def dependencies(binary: Path) -> list[Path]:
    return sorted(set(dependency_map(binary).values()))


def _rewrite(path: Path, is_lib: bool):
    """Point every Homebrew reference at the bundle. Load commands cannot grow past the header padding, so the
    changes stay short: libraries reference siblings with @loader_path, executables use @executable_path/lib,
    and an rpath on the executables covers the libraries that reference each other through @rpath."""
    changes = []
    if is_lib:
        own_id = subprocess.run(["/usr/bin/otool", "-D", str(path)], capture_output=True, text=True).stdout.splitlines()[-1].strip()
        if own_id.startswith("/"):                 # an absolute id is always longer than @loader_path/<name>: safe to shrink
            changes += ["-id", f"@loader_path/{path.name}"]
    for ref in _otool_L(path):
        if ref.startswith(SYSTEM) or ref.startswith("@loader_path/"):
            continue
        name = Path(ref).name
        if is_lib and name == path.name:
            continue                                   # the library's own id line
        changes += ["-change", ref, f"@loader_path/{name}" if is_lib else f"@executable_path/lib/{name}"]
    if changes:
        subprocess.run(["/usr/bin/install_name_tool", *changes, str(path)], check=True, capture_output=True, text=True)
    if not is_lib:
        subprocess.run(["/usr/bin/install_name_tool", "-add_rpath", "@executable_path/lib", str(path)], capture_output=True, text=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True, text=True)


def bundle(out_dir: str | Path, names=("ffmpeg", "ffprobe"), source_dir: Path | None = None) -> Path:
    out = Path(out_dir)
    lib = out / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    source_dir = source_dir or HOMEBREW / "bin"
    libs: dict[str, Path] = {}
    for n in names:
        src = (source_dir / n).resolve()
        dst = out / n
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        libs.update(dependency_map(src))
    for name, real in libs.items():
        dst = lib / name
        shutil.copy2(real, dst)
        os.chmod(dst, 0o755)
    for name in libs:
        _rewrite(lib / name, is_lib=True)
    for n in names:
        _rewrite(out / n, is_lib=False)
    return out


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("macOS only")
    out = bundle(sys.argv[1] if len(sys.argv) > 1 else "build/ffmpeg-bin")
    print(f"✓ {out}: {len(list((out / 'lib').iterdir()))} dylibs")
