"""LLM 适配层：决策层只依赖 LLMAdapter 协议（ask_json），换模型只改适配器 / 配置。

- ClaudeCLI：本机 Claude Code CLI（claude -p），走用户订阅额度，无需 API key。
- CodexCLI：本机 OpenAI Codex CLI（codex exec），用 ChatGPT 账号登录，无需 API key；Windows 上可用 Codex 应用自带的 codex.exe。
- OpenAICompatible：任何 OpenAI Chat Completions 兼容接口（OpenAI / DeepSeek / OpenRouter / Ollama / vLLM…），只用标准库 urllib。
- MockLLM：测试与离线回放用，按序返回预设回复。

配置：from_spec("claude:sonnet") / from_spec("codex") / from_spec("openai:gpt-4o-mini")；环境变量 HIRO_LLM 为默认 spec，
OpenAI 兼容端点读 OPENAI_BASE_URL（默认 https://api.openai.com/v1）与 OPENAI_API_KEY。
提示词版本：prompts/*.md 首行 <!-- version: x.y -->，由 prompt_version() 读出并写进每次调用记录。
"""

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

PROMPTS = Path(__file__).parent / "prompts"
REPO = Path(__file__).resolve().parents[3]
_VERSION = re.compile(r"<!--\s*version:\s*([\w.\-]+)\s*-->")

logger = logging.getLogger(__name__)


def load_prompt(*names: str) -> str:
    return "\n\n".join((PROMPTS / n).read_text(encoding="utf-8") for n in names)


def prompt_version(*names: str) -> str:
    """各提示词文件首行 <!-- version: x --> 拼成 "exam@1.0+idols/campus_hiro@1.2"；没标版本的记 "?"。"""
    parts = []
    for n in names:
        head = (PROMPTS / n).read_text(encoding="utf-8").split("\n", 1)[0]
        m = _VERSION.search(head)
        parts.append(f"{n.removesuffix('.md')}@{m.group(1) if m else '?'}")
    return "+".join(parts)


def _claude_usage(out: dict) -> dict:
    """claude -p 结果 JSON → LLMResult 的用量字段。"""
    u = out.get("usage") or {}
    cost = out.get("total_cost_usd")
    return {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
            "cache_read_input_tokens": u.get("cache_read_input_tokens"), "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
            "cost_usd": float(cost) if isinstance(cost, (int, float)) else None}  # fmt: skip


def _openai_usage(payload: dict) -> dict:
    """OpenAI 兼容 usage → 用量字段。prompt_tokens 已含缓存命中部分（prompt_tokens_details.cached_tokens），拆开记，避免总输入重复计。"""
    u = payload.get("usage") or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    prompt = u.get("prompt_tokens")
    return {"input_tokens": (prompt - cached) if isinstance(prompt, int) and isinstance(cached, int) else prompt,
            "output_tokens": u.get("completion_tokens"), "cache_read_input_tokens": cached}  # fmt: skip


@dataclass
class LLMResult:
    data: dict | None
    raw: str
    seconds: float
    error: str | None = None
    adapter: str = "unknown"
    model: str | None = None
    input_tokens: int | None = (
        None  # 不含缓存的输入（Claude：usage.input_tokens；OpenAI 兼容：prompt_tokens − cached_tokens）
    )
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None  # 命中提示缓存读进来的输入
    cache_creation_input_tokens: int | None = None  # 写进提示缓存的输入
    cost_usd: float | None = None  # claude -p 结果里的 total_cost_usd（有才记）

    def record(self, **extra) -> dict:
        """写进 BattleBrain.calls / PickDecision.calls 的一条记录（hiro.contracts.llm_trace_from_call 能读）。"""
        return {
            "seconds": round(self.seconds, 2), "error": self.error, "reply": self.data or self.raw[:500],
            "adapter": self.adapter, "model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens, "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cost_usd": self.cost_usd,
            **extra,
        }  # fmt: skip


@runtime_checkable
class LLMAdapter(Protocol):
    name: str
    model: str | None

    def ask_json(self, system: str, user: str) -> LLMResult: ...


class ClaudeCLI:
    """本机 Claude Code CLI。--setting-sources project 不加载用户级 CLAUDE.md，--tools "" 关掉工具，单次约 4–7 秒。"""

    name = "claude-cli"

    def __init__(self, model: str = "sonnet", timeout: float = 60, cwd: Path = REPO, retries: int = 1):
        self.model = model
        self.timeout = timeout
        self.cwd = cwd
        self.retries = retries  # returncode≠0 时的重试次数（超时不重试，避免一步卡两分钟）

    USAGE_LIMIT_WAIT = 300.0  # 用量上限时每次等待的秒数（游戏没有计时，停在原地等是安全的）
    USAGE_LIMIT_MAX_WAIT = 6 * 3600.0  # 最长等这么久，之后才退回规则兜底
    usage_limit_hits = 0  # 本进程内检测到用量上限的次数（报告单独统计）

    @staticmethod
    def _usage_limited(stdout: str) -> str | None:
        """claude -p 因账号用量上限失败时：stdout 是 duration_api_ms=0、无 token 的结果 JSON（result 里可能有 limit 字样）。"""
        out = None
        for line in (stdout or "").strip().splitlines():
            try:
                cand = json.loads(line)
            except ValueError:
                continue
            if isinstance(cand, dict):
                out = cand
        if out is None:
            return None
        text = str(out.get("result") or "")
        if out.get("duration_api_ms") == 0 or "limit" in text.lower() or "上限" in text:
            return text[:200] or "duration_api_ms=0（未发出请求）"
        return None

    def ask_json(self, system: str, user: str) -> LLMResult:
        """用量上限时不静默兜底：打 ERROR、每 5 分钟重试，直到恢复（最长 6 小时）。用户 2026-10-31 要求。"""
        waited = 0.0
        while True:
            res = self._ask_once(system, user)
            reason = self._usage_limited(res.raw) if res.error and res.error.startswith("exit") else None
            if reason is None:
                return res
            ClaudeCLI.usage_limit_hits += 1
            res.error = f"usage_limit: {reason}"
            if waited >= self.USAGE_LIMIT_MAX_WAIT:
                logger.error("Claude 用量上限：已等待 %.0f 分钟仍未恢复，本步退回规则兜底", waited / 60)
                return res
            logger.error(
                "Claude 用量上限（%s）：暂停 %.0f 分钟后重试，已等 %.0f 分钟。游戏停在原地等待。",
                reason,
                self.USAGE_LIMIT_WAIT / 60,
                waited / 60,
            )
            time.sleep(self.USAGE_LIMIT_WAIT)
            waited += self.USAGE_LIMIT_WAIT

    def _ask_once(self, system: str, user: str) -> LLMResult:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        cmd = [
            "claude", "-p", user,
            "--model", self.model,
            "--system-prompt", system,
            "--setting-sources", "project",
            "--tools", "",
            "--output-format", "json",
        ]  # fmt: skip
        t0 = time.monotonic()
        # 实机 2026-10-31 12:36–12:39：连续 exit 1（stdout 是 output_tokens=0 的空结果），偶发故障重试一次即可
        for _attempt in range(1 + self.retries):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, env=env, cwd=self.cwd)
            except subprocess.TimeoutExpired:
                return LLMResult(None, "", time.monotonic() - t0, "timeout", self.name, self.model)
            if proc.returncode == 0:
                break
        dt = time.monotonic() - t0
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:300]
            # claude -p 的错误原因常在 stdout JSON 的 result 里（例如用量上限），stderr 为空；一并记下
            result_text = ""
            try:
                out = json.loads((proc.stdout or "").strip().splitlines()[-1])
                if isinstance(out, dict):
                    result_text = str(out.get("result") or out.get("error") or "")[:300]
                    if out.get("duration_api_ms") == 0:
                        result_text = (result_text + "（未发出 API 请求：多半是账号用量上限或登录失效）").strip()
            except (ValueError, IndexError):
                pass
            detail = "；".join(x for x in (result_text, stderr) if x)
            error = f"exit {proc.returncode}（已重试 {self.retries} 次）" + (f": {detail}" if detail else "")
            return LLMResult(None, proc.stdout + proc.stderr, dt, error, self.name, self.model)
        try:
            out = json.loads(proc.stdout)
            text = out["result"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return LLMResult(None, proc.stdout, dt, "bad cli output", self.name, self.model)
        data = extract_json(text)
        return LLMResult(
            data,
            text,
            dt,
            None if data is not None else "no json in reply",
            self.name,
            self.model,
            **_claude_usage(out),
        )

    def ask_json_image(self, system: str, user: str, image_jpeg: bytes) -> LLMResult:
        """带一张图的单次调用（认不出的画面交 LLM 看图，hiro.loop.rescue）：stream-json 输入，消息里放 base64 图片。
        不做用量上限的长等待（兜底场景宁可快速失败、退回停机报警）。"""
        import base64

        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image_jpeg).decode()}},
            {"type": "text", "text": user}]}}  # fmt: skip
        cmd = ["claude", "-p", "--model", self.model, "--system-prompt", system, "--setting-sources", "project", "--tools", "",
               "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]  # fmt: skip
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True, text=True, timeout=self.timeout,
                                  env=env, cwd=self.cwd)  # fmt: skip
        except subprocess.TimeoutExpired:
            return LLMResult(None, "", time.monotonic() - t0, "timeout", self.name, self.model)
        dt = time.monotonic() - t0
        result = None
        for line in (proc.stdout or "").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "result":
                result = d
        if proc.returncode != 0 or result is None or result.get("is_error"):
            detail = str((result or {}).get("result") or proc.stderr or "")[:300]
            return LLMResult(
                None, (proc.stdout or "")[-2000:], dt, f"exit {proc.returncode}: {detail}", self.name, self.model
            )
        text = str(result.get("result") or "")
        data = extract_json(text)
        return LLMResult(
            data,
            text,
            dt,
            None if data is not None else "no json in reply",
            self.name,
            self.model,
            **_claude_usage(result),
        )


def find_codex() -> str | None:
    """codex 可执行文件：HIRO_CODEX_BIN → PATH 里的 codex → Windows 上 Codex 应用包自带的 resources\\codex.exe。"""
    import glob
    import shutil

    if os.environ.get("HIRO_CODEX_BIN"):
        return os.environ["HIRO_CODEX_BIN"]
    if found := shutil.which("codex"):
        return found
    if os.name != "nt":
        return None
    # Windows：Codex 应用（Microsoft Store 包）自带 codex.exe。WindowsApps 目录普通进程列不出来，
    # 用 Get-AppxPackage 查安装位置（2026-11-06 Windows 实测：glob 返回空，Get-AppxPackage 能拿到）
    try:
        loc = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-AppxPackage -Name 'OpenAI.Codex').InstallLocation"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        loc = []
    for root in reversed(loc):
        exe = Path(root) / "app" / "resources" / "codex.exe"
        if exe.exists():
            return str(exe)
    hits = sorted(glob.glob(r"C:\Program Files\WindowsApps\OpenAI.Codex_*\app\resources\codex.exe"))
    return hits[-1] if hits else None


class CodexCLI:
    """本机 OpenAI Codex CLI：codex exec 非交互调用，回复写进 -o 文件。用 ChatGPT 账号的登录（~/.codex），不需要 API key。

    codex exec 没有单独的系统提示参数，系统提示和用户提示拼在一起从标准输入（UTF-8）传入；在空的临时目录里跑，
    不让 Codex 把仓库的 AGENTS.md 当成指令读进去；-s read-only、--ephemeral 不留会话。
    需要代理的网络：给进程设 HTTPS_PROXY（Codex 认这个环境变量）。2026-11-06 Windows 实测单次约 12 秒。"""

    name = "codex-cli"

    def __init__(self, model: str | None = None, timeout: float = 120, binary: str | None = None):
        self.model = model or None
        self.timeout = timeout
        self.binary = binary or find_codex()

    def _run(self, prompt: str, image_jpeg: bytes | None = None) -> LLMResult:
        import tempfile

        if not self.binary:
            return LLMResult(
                None, "", 0.0, "找不到 codex（设 HIRO_CODEX_BIN 或把 codex 放进 PATH）", self.name, self.model
            )
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hiro-codex-") as tmp:
            out = Path(tmp) / "reply.txt"
            cmd = [
                self.binary,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-s",
                "read-only",
                "--color",
                "never",
                "-o",
                str(out),
            ]
            if self.model:
                cmd += ["-m", self.model]
            if image_jpeg is not None:
                img = Path(tmp) / "screen.jpg"
                img.write_bytes(image_jpeg)
                cmd += ["-i", str(img)]
            cmd.append("-")
            try:
                proc = subprocess.run(
                    cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=self.timeout, cwd=tmp
                )
            except subprocess.TimeoutExpired:
                return LLMResult(None, "", time.monotonic() - t0, "timeout", self.name, self.model)
            dt = time.monotonic() - t0
            text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        if proc.returncode != 0 or not text.strip():
            detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-300:]
            return LLMResult(None, text, dt, f"exit {proc.returncode}: {detail}", self.name, self.model)
        data = extract_json(text)
        return LLMResult(data, text, dt, None if data is not None else "no json in reply", self.name, self.model)

    def ask_json(self, system: str, user: str) -> LLMResult:
        return self._run(f"{system}\n\n---\n\n{user}")

    def ask_json_image(self, system: str, user: str, image_jpeg: bytes) -> LLMResult:
        return self._run(f"{system}\n\n---\n\n{user}", image_jpeg)


class OpenAICompatible:
    """OpenAI Chat Completions 兼容接口。要求 JSON 回复（response_format json_object，不支持的端点也靠 extract_json 兜底）。"""

    name = "openai"

    def __init__(self, model: str, *, base_url: str | None = None, api_key: str | None = None, timeout: float = 60,
                 temperature: float = 0.2, json_mode: bool = True, extra_headers: dict | None = None):  # fmt: skip
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.temperature = temperature
        self.json_mode = json_mode
        self.extra_headers = extra_headers or {}

    def ask_json_image(self, system: str, user: str, image_jpeg: bytes) -> LLMResult:
        import base64

        url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        return self.ask_json(system, [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": user}])

    def ask_json(self, system: str, user) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                **self.extra_headers,
            },
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            return LLMResult(None, detail, time.monotonic() - t0, f"http {e.code}", self.name, self.model)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return LLMResult(None, "", time.monotonic() - t0, f"timeout/network: {e}", self.name, self.model)
        except json.JSONDecodeError:
            return LLMResult(None, "", time.monotonic() - t0, "bad http body", self.name, self.model)
        dt = time.monotonic() - t0
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return LLMResult(None, json.dumps(payload)[:500], dt, "bad completion", self.name, self.model)
        data = extract_json(text)
        return LLMResult(
            data,
            text,
            dt,
            None if data is not None else "no json in reply",
            self.name,
            self.model,
            **_openai_usage(payload),
        )


@dataclass
class MockLLM:
    """按序返回预设回复（dict 或原始字符串）；用完后返回错误。记录收到的提示词便于断言。"""

    replies: list = field(default_factory=list)
    name: str = "mock"
    model: str | None = "mock"
    seconds: float = 0.01
    calls: list[tuple[str, str]] = field(default_factory=list)

    def ask_json_image(self, system: str, user: str, image_jpeg: bytes) -> LLMResult:
        return self.ask_json(system, user)

    def ask_json(self, system: str, user: str) -> LLMResult:
        self.calls.append((system, user))
        if not self.replies:
            return LLMResult(None, "", self.seconds, "mock exhausted", self.name, self.model)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            return LLMResult(None, "", self.seconds, f"{type(r).__name__}: {r}", self.name, self.model)
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        data = extract_json(text)
        return LLMResult(
            data, text, self.seconds, None if data is not None else "no json in reply", self.name, self.model, 100, 50
        )


def from_spec(spec: str | None = None, **kw) -> LLMAdapter:
    """ "claude[:model]" / "codex[:model]" / "openai:<model>" / "mock" → 适配器。spec 为 None 时读 HIRO_LLM，缺省 claude:sonnet。"""
    spec = spec or os.environ.get("HIRO_LLM") or "claude:sonnet"
    kind, _, model = spec.partition(":")
    if kind in ("claude", "claude-cli"):
        return ClaudeCLI(model=model or "sonnet", **kw)
    if kind in ("codex", "codex-cli"):
        return CodexCLI(model=model or None, **kw)
    if kind in ("openai", "openai-compatible"):
        if not model:
            raise ValueError("openai 适配器需要模型名：openai:<model>")
        return OpenAICompatible(model, **kw)
    if kind == "mock":
        return MockLLM(**kw)
    raise ValueError(f"未知的 LLM spec：{spec!r}（支持 claude[:model] / codex[:model] / openai:<model> / mock）")


def extract_json(text: str) -> dict | None:
    """取回复里第一个 JSON 对象（容忍 ```json 代码块）。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start : text.rfind("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None
