"""Projects of the desktop app: one folder holds them all (changeable in the settings), a recent list for the home page.
Settings live in a per-user file (`KANTON_HOME` overrides the folder); the projects themselves are plain `montage.json`."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

RECENT_MAX = 12
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def config_dir() -> Path:
    if os.environ.get("KANTON_HOME"):
        return Path(os.environ["KANTON_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kanton"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kanton"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kanton"


def _load() -> dict:
    try:
        d = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    f = config_dir() / "settings.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def projects_dir() -> Path:
    p = _load().get("projects_dir")
    return Path(p) if p else Path.home() / "Documents" / "kanton"


def set_projects_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    _save({**_load(), "projects_dir": str(p)})
    return p


def folder_name(name: str) -> str:
    """A project name made safe as a folder name on macOS, Windows and Linux."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name).strip(" .-")
    if not s:
        raise ValueError("give the project a name")
    return f"{s}-project" if s.upper() in _RESERVED else s


def create(name: str) -> Path:
    """`<projects dir>/<name>/montage.json`, an empty cut list ready for media."""
    folder = projects_dir() / folder_name(name)
    out = folder / "montage.json"
    if out.exists() or (folder.exists() and any(folder.iterdir())):
        raise ValueError(f"a project called “{folder.name}” already exists")
    folder.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"kanton": 1, "sources": {}, "blocks": [], "texts": [], "overlays": []}, indent=1) + "\n", encoding="utf-8")
    return out


def _recent_paths(d: dict) -> list[str]:
    r = d.get("recent")
    return [p for p in r if isinstance(p, str)] if isinstance(r, list) else []


def touch(path: str | Path) -> None:
    """Put a project at the head of the recent list."""
    d, p = _load(), str(Path(path).resolve())
    d["recent"] = [p] + [r for r in _recent_paths(d) if r != p][:RECENT_MAX - 1]
    try:
        _save(d)
    except OSError:
        pass


def recent() -> list[dict]:
    out = []
    for p in _recent_paths(_load()):
        f = Path(p)
        if f.is_file():
            out.append({"name": f.parent.name if f.name == "montage.json" else f.stem, "path": p, "mtime": f.stat().st_mtime})
    return out
