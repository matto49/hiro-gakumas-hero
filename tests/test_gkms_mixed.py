"""相談、轮间（spec 4.5 混合页）：内置先看去掉卡的页面；它要结束时问插件的卡怎么办，预算 = P 点 − 预留。"""

import copy

import pytest

from hiro import plugins, strategy
from hiro.brain import plugin_gate
from hiro.brain.deciders import ProduceDeciders
from hiro.contracts import Action, ActionKind, Decision, GameState
from hiro.gkms.advisor import GkmsProduceAdvisor
from gkms_fakes import FakeClient, values

SHELF = [{"index": 0, "kind": "card", "card_id": "p_card-a", "upgrade": 0, "price": 100, "purchased": False},
         {"index": 1, "kind": "card", "card_id": "p_card-b", "upgrade": 0, "price": 60, "purchased": False},
         {"index": 4, "kind": "drink", "drink_id": "pdrink_x", "price": 50, "purchased": False}]  # fmt: skip
SERVICES = {"delete": {"price": 100, "available": True}, "upgrade": {"price": 80, "available": True}}
DECK = [{"card_id": "p_card-d", "upgrade": 0}, {"card_id": "p_card-e", "upgrade": 0}]


def consult(p=300, mode="produce-002", shelf=None, services=None):
    return GameState.from_dict({
        "mode": "produce", "page": {"name": "CONSULT", "since_seq": 5},
        "produce": {"mode": mode, "idol_card_id": "i_card-x", "produce_point": p, "stamina": 20, "max_stamina": 30, "step": 5,
                    "params": {"Vo": 1, "Da": 1, "Vi": 1}, "growth_permil": {"Vo": 1000, "Da": 1000, "Vi": 1000}, "deck": DECK, "star": 0},
        "page_fields": {"shelf": copy.deepcopy(SHELF if shelf is None else shelf), "services": copy.deepcopy(SERVICES if services is None else services),
                        "produce_point": p},
    })  # fmt: skip


@pytest.fixture
def gkms(monkeypatch):
    plugins.reset()
    strategy.reset()
    plugin_gate.reset()
    monkeypatch.delenv("HIRO_GKMS", raising=False)
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    box = {}

    def make():
        box["adv"] = GkmsProduceAdvisor(client=box["client"])
        return box["adv"]

    monkeypatch.setitem(plugins.BUNDLED, ("produce_advisor", "gkms"), make)
    yield box
    plugins.reset()
    strategy.reset()
    plugin_gate.reset()


def builtin_spy(monkeypatch, result):
    seen = []

    def fake(self, state, **kw):
        seen.append(copy.deepcopy(state.page_fields))
        return result(state) if callable(result) else result

    monkeypatch.setattr(ProduceDeciders, "consult", fake)
    return seen


END = Decision(Action(ActionKind.SHOP_END), "内置：不买了", "rule", 0.8)


def test_builtin_first_sees_no_cards(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.9}))
    drink = Decision(Action(ActionKind.SHOP_BUY, {"index": 4}), "内置：买饮料", "rule", 0.8)
    seen = builtin_spy(monkeypatch, drink)
    d = ProduceDeciders(None).decide("CONSULT", consult())
    assert d.action.args == {"index": 4}
    assert [it["kind"] for it in seen[0]["shelf"]] == ["drink"]
    assert "delete" not in seen[0]["services"] and "upgrade" not in seen[0]["services"]


def test_then_plugin_buys_best_card(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.3, "p_card-b": 0.8}, held={"p_card-d": 0.5, "p_card-e": 0.6}))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult())
    assert d.action.kind is ActionKind.SHOP_BUY and d.action.args == {"index": 1}
    assert d.plugin and d.plugin.startswith("gkms")


def test_budget_limits(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.9, "p_card-b": 0.8}, held={"p_card-d": 0.5, "p_card-e": 0.6}))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult(p=70))
    assert d.action.args == {"index": 1}  # 100P 的 a 买不起


def test_delete_low_v_once_per_visit(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({}, held={"p_card-d": 0.01, "p_card-e": 0.6}),
                                removal={"state": "ok", "order": [{"card_id": "p_card-d", "upgrade": 0}, {"card_id": "p_card-e", "upgrade": 0}]})  # fmt: skip
    builtin_spy(monkeypatch, END)
    dec = ProduceDeciders(None)
    s = consult(shelf=[], services={"delete": {"price": 100, "available": True}})
    d = dec.decide("CONSULT", s)
    assert d.action.args == {"service": "delete", "card_index": 0}
    d2 = dec.decide("CONSULT", s)  # 同一次相談再问：删卡服务用过了
    assert d2.action.kind is ActionKind.SHOP_END


def test_nothing_worth_it_ends(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.01, "p_card-b": 0.02}, held={"p_card-d": 0.5, "p_card-e": 0.6}, up={"p_card-d": 0.01}))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult(services={}))
    assert d.action.kind is ActionKind.SHOP_END


def test_hif_finals_consult_keeps_interval_reserve(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.9, "p_card-b": 0.8}, held={"p_card-d": 0.5, "p_card-e": 0.6}))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult(p=250, mode="produce-008", services={}))
    assert d.action.kind is ActionKind.SHOP_END  # 250 − 留给轮间的 200 = 50，两张卡都买不起


def test_upgrade_uses_deck_index(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({}, held={"p_card-d": 0.5, "p_card-e": 0.6}, up={"p_card-d": 0.1, "p_card-e": 0.4}))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult(shelf=[], services={"upgrade": {"price": 80, "available": True}}))
    assert d.action.kind is ActionKind.SHOP_UPGRADE_CARD and d.action.args == {"card_index": 1}


def test_interval_buys_card_after_builtin(gkms, monkeypatch):
    gkms["client"] = FakeClient(values=values({"p_card-a": 0.7}, held={"p_card-d": 0.5, "p_card-e": 0.6}))
    seen = []

    def fake(self, state, **kw):
        seen.append(copy.deepcopy(state.page_fields))
        return Decision(Action(ActionKind.INTERVAL_END), "内置：结束", "rule", 0.8)

    monkeypatch.setattr(ProduceDeciders, "interval", fake)
    s = GameState.from_dict({
        "mode": "produce", "page": {"name": "HIF_ROUND_INTERVAL", "since_seq": 9},
        "produce": {"mode": "produce-008", "idol_card_id": "i_card-x", "produce_point": 300, "deck": DECK},
        "page_fields": {"produce_point": 300, "interval": {"cards": [{"index": 0, "card_id": "p_card-a", "upgrade": 0, "price": 80, "purchased": False}],
                                                           "drinks": [{"index": 6, "drink_id": "pdrink_x", "price": 100}],
                                                           "upgrade": {"price": 100, "available": True}}},
    })  # fmt: skip
    d = ProduceDeciders(None).decide("HIF_ROUND_INTERVAL", s)
    assert seen[0]["interval"]["cards"] == [] and "upgrade" not in seen[0]["interval"]
    assert d.action.kind is ActionKind.INTERVAL_BUY and d.action.args == {"index": 0}


def test_plugin_error_falls_back_to_builtin_end(gkms, monkeypatch):
    from hiro.gkms.client import GkmsError

    gkms["client"] = FakeClient(values=GkmsError("boom"))
    builtin_spy(monkeypatch, END)
    d = ProduceDeciders(None).decide("CONSULT", consult())
    assert d.action.kind is ActionKind.SHOP_END


def test_without_gkms_builtin_sees_whole_page(monkeypatch):
    plugins.reset()
    strategy.reset()
    monkeypatch.setenv("HIRO_GKMS", "0")
    seen = builtin_spy(monkeypatch, END)
    ProduceDeciders(None).decide("CONSULT", consult())
    assert len(seen[0]["shelf"]) == 3 and "delete" in seen[0]["services"]
    strategy.reset()
