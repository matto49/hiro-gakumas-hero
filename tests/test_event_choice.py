"""事件选项页（EVENT_CHOICE，去 KAA 设计稿 §8-2，用户 11-14）：算法（严格占优）→ LLM → 兜底（粗估分）；读不出 / 解析不出且 LLM 没答案时停机。"""

import json

from hiro.brain import event_choice as ev
from hiro.brain.deciders import ProduceDeciders, action_key
from hiro.brain.llm import MockLLM
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

pytestmark = pytest.mark.master


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "off")


def _st(*choices, title="ある日の出来事", hp=20, max_hp=33):
    produce = {
        "mode": "produce-007",
        "step": 6,
        "stamina": hp,
        "max_stamina": max_hp,
        "produce_point": 200,
        "params": {"Vo": 300, "Da": 300, "Vi": 300},
    }
    return GameState(
        page_fields={"title": title, "kind": "random", "choices": [dict(index=i, **c) for i, c in enumerate(choices)]},
        produce=produce,
    )


def test_parse_effects():
    v, ok = ev.parse({"text": "レッスンする", "effect": "ボーカル上昇+30、体力-5"})
    assert ok and v["Vo"] == 30 and v["hp"] == -5
    v, ok = ev.parse({"text": "休む", "effect": "体力回復+8"})
    assert ok and v["hp"] == 8
    v, ok = ev.parse({"text": "付き合う", "cost_p": 50, "effect": "スキルカードを選択して獲得"})
    assert ok and v["p"] == -50 and v["card"] == 1
    v, ok = ev.parse({"text": "謎の選択肢", "effect": "なにかが起こる"})
    assert not ok


def test_dominant_option_is_rule(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "all")
    llm = MockLLM(replies=[{"choice": 1}])
    d = ProduceDeciders(CAMPUS_HIRO, llm).decide(
        "EVENT_CHOICE", _st({"text": "A", "effect": "ダンス+20"}, {"text": "B", "effect": "ダンス+10"})
    )
    assert d.source == "rule" and action_key(d.action) == action_key(d.action.event_select(0)) and not llm.calls


def test_tradeoff_goes_to_llm_with_situation(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "event_choice")
    llm = MockLLM(replies=[{"choice": 1, "reason": "下一场考试前体力要留够", "line": "休もう"}])
    d = ProduceDeciders(CAMPUS_HIRO, llm).decide(
        "EVENT_CHOICE",
        _st({"text": "練習", "effect": "ボーカル+40、体力-6"}, {"text": "休む", "effect": "体力回復+10"}),
    )
    assert d.source == "llm" and d.decider == "event_choice" and d.action.kind is ActionKind.EVENT_SELECT
    user = json.loads(llm.calls[0][1])
    assert "局面" in user and len(user["候选"]) == 2 and "ある日の出来事" in user["问题"]
    picked = user["候选"][1]["名字"]
    assert d.action.args["select"] == (0 if picked == "練習" else 1)


def test_tradeoff_without_llm_falls_back_by_score():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "EVENT_CHOICE",
        _st({"text": "練習", "effect": "ボーカル+40、体力-6"}, {"text": "休む", "effect": "体力回復+10"}),
    )
    assert d.source == "fallback" and d.action.kind is ActionKind.EVENT_SELECT


def test_unparsed_without_llm_stops():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "EVENT_CHOICE", _st({"text": "謎", "effect": "なにかが起こる"}, {"text": "B", "effect": "ダンス+10"})
    )
    assert d.action.kind is ActionKind.WAIT and d.source == "fallback"


def test_unparsed_with_llm_can_answer(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "event_choice")
    llm = MockLLM(replies=[{"choice": 1, "reason": "看效果说明"}])
    d = ProduceDeciders(CAMPUS_HIRO, llm).decide(
        "EVENT_CHOICE", _st({"text": "謎", "effect": "なにかが起こる"}, {"text": "B", "effect": "ダンス+10"})
    )
    assert d.source == "llm" and d.action.args["select"] == 0  # 候选按「解析出来的在前」：第 1 个是 謎


def test_missing_choices_stops():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("EVENT_CHOICE", GameState(page_fields={"title": "x", "choices": []}))
    assert d.action.kind is ActionKind.WAIT


def test_cannot_afford_hp_not_picked_by_fallback():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "EVENT_CHOICE",
        _st({"text": "無理", "effect": "ボーカル+200、体力-30"}, {"text": "休む", "effect": "ダンス+5、体力+3"}, hp=10),
    )
    assert d.action.args["select"] == 1
