"""命令行：run start --plugins / --sources、任务配置快照、plugins list、doctor、事件来源（2026-11-25 插件架构 spec 4.2 / 4.6）。"""

import json

from hiro import plugins, strategy

import pytest


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    plugins.reset()
    strategy.reset()
    yield
    plugins.reset()
    strategy.reset()


class SP:
    id = "sp"
    version = "0.2"
    contracts = "1"
    pages = frozenset({"STUDY"})

    def begin(self, context): ...
    def decide(self, page, state, *, n_options, kw):
        return None

    def end(self, result): ...


def _job(home, params):
    from hiro.cli import jobs

    return jobs.Job(home, jobs.KINDS["selftest"], params)


def test_job_writes_strategy_snapshot_and_env(hiro_home):
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "strategy.json").write_text(json.dumps({"version": 1, "produce": {"STUDY": "a", "OUTING": "b"}}))
    j = _job(hiro_home, {"plugins": "produce.STUDY=sp,exam=gn", "sources": "hook"})
    env = j.child_env()
    p = env["HIRO_STRATEGY_FILE"]
    assert p == str(j.dir / "strategy.json")
    s = strategy.load(p)
    assert s.produce_for("STUDY") == "sp" and s.produce_for("OUTING") == "b"
    assert s.exam_for("hif") == "gn" and s.sources == ["hook"]


def test_job_without_config_sets_nothing(hiro_home):
    hiro_home.mkdir(parents=True, exist_ok=True)
    j = _job(hiro_home, {})
    env = j.child_env()
    assert "HIRO_STRATEGY_FILE" not in env
    assert not (j.dir / "strategy.json").exists()


def test_run_start_params_parse():
    from hiro.cli import cmd_run

    got = cmd_run._params({"kind": "hif", "plugins": "produce.STUDY=sp", "sources": "hook", "idol": None})
    assert got == {"plugins": "produce.STUDY=sp", "sources": "hook"}


def test_plugins_list_shape(run_cli, hiro_home):
    plugins.register("produce_advisor", SP, id="sp")
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "strategy.json").write_text(json.dumps({"version": 1, "produce": {"STUDY": "sp", "NOPE": "x"}}))
    code, env = run_cli("plugins", "list")
    assert code == 0, env
    d = env["data"]
    sp = next(p for p in d["plugins"] if p["id"] == "sp")
    assert sp["kind"] == "produce_advisor" and sp["ok"] is True and sp["version"] == "0.2" and sp["pages"] == ["STUDY"]
    assert d["config"]["mapping"] == {"sources": [], "produce.STUDY": "sp"}
    assert any("NOPE" in w for w in d["config"]["warnings"])


def test_step_event_plugin_source(tmp_path):
    from hiro.cli.jobs import step_event

    e = step_event(
        {"seq": 3, "decision": {"source": "sim", "plugin": "uinet@1", "reason": "r", "action": {"kind": "Wait"}}},
        tmp_path,
    )
    assert e["source"] == "plugin:uinet@1"
    e2 = step_event({"seq": 3, "decision": {"source": "rule", "reason": "r", "action": {"kind": "Wait"}}}, tmp_path)
    assert e2["source"] == "rule"


def test_doctor_reports_bad_plugin(run_cli, hiro_home):
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "strategy.json").write_text(json.dumps({"version": 1, "produce": {"STUDY": "ghost"}}))
    _code, env = run_cli("doctor")
    it = {i["id"]: i for i in env["data"]["items"]}
    assert it["plugins"]["ok"] is False and "ghost" in it["plugins"]["detail"]
    assert it["plugins"]["optional"] is True
