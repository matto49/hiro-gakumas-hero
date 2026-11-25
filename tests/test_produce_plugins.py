"""育成插件接入 ProduceDeciders.decide（2026-11-25 插件架构 spec 4.5 / 4.7 / 4.8）。"""

import json
import time

from hiro import plugins, strategy
from hiro.brain import plugin_gate
from hiro.brain.deciders import ProduceDeciders
from hiro.contracts import Action, ActionKind, Decision, GameState

import pytest


class StudyPlugin:
    id = "sp"
    version = "0.1"
    contracts = "1"
    pages = frozenset({"STUDY"})
    reply: object = None
    begins = 0

    def begin(self, context):
        type(self).begins += 1
        type(self).ctx = context

    def decide(self, page, state, *, n_options, kw):
        r = type(self).reply
        if callable(r):
            return r()
        return r

    def end(self, result): ...


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    plugins.reset()
    strategy.reset()
    plugin_gate.reset()
    StudyPlugin.reply, StudyPlugin.begins = None, 0
    plugins.register("produce_advisor", StudyPlugin, id="sp")
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps({"version": 1, "produce": {"STUDY": "sp", "OUTING": "sp"}, "timeouts": {"sp": 0.3}}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    yield
    plugins.reset()
    strategy.reset()
    plugin_gate.reset()


def _d(sel, source="rule"):
    return Decision(Action(ActionKind.EVENT_SELECT, {"select": sel}), reason="插件选的", source=source)


def test_plugin_decides_page():
    StudyPlugin.reply = _d(1, source="sim")
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert d.plugin == "sp@0.1"
    assert d.action.args["select"] == 1 and d.source == "sim" and d.reason == "插件选的"


def test_plugin_bad_source_becomes_plugin():
    StudyPlugin.reply = _d(0, source="my-magic")
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert d.plugin == "sp@0.1" and d.source == "plugin"


def _builtin_used(d, why):
    assert d.plugin is None
    assert any("sp@0.1" in w and why in w for w in d.warnings), d.warnings


def test_plugin_none_falls_back():
    StudyPlugin.reply = None
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert d.plugin is None
    assert not any("sp@0.1" in w for w in d.warnings)  # 插件不管不算出错，不写警告


def test_plugin_invalid_index_falls_back():
    StudyPlugin.reply = _d(7)
    _builtin_used(ProduceDeciders(None).decide("STUDY", GameState(), n_options=3), "越界")


def test_plugin_wrong_action_kind_falls_back():
    StudyPlugin.reply = Decision(Action(ActionKind.SHOP_BUY, {"index": 0}), source="rule")
    _builtin_used(ProduceDeciders(None).decide("STUDY", GameState(), n_options=3), "ShopBuy")


def test_plugin_exception_falls_back():
    def boom():
        raise RuntimeError("插件炸了")

    StudyPlugin.reply = boom
    _builtin_used(ProduceDeciders(None).decide("STUDY", GameState(), n_options=3), "插件炸了")


def test_plugin_timeout_falls_back():
    def slow():
        time.sleep(1.0)
        return _d(0)

    StudyPlugin.reply = slow
    t0 = time.monotonic()
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert time.monotonic() - t0 < 0.9
    _builtin_used(d, "超时")


def test_undeclared_page_not_called():
    StudyPlugin.reply = lambda: pytest.fail("OUTING 没声明，不该被调用")
    d = ProduceDeciders(None).decide("OUTING", GameState(), n_options=2)
    assert d.plugin is None


def test_no_config_unchanged(monkeypatch):
    monkeypatch.delenv("HIRO_STRATEGY_FILE")
    strategy.reset()
    StudyPlugin.reply = lambda: pytest.fail("没配置不该调用插件")
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert d.plugin is None


def test_begin_called_once():
    StudyPlugin.reply = _d(0)
    dec = ProduceDeciders(None, main_attr="Vi")
    dec.decide("STUDY", GameState(), n_options=3)
    dec.decide("STUDY", GameState(), n_options=3)
    assert StudyPlugin.begins == 1
    assert StudyPlugin.ctx["main_attr"] == "Vi"


def test_allowed_kinds_cover_all_pages():
    assert set(plugin_gate.ALLOWED_KINDS) == set(strategy.PRODUCE_PAGES)
    assert all(ActionKind.RECOVER not in v for v in plugin_gate.ALLOWED_KINDS.values())
