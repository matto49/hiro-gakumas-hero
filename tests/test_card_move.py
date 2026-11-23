"""考试里的技能卡移动对话框（EXAM_CARD_MOVE，协调 11-14 新决策点）：算法（移走选最该丢 / 留着用选最想用）→ LLM → 兜底。"""

import json

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.llm import MockLLM
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

pytestmark = pytest.mark.master

SLEEPY = "p_card-00-acc-0_002"  # 眠気（トラブル）
CORE = "p_card-02-act-1_037"  # デイドリーミング（核心）
BASIC = "p_card-02-act-0_037"


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "off")


def _st(dest, cards, **kw):
    return GameState(
        page_fields={
            "destination": dest,
            "candidates": [{"index": i, "card_id": c, "upgrade": 0} for i, c in enumerate(cards)],
            **kw,
        }
    )


def _pick(d):
    return d.action.args["pick_indexes"]


def test_move_out_prefers_trouble_and_spares_core():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("EXAM_CARD_MOVE", _st("lost", [CORE, BASIC, SLEEPY]))
    assert d.source == "rule" and _pick(d) == [2]
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("EXAM_CARD_MOVE", _st("lost", [CORE, BASIC]))
    assert _pick(d) == [1]


def test_keep_prefers_core():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("EXAM_CARD_MOVE", _st("hold", [BASIC, CORE, SLEEPY]))
    assert d.source == "rule" and _pick(d) == [1]


def test_unknown_destination_keeps_old_behavior():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("EXAM_CARD_MOVE", _st(None, [CORE, BASIC, SLEEPY]))
    assert d.source == "fallback" and _pick(d) == [2]


def test_no_candidates_waits():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "EXAM_CARD_MOVE", GameState(page_fields={"destination": "lost", "candidates": []})
    )
    assert d.action.kind is ActionKind.WAIT


def test_unrecognized_card_goes_to_llm(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "card_move")
    llm = MockLLM(replies=[{"choice": 1, "reason": "留核心卡"}])
    st = GameState(
        page_fields={
            "destination": "lost",
            "title": "除外するカードを選択",
            "candidates": [{"index": 0, "card_id": None}, {"index": 1, "card_id": BASIC}],
        }
    )
    d = ProduceDeciders(CAMPUS_HIRO, llm).decide("EXAM_CARD_MOVE", st)
    assert d.source == "llm" and llm.calls
    user = json.loads(llm.calls[0][1])
    assert user["对话框标题"] == "除外するカードを選択" and len(user["候选"]) == 2
