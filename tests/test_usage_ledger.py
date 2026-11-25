"""模型调用流水账（hiro.usage.ledger）：一次调用一行 JSONL，写到 $HIRO_HOME/usage/YYYY-MM.jsonl。"""

import json
from types import SimpleNamespace as NS

from hiro.brain.llm import LLMResult
from hiro.usage import ledger

import pytest


def _result(**kw):
    base = {"data": {"a": 1}, "raw": '{"a": 1}', "seconds": 1.2, "error": None, "adapter": "openai", "model": "deepseek-chat",
            "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 30}  # fmt: skip
    base.update(kw)
    return LLMResult(**base)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_HOME", str(tmp_path))
    monkeypatch.delenv("HIRO_JOB_ID", raising=False)
    return tmp_path


def _lines(home):
    files = sorted((home / "usage").glob("*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()]


def test_record_appends_one_line_per_call(home):
    adapter = NS(name="openai", model="deepseek-chat", base_url="https://api.deepseek.com", api_key="sk-secret")
    for _ in range(2):
        ledger.record(ledger.row_from_result(_result(), source="decision", provider=ledger.provider_of(adapter)))
    rows = _lines(home)
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]
    r = rows[0]
    assert r["v"] == 1 and r["source"] == "decision" and r["provider"] == "deepseek"
    assert r["model"] == "deepseek-flash"  # deepseek-chat 别名归一
    assert (r["input"], r["output"], r["cache_read"], r["cache_write"]) == (100, 20, 30, 0)
    assert r["cost"] is None and r["ok"] is True and r["job"] is None
    text = (home / "usage").glob("*.jsonl").__next__().read_text(encoding="utf-8")
    assert "sk-secret" not in text and '"a": 1' not in text  # 不写 Key、不写回复


def test_reported_cost_and_failed_call(home):
    r = ledger.row_from_result(_result(error="timeout", data=None, input_tokens=None, output_tokens=None,
                                       cache_read_input_tokens=None, adapter="claude-cli", model="sonnet", cost_usd=0.012),
                               source="rescue", provider="claude-cli")  # fmt: skip
    assert r["ok"] is False and r["input"] is None and r["output"] is None  # 没有用量就是 None，不是 0
    assert (r["cost"], r["currency"], r["cost_source"]) == (0.012, "USD", "reported")


def test_record_never_raises_when_dir_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "home"
    blocker.write_text("not a dir")  # HIRO_HOME 指向一个文件：建不了 usage/
    monkeypatch.setenv("HIRO_HOME", str(blocker))
    ledger.record(ledger.row_from_result(_result(), source="decision", provider="deepseek"))  # 不抛


def test_no_home_no_record(monkeypatch, tmp_path):
    monkeypatch.delenv("HIRO_HOME", raising=False)
    assert ledger.ledger_dir() is None
    ledger.record(ledger.row_from_result(_result(), source="decision", provider="deepseek"))  # 不抛、不写


@pytest.mark.parametrize(
    "url,want",
    [("https://api.deepseek.com", "deepseek"), ("https://api.moonshot.cn/v1", "kimi"),
     ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen"), ("https://open.bigmodel.cn/api/paas/v4", "glm"),
     ("https://api.openai.com/v1", "openai"), ("https://openrouter.ai/api/v1", "openrouter"),
     ("http://127.0.0.1:8000/v1", "openai-compatible")],
)  # fmt: skip
def test_provider_from_base_url(url, want):
    assert ledger.provider_of(NS(name="openai", base_url=url)) == want


def test_provider_of_cli_adapters():
    assert ledger.provider_of(NS(name="claude-cli")) == "claude-cli"
    assert ledger.provider_of(NS(name="codex-cli")) == "codex-cli"


def test_job_from_env(home, monkeypatch):
    monkeypatch.setenv("HIRO_JOB_ID", "20261125-062938-df2d")
    assert ledger.row_from_result(_result(), source="decision", provider="deepseek")["job"] == "20261125-062938-df2d"


def test_read_rows_dedupes_by_id(home):
    row = ledger.row_from_result(_result(), source="decision", provider="deepseek")
    ledger.record(row)
    ledger.record(row)  # 同一行写了两次（重试 / 重放）
    ledger.record(dict(row, id="other", ts="2026-12-01T00:00:00+08:00"))
    rows = ledger.read_rows(ledger.ledger_dir())
    assert sorted(r["id"] for r in rows) == sorted([row["id"], "other"])


def test_openai_usage_deepseek_cache_hit_fallback():
    """只给 DeepSeek 自己的 prompt_cache_hit_tokens 时也拆出缓存命中（同 Go OpenAICompat.record）。"""
    from hiro.brain.llm import _openai_usage

    u = _openai_usage({"usage": {"prompt_tokens": 500, "completion_tokens": 5, "prompt_cache_hit_tokens": 400}})
    assert (u["input_tokens"], u["cache_read_input_tokens"], u["output_tokens"]) == (100, 400, 5)


@pytest.mark.parametrize(
    "old", ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"]
)
def test_old_deepseek_names_priced_as_flash(old):
    """官方价格页：这些旧名字按 Flash 计费；不归一的话每次都显示「—」。"""
    assert ledger.normalize_model("deepseek", old) == "deepseek-flash"


def test_suite_does_not_inherit_real_home():
    """跑测试时不继承外面的 HIRO_HOME / HIRO_JOB_ID：mock 调用不能写进开发者真的流水账。"""
    import os

    assert "HIRO_HOME" not in os.environ and "HIRO_JOB_ID" not in os.environ
    assert ledger.ledger_dir() is None
