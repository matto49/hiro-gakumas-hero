"""流水账：每次模型调用追加一行 JSONL 到 $HIRO_HOME/usage/YYYY-MM.jsonl（设计稿 §1）。

- 只记用量和调用结果自带的费用（claude -p 的 total_cost_usd）；按价格表算钱在汇总时做（hiro.usage.report）。
- 不记 API Key、提示词、回复正文。
- 写失败只打警告，绝不抛出：记账不能影响对局。HIRO_HOME 没设时不记（离线工具、测试）。
- 多个进程（任务子进程、服务、桌面程序）同时追加：每行一次 write（远小于 PIPE_BUF，POSIX 下不交错）；
  Windows 上 O_APPEND 不保证原子，额外加文件锁。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

VERSION = 1
# OpenAI 兼容接口的域名 → 服务商 id（与桌面程序 app/assistant.go 的 Presets 同一套）
HOSTS = {
    "api.deepseek.com": "deepseek",
    "api.moonshot.cn": "kimi",
    "dashscope.aliyuncs.com": "qwen",
    "open.bigmodel.cn": "glm",
    "api.openai.com": "openai",
    "openrouter.ai": "openrouter",
}
# 模型别名（同 app/usagelog 的 aliases）：这些旧名字现在都由 deepseek-flash 应答、按 Flash 计费（官方价格页）
ALIASES = {("deepseek", m): "deepseek-flash"
           for m in ("deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp")}  # fmt: skip
CLI_PROVIDERS = {"claude-cli", "codex-cli"}


def ledger_dir() -> Path | None:
    home = os.environ.get("HIRO_HOME")
    return Path(home) / "usage" if home else None


def provider_of(adapter) -> str:
    """适配器 → 服务商 id：claude-cli / codex-cli 原样；OpenAI 兼容按 base_url 域名反推，认不出 openai-compatible。"""
    name = getattr(adapter, "name", None) or ""
    if name in CLI_PROVIDERS:
        return name
    host = (urlparse(getattr(adapter, "base_url", "") or "").hostname or "").lower()
    return HOSTS.get(host, "openai-compatible" if name == "openai" else (name or "unknown"))


def normalize_model(provider: str, model: str | None) -> str | None:
    return ALIASES.get((provider, model or ""), model)


def row_from_result(result, *, source: str, provider: str | None = None) -> dict:
    """LLMResult → 一行流水（没有的用量记 None，不当 0）。"""
    prov: str = provider or getattr(result, "adapter", None) or "unknown"
    cost = getattr(result, "cost_usd", None)
    return {
        "v": VERSION,
        "id": uuid.uuid4().hex,
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "source": source,
        "job": os.environ.get("HIRO_JOB_ID") or None,
        "thread": None,
        "provider": prov,
        "model": normalize_model(prov, getattr(result, "model", None)),
        "input": result.input_tokens,
        "output": result.output_tokens,
        "cache_read": result.cache_read_input_tokens,
        "cache_write": result.cache_creation_input_tokens if result.cache_creation_input_tokens is not None
        else (0 if result.input_tokens is not None else None),
        "cost": cost,
        "currency": "USD" if cost is not None else None,
        "cost_source": "reported" if cost is not None else None,
        "ok": result.error is None,
    }  # fmt: skip


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (line + "\n").encode("utf-8")
    if sys.platform == "win32":
        from hiro.cli.lock import FileLock

        lock = FileLock(path.with_suffix(".lock"))
        if not lock.acquire(timeout=5.0):
            raise OSError("usage ledger lock busy")
        try:
            with open(path, "ab") as f:
                f.write(data)
        finally:
            lock.release()
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)  # 一次 write 写完整一行
    finally:
        os.close(fd)


def record(row: dict) -> None:
    """追加一行；HIRO_HOME 没设时不记；任何错误只打警告。"""
    d = ledger_dir()
    if d is None:
        return
    try:
        _append(d / f"{time.strftime('%Y-%m')}.jsonl", json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:  # noqa: BLE001 记账失败绝不影响调用方
        logger.warning("模型用量没记上（%s）：%s", d, e)


def read_rows(d: Path | None) -> list[dict]:
    """读全部流水（按 id 去重；坏行跳过），按时间排序。"""
    if d is None or not d.is_dir():
        return []
    seen: dict[str, dict] = {}
    for f in sorted(d.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id") and row["id"] not in seen:
                seen[row["id"]] = row
    return sorted(seen.values(), key=lambda r: r.get("ts") or "")
