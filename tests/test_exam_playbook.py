"""社区打法规则层（hiro.brain.rules.playbook_rules，开关 EXAM_PLAYBOOK）：不耗行动的牌先打、末回合喝加行动饮料再兑现。
离线依据：657 个考试步对照，接管的 325 步与 LLM / 实机一致 89%（.temp/platforms/eval-playbook，2026-11-07）。"""

import pytest

from hiro.brain import rules
from hiro.brain.cards import CardView, DrinkView
from hiro.brain.state import CAMPUS_HIRO, BattleState, ExamMemory

WHEY = "pdrink_00-3-001"  # 初星ホエイプロテイン（加行动；drink_class 读主数据）

# 和实机 trace 的手牌字段同一格式（hiro.brain.cards.CardView.to_dict）
CARDS = {
    "aisatsu": dict(name="元気な挨拶+", card_id="p_card-02-act-1_004", upgrade=1, category="active", cost=0, hp_cost=0,
                    text="元気の120%分パラメータ上昇 レッスン中1回", tags={"once": True, "genki_score_pct": 120}, badge_score=20000),
    "yumemi": dict(name="ゆめみごこち+", card_id="p_card-02-men-2_057", upgrade=1, category="mental", cost=0, hp_cost=0,
                   text="やる気+5スキルカード使用数追加+1", tags={"extra_actions": 1}, badge_score=None),
    "todoite": dict(name="届いて！+", card_id="p_card-02-act-3_039", upgrade=1, category="active", cost=0, hp_cost=0,
                    text="元気の400%分パラメータ上昇元気を0にする", tags={"clears_genki": True, "genki_score_pct": 400}, badge_score=90000),
    "kamifubuki": dict(name="きらきら紙吹雪+", card_id="p_card-02-act-2_062", upgrade=1, category="active", cost=0, hp_cost=0,
                       text="元気の110%分パラメータ上昇", tags={"genki_score_pct": 110}, badge_score=18000),
}


def _state(names, *, remaining=5, genki=300, drinks=()):
    hand = [CardView.from_dict({"slot": i, "available": True, "extra_cost": None, **CARDS[n]}) for i, n in enumerate(names)]
    st = BattleState.build(ExamMemory(exam="r2"), CAMPUS_HIRO, source="screen", remaining_turns=remaining, hp=18, genki=genki,
                           motivation=20, impression=0, score=1000, buffs={}, attr="Vo", multiplier_permil=3000, npc_scores=None,
                           my_rank_index=None, deck_count=None, pitem_charges=None, statuses=None, attr_permil=None,
                           hand=hand, drinks=list(drinks))  # fmt: skip
    return st


def test_free_action_card_first():
    st = _state(["aisatsu", "yumemi", "kamifubuki"])
    d = rules.playbook_rules(st)
    assert d is not None and d.action == "play:1" and d.source == "rule"  # ゆめみごこち（使用数追加 + やる気）先打


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_last_turn_drink_after_free_cards():
    whey = DrinkView(0, "初星ホエイプロテイン", WHEY, "スキルカード使用数追加+1")
    # 有不耗行动的牌：先打它（加元気后再喝再兑现）
    d = rules.playbook_rules(_state(["aisatsu", "yumemi", "todoite"], remaining=1, drinks=[whey]))
    assert d is not None and d.action == "play:1"
    # 只剩两张兑现牌、行动只有 1：先喝加行动饮料
    d = rules.playbook_rules(_state(["aisatsu", "todoite"], remaining=1, drinks=[whey]))
    assert d is not None and d.action == "drink:0"


def test_no_takeover_when_nothing_certain():
    assert rules.playbook_rules(_state(["aisatsu", "todoite"], remaining=5)) is None  # 非末回合、没有不耗行动的牌：交给 LLM


def test_switch_default_off():
    assert rules.EXAM_PLAYBOOK is False
