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
    monkeypatch.setenv("KANTON_AGENT", "cli"); monkeypatch.setenv("KANTON_CLAUDE", str(fake)); monkeypatch.setenv("FAKE_LOG", str(log))
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


# ---- the default agent: a token (a fake API on localhost) ----

WRITE = {"path": "montage.json", "content": ""}   # filled by the fixture: the spec with blocks.1.zoom = 110


class FakeAPI(threading.Thread):
    """Anthropic-style and OpenAI-style: first reply asks for a kanton call, the next one (after the tool result) says it is done."""
    def __init__(self):
        super().__init__(daemon=True)
        import http.server
        outer = self; self.requests = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                if self.path.endswith("/v1/messages"):
                    done = body["messages"][-1]["role"] == "user" and isinstance(body["messages"][-1]["content"], list)
                    r = {"content": [{"type": "text", "text": "Done."}]} if done else {"content": [
                        {"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "t1", "name": "write_file", "input": WRITE}]}
                else:
                    done = body["messages"][-1]["role"] == "tool"
                    r = {"choices": [{"message": {"role": "assistant", "content": "Done."}}]} if done else {"choices": [{"message": {
                        "role": "assistant", "content": "Looking.", "tool_calls": [{"id": "c1", "type": "function", "function": {
                            "name": "write_file", "arguments": json.dumps(WRITE)}}]}}]}
                out = json.dumps(r).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def run(self):
        self.srv.serve_forever()


@pytest.fixture
def api(monkeypatch, server):
    spec = server['spec']
    a = FakeAPI(); a.start()
    d = json.loads(spec.read_text()); d["blocks"][1]["zoom"] = 110; WRITE["content"] = json.dumps(d)
    for k in ("KANTON_AGENT", "KANTON_AGENT_CMD", "KANTON_AGENT_API", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KANTON_AGENT_TOKEN", "sk-test"); monkeypatch.setenv("KANTON_AGENT_URL", a.url)
    yield a
    a.srv.shutdown()


def test_token_is_the_default_and_edits_the_file(server, api):
    st, _ = call(server["url"] + "/api/chat", {"text": "zoom 110 on clip 2"})
    assert st == 200
    j = wait(server["url"])
    assert j["status"] == "done", j
    assert json.loads(server["spec"].read_text())["blocks"][1]["zoom"] == 110
    assert [m["role"] for m in j["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert api.requests[0]["headers"]["X-Api-Key"] == "sk-test" and not server["log"].exists()   # no claude run


def test_token_openai_flavour(server, api, monkeypatch):
    monkeypatch.setenv("KANTON_AGENT_API", "openai")
    call(server["url"] + "/api/chat", {"text": "zoom"})
    j = wait(server["url"])
    assert j["status"] == "done", j
    assert api.requests[0]["path"].endswith("/chat/completions") and api.requests[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert json.loads(server["spec"].read_text())["blocks"][1]["zoom"] == 110


def test_token_keeps_the_conversation(server, api):
    call(server["url"] + "/api/chat", {"text": "one"}); wait(server["url"])
    call(server["url"] + "/api/chat", {"text": "two"}); wait(server["url"])
    first_user = [m for m in api.requests[-1]["body"]["messages"] if m["role"] == "user" and m["content"] == "one"]
    assert first_user


def test_no_token_says_how_to_give_one(server, monkeypatch):
    for k in ("KANTON_AGENT", "KANTON_AGENT_CMD", "KANTON_AGENT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    st, j = call(server["url"] + "/api/chat", {"text": "hello"})
    assert st == 200 and j["status"] == "error" and "KANTON_AGENT_TOKEN" in j["error"] and "KANTON_AGENT=cli" in j["error"]


def test_api_error_is_reported(server, api, monkeypatch):
    monkeypatch.setenv("KANTON_AGENT_URL", "http://127.0.0.1:1")
    call(server["url"] + "/api/chat", {"text": "hello"})
    j = wait(server["url"])
    assert j["status"] == "error" and "cannot reach" in j["error"]


def test_any_command_line_agent(server, tmp_path, monkeypatch):
    script = tmp_path / "myagent"; script.write_text(f"#!{sys.executable}\nimport sys\nprint('heard: ' + sys.argv[-1])\n"); script.chmod(0o755)
    monkeypatch.setenv("KANTON_AGENT_CMD", str(script))
    monkeypatch.delenv("KANTON_AGENT", raising=False)
    call(server["url"] + "/api/chat", {"text": "hi there"})
    j = wait(server["url"])
    assert j["status"] == "done" and j["messages"][-1]["text"] == "heard: hi there"


def test_tools_stay_in_the_spec_folder(tmp_path):
    from kanton import agent
    (tmp_path / "a.txt").write_text("hi")
    assert agent.run_tool("read_file", {"path": "a.txt"}, tmp_path) == "hi"
    assert "outside" in agent.run_tool("read_file", {"path": "../x"}, tmp_path)
    assert "outside" in agent.run_tool("write_file", {"path": "/etc/x", "content": "y"}, tmp_path)
    assert "show" in agent.run_tool("kanton", {"args": "--help"}, tmp_path)


def test_provider_key_follows_the_chosen_api(monkeypatch):
    from kanton import agent
    for k in ("KANTON_AGENT_TOKEN", "KANTON_AGENT_API", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant"); monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    assert agent.config()["token"] == "sk-ant"
    monkeypatch.setenv("KANTON_AGENT_API", "openai")
    assert agent.config()["token"] == "sk-oai"          # never the Anthropic key sent to OpenAI
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(agent.AgentError, match="OPENAI_API_KEY"):
        agent.config()
