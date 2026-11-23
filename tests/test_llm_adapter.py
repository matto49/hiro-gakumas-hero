"""LLM 适配层：协议、OpenAI 兼容端点（本地假服务）、Mock、非法输出走规则兜底、调用记录进契约。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import hiro  # noqa: F401
from hiro.brain.llm import ClaudeCLI, LLMAdapter, MockLLM, OpenAICompatible, extract_json, from_spec, prompt_version
from hiro.contracts import Decision  # noqa: F401

import pytest


class _FakeOpenAI(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[dict] = []  # noqa: RUF012

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOpenAI.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if _FakeOpenAI.mode == "error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "boom"}')
            return
        content = '{"action": "skip", "reason": "test"}' if _FakeOpenAI.mode == "ok" else "我不知道"
        payload = {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 321, "completion_tokens": 12},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # 静音
        pass


@pytest.fixture(scope="module")
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_adapters_satisfy_protocol_and_spec_parsing():
    assert (
        isinstance(ClaudeCLI(), LLMAdapter)
        and isinstance(OpenAICompatible("m", api_key="k"), LLMAdapter)
        and isinstance(MockLLM(), LLMAdapter)
    )
    assert from_spec("claude:opus").model == "opus" and from_spec("claude").model == "sonnet"
    o = from_spec("openai:deepseek-chat", base_url="http://x/v1/", api_key="k")
    assert (o.name, o.model, o.base_url) == ("openai", "deepseek-chat", "http://x/v1")
    assert from_spec("mock").name == "mock"
    with pytest.raises(ValueError):
        from_spec("openai")
    with pytest.raises(ValueError):
        from_spec("gemini:pro")


def test_openai_compatible_roundtrip(fake_server, monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(
            k, raising=False
        )  # 本机假服务：环境里的代理（Windows 实机设了 HTTPS_PROXY）会把请求转走、回 502
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    llm = OpenAICompatible("test-model", base_url=fake_server, api_key="sk-test", timeout=5)
    _FakeOpenAI.mode = "ok"
    r = llm.ask_json("sys", "user")
    assert r.data == {"action": "skip", "reason": "test"} and r.error is None
    assert (r.adapter, r.model, r.input_tokens, r.output_tokens) == ("openai", "test-model", 321, 12)
    req = _FakeOpenAI.seen[-1]
    assert req["path"].endswith("/chat/completions") and req["auth"] == "Bearer sk-test"
    assert req["body"]["messages"][0] == {"role": "system", "content": "sys"} and req["body"]["response_format"] == {
        "type": "json_object"
    }
    _FakeOpenAI.mode = "nojson"
    r = llm.ask_json("sys", "user")
    assert r.data is None and r.error == "no json in reply" and r.raw == "我不知道"
    _FakeOpenAI.mode = "error"
    r = llm.ask_json("sys", "user")
    assert r.data is None and r.error == "http 500"
    r = OpenAICompatible("m", base_url="http://127.0.0.1:9/v1", api_key="k", timeout=1).ask_json("s", "u")
    assert r.error.startswith("timeout/network")


def test_extract_json_and_prompt_version():
    assert extract_json('前言 ```json\n{"a": 1}\n``` 后记') == {"a": 1}
    assert extract_json('{"a": {"b": 2}} 尾巴') == {"a": {"b": 2}} and extract_json("没有") is None
    assert (
        prompt_version("exam.md").startswith("exam@1.")
        and prompt_version("exam.md", "idols/example_idol.md").count("@") == 2
    )


def test_claude_cli_retries_once_on_nonzero_exit(monkeypatch):
    """实机 2026-10-31 12:36–12:39：claude -p 连续 exit 1。returncode≠0 重试一次，error 带 stderr 前 300 字。"""
    import subprocess
    from types import SimpleNamespace

    calls = []
    ok = json.dumps({"result": '{"action": "skip", "reason": "r"}', "usage": {"input_tokens": 5, "output_tokens": 3}})

    def run(results):
        def fake(cmd, **kw):
            calls.append(cmd)
            return results[len(calls) - 1]

        return fake

    monkeypatch.setattr(
        subprocess,
        "run",
        run(
            [
                SimpleNamespace(returncode=1, stdout="{}", stderr="boom"),
                SimpleNamespace(returncode=0, stdout=ok, stderr=""),
            ]
        ),
    )
    r = ClaudeCLI().ask_json("s", "u")
    assert len(calls) == 2 and r.error is None and r.data == {"action": "skip", "reason": "r"}

    calls.clear()
    monkeypatch.setattr(subprocess, "run", run([SimpleNamespace(returncode=1, stdout="", stderr="x" * 400)] * 2))
    r = ClaudeCLI().ask_json("s", "u")
    assert len(calls) == 2 and r.data is None and r.error.startswith("exit 1") and r.error.endswith(": " + "x" * 300)


def test_claude_cli_waits_on_usage_limit_then_recovers(monkeypatch):
    """用量上限（duration_api_ms=0、exit 1）不静默兜底：等待后重试，恢复后返回正常结果，并计数。"""
    import json as _json
    import subprocess

    from hiro.brain import llm as llm_mod

    limited = _json.dumps({"duration_api_ms": 0, "result": "", "usage": {"output_tokens": 0}})
    ok = _json.dumps({"result": '{"action": "skip", "reason": "ok"}', "usage": {"input_tokens": 1, "output_tokens": 1}})
    replies = [(1, limited), (1, limited), (1, limited), (1, limited), (0, ok)]

    def fake_run(*a, **k):
        rc, out = replies.pop(0)
        return subprocess.CompletedProcess(a, rc, out, "")

    slept = []
    monkeypatch.setattr(llm_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: slept.append(s))
    cli = llm_mod.ClaudeCLI(retries=1)
    before = llm_mod.ClaudeCLI.usage_limit_hits
    res = cli.ask_json("sys", "user")
    assert res.error is None and res.data["action"] == "skip"
    assert len(slept) == 2 and llm_mod.ClaudeCLI.usage_limit_hits - before == 2
