"""The chat in the editor: one turn = `claude -p` run in the spec's folder (a fake `claude` here, through KANTON_CLAUDE)."""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kanton import editor
from kanton.spec import Spec

FAKE = '''#!{py}
import json, os, sys, time
log = open(os.environ["FAKE_LOG"], "a"); log.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n"); log.close()
text = sys.argv[sys.argv.index("-p") + 1]
say = lambda d: print(json.dumps({{**d, "session_id": "s-1"}}), flush=True)
say({{"type": "system", "subtype": "init"}})
if "fail" in text:
    say({{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "no credit left"}}); sys.exit(1)
if "slow" in text:
    time.sleep(1.0)
say({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Bash", "input": {{"command": "kanton show montage.json"}}}}]}}}})
if "zoom" in text:
    d = json.load(open("montage.json")); d["blocks"][1]["zoom"] = 110; json.dump(d, open("montage.json", "w"))
say({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Zoom set to 110 on clip 2."}}]}}}})
say({{"type": "result", "subtype": "success", "is_error": False, "result": "Zoom set to 110 on clip 2."}})
'''


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def server(media, spec_file, tmp_path, monkeypatch):
    p = spec_file({"sources": {"face": str(media["face"])}, "blocks": [{"src": "face", "in": 0.5, "out": 2.0}, {"src": "face", "in": 3.0, "out": 4.0, "zoom": 104}]})
    fake = tmp_path / "claude"; fake.write_text(FAKE.format(py=sys.executable)); fake.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("KANTON_CLAUDE", str(fake)); monkeypatch.setenv("FAKE_LOG", str(log))
    port = free_port()
    srv = editor.make_server(Spec.load(p), port)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    yield {"url": f"http://127.0.0.1:{port}", "spec": p, "log": log}
    srv.shutdown()


def call(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait(url):
    for _ in range(100):
        st, j = call(url + "/api/chat")
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("the turn never ended")


def test_a_turn_runs_claude_in_the_spec_folder_and_the_page_follows_the_file(server):
    st, j = call(server["url"] + "/api/chat", {"text": "zoom 110 on clip 2"})
    assert st == 200 and j["status"] == "running" and j["messages"][0] == {"role": "user", "text": "zoom 110 on clip 2"}
    j = wait(server["url"])
    assert j["status"] == "done" and j["session"] == "s-1" and j["error"] is None
    assert [m["role"] for m in j["messages"]] == ["user", "tool", "assistant"]
    assert j["messages"][1]["text"] == "Bash: kanton show montage.json" and j["messages"][2]["text"] == "Zoom set to 110 on clip 2."
    c = json.loads(server["log"].read_text().splitlines()[0])
    assert c["cwd"] == str(Path(server["spec"]).parent) and "--resume" not in c["argv"]
    a = c["argv"]
    assert a[a.index("-p") + 1] == "zoom 110 on clip 2" and a[a.index("--output-format") + 1] == "stream-json"
    assert a[a.index("--permission-mode") + 1] == "acceptEdits" and a[a.index("--allowedTools") + 1] == "Bash(kanton:*)"
    assert str(server["spec"]) in a[a.index("--append-system-prompt") + 1]
    assert call(server["url"] + "/api/spec")[1]["spec"]["blocks"][1]["zoom"] == 110                  # the agent's edit is served
    call(server["url"] + "/api/chat", {"text": "and now the title"})
    j = wait(server["url"])
    c2 = json.loads(server["log"].read_text().splitlines()[1])
    assert c2["argv"][c2["argv"].index("--resume") + 1] == "s-1" and len(j["messages"]) == 6     # same session, history kept


def test_a_failed_turn_is_reported(server):
    call(server["url"] + "/api/chat", {"text": "please fail"})
    j = wait(server["url"])
    assert j["status"] == "error" and "no credit left" in j["error"]


def test_one_turn_at_a_time(server):
    assert call(server["url"] + "/api/chat", {"text": "slow one"})[0] == 200
    st, j = call(server["url"] + "/api/chat", {"text": "another"})
    assert st == 409 and "answering" in j["error"]
    wait(server["url"])
    assert call(server["url"] + "/api/chat", {"text": ""})[0] == 400


def test_without_claude_the_error_says_how_to_point_at_it(server, monkeypatch):
    monkeypatch.setenv("KANTON_CLAUDE", "/nonexistent/claude")
    st, j = call(server["url"] + "/api/chat", {"text": "hello"})
    assert st == 200 and j["status"] == "error" and "KANTON_CLAUDE" in j["error"]


def test_the_page_has_the_agent_tab(server):
    with urllib.request.urlopen(server["url"] + "/") as r:
        body = r.read()
    assert b'data-tab="agent"' in body and b"/api/chat" in body
