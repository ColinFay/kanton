"""An agent reached by a token: a small tool loop over an HTTP API, no SDK (urllib only).

Three tools: `kanton` (the arguments of a CLI command, run in the spec's folder), `read_file` and `write_file` (inside
that folder only). The model edits the same JSON file as every other agent; the editor follows the file.

KANTON_AGENT_TOKEN    the token (else ANTHROPIC_API_KEY, else OPENAI_API_KEY)
KANTON_AGENT_API      "anthropic" (Messages API, default) or "openai" (chat completions: OpenAI and the many servers that speak it)
KANTON_AGENT_URL      base URL (default: the provider's)
KANTON_AGENT_MODEL    model name (default: claude-sonnet-5-5, or gpt-4.1 for "openai")
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

MAX_STEPS = 12
_DEFAULTS = {"anthropic": ("https://api.anthropic.com", "claude-sonnet-5-5"), "openai": ("https://api.openai.com/v1", "gpt-4.1")}
_P = lambda **props: {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()}, "required": list(props)}
TOOLS = [
    {"name": "kanton", "description": "Run one kanton CLI command (arguments only, no shell, no leading `kanton`), e.g. `show montage.json`. "
                                      "Runs in the spec's folder; returns its output.", "schema": _P(args="the command's arguments")},
    {"name": "read_file", "description": "Read a text file of the spec's folder (the montage.json, for one).", "schema": _P(path="relative path")},
    {"name": "write_file", "description": "Write a text file of the spec's folder, whole (edit montage.json this way: read it, change it, write it back).",
     "schema": _P(path="relative path", content="the whole new content")},
]


class AgentError(Exception):
    pass


def config() -> dict:
    """Token, API flavour, URL and model from the environment; AgentError when there is no token."""
    env = os.environ
    api = env.get("KANTON_AGENT_API") or ("openai" if not env.get("KANTON_AGENT_TOKEN") and not env.get("ANTHROPIC_API_KEY") else "anthropic")
    if api not in _DEFAULTS:
        raise AgentError(f"KANTON_AGENT_API must be anthropic or openai, not {api!r}")
    # the key of the chosen API only: never send an Anthropic key to OpenAI, or the reverse
    provider_key = "ANTHROPIC_API_KEY" if api == "anthropic" else "OPENAI_API_KEY"
    token = env.get("KANTON_AGENT_TOKEN") or env.get(provider_key)
    if not token:
        raise AgentError(f"no agent token for the {api} API: set KANTON_AGENT_TOKEN (or {provider_key}), "
                         "or use a command-line agent with KANTON_AGENT=cli")
    url, model = _DEFAULTS[api]
    return {"token": token, "api": api, "url": (env.get("KANTON_AGENT_URL") or url).rstrip("/"), "model": env.get("KANTON_AGENT_MODEL") or model}


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AgentError(f"{e.code} from {url}: {e.read().decode('utf-8', 'replace')[:500]}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AgentError(f"cannot reach {url}: {e}") from None


def run_kanton(args: str, cwd: Path) -> str:
    """The tool: a kanton command, without a shell. Output (both streams) tail-capped."""
    try:
        argv = shlex.split(args)
    except ValueError as e:
        return f"bad arguments: {e}"
    exe = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "kanton"]
    try:
        pr = subprocess.run(exe + argv, cwd=cwd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "timeout after 600 s"
    out = (pr.stdout + pr.stderr).strip()
    return (out[-8000:] or "(no output)") + ("" if pr.returncode == 0 else f"\n[exit {pr.returncode}]")


def _inside(path: str, cwd: Path) -> Path:
    p = (cwd / path).resolve()
    if not p.is_relative_to(cwd.resolve()):
        raise AgentError(f"{path!r} is outside the spec's folder")
    return p


def run_tool(name: str, a: dict, cwd: Path) -> str:
    """Run one tool call; its failures are returned as text for the model to read, never raised."""
    try:
        if name == "kanton":
            return run_kanton(str(a.get("args") or ""), cwd)
        if name == "read_file":
            return _inside(str(a.get("path") or ""), cwd).read_text()[-20000:]
        if name == "write_file":
            p = _inside(str(a.get("path") or ""), cwd)
            p.write_text(str(a.get("content") or ""))
            return f"wrote {p.name}"
        return f"unknown tool {name!r}"
    except (AgentError, OSError, UnicodeDecodeError) as e:
        return f"error: {e}"


def _line(name: str, a: dict) -> str:
    return f"{name}: {str(a.get('args') or a.get('path') or '')[:160]}"


def turn(history: list, text: str, system: str, cwd: Path, emit: Callable[[str, str], None]) -> None:
    """One user turn: `history` (kept by the caller, in the provider's own format) grows with the exchange; `emit(role, text)`
    is called with "assistant" text and "tool" lines as they happen. Raises AgentError."""
    cfg = config()
    anth = cfg["api"] == "anthropic"
    if anth:
        url, headers = cfg["url"] + "/v1/messages", {"x-api-key": cfg["token"], "anthropic-version": "2023-06-01"}
        tools = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in TOOLS]
    else:
        url, headers = cfg["url"] + "/chat/completions", {"Authorization": "Bearer " + cfg["token"]}
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}} for t in TOOLS]
    history.append({"role": "user", "content": text})
    for _ in range(MAX_STEPS):
        if anth:
            r = _post(url, headers, {"model": cfg["model"], "max_tokens": 4096, "system": system, "tools": tools, "messages": history})
            blocks = r.get("content") or []
            history.append({"role": "assistant", "content": blocks})
            results = []
            for b in blocks:
                if b.get("type") == "text" and b.get("text"):
                    emit("assistant", b["text"])
                elif b.get("type") == "tool_use":
                    a = b.get("input") or {}
                    emit("tool", _line(b.get("name"), a))
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": run_tool(b.get("name"), a, cwd)})
            if not results:
                return
            history.append({"role": "user", "content": results})
        else:
            r = _post(url, headers, {"model": cfg["model"], "tools": tools, "messages": [{"role": "system", "content": system}] + history})
            try:
                m = r["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                raise AgentError(f"unexpected reply: {json.dumps(r)[:300]}") from None
            history.append(m)
            if m.get("content"):
                emit("assistant", m["content"])
            calls = m.get("tool_calls") or []
            for c in calls:
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    a = {}
                emit("tool", _line(c["function"].get("name"), a))
                history.append({"role": "tool", "tool_call_id": c["id"], "content": run_tool(c["function"].get("name"), a, cwd)})
            if not calls:
                return
    raise AgentError(f"stopped after {MAX_STEPS} steps; send another message to carry on")
