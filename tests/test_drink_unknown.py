"""第十八局选拔 20:23：饮料溢出页持有一格认不出（drink_id=None、name 也空）→ drink_name(None) 抛异常 → KAA 默认 Leave 跳过新饮料。
现在：未知饮料按中等价值（不当空位、不优先丢），决策不抛异常；决策给不出时 hif 重读再决策。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.state.context import drink_name
from hiro.state.knowledge import drink_class

import pytest

WHEY, SENBURI, BOOST, TOKUSEI, ROOIBOS = (
    "pdrink_00-3-001",
    "pdrink_00-2-010",
    "pdrink_00-2-008",
    "pdrink_02-3-010",
    "pdrink_02-1-006",
)


def _decide(held_ids, incoming):
    held = [{"index": i, "drink_id": d, "name": None} for i, d in enumerate(held_ids)]
    st = GameState(
        page={"name": "PDRINK_MAX"},
        page_fields={"held": held, "incoming": {"drink_id": incoming}},
        produce={"mode": "produce-007", "drink_capacity": 4},
    )
    return ProduceDeciders(CAMPUS_HIRO, None).decide("PDRINK_MAX", st)


@pytest.mark.master
def test_unknown_held_drink_no_crash():
    assert (
        drink_name(None) == "未知饮料"
        and drink_name("pdrink_xx").startswith("未知饮料")
        and drink_class(None) == "unknown"
    )
    d = _decide([None, WHEY, SENBURI, BOOST], TOKUSEI)
    assert d.source != "fallback" and d.action.kind is ActionKind.DISCARD_DRINK, d.reason
    assert d.action.args["receive"] is True  # 特製（复制，持有里第一瓶 → 优先）要收
    assert "未知饮料" in d.reason or "未知" in d.reason


@pytest.mark.master
def test_unknown_kept_over_known_bad():
    """有已知的差饮料（ルイボス 好印象）时先丢它，不丢认不出的那瓶。"""
    d = _decide([None, WHEY, ROOIBOS, BOOST], TOKUSEI)
    assert d.action.args["discard"] == [2], d.reason


@pytest.mark.kaa
def test_hif_pdrink_max_rereads_on_wait(monkeypatch):
    """决策给不出（Wait）时 on_pdrink_max 重读再决策，不直接走 KAA 默认的跳过新饮料。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    from hiro.contracts import Action, Decision

    calls = []

    class Deciders:
        def decide(self, page, state):
            calls.append(page)
            if len(calls) < 2:
                return Decision(Action.ui(ActionKind.WAIT, why="x"), "", "fallback", 0.1)
            return Decision(Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False}), "不收", "rule", 0.8)

    sess = NS(
        state=GameState(), observe=lambda **k: NS(fields={}), record=lambda *a, **k: None, recover=lambda *a, **k: None
    )
    strat = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    strat.s, strat.deciders = sess, Deciders()
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))  # 合并后执行侧版本重读前会先截图
    monkeypatch.setattr("hiro.produce.drink_swap.skip_incoming", lambda **k: True)
    strat.on_pdrink_max(None)
    assert calls == ["PDRINK_MAX", "PDRINK_MAX"]
