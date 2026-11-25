"""开局交给任务的模型环境变量，从桌面程序的设置推出来（设计稿 §5）。

桌面程序开局时自己把 HIRO_LLM 等传进来（app/assistant.go Assistant.jobEnv）；命令行 `hiro confirm` 开的任务原来拿不到，
悄悄退回 claude:sonnet。这里按同一套规则从 HIRO_HOME/settings.json + model-keys.json 推，用例和 Go 共用
app/testdata/llm_env_cases.json。没有 settings.json（纯命令行用户）时返回 {}，保持原来的行为。不打印、不记录 Key。
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
LEGACY_DEEPSEEK = {"", "deepseek-chat", "deepseek-reasoner"}
# 与 app/assistant.go 的 Presets 同一张表（id → 接口地址、缺省模型）
PRESETS = {
    "deepseek": (DEFAULT_DEEPSEEK_URL, DEFAULT_DEEPSEEK_MODEL),
    "kimi": ("https://api.moonshot.cn/v1", ""),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", ""),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", ""),
    "openai": ("https://api.openai.com/v1", ""),
    "openrouter": ("https://openrouter.ai/api/v1", ""),
    "custom": ("", ""),
}


def _read(p: Path) -> dict:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def env_from_settings(home: Path) -> dict:
    """{HIRO_LLM[, OPENAI_BASE_URL, OPENAI_API_KEY]}；没有 settings.json 时 {}。"""
    p = Path(home) / "settings.json"
    if not p.is_file():
        return {}
    s = _read(p)
    provider = s.get("provider") or "deepseek"
    model = s.get("model") or ""
    if provider == "deepseek" and model in LEGACY_DEEPSEEK:
        model = DEFAULT_DEEPSEEK_MODEL
    if provider == "claude":
        return {"HIRO_LLM": "claude:sonnet"}
    if provider == "codex":
        return {"HIRO_LLM": "codex"}
    if provider not in PRESETS:
        return {"HIRO_LLM": "off"}
    key = _read(Path(home) / "model-keys.json").get(f"{provider}-api-key") or ""
    base, default_model = PRESETS[provider]
    url = base
    if provider == "custom" or (provider == "deepseek" and s.get("base_url")):
        url = (s.get("base_url") or "").strip().rstrip("/")
    model = model or default_model
    if not key or not url or not model:
        return {"HIRO_LLM": "off"}
    return {"HIRO_LLM": f"openai:{model}", "OPENAI_BASE_URL": url, "OPENAI_API_KEY": key}
