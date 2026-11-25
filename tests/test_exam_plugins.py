"""考试：出招按 strategy.json 选、局面来源转接、BattleState.hidden（2026-11-25 插件架构 spec 4.3 / 4.4 / 4.8）。"""

import json
import time
from types import SimpleNamespace

from hiro import plugins, strategy
from hiro.brain.state import BattleState
from hiro.defaults.exam_bridge import ObservationExamStateSource
from hiro.exec import exam as ex
from hiro.loop import sources
from hiro.plugin_record import RECORD
from hiro.plugins import SourceRead

import pytest


class Adv:
    id = "gn"
    version = "3"
    contracts = "1"

    def on_round_start(self, round_no, context): ...
    def decide(self, state, memory, previews, legal):
        return legal[0]

    def on_round_end(self, result): ...


class Inner:
    """原来的局面来源：给一个只有手牌、体力读不到的局面。"""

    def __init__(self, st):
        self.st = st

    def begin(self, context):
        self.ctx = context

    def read(self, frame, memory):
        return self.st, "画面"

    def confidence(self):
        return "screen"

    def executed(self, action, result): ...
    def previews(self, state):
        return None

    def end(self, result): ...


class Hook:
    id = "hook"
    version = "1"
    contracts = "1"
    trust = "save"
    pages = frozenset({"HIF_EXAM"})
    reply = None

    def start(self, context): ...
    def read(self, page, frame_t, state):
        return type(self).reply(frame_t) if type(self).reply else None

    def stop(self): ...


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    plugins.reset()
    strategy.reset()
    sources.reset()
    RECORD.reset()
    Hook.reply = None
    plugins.register("exam_advisor", Adv, id="gn")
    plugins.register("observation_source", Hook, id="hook")
    yield
    plugins.reset()
    strategy.reset()
    sources.reset()
    RECORD.reset()


def _cfg(tmp_path, monkeypatch, d):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, **d}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    strategy.reset()


def _state():
    return SimpleNamespace(hp=None, genki=5, hand=["a"], hidden={})


def test_mode_of_exam_labels():
    assert ex.mode_of_exam("r1") == "hif" and ex.mode_of_exam("qualify-2") == "hif"
    assert ex.mode_of_exam("hajime-mid1") == "hajime" and ex.mode_of_exam("tower-12") == "tower"
    assert ex.mode_of_exam("hajime-final", "produce-004") == "nia"


def test_exam_advisor_from_strategy(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"exam": "gn"})
    a = ex.pick_advisor("r1", None, given=object())
    assert isinstance(a, Adv)


def test_exam_mode_override(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"exam.tower": "gn"})
    given = object()
    assert ex.pick_advisor("r1", None, given=given) is given
    assert isinstance(ex.pick_advisor("tower-3", None, given=given), Adv)


def test_exam_advisor_missing_falls_back_to_chain(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"exam": "nope"})
    given = object()
    assert ex.pick_advisor("r1", None, given=given) is given
    assert any("nope" in w for w in RECORD.warnings)


def test_no_config_keeps_given(monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    strategy.reset()
    given = object()
    assert ex.pick_advisor("r1", None, given=given) is given


def test_bridge_passthrough_without_sources(monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    strategy.reset()
    st = _state()
    b = ObservationExamStateSource(Inner(st))
    got, why = b.read(SimpleNamespace(wall_t=time.time()), None)
    assert got is st and why == "画面" and b.confidence() == "screen"


def test_bridge_overlays_fresh_fields_and_hidden(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Hook.reply = lambda t: SourceRead(t=t, fields={"hp": 12, "genki": 9}, hidden={"deck_order": ["x"]})
    st = _state()
    b = ObservationExamStateSource(Inner(st))
    got, _ = b.read(SimpleNamespace(wall_t=time.time()), None)
    assert got.hp == 12 and got.genki == 9  # save 档：缺的补上、读到的也以插件为准
    assert got.hidden == {"deck_order": ["x"]}
    assert RECORD.uses_hidden


def test_bridge_stale_ignored(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Hook.reply = lambda t: SourceRead(t=t - 10, fields={"hp": 12})
    st = _state()
    got, _ = ObservationExamStateSource(Inner(st)).read(SimpleNamespace(wall_t=time.time()), None)
    assert got.hp is None


def test_bridge_confidence_save_for_save_trust(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Hook.reply = lambda t: SourceRead(t=t, fields={"hp": 1})
    b = ObservationExamStateSource(Inner(_state()))
    b.read(SimpleNamespace(wall_t=time.time()), None)
    assert b.confidence() == "save"


def test_battle_state_has_hidden():
    import dataclasses

    assert "hidden" in {f.name for f in dataclasses.fields(BattleState)}


def test_exam_frame_has_time():
    import inspect

    assert "self.wall_t = time.time()" in inspect.getsource(ex.Frame.__init__)
