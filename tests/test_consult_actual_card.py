"""相談删卡记实际删的那张（DMM 第 12 天）：决策写「删 アイコンタクトの基本」（只给 card_index），执行器在对话框里让决策重选、
删的是 眠気；以前状态账本按 card_index 删了 アイコンタクトの基本，日志和记账也写的是它。"""

from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision, GameState
from hiro.loop.events import events_from_step

import pytest

DECK = [
    {"card_id": "p_card-02-men-0_001"},
    {"card_id": "p_card-02-men-0_002"},
    {"card_id": "p_card-00-men-0_003", "upgrade": 0},
]
SLEEPY = "p_card-00-acc-0_002"


def test_delete_event_prefers_actual_card_id():
    args = {"service": "delete", "card_index": 2}
    ev = events_from_step("CONSULT", {"kind": "ShopBuy", "args": args}, True, {}, {"deck": DECK}, seq=1)
    assert ev[0].args["card_id"] == "p_card-00-men-0_003"
    ev = events_from_step(
        "CONSULT",
        {"kind": "ShopBuy", "args": {**args, "card_id": SLEEPY, "upgrade": 0}},
        True,
        {},
        {"deck": DECK},
        seq=1,
    )
    assert (ev[0].kind, ev[0].args["card_id"]) == ("CARD_DELETE", SLEEPY)


def test_on_consult_records_dialog_pick(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif

    obs = NS(fields={"shelf": [{"index": 0}]}, page=NS(name="CONSULT"))
    recorded, notes = [], []
    sess = NS(state=GameState(page_fields={"shelf": [{"index": 0}]}, produce={"produce_point": 405}), observe_complete=lambda *a, **k: obs,
              observe=lambda **k: obs, left_page=lambda *a: None, record=lambda o, decision=None, **k: recorded.append(decision),
              recover=lambda *a, **k: None, note=notes.append)  # fmt: skip
    ledger = {"last": {"label": "删初始卡 アイコンタクトの基本", "cost": 100, "service": "delete"}}
    plan = iter([Decision(Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": 2}), "【删卡】删初始卡 アイコンタクトの基本（100P）。P 405", "rule", 0.9),
                 Decision(Action(ActionKind.SHOP_END), "结束", "rule", 0.9)])  # fmt: skip
    h = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    h.s = sess
    h.deciders = NS(decide=lambda page, st: next(plan), _consult_state=ledger)
    h._consult_open_dialog = lambda d, done: d
    h._choose_dialog_card, h._deck_size = None, lambda: None

    def execute(action, *a, **k):
        if action.kind is ActionKind.SHOP_BUY:
            hif.HiroHifStrategy.last_dialog_pick = ("remove", SLEEPY, 0, "眠気")
        return NS(ok=True, error=None)

    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr("hiro.produce.shop.execute_shop", execute)
    h.on_consult(None)
    assert recorded[0].action.args == {"service": "delete", "card_index": 2, "card_id": SLEEPY, "upgrade": 0}
    assert ledger["last"]["label"] == "删 眠気" and "眠気" in notes[0] and "アイコンタクトの基本" in notes[0]
