"""授業セレクトチェンジ：换卡未受控时授業改选眠気档；换卡决策器选换入 / 换出（核心卡不换出）。

第 4 局本战两次授業セレクトチェンジ 由 KAA 固定换第 0 张，换走了 デイドリーミング、がむしゃら
（reports/analysis/q4-daydreaming-missing-exec.md）。"""

from hiro.brain.deciders import ProduceDeciders, card_value
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

DAYDREAM, TODOITE, HIGH, BASIC, SLEEP, AYAKU = ("p_card-02-act-1_037", "p_card-02-act-3_039", "p_card-00-men-2_014",
                                               "p_card-02-act-0_037", "p_card-00-acc-0_002", "p_card-02-act-3_045")  # fmt: skip
CHANGE_CHOICES = [
    {"index": 0, "text": "トラブルカード以外のスキルカードを選択して異なる好印象関係のスキルカードにセレクトチェンジ"},
    {"index": 1, "text": "トラブルカード以外のスキルカードを選択して異なるやる気関係のスキルカードにセレクトチェンジ"},
    {"index": 2, "text": "トラブル追加（眠気獲得）", "trouble": True},
]


FINALS_DAY1 = {"idol_card_id": "i_card-shro-3-007", "mode": "produce-008", "step": 1, "stamina": 33, "max_stamina": 33, "produce_point": 200,
               "params": {"Vo": 1697, "Da": 1936, "Vi": 815}, "growth_permil": {"Vo": 1413, "Da": 1394, "Vi": 1352}, "star": 759}  # fmt: skip


@pytest.mark.master
def test_study_change_uncontrolled_finals_picks_trouble():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "STUDY", GameState(produce=FINALS_DAY1, page_fields={"choices": CHANGE_CHOICES})
    )
    assert d.action.args["select"] == 2 and "眠気档" in d.reason


@pytest.mark.master
def test_study_change_uncontrolled_qualify_keeps_effect_option():
    qualify = dict(FINALS_DAY1, mode="produce-007", step=10)
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "STUDY", GameState(produce=qualify, page_fields={"choices": CHANGE_CHOICES})
    )
    assert d.action.args["select"] == 1 and d.warnings  # 执行侧保护核心卡；换进冲突牌之后相談再删


@pytest.mark.master
def test_study_change_controlled_keeps_effect_option():
    d = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True).decide(
        "STUDY", GameState(page_fields={"choices": CHANGE_CHOICES})
    )
    assert d.action.args["select"] == 1


def test_change_pick_and_remove():
    deciders = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True)
    cands = [
        {"index": 0, "card_id": HIGH, "upgrade": 0},
        {"index": 1, "card_id": AYAKU, "upgrade": 0},
        {"index": 2, "card_id": BASIC, "upgrade": 0},
    ]
    d1 = deciders.decide("SKILL_CARD_CHANGE_1", GameState(page_fields={"candidates": cands}))
    assert d1.action.kind is ActionKind.CHANGE_CARD and d1.action.args == {"stage": 1, "pick_index": 1}
    grid = [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}, {"index": 1, "card_id": TODOITE, "upgrade": 1},
            {"index": 2, "card_id": SLEEP, "upgrade": 0}, {"index": 3, "card_id": HIGH, "upgrade": 0}, {"index": 4, "card_id": BASIC, "upgrade": 0}]  # fmt: skip
    d2 = deciders.decide("SKILL_CARD_CHANGE_2", GameState(page_fields={"cards": grid}))
    # 用户 2026-11-03 顺序：冲突牌（ハイテンション）先于初始卡；第 0 张 デイドリーミング 不动
    assert d2.action.args["remove_card_id"] == HIGH and d2.action.args["remove_index"] == 3


def test_change_remove_cancels_when_only_core_or_unread():
    deciders = ProduceDeciders(CAMPUS_HIRO, None)
    only_core = [
        {"index": 0, "card_id": DAYDREAM, "upgrade": 1},
        {"index": 1, "card_id": TODOITE, "upgrade": 1},
        {"index": 2, "card_id": SLEEP},
    ]
    assert deciders.decide("SKILL_CARD_CHANGE_2", GameState(page_fields={"cards": only_core})).action.args == {
        "cancel": True
    }
    assert deciders.decide("SKILL_CARD_CHANGE_2", GameState(page_fields={})).action.args == {"cancel": True}


def test_card_value_order():
    assert card_value(TODOITE) > card_value(AYAKU) > card_value(BASIC) > card_value(HIGH)


def test_choose_card_to_change():
    from hiro.brain.deciders import choose_card_to_change
    from hiro.state.produce import DeckCard

    GAMUSHARA = "p_card-02-act-1_071"
    deck = [
        {"card_id": DAYDREAM, "upgrade": 1},
        {"card_id": GAMUSHARA, "upgrade": 1},
        {"card_id": BASIC, "upgrade": 0},
        {"card_id": SLEEP},
    ]
    assert choose_card_to_change(deck) == BASIC  # デイドリーミング、がむしゃら（protect）、眠気 都不换
    assert choose_card_to_change(deck, AYAKU) == BASIC
    assert choose_card_to_change(deck, HIGH) is None  # 换入冲突牌不如不换
    assert choose_card_to_change([DeckCard(DAYDREAM, 1), DeckCard(GAMUSHARA, 0)]) is None


def test_protected_list_has_gamushara_and_core():
    from hiro.produce.card_ops import protected_card_ids

    ids = protected_card_ids()
    assert {"p_card-02-act-1_071", DAYDREAM, TODOITE} <= ids and HIGH not in ids


@pytest.mark.master
def test_study_change_only_with_junk_and_never_cancel():
    """main8：セレクトチェンジ 取消不了（阶段 1 没有取消键，取消后绕回换卡页死循环）。"""
    controlled = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True)
    finals_core = dict(
        FINALS_DAY1,
        deck=[
            {"card_id": DAYDREAM, "upgrade": 1},
            {"card_id": TODOITE, "upgrade": 1},
            {"card_id": AYAKU, "upgrade": 1},
        ],
    )
    d = controlled.decide("STUDY", GameState(produce=finals_core, page_fields={"choices": CHANGE_CHOICES}))
    assert d.action.args["select"] == 2  # 没有废牌：选眠気档
    with_basic = dict(finals_core, deck=finals_core["deck"] + [{"card_id": BASIC, "upgrade": 0}])
    assert (
        controlled.decide("STUDY", GameState(produce=with_basic, page_fields={"choices": CHANGE_CHOICES})).action.args[
            "select"
        ]
        == 1
    )
    # 本战牌组未知：换（2026-11-04 起，手册「本战换掉剩余弱卡」+ 眠気档会加 眠気；阶段 2 读对话框牌组再挑换出）
    assert (
        controlled.decide("STUDY", GameState(produce=FINALS_DAY1, page_fields={"choices": CHANGE_CHOICES})).action.args[
            "select"
        ]
        == 1
    )
    # 阶段 2：换入的卡更弱也照样换（取消会绕回），只给提醒
    controlled.change_target = BASIC
    win = "p_card-00-sup-3_158"  # 勝利をつかめ！（不在保护名单）
    grid = [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}, {"index": 1, "card_id": win, "upgrade": 1}]
    d2 = controlled.decide("SKILL_CARD_CHANGE_2", GameState(page_fields={"cards": grid}))
    assert d2.action.args.get("remove_card_id") == win and any("只能换" in w for w in d2.warnings)


def test_eieiou_protected_two_copies():
    """main8-r1：第 2 次授業换卡换出了 えいえいおー（固有道具触发牌，エウレカ 靠它压血）。保留 2 张、价值按 A 级。"""
    from hiro.brain.deciders import choose_card_to_change

    EIEI, WIN = "p_card-02-men-1_031", "p_card-00-sup-3_158"
    assert card_value(EIEI) >= 6
    two = [
        {"index": 0, "card_id": EIEI, "upgrade": 1},
        {"index": 1, "card_id": EIEI, "upgrade": 0},
        {"index": 2, "card_id": WIN, "upgrade": 1},
    ]
    assert choose_card_to_change(two) == WIN
    three = two + [{"index": 3, "card_id": EIEI, "upgrade": 0}]
    from hiro.state.produce import DeckCard

    assert (
        choose_card_to_change([c for c in three if c["card_id"] == EIEI]) is None
    )  # 对话框张数不可信（总控 2026-11-03）：全留
    assert (
        choose_card_to_change([DeckCard(EIEI, 1), DeckCard(EIEI, 0), DeckCard(EIEI, 0)]) == EIEI
    )  # 记账确认 3 张：第 3 张起可换
    assert choose_card_to_change(two[:2]) is None
