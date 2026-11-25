"""D42 第三十三局本战 18:45：礼物强化页（画面只有「強化」，没有「キャンセル」），决策说「没有值得强化的卡：取消」，钩子直接停机。
- 有「キャンセル」：决策取消 → 点キャンセル 退回、记一条，不停机；
- 没有「キャンセル」：钩子按画面填 page_fields.cancelable=False，决策（512e3c3）在必选页按「非核心价值最高 → 基础 → 冲突」选一张；
  决策等待（卡格没读到）时钩子重读再决策，不盲选。"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ExecutionResult, GameState

import pytest

FIX = Path(__file__).parent / "data" / "g33-main-seq81-enhance.json"  # 实机状态（page_fields 文本），随仓库发布


def _state():
    return GameState.from_dict(json.loads(FIX.read_text(encoding="utf-8"))["state"])


def _with(st, **pf):
    from dataclasses import replace

    return replace(st, page_fields={**st.page_fields, **pf})


@pytest.mark.master
def test_g33_gift_enhance_cancels_only_when_cancel_exists():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("SKILL_CARD_ENHANCE", _with(_state(), cancelable=True))
    assert d.action.args.get("cancel")  # 有キャンセル时照旧：不值得就不强化


@pytest.mark.master
def test_g33_gift_enhance_without_cancel_picks_a_card():
    st = _with(_state(), cancelable=False)
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("SKILL_CARD_ENHANCE", st)
    cid = d.action.args.get("card_id")
    assert cid and not d.action.args.get("cancel"), d
    assert cid in [c["card_id"] for c in st.page_fields["cards"] if not c.get("upgrade")]


def _hook(monkeypatch, *, cancel_btn, decision):
    pytest.importorskip("kaa")  # 策略钩子导入时就要 KAA
    from hiro.produce import hif

    clicks, stops, records = [], [], []
    from hiro.vision.ocr import Box, Text

    texts = [Text("キャンセル" if cancel_btn else "強化", Box(155, 1150, 175, 1170))]
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: texts)
    monkeypatch.setattr(hif, "device", NS(click=lambda *r: clicks.append(r), screenshot=lambda: None))
    monkeypatch.setattr("hiro.produce.card_ops.CARD_OP_REGION", {"enhance": None, "remove": None})
    st = GameState(page_fields={"cards": [{"card_id": "x"}]})
    s = NS(
        state=st,
        observe=lambda **k: "obs",
        verify=lambda a, **k: ExecutionResult(action=a, ok=True),
        record=lambda *a, **k: records.append(k),
    )
    seen = {}

    decisions = list(decision) if isinstance(decision, list) else [decision]

    def decide(page, state):
        seen.setdefault("cancelable", []).append(state.page_fields.get("cancelable"))
        return decisions.pop(0) if len(decisions) > 1 else decisions[0]

    h = hif.HifCore.__new__(hif.HifCore)
    h.s, h.deciders = s, NS(decide=decide)
    h._scan_change_deck = lambda *a, **k: False

    def stop(page, why, **k):
        stops.append(why)
        raise RuntimeError(why)

    h._stop = stop
    monkeypatch.setattr(hif.HifCore, "op_failed", {})
    monkeypatch.setattr(hif, "sleep", lambda x: None)
    return h, clicks, stops, records, seen


def test_hook_cancel_decision_taps_cancel_and_returns(monkeypatch):
    from hiro.contracts import Action, ActionKind, Decision

    d = Decision(Action(ActionKind.ENHANCE_CARD, {"cancel": True}), "没有值得强化的卡", "rule", 0.7)
    h, clicks, stops, records, seen = _hook(monkeypatch, cancel_btn=True, decision=d)
    h._card_event("SKILL_CARD_ENHANCE", None, "enhance_card")
    assert seen["cancelable"] == [True] and len(clicks) == 1 and not stops and len(records) == 1


def test_hook_without_cancel_button_stops_instead_of_guessing(monkeypatch):
    from hiro.contracts import Action, ActionKind, Decision

    d = Decision(Action(ActionKind.ENHANCE_CARD, {"cancel": True}), "卡格里没有能强化的卡", "rule", 0.7)
    h, clicks, stops, _records, seen = _hook(monkeypatch, cancel_btn=False, decision=d)
    with pytest.raises(RuntimeError):
        h._card_event("SKILL_CARD_ENHANCE", None, "enhance_card")
    assert seen["cancelable"] == [False] and clicks == [] and "キャンセル" in stops[0]


def test_hook_forced_page_rereads_on_wait_then_executes(monkeypatch):
    from hiro.contracts import Action, ActionKind, Decision
    from hiro.produce import card_ops

    wait = Decision(Action.ui(ActionKind.WAIT, why="卡格没读到"), "等重新识别", "rule", 0.3)
    pick = Decision(Action(ActionKind.ENHANCE_CARD, {"card_id": "x", "index": 0, "upgrade": 0}), "强化 x", "rule", 0.7)
    h, clicks, stops, _records, seen = _hook(monkeypatch, cancel_btn=False, decision=[wait, pick])
    done = []
    monkeypatch.setattr(card_ops, "enhance_card", lambda ctx, cid: done.append(cid) or True, raising=False)
    h._card_event("SKILL_CARD_ENHANCE", None, "enhance_card")
    assert seen["cancelable"] == [False, False] and done == ["x"] and not stops and clicks == []


def test_hook_recognition_cancelable_wins_and_falls_back_to_fixed_cancel(monkeypatch):
    """识别填了 cancelable=True（以它为准），这一帧 OCR 没读到「キャンセル」：按固定位置点，不停机。"""
    from hiro.contracts import Action, ActionKind, Decision

    d = Decision(Action(ActionKind.ENHANCE_CARD, {"cancel": True}), "没有值得强化的卡", "rule", 0.7)
    h, clicks, stops, _records, seen = _hook(monkeypatch, cancel_btn=False, decision=d)
    h.s.state = GameState(page_fields={"cards": [{"card_id": "x"}], "cancelable": True})
    h._card_event("SKILL_CARD_ENHANCE", None, "enhance_card")
    assert seen["cancelable"] == [True] and clicks == [h.CARD_OP_CANCEL_XY] and not stops
