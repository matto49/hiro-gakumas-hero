"""每局插件记录：plugins.json、manifest、报告（2026-11-25 插件架构 spec 4.6、4.8 第 5、6 条）。"""

import json

from hiro import plugins, strategy
from hiro.contracts import TraceWriter
from hiro.loop import manifest
from hiro.loop.report import markdown, report
from hiro.loop.session import Session
from hiro.plugin_record import RECORD

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    RECORD.reset()
    strategy.reset()
    plugins.reset()
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "sources": ["hook"], "produce": {"STUDY": "sp"}}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    yield
    RECORD.reset()
    strategy.reset()
    plugins.reset()


def test_record_counts_and_flush(tmp_path):
    RECORD.bind(tmp_path)
    RECORD.decided("produce.STUDY", "sp@1")
    RECORD.deferred("produce.STUDY", "sp@1")
    RECORD.error("produce.STUDY", "sp@1", "炸了")
    RECORD.hidden({"seed"}, "hook@1")
    RECORD.flush()
    d = json.loads((tmp_path / "plugins.json").read_text())
    assert d["points"]["produce.STUDY"] == {
        "plugin": "sp@1",
        "decided": 1,
        "deferred": 1,
        "errors": 1,
        "last_error": "炸了",
    }
    assert d["uses_hidden"] is True and d["hidden_fields"] == {"hook@1": ["seed"]}
    assert d["mapping"] == {"sources": ["hook"], "produce.STUDY": "sp"}


def test_flush_without_config_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE")
    strategy.reset()
    RECORD.bind(tmp_path)
    RECORD.flush()
    assert not (tmp_path / "plugins.json").exists()


def test_session_binds_and_flushes(tmp_path):
    img = np.zeros((1280, 720, 3), np.uint8)
    s = Session(TraceWriter(root=tmp_path / "traces", name="t"), shoot=lambda: img, sleep=lambda x: None)
    RECORD.decided("produce.STUDY", "sp@1")
    obs = s.observe(scene="ACTION_SELECT", img=img, fields={"stamina": 1}, ready=True)
    s.record(obs)
    assert (s.trace.dir / "plugins.json").is_file()


def test_report_reads_plugins_json(tmp_path):
    (tmp_path / "steps.jsonl").write_text("")
    (tmp_path / "plugins.json").write_text(
        json.dumps(
            {
                "points": {
                    "produce.STUDY": {"plugin": "sp@1", "decided": 3, "deferred": 0, "errors": 1, "last_error": "x"}
                },
                "uses_hidden": True,
                "hidden_fields": {"hook@1": ["seed"]},
                "warnings": [],
                "mapping": {},
            }
        )
    )
    r = report(tmp_path)
    assert r["uses_hidden"] is True and r["plugins"]["points"]["produce.STUDY"]["decided"] == 3


def test_report_without_plugins_json(tmp_path):
    (tmp_path / "steps.jsonl").write_text("")
    r = report(tmp_path)
    assert r["uses_hidden"] is False and r["plugins"] is None


def test_markdown_marks_hidden(tmp_path):
    (tmp_path / "steps.jsonl").write_text("")
    (tmp_path / "plugins.json").write_text(
        json.dumps(
            {
                "points": {
                    "produce.STUDY": {"plugin": "sp@1", "decided": 3, "deferred": 1, "errors": 0, "last_error": None}
                },
                "uses_hidden": True,
                "hidden_fields": {},
                "warnings": [],
                "mapping": {},
            }
        )
    )
    md = markdown(report(tmp_path))
    assert "用了隐藏信息" in md.splitlines()[0]
    assert "produce.STUDY" in md and "sp@1" in md


def test_manifest_has_mapping():
    m = manifest.collect(check_master=False)
    assert m.plugins["mapping"] == {"sources": ["hook"], "produce.STUDY": "sp"}


def test_end_called_at_exit(tmp_path):
    called = []
    RECORD.bind(tmp_path)
    RECORD.on_exit(lambda: called.append(1))
    RECORD.on_exit(lambda: (_ for _ in ()).throw(RuntimeError("插件 end 炸了")))
    RECORD.at_exit()
    assert called == [1]
