"""数据来源插件接入 Session.observe（2026-11-25 插件架构 spec 4.3 / 4.8 第 3、4、8 条）。"""

import json
import time

from hiro import plugins, strategy
from hiro.contracts import TraceWriter
from hiro.loop import sources
from hiro.loop.session import Session
from hiro.plugin_record import RECORD
from hiro.plugins import SourceRead

import numpy as np
import pytest

IMG = np.zeros((1280, 720, 3), np.uint8)


class Src:
    id = "hook"
    version = "1"
    contracts = "1"
    trust = "screen"
    pages = frozenset({"ACTION_SELECT", "HIF_EXAM"})
    reply = None
    calls = 0
    starts = 0

    def start(self, context):
        type(self).starts += 1

    def read(self, page, frame_t, state):
        type(self).calls += 1
        r = type(self).reply
        return r(frame_t) if callable(r) else r

    def stop(self): ...


class SaveSrc(Src):
    id = "save"
    trust = "save"


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    plugins.reset()
    strategy.reset()
    sources.reset()
    RECORD.reset()
    Src.reply, Src.calls, Src.starts = None, 0, 0
    SaveSrc.reply = None
    plugins.register("observation_source", Src, id="hook")
    plugins.register("observation_source", SaveSrc, id="save")
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps({"version": 1, "sources": ["hook"]}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    yield
    plugins.reset()
    strategy.reset()
    sources.reset()
    RECORD.reset()


def _use(tmp_path, ids):
    (tmp_path / "strategy.json").write_text(json.dumps({"version": 1, "sources": ids}))
    strategy.reset()


def _session(tmp_path):
    return Session(TraceWriter(root=tmp_path / "traces", name="t"), shoot=lambda: IMG, sleep=lambda s: None)


def _observe(s, fields):
    s.observe(scene="ACTION_SELECT", img=IMG, fields=fields, ready=True)
    return s.observe(scene="ACTION_SELECT", img=IMG, fields=fields, ready=True)


def now_read(fields, hidden=None):
    return lambda frame_t: SourceRead(t=frame_t, fields=fields, hidden=hidden or {})


def test_source_fills_missing_field(tmp_path):
    Src.reply = now_read({"produce_point": 120})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["produce_point"] == 120
    assert s.state.sources["produce.produce_point"].by == "hook@1"
    assert s.state.produce["stamina"] == 30


def test_same_tier_screen_wins_and_conflict_noted(tmp_path):
    Src.reply = now_read({"stamina": 25})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["stamina"] == 30
    assert any("冲突" in n and "stamina" in n for n in s.notes), s.notes


def test_higher_trust_overrides_screen(tmp_path):
    _use(tmp_path, ["save"])
    SaveSrc.reply = now_read({"stamina": 25})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["stamina"] == 25
    assert s.state.sources["produce.stamina"].origin == "save"


def test_stale_read_dropped(tmp_path):
    Src.reply = lambda frame_t: SourceRead(t=frame_t - 5, fields={"produce_point": 1})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert (s.state.produce or {}).get("produce_point") is None


def test_too_new_read_dropped(tmp_path):
    Src.reply = lambda frame_t: SourceRead(t=frame_t + 5, fields={"produce_point": 1})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert (s.state.produce or {}).get("produce_point") is None


def test_unregistered_field_dropped(tmp_path):
    Src.reply = now_read({"produce_point": 5, "secret_seed": 9})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["produce_point"] == 5
    assert "secret_seed" not in (s.state.produce or {}) and "secret_seed" not in s.state.page_fields
    assert any("secret_seed" in n for n in s.notes)


def test_hidden_lands_in_state(tmp_path):
    Src.reply = now_read({}, hidden={"deck_order": ["a"]})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.hidden == {"deck_order": ["a"]}
    assert RECORD.uses_hidden and RECORD.hidden_fields == {"hook@1": ["deck_order"]}


def test_not_called_for_fields_false(tmp_path):
    Src.reply = now_read({"produce_point": 1})
    s = _session(tmp_path)
    s.observe(scene="ACTION_SELECT", img=IMG, fields=False)
    s.observe_save({"hp": 3})
    assert Src.calls == 0


def test_not_called_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE")
    strategy.reset()
    Src.reply = now_read({"produce_point": 1})
    _observe(_session(tmp_path), {"stamina": 30})
    assert Src.calls == 0


def test_source_exception_noted(tmp_path):
    def boom(frame_t):
        raise RuntimeError("采集站没开")

    Src.reply = boom
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["stamina"] == 30
    assert any("采集站没开" in n for n in s.notes)
    assert RECORD.points["source.hook"]["errors"] >= 1


def test_start_called_once(tmp_path):
    Src.reply = now_read({})
    _observe(_session(tmp_path), {"stamina": 30})
    assert Src.starts == 1 and Src.calls == 2


def test_earlier_source_wins_same_tier(tmp_path):
    class Hook2(Src):
        id = "hook2"

    plugins.register("observation_source", Hook2, id="hook2")
    _use(tmp_path, ["hook", "hook2"])
    Src.reply = now_read({"produce_point": 1})
    Hook2.reply = now_read({"produce_point": 2})
    s = _session(tmp_path)
    _observe(s, {"stamina": 30})
    assert s.state.produce["produce_point"] == 1


def test_fresh():
    t = time.time()
    assert sources.fresh(t + 0.2, t) and not sources.fresh(t + 2, t) and not sources.fresh(t - 2, t)
