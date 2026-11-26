"""D12（11-26 用户：「换饮料没有策略吗，参考换卡策略完善一下」）：初 / N.I.A 的饮料按偶像流派定档，同档按社区顺序直接定。

原来的档位表是按篠澤広（やる気）定的：やる気 2 档、好印象 4 档；センス / アノマリー 的饮料都落到「其他」。初 / N.I.A 没有饮料模拟，
同档一打平就交 LLM——G1-F8：藤田ことね（好印象）饮料满，规则拿不准，LLM 说「ホットコーヒー是やる気系，和篠澤広的打法契合」要收下。
"""

from hiro.brain import drinks as dk

import pytest

WATER, OOLONG, AOJIRU = "pdrink_00-1-001", "pdrink_00-1-004", "pdrink_00-3-005"
VITAMIN, ICE_COFFEE = "pdrink_01-1-002", "pdrink_01-1-003"
ROOIBOS, HOT_COFFEE, HERB_TEA = "pdrink_02-1-006", "pdrink_02-1-008", "pdrink_02-2-006"
GINGER = "pdrink_03-1-009"

pytestmark = pytest.mark.master


def test_new_classes():
    from hiro.state.knowledge import drink_class

    assert drink_class(VITAMIN) == "kocho" and drink_class(ICE_COFFEE) == "shuchu" and drink_class(GINGER) == "anomaly"


@pytest.mark.parametrize(
    ("buff", "drink", "tier"),
    [
        (None, HOT_COFFEE, 2),  # 原表（广）
        (None, ROOIBOS, 4),
        ("やる気", HOT_COFFEE, 2),  # 广：和原表一样
        ("やる気", ROOIBOS, 4),
        ("好印象", ROOIBOS, 2),  # ことね：反过来
        ("好印象", HERB_TEA, 2),
        ("好印象", HOT_COFFEE, 4),
        ("好調", VITAMIN, 2),
        ("好調", ICE_COFFEE, 4),
        ("集中", ICE_COFFEE, 2),
        ("強気", GINGER, 2),
        ("好印象", WATER, 3),  # 不加 buff 的饮料不受流派影响
    ],
)
def test_tier_follows_signature_buff(buff, drink, tier):
    assert dk.tag([drink], "selection", buff)[0][0] == tier


def _overflow_opts(held, incoming):
    """饮料满：丢第 j 瓶换新来的 / 不收。"""
    opts = [dk.Option(f"drop{j}", f"丢 {h}", held[:j] + [incoming] + held[j + 1 :], None) for j, h in enumerate(held)]
    return [*opts, dk.Option("keep", "不收", held, None)]


def test_kotone_does_not_take_hot_coffee():
    """G1-F8 那一刻（持有 初星水 / ルイボスティー / 青汁，新来 ホットコーヒー）：ことね 不收やる気饮料，按规则定、不交 LLM。"""
    held = [WATER, ROOIBOS, AOJIRU]
    res = dk.choose(_overflow_opts(held, HOT_COFFEE), "selection", 3, take_on_tie=True, buff="好印象", decide_ties=True)
    assert res["pick"].key == "keep" and res["unsure"] is None
    assert "好印象" in res["why"]


def test_kotone_swaps_water_for_rooibos():
    held = [WATER, OOLONG, AOJIRU]
    res = dk.choose(_overflow_opts(held, ROOIBOS), "selection", 3, take_on_tie=True, buff="好印象", decide_ties=True)
    assert ROOIBOS in res["pick"].drinks and res["unsure"] is None


def test_ties_still_go_to_llm_without_decide_ties_or_with_unknown():
    opts = [dk.Option("a", "a", [WATER, AOJIRU], None), dk.Option("b", "b", [OOLONG, AOJIRU], None)]
    assert dk.choose(opts, "selection", 2)["unsure"] is not None  # H.I.F 路径：原样
    assert dk.choose(opts, "selection", 2, decide_ties=True)["unsure"] is None
    opts_unknown = [dk.Option("a", "a", [None, WATER], None), dk.Option("b", "b", [None, OOLONG], None)]  # 同档
    assert dk.choose(opts_unknown, "selection", 2, decide_ties=True)["unsure"] is not None  # 有没认出的饮料：仍交 LLM


def test_decider_uses_flow_only_for_hajime_nia():
    from types import SimpleNamespace as NS

    from hiro.brain.deciders import ProduceDeciders

    d = ProduceDeciders(None, None)
    assert d._drink_flow(NS(mode="produce-001", idol_card_id="i_card-skin-fktn-3-000")) == {
        "buff": "好印象",
        "decide_ties": True,
    }
    assert d._drink_flow(NS(mode="produce-008", idol_card_id="i_card-skin-fktn-3-000")) == {}  # H.I.F：不变
    assert d._drink_flow(None) == {}
