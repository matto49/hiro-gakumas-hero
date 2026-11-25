"""所有 Python 模型调用自动记账：from_spec 返回的适配器每次调用都写一行流水（hiro.usage.ledger）。"""

import json

from hiro.brain import llm as llm_mod
from hiro.brain.llm import from_spec
from hiro.usage import ledger

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_HOME", str(tmp_path))
    monkeypatch.setenv("HIRO_JOB_ID", "J1")
    return tmp_path


def _rows(home):
    return ledger.read_rows(home / "usage")


def test_from_spec_mock_records_each_call(home):
    m = from_spec("mock")
    m.replies = [{"a": 1}, {"b": 2}]  # 属性写入透传到真正的适配器
    assert m.ask_json("s", "u").data == {"a": 1}
    assert m.ask_json_image("s", "u", b"jpg").data == {"b": 2}
    rows = _rows(home)
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"decision"} and {r["job"] for r in rows} == {"J1"}
    assert (rows[0]["input"], rows[0]["output"], rows[0]["provider"], rows[0]["model"]) == (100, 50, "mock", "mock")


def test_attributes_pass_through(home):
    o = from_spec("openai:deepseek-chat", base_url="https://api.deepseek.com", api_key="k")
    assert (o.name, o.model, o.base_url) == ("openai", "deepseek-chat", "https://api.deepseek.com")


def test_failed_call_still_recorded(home):
    m = from_spec("mock")  # 没有预设回复：mock exhausted
    assert m.ask_json("s", "u").error
    (row,) = _rows(home)
    assert row["ok"] is False


def test_openai_provider_recorded(home, monkeypatch):
    o = from_spec("openai:deepseek-chat", base_url="https://api.deepseek.com", api_key="k")

    def fake(system, user):
        return llm_mod.LLMResult({"x": 1}, "{}", 0.1, None, "openai", "deepseek-chat", 10, 2, 5)

    monkeypatch.setattr(o.__wrapped__, "ask_json", fake)
    o.ask_json("s", "u")
    (row,) = _rows(home)
    assert (row["provider"], row["model"], row["input"], row["cache_read"]) == ("deepseek", "deepseek-flash", 10, 5)


def test_rescue_source(home, monkeypatch):
    from hiro.loop import rescue

    monkeypatch.setenv("HIRO_LLM", "mock")
    r = rescue.Rescuer.__new__(rescue.Rescuer)
    r._llm = None
    r.llm.ask_json("s", "u")
    (row,) = _rows(home)
    assert row["source"] == "rescue"


def test_child_env_has_job_id_and_home(tmp_path):
    from hiro.cli.jobs import KINDS, Job

    job = Job(tmp_path, KINDS["selftest"], {})
    env = job.child_env()
    assert env["HIRO_JOB_ID"] == job.id and env["HIRO_HOME"] == str(tmp_path)
    json.dumps(env)  # 可序列化（没有塞进奇怪的对象）


def test_isinstance_sees_real_adapter_class(home):
    """包了记账层后 isinstance 仍认得出真正的适配器（调用方按类分支时不坏）。"""
    m = llm_mod.from_spec("mock")  # 同一个模块对象（别的测试会 reload hiro.brain.llm）
    assert isinstance(m, llm_mod.MockLLM) and isinstance(m, llm_mod.Metered)
