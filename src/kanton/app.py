"""The desktop app: the editor server in a native window (pywebview). `kanton.app` also accepts every CLI
subcommand (`kanton.app/Contents/MacOS/kanton render montage.json`), so one bundle serves both uses."""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

from . import ffmpeg, projects
from .editor import friendly, make_server
from .spec import Spec

CLI_COMMANDS = {"render", "preview", "show", "probe", "frame", "transcribe", "captions", "blanks", "qa", "import-fcproj", "clean", "edit", "words", "new", "catalog"}


def source_key(path: str, taken: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(path).stem).strip("-") or "clip"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def new_spec(videos: list[str], out: str | Path, preset: str | None = None) -> Path:
    """A spec with one block per clip, each covering the whole clip (an image: a 3 s still), to start cutting."""
    d = {"kanton": 1}
    if preset:
        d["preset"] = preset
    sources, blocks = {}, []
    for v in videos:
        info = ffmpeg.probe(v)
        key = source_key(v, sources)
        sources[key] = str(Path(v).resolve())
        blocks.append({"src": key, "dur": 3.0} if info["kind"] == "image" else {"src": key, "in": 0.0, "out": round(info["duration"], 3)})
    d.update({"sources": sources, "blocks": blocks, "texts": [], "overlays": []})
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def new_spec_from_video(video: str, out: str | Path, preset: str | None = None) -> Path:
    return new_spec([video], out, preset)


def start_server(spec: Spec | None, port: int = 0):
    srv = make_server(spec, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Api:
    """What the page can call through `window.pywebview.api`."""

    def __init__(self, srv, window=None):
        self.srv, self.window = srv, window

    def url(self) -> str:
        return f"http://127.0.0.1:{self.srv.server_address[1]}/"

    def current(self) -> dict:
        spec = self.srv.state.spec
        return {"name": spec.path.name if spec and spec.path else None, "path": str(spec.path) if spec and spec.path else None}

    def open_spec(self, path: str) -> dict:
        try:
            self.srv.state.load(path)
        except SystemExit as e:
            return {"ok": False, "error": friendly(str(e))}
        projects.touch(path)
        return {"ok": True, **self.current()}

    # home page: named projects in one folder
    def home(self) -> dict:
        return {"recent": projects.recent(), "projects_dir": str(projects.projects_dir())}

    def create_project(self, name: str) -> dict:
        try:
            return self.open_spec(str(projects.create(name)))
        except (ValueError, OSError) as e:
            return {"ok": False, "error": str(e)}

    def set_projects_dir(self, path: str) -> dict:
        try:
            projects.set_projects_dir(path)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **self.home()}

    def pick_projects_dir_dialog(self) -> dict:
        import webview
        r = self.window.create_file_dialog(webview.FOLDER_DIALOG, directory=str(projects.projects_dir())) if self.window else None
        if not r:
            return {"ok": False, "error": "cancelled"}
        return self.set_projects_dir(r[0] if isinstance(r, (list, tuple)) else r)

    def new_from_video(self, video: str, preset: str | None = None, out: str | None = None) -> dict:
        try:
            out = new_spec_from_video(video, out or Path(video).with_suffix(".kanton.json"), preset=preset)
            self.srv.state.load(out)
        except SystemExit as e:
            return {"ok": False, "error": friendly(str(e))}
        projects.touch(out)
        return {"ok": True, **self.current()}

    # dialogs (need the window)
    def open_spec_dialog(self) -> dict:
        import webview
        r = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("kanton project (*.json)",)) if self.window else None
        if not r:
            return {"ok": False, "error": "cancelled"}
        return self.open_spec(r[0] if isinstance(r, (list, tuple)) else r)

    def new_from_video_dialog(self) -> dict:
        import webview
        r = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("Video (*.mp4;*.mov;*.MP4;*.MOV;*.m4v;*.mkv)",)) if self.window else None
        if not r:
            return {"ok": False, "error": "cancelled"}
        video = r[0] if isinstance(r, (list, tuple)) else r
        out = self.window.create_file_dialog(webview.SAVE_DIALOG, directory=str(Path(video).parent), save_filename="montage.json",
                                             file_types=("kanton project (*.json)",))        # where the project lives (a folder can be created here)
        if not out:
            return {"ok": False, "error": "cancelled"}
        return self.new_from_video(video, out=out[0] if isinstance(out, (list, tuple)) else out)

    def pick_model_dialog(self) -> dict:
        import webview
        r = self.window.create_file_dialog(webview.FOLDER_DIALOG) if self.window else None
        if not r:
            return {"ok": False, "error": "cancelled"}
        return {"ok": True, "path": r[0] if isinstance(r, (list, tuple)) else r}

    def reveal(self, path: str) -> dict:
        import subprocess
        subprocess.run(["open", "-R", path]) if sys.platform == "darwin" else None
        return {"ok": True}


def run_gui(spec_path: str | None):
    import webview
    spec = Spec.load(spec_path) if spec_path else None
    srv = start_server(spec, 0)
    api = Api(srv)
    window = webview.create_window("kanton", api.url(), js_api=api, width=1400, height=860, min_size=(900, 600))
    api.window = window
    webview.start()
    srv.shutdown()


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [a for a in argv if not a.startswith("-psn_")]           # macOS launch services
    if argv and (argv[0] in CLI_COMMANDS or argv[0].startswith("-")):
        from .cli import main as cli_main
        return cli_main(argv)
    run_gui(argv[0] if argv else None)


if __name__ == "__main__":
    main()
