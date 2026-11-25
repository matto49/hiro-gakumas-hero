"""示例插件端到端：数据来源 + 育成插件经真 Session / ProduceDeciders / trace / 报告（2026-11-25 插件架构 spec 6）。"""

import json
import sys
from pathlib import Path

from hiro import plugins, strategy
from hiro.brain import plugin_gate
from hiro.brain.deciders import ProduceDeciders
from hiro.cli.jobs import step_event
from hiro.contracts import TraceWriter
from hiro.loop import sources
from hiro.loop.report import markdown, report
from hiro.loop.session import Session
from hiro.plugin_record import RECORD

import numpy as np
import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "hiro_example_plugins" / "src"
IMG = np.zeros((1280, 720, 3), np.uint8)


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    for m in [m for m in sys.modules if m.startswith("hiro_example_plugins")]:
        del sys.modules[m]
    from hiro_example_plugins.produce import StudyFirstOption
    from hiro_example_plugins.source import ReplaySource

    plugins.reset()
    strategy.reset()
    sources.reset()
    plugin_gate.reset()
    RECORD.reset()
    plugins.register("produce_advisor", StudyFirstOption, id="example_study")
    plugins.register("observation_source", ReplaySource, id="example_replay")
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"ACTION_SELECT": {"produce_point": 77}, "hidden": {"seed": 1}}))
    monkeypatch.setenv("HIRO_EXAMPLE_REPLAY", str(replay))
    cfg = tmp_path / "strategy.json"
    cfg.write_text(json.dumps({"version": 1, "sources": ["example_replay"], "produce": {"STUDY": "example_study"}}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(cfg))
    yield
    plugins.reset()
    strategy.reset()
    sources.reset()
    plugin_gate.reset()
    RECORD.reset()


def test_example_plugins_end_to_end(tmp_path):
    s = Session(TraceWriter(root=tmp_path / "traces", name="e2e"), shoot=lambda: IMG, sleep=lambda x: None)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    obs = s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    assert s.state.produce["produce_point"] == 77
    assert s.state.hidden == {"seed": 1}
    s.record(obs)
    d = ProduceDeciders(None).decide("STUDY", s.state, n_options=3)
    assert d.plugin and d.plugin.startswith("example_study@")
    obs2 = s.observe(scene="STUDY", img=IMG, fields=False)
    s.record(obs2, decision=d)

    pj = json.loads((s.trace.dir / "plugins.json").read_text())
    assert pj["mapping"] == {"sources": ["example_replay"], "produce.STUDY": "example_study"}
    assert pj["points"]["produce.STUDY"]["decided"] == 1
    assert pj["points"]["source.example_replay"]["decided"] >= 1
    assert pj["uses_hidden"] is True

    r = report(s.trace.dir)
    assert r["uses_hidden"] and "produce.STUDY" in markdown(r)

    lines = [json.loads(x) for x in (s.trace.dir / "steps.jsonl").read_text().splitlines() if x.strip()]
    ev = step_event(lines[-1], s.trace.dir)
    assert ev["source"].startswith("plugin:example_study@")


def test_example_entry_points_declared():
    text = (EXAMPLES.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"produce_advisor.example_study"' in text and '"observation_source.example_replay"' in text
