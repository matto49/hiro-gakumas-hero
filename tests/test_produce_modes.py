"""培育模式表（hiro.state.modes）：produce id → 家族 / 难度 / 步数 / 考试过关名次 / 各场考试的档位（主数据 ProduceStepAuditionDifficulty）。
设计稿 （作者本机，未发布） 第二、九节。"""

from hiro.state import modes
from hiro.state.produce import ProduceState

import pytest


@pytest.mark.parametrize(
    "pid, family, difficulty",
    [("produce-001", "hajime", "regular"), ("produce-002", "hajime", "pro"), ("produce-003", "hajime", "master"),
     ("produce-006", "hajime", "legend"), ("produce-004", "nia", "pro"), ("produce-005", "nia", "master"),
     ("produce-007", "hif", "selection"), ("produce-008", "hif", "finals")],
)  # fmt: skip
def test_family_and_difficulty(pid, family, difficulty):
    m = modes.mode(pid)
    assert (m.family, m.difficulty) == (family, difficulty)
    assert modes.family(pid) == family


def test_unknown_mode():
    assert modes.family("produce-999") is None
    with pytest.raises(KeyError):
        modes.mode("produce-999")


@pytest.mark.sim_master
@pytest.mark.parametrize(
    "pid, steps",
    [("produce-001", 13), ("produce-002", 16), ("produce-003", 18), ("produce-004", 27), ("produce-005", 26)],
)
def test_steps_from_master(pid, steps):
    assert modes.steps(pid) == steps


def test_pass_rank():
    """初：前 3 名过；N.I.A：必须第 1（主数据 rankThreshold，设计稿第二节）。"""
    assert modes.mode("produce-001").pass_rank == 3
    assert modes.mode("produce-004").pass_rank == 1


@pytest.mark.sim_master
def test_nia_audition_tiers_for_hiro():
    """N.I.A PRO 篠澤広：三场试镜，档位按编号排，档 1 不要票、高档要先攒票（voteCount）。"""
    stages = modes.auditions("produce-004", "i_card-shro-3-007")
    assert [s.kind for s in stages] == ["mid1", "mid2", "final"]
    for s in stages:
        assert [t.number for t in s.tiers] == sorted(t.number for t in s.tiers)
        assert s.tiers[0].vote_unlock == 0 and all(t.rank_threshold == 1 for t in s.tiers)
        assert all(a.base_score < b.base_score for a, b in zip(s.tiers, s.tiers[1:]))
    assert len(stages[2].tiers) >= 3


@pytest.mark.sim_master
def test_hajime_exams_single_tier():
    stages = modes.auditions("produce-001", "i_card-shro-3-007")
    assert [s.kind for s in stages] == ["mid1", "final"]
    assert all(len(s.tiers) == 1 and s.tiers[0].rank_threshold == 3 for s in stages)


def test_produce_state_new_fields_roundtrip():
    """新字段有默认值，H.I.F 的状态照旧；N.I.A 的投票数 / 剩余休息次数能存能取。"""
    ps = ProduceState(idol_card_id="i_card-shro-3-007", mode="produce-004", step=3, stamina=20, max_stamina=30, produce_point=0,
                      params={"Vo": 100, "Da": 100, "Vi": 100}, growth_permil={}, star=0, votes=4200, rest_left=4)  # fmt: skip
    back = ProduceState.from_dict(ps.to_dict())
    assert back.votes == 4200 and back.rest_left == 4 and back.family == "nia"
    hif = ProduceState(
        idol_card_id="x",
        mode="produce-007",
        step=1,
        stamina=1,
        max_stamina=1,
        produce_point=0,
        params={},
        growth_permil={},
        star=0,
    )
    assert hif.votes is None and hif.rest_left is None and hif.family == "hif"


def test_deck_card_keeps_customize_ledger():
    """特別指導记账往牌组写 customize（定制项 → 级数，ddecb43）：ProduceState.from_dict 要接得住。
    p13 第 19 天定制完第一张后决策器抛 TypeError（DeckCard 没有 customize），第二张定制没做、304P 浪费。"""
    ps = ProduceState.from_dict({"idol_card_id": "i_card-shro-3-007", "mode": "produce-007", "step": 19, "stamina": 18, "max_stamina": 39,
                                 "produce_point": 304, "params": {}, "growth_permil": {}, "star": 460,
                                 "deck": [{"card_id": "p_card-02-men-1_031", "upgrade": 1, "customized": True,
                                           "customize": {"p_card_custom-040_040_070-g_effect-aggressive_add-1": 3}}]})  # fmt: skip
    assert ps.deck[0].customize == {"p_card_custom-040_040_070-g_effect-aggressive_add-1": 3} and ps.deck[0].customized
    assert ProduceState.from_dict(ps.to_dict()).deck[0].customize == ps.deck[0].customize
