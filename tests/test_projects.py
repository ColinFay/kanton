"""Home page: named projects in one folder (set in the settings), a recent list, and the Api that serves them."""
import json

import pytest

from kanton import app, projects


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KANTON_HOME", str(tmp_path / "cfg"))
    return tmp_path


def test_default_projects_dir_is_in_the_user_home(monkeypatch, tmp_path):
    monkeypatch.setattr(projects.Path, "home", classmethod(lambda cls: tmp_path / "me"))
    assert projects.projects_dir() == tmp_path / "me" / "Documents" / "kanton"


def test_set_projects_dir_persists(tmp_path):
    projects.set_projects_dir(tmp_path / "all")
    assert projects.projects_dir() == tmp_path / "all"
    assert json.loads((tmp_path / "cfg" / "settings.json").read_text())["projects_dir"] == str(tmp_path / "all")


def test_create_makes_an_empty_project_in_its_own_folder(tmp_path):
    projects.set_projects_dir(tmp_path / "all")
    p = projects.create("My first reel")
    assert p == tmp_path / "all" / "My first reel" / "montage.json"
    d = json.loads(p.read_text())
    assert d["blocks"] == [] and d["sources"] == {}
    with pytest.raises(ValueError, match="already"):
        projects.create("My first reel")


@pytest.mark.parametrize("name,folder", [("a/b:c?", "a-b-c"), ("  ..x.. ", "x"), ("CON", "CON-project")])
def test_names_are_safe_on_every_os(tmp_path, name, folder):
    projects.set_projects_dir(tmp_path / "all")
    assert projects.create(name).parent.name == folder


def test_empty_name_is_refused():
    for n in ("", "   ", "///"):
        with pytest.raises(ValueError, match="name"):
            projects.create(n)


def test_recent_most_recent_first_without_duplicates_or_missing_files(tmp_path):
    projects.set_projects_dir(tmp_path / "all")
    a, b = projects.create("a"), projects.create("b")
    projects.touch(a); projects.touch(b); projects.touch(a)
    assert [r["name"] for r in projects.recent()] == ["a", "b"]
    b.unlink()
    assert [r["name"] for r in projects.recent()] == ["a"]


def test_api_home_create_and_settings(tmp_path):
    srv = app.start_server(None, port=0)
    try:
        api = app.Api(srv)
        assert api.home()["recent"] == []
        assert api.set_projects_dir(str(tmp_path / "all"))["projects_dir"] == str(tmp_path / "all")
        r = api.create_project("Reel")
        assert r["ok"] and r["path"] == str(tmp_path / "all" / "Reel" / "montage.json") and srv.state.spec.path.exists()
        assert [x["name"] for x in api.home()["recent"]] == ["Reel"]
        assert not api.create_project("Reel")["ok"]
        assert api.open_spec(str(tmp_path / "all" / "Reel" / "montage.json"))["ok"]
    finally:
        srv.shutdown()
