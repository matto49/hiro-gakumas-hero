"""决策-08：删卡 / 强化 / 報酬スキップ 确认 / 兜底不选第 0 项。"""

import hiro.sim  # noqa: F401  gakumas-player 路径
import pytest
from hiro.brain.deciders import ProduceDeciders, action_key
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

DAY, TODOITE, EUREKA, SLEEP, HIGH, BASIC, YUME, EIEI = ("p_card-02-act-1_037", "p_card-02-act-3_039", "p_card-02-ido-3_105", "p_card-00-acc-0_002",
                                                        "p_card-00-men-2_014", "p_card-02-act-0_037", "p_card-02-men-3_040", "p_card-02-men-1_031")  # fmt: skip


def _d(page, **pf):
    return ProduceDeciders(CAMPUS_HIRO, None).decide(page, GameState(page_fields=pf))


def _grid(*ids, up=0):
    return [{"index": i, "card_id": c, "upgrade": up} for i, c in enumerate(ids)]


def test_removal_order_and_protection():
    # 删卡顺序（用户 2026-11-02 改定，决策-11）：眠気 > 可复用非核心 > 纯好印象 / 冲突 > 基础
    assert action_key(_d("SKILL_CARD_REMOVAL", cards=_grid(DAY, TODOITE, BASIC, HIGH, SLEEP)).action) == "remove:" + SLEEP
    assert action_key(_d("SKILL_CARD_REMOVAL", cards=_grid(DAY, TODOITE, BASIC, SLEEP)).action) == "remove:" + SLEEP
    assert action_key(_d("SKILL_CARD_REMOVAL", cards=_grid(DAY, TODOITE, BASIC, HIGH)).action) == "remove:" + HIGH  # 用户 2026-11-03：冲突牌 > 初始卡
    assert action_key(_d("SKILL_CARD_REMOVAL", cards=_grid(DAY, EUREKA, YUME, EIEI, BASIC)).action) in ("remove:" + YUME, "remove:" + BASIC)  # 夢色リップ 按好印象系先删
    assert action_key(_d("SKILL_CARD_REMOVAL", cards=_grid(DAY, EUREKA, TODOITE)).action) == "remove:cancel"  # 固有卡也不删
    assert action_key(_d("SKILL_CARD_REMOVAL").action) == "remove:cancel"


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_enhance_order():
    assert action_key(_d("SKILL_CARD_ENHANCE", cards=_grid(BASIC, DAY, TODOITE)).action) == "enhance:" + TODOITE
    grid = _grid(BASIC, DAY, TODOITE)
    grid[2]["upgrade"] = 1
    assert action_key(_d("SKILL_CARD_ENHANCE", cards=grid).action) == "enhance:" + DAY
    assert action_key(_d("SKILL_CARD_ENHANCE", cards=_grid(DAY, TODOITE, up=1)).action) == "enhance:cancel"


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_reward_skip_follows_overflow_decision():
    deciders = ProduceDeciders(CAMPUS_HIRO, None)
    held = [{"index": i, "drink_id": d} for i, d in enumerate(["pdrink_02-3-010", "pdrink_00-3-001", "pdrink_00-2-010"])]
    keep = deciders.decide("PDRINK_MAX", GameState(page_fields={"held": held, "incoming": {"drink_id": "pdrink_02-1-008"}}))
    assert keep.action.args == {"discard": [], "receive": False}
    assert action_key(deciders.decide("PDRINK_MAX_CONFIRM", GameState()).action) == "dialog:reward_skip:yes"
    swap = deciders.decide("PDRINK_MAX", GameState(page_fields={"held": held[:1] + [{"index": 1, "drink_id": "pdrink_02-1-008"}],
                                                               "incoming": {"drink_id": "pdrink_00-3-001"}}))  # fmt: skip
    assert swap.action.args["receive"] is True
    assert action_key(deciders.decide("PDRINK_MAX_CONFIRM", GameState()).action) == "dialog:reward_skip:no"


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_fallbacks_never_zero():
    choices = [{"index": i, "text": "?"} for i in range(3)]
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("STUDY", GameState(page_fields={"choices": choices}), n_options=1)  # 越界
    assert d.source == "fallback" and d.action.kind is ActionKind.WAIT  # 本人档（B=1）也越界：停下，不选 0
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("STUDY", GameState(page_fields={"choices": []}), n_options=3)
    assert d.action.args["select"] == 1  # 识别缺失时按 STUDY_LAYOUT 选本人档（やる気 = B）
    cands = [{"drink_id": "pdrink_02-1-008", "name": "ホットコーヒー"}, {"drink_id": "pdrink_00-2-010", "name": "センブリソーダ"}]
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("SELECT_DRINK", GameState(page_fields={"candidates": cands}), can_skip=False, n_options=2)
    assert action_key(d.action) == "pick:1"  # DRINK_PRIORITY，不是第 1 瓶
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("SELECT_DRINK", GameState(page_fields={"candidates": []}), can_skip=False)
    assert d.action.kind is ActionKind.WAIT


def test_decider_exception_goes_to_template(monkeypatch):
    deciders = ProduceDeciders(CAMPUS_HIRO, None)
    monkeypatch.setattr(deciders, "outing", lambda state: 1 / 0)
    d = deciders.decide("OUTING", GameState(), n_options=3)
    assert d.source == "fallback" and d.action.kind is ActionKind.EVENT_SELECT and "异常" in d.warnings[0]
