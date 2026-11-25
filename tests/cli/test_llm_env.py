"""命令行确认的任务也用桌面设置里的模型（设计稿 §5）：规则和 Go Assistant.jobEnv 共用 app/testdata/llm_env_cases.json。"""

import json
from pathlib import Path

from hiro.cli import llm_env

import pytest

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "app" / "testdata" / "llm_env_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_cases_match_go_rules(case, tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(case["settings"]), encoding="utf-8")
    (tmp_path / "model-keys.json").write_text(json.dumps(case["keys"]), encoding="utf-8")
    assert llm_env.env_from_settings(tmp_path) == case["want"]


def test_no_settings_keeps_default(tmp_path):
    """纯命令行用户（没有 settings.json）：不改，任务照旧按 HIRO_LLM / 缺省走。"""
    assert llm_env.env_from_settings(tmp_path) == {}


def test_child_env_uses_settings_when_cli_confirmed(hiro_home, monkeypatch):
    from hiro.cli.jobs import KINDS, Job

    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(json.dumps({"provider": "deepseek"}), encoding="utf-8")
    (hiro_home / "model-keys.json").write_text(json.dumps({"deepseek-api-key": "sk-9"}), encoding="utf-8")
    monkeypatch.delenv("HIRO_LLM", raising=False)
    env = Job(hiro_home, KINDS["selftest"], {}).child_env()  # 没有 llm_env：命令行 hiro confirm 开的
    assert env["HIRO_LLM"] == "openai:deepseek-flash" and env["OPENAI_API_KEY"] == "sk-9"
    env = Job(hiro_home, KINDS["selftest"], {}, llm_env={"HIRO_LLM": "codex"}).child_env()  # 界面传了：以界面为准
    assert env["HIRO_LLM"] == "codex"
