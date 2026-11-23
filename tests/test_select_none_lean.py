"""D40（协调 2026-11-04，第三十三局选拔 #339，差し入れ 选卡页）：LLM 说「不拿」（pick index -1），格式无效 → 兜底取了游戏推荐 ゆるふわおしゃべり。
选卡页没有「不拿」按钮（画面只有「受け取る」，课程页另有「カード除去」/ 再抽選）。SELECT_NONE_LEAN（默认关）：不想拿 / 无效且必拿已齐、
候选都是社区没提到的卡 → 能刷新先刷新，否则拿最不污染的。"""

import hiro.brain.deciders as D
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.llm import MockLLM
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.state.knowledge import YARUKI_CARDS

import pytest

pytestmark = pytest.mark.master  # 决策器要查主数据（game.db），没有就跳过

CANDS = [
    "p_card-02-men-3_044",
    "p_card-02-act-1_028",
    "p_card-00-men-1_005",
]  # 手書きのメッセージ / ゆるふわおしゃべり / 気合十分！（#339）
MUSTS = [cid for cid, k in YARUKI_CARDS.items() if k.get("must")]


def st(rerolls=0, deck=None):
    raw = [{"index": i, "card_id": c, "upgrade": 0, "name": c} for i, c in enumerate(CANDS)]
    deck = MUSTS + ["p_card-02-men-0_039"] * 8 if deck is None else deck
    return GameState(page_fields={"candidates": raw, "rerolls_left": rerolls, "excludes_left": 0, "recommended": 1},
                     produce={"idol_card_id": "i_card-shro-3-007", "mode": "produce-007", "step": 14, "params": {"Vo": 500, "Da": 600, "Vi": 400},
                              "growth_permil": {"Vo": 1400, "Da": 1400, "Vi": 1400}, "star": 100, "stamina": 25, "max_stamina": 33, "produce_point": 340,
                              "deck": [{"card_id": c, "upgrade": 0} for c in deck], "drinks": [], "rerolls_left": rerolls, "excludes_left": 0})  # fmt: skip


SKIP = {"action": "pick", "index": -1, "reason": "必拿卡已齐,三张候选都不在攻略里,所以不拿。"}


def test_switch_off_keeps_old_fallback(monkeypatch):
    monkeypatch.setattr(
        D, "SELECT_NONE_LEAN", False
    )  # 默认值由总控定（协调 2026-11-04：全量过后设 True），这里只测关时的行为
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([SKIP])).decide("SELECT_CARD", st(), n_options=3)
    assert d.source == "fallback" and "LLM 输出无效" in d.reason


@pytest.mark.parametrize("reply", [SKIP, {"action": "skip", "reason": "都不要"}, {"action": "none"}])
def test_skip_intent_takes_least_polluting(monkeypatch, reply):
    monkeypatch.setattr(D, "SELECT_NONE_LEAN", True)
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([reply])).decide("SELECT_CARD", st(), n_options=3)
    assert d.action.kind is ActionKind.ACTIVATE_EFFECT and "不能不拿" in d.reason, d.reason
    assert 2 not in d.action.args["pick_indexes"]  # 気合十分！ 可复用，最后才拿
    assert d.llm  # LLM 调用照样记进 trace


def test_clean_candidate_picked_without_reroll(monkeypatch):
    """协调 2026-11-04：有一次性 / 不污染的候选就直接拿，不刷新（#339 的 手書き / ゆるふわ 都是一次性）。"""
    monkeypatch.setattr(D, "SELECT_NONE_LEAN", True)
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([SKIP])).decide("SELECT_CARD", st(rerolls=3), n_options=3)
    assert d.action.kind is ActionKind.ACTIVATE_EFFECT and 2 not in d.action.args["pick_indexes"], d.reason


def test_all_polluting_rerolls_first(monkeypatch):
    """候选全部会污染牌组（可复用的非核心 / 好印象系）时才先刷新。"""
    monkeypatch.setattr(D, "SELECT_NONE_LEAN", True)
    cands = ["p_card-00-men-1_005", "p_card-02-act-2_049", "p_card-02-men-1_009"]  # 気合十分！/ みんな大好き / 励まし
    raw = [{"index": i, "card_id": c, "upgrade": 0, "name": c} for i, c in enumerate(cands)]
    s = st(rerolls=3)
    s.page_fields["candidates"] = raw
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([SKIP])).decide("SELECT_CARD", s, n_options=3)
    assert d.action.kind in (ActionKind.REROLL, ActionKind.EXCLUDE_CARD) or "污染" in d.reason, d.reason


def test_invalid_output_unrated_musts_done(monkeypatch):
    monkeypatch.setattr(D, "SELECT_NONE_LEAN", True)
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([{"action": "pick", "index": 7}])).decide("SELECT_CARD", st(), n_options=3)
    assert "必拿已齐、候选都是社区没提到的卡" in d.reason and 2 not in d.action.args["pick_indexes"], d.reason


def test_musts_missing_keeps_old_fallback(monkeypatch):
    monkeypatch.setattr(D, "SELECT_NONE_LEAN", True)
    d = ProduceDeciders(CAMPUS_HIRO, MockLLM([{"action": "pick", "index": 7}])).decide("SELECT_CARD", st(deck=MUSTS[1:] + ["p_card-02-men-0_039"] * 8),
                                                                                       n_options=3)  # fmt: skip
    assert "不能不拿" not in d.reason
