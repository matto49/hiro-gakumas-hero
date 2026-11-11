"""预测器 hiro.state.events：docs/state-events.md §3 每个事件至少一个单测（H1 任务单）。"""

import pytest

import hiro.sim  # noqa: F401  gakumas-player 路径
from hiro.state.events import EVENT_KINDS, check_value, deck_keys, initial_deck, make_event, predict

DAY, TODOITE, EIEI, SLEEP = "p_card-02-act-1_037", "p_card-02-act-3_039", "p_card-02-men-1_031", "p_card-00-acc-0_002"
# 第 6 局第 2 天（traces/lead35-qualify6-20261031-232418 #14）：普通 Da 课后实机为 Vo 436 / Da 426 / Vi 243、体力 27、P 340、星星 60
DAY2 = {"idol_card_id": "i_card-shro-3-007", "mode": "produce-007", "step": 2, "stamina": 33, "max_stamina": 33, "produce_point": 310,
        "params": {"Vo": 436, "Da": 357, "Vi": 230}, "growth_permil": {"Vo": 1399, "Da": 1380, "Vi": 1352}, "star": 30, "param_cap": 3000,
        "star_permil": 1000, "wappen_left": 3, "drinks": ["pdrink_02-3-010"], "drink_capacity": 3,
        "deck": [{"card_id": DAY, "upgrade": 1}, {"card_id": TODOITE, "upgrade": 0}, {"card_id": "p_card-02-act-0_037", "upgrade": 0}],
        "rerolls_left": 4, "excludes_left": 3, "upgrades_done": 0, "deletes_done": 0}  # fmt: skip


def _f(kind, args=None, **produce):
    preds = predict({"produce": {**DAY2, **produce}}, make_event(kind, args))
    return preds[0].fields if preds else {}


def test_every_event_has_a_predictor():
    from hiro.state.events import PREDICTORS

    assert set(PREDICTORS) == set(EVENT_KINDS)


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_initial_deck_matches_removal_frame():
    """开局牌组：基础卡 7 张（1 张距離感の基本 被能力换成 えいえいおー+）+ エウレカ！+；与删卡实拍帧 00144 的基础部分一致。"""
    from hiro.state.context import card_name

    names = sorted(card_name(c["card_id"], c["upgrade"]) for c in initial_deck("i_card-shro-3-007"))
    assert names == sorted(["アイコンタクトの基本", "仕草の基本", "えいえいおー+", "距離感の基本", "距離感の基本", "距離感の基本", "セルフケアの基本", "エウレカ！+"])
    assert len(initial_deck("i_card-shro-3-007", memories=[{"card_id": DAY, "upgrade": 1}])) == 9


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_produce_start():
    f = predict({"produce": {}}, make_event("PRODUCE_START", {"idol_card_id": "i_card-shro-3-007"}))[0].fields
    assert f["produce.deck_size"].value == 8 and f["produce.step"].value == 1


def test_day_advance():
    assert _f("DAY_ADVANCE")["produce.step"].value == 3


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_lesson_matches_real_day2():
    f = _f("LESSON", {"attr": "Da", "sp": False}, star_permil=None)  # 实机 trace 里星星倍率没读到
    obs = {"produce.params.Vo": 436, "produce.params.Da": 426, "produce.params.Vi": 243, "produce.stamina": 27, "produce.produce_point": 340,
           "produce.star": 60}  # fmt: skip
    assert all(check_value(f[k], v) for k, v in obs.items())
    assert f["produce.params.Da"].min == 426  # 主属性基础收益精确：69 × 1.380


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_lesson_sub_known_is_tighter_and_catches_read_error():
    f = _f("LESSON", {"attr": "Da", "sp": True, "sub": "Vi"})
    assert f["produce.params.Vo"].min == 436 and not check_value(f["produce.params.Da"], 73)  # 第 2 局 Da 读成 73


def test_study():
    f = _f("STUDY", {"attr": "Vo", "choice": "B"}, step=3)
    assert f["produce.params.Vo"].min == 556 and f["produce.stamina"].value == 28  # 120，不套成长率
    assert _f("STUDY", {"attr": "Vo", "choice": "trouble"}, step=3)["produce.deck_size"].value == 5  # 获得日 +1、眠気 +1


def test_outing():
    f = _f("OUTING", {"tier": "paid"}, stamina=12)
    assert f["produce.stamina"].value == 31 and f["produce.produce_point"].value == 260  # 12 + floor(33×0.6)


def test_gift():
    assert _f("GIFT")["produce.produce_point"].value == 390


def test_allowance():
    assert _f("ALLOWANCE", {"produce_point": 30})["produce.produce_point"].value == 340
    assert _f("ALLOWANCE") == {}


def test_rest():
    assert _f("REST", stamina=10)["produce.stamina"].value == 26


def test_card_acquire_triggers_wappen():
    f = _f("CARD_ACQUIRE", {"card_id": EIEI, "upgrade": 1})
    assert f["produce.deck_size"].value == 4 and f"{EIEI}+1" in f["produce.deck"].value
    assert f["produce.wappen_left"].value == 2 and f["produce.star"].value == 40
    assert "produce.wappen_left" not in _f("CARD_ACQUIRE", {"card_id": EIEI, "change": True})


def test_card_reroll_and_exclude():
    assert _f("CARD_REROLL")["produce.rerolls_left"].value == 3
    f = _f("CARD_EXCLUDE", {"card_id": EIEI})
    assert f["produce.excludes_left"].value == 2 and f["produce.excluded"].value == [EIEI]


def test_card_upgrade_consult_price():
    f = _f("CARD_UPGRADE", {"card_id": TODOITE, "upgrade": 0, "source": "consult"})
    assert f"{TODOITE}+1" in f["produce.deck"].value and f["produce.produce_point"].value == 210 and f["produce.upgrades_done"].value == 1


def test_card_delete():
    f = _f("CARD_DELETE", {"card_id": "p_card-02-act-0_037", "source": "consult"})
    assert f["produce.deck_size"].value == 2 and f["produce.produce_point"].value == 210
    assert "produce.produce_point" not in _f("CARD_DELETE", {"card_id": "p_card-02-act-0_037", "source": "event"})


def test_card_change_keeps_upgrade():
    f = _f("CARD_CHANGE", {"remove_card_id": DAY, "remove_upgrade": 1, "card_id": EIEI})
    assert f"{EIEI}+1" in f["produce.deck"].value and f"{DAY}+1" not in f["produce.deck"].value


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_card_auto_process():
    f = predict({"produce": DAY2}, make_event("CARD_AUTO_PROCESS", {"exam": 1}))[0]
    assert f.fields["produce.deck_size"].value == 2 and f.due == "DECK_LIST"  # 只有 1 张基础卡：最多删 1


def test_card_customize():
    f = _f("CARD_CUSTOMIZE", {"deck_index": 0, "price": 120})
    assert f["produce.deck.0.customized"].value is True and f["produce.produce_point"].value == 190


def test_shop_buy():
    f = _f("SHOP_BUY", {"kind": "card", "card_id": EIEI, "price": 100})
    assert f["produce.produce_point"].value == 210 and f["produce.deck_size"].value == 4
    assert _f("SHOP_BUY", {"kind": "drink", "drink_id": "pdrink_00-3-001", "price": 75})["produce.drinks"].value == ["pdrink_00-3-001", "pdrink_02-3-010"]


def test_drink_acquire_use_discard():
    assert len(_f("DRINK_ACQUIRE", {"drink_id": "pdrink_00-3-001"})["produce.drinks"].value) == 2
    assert _f("DRINK_USE", {"slot": 0})["produce.drinks"].value == []
    f = _f("DRINK_DISCARD", {"discard": [0], "receive_drink_id": "pdrink_00-2-010"})
    assert f["produce.drinks"].value == ["pdrink_00-2-010"]
    assert _f("DRINK_DISCARD", {"discard": []})["produce.drinks"].value == ["pdrink_02-3-010"]


@pytest.mark.parametrize("kind", ["PITEM_ACQUIRE", "PITEM_CUSTOMIZE"])
def test_pitem(kind):
    f = _f(kind, {"item_id": "pitem_new", "replaces": "pitem_old"}, items=["pitem_old"])
    assert f["produce.items"].value == ["pitem_new"]


def test_wappen_trigger():
    assert _f("WAPPEN_TRIGGER", {"gain": 15})["produce.star"].value == 45
    f = _f("WAPPEN_TRIGGER", star_permil=None)
    assert f["produce.star"].min == 40 and f["produce.star"].max == 50  # 倍率未识别：1.0–2.0×


def test_exam_start():
    f = predict({"produce": {**DAY2, "stamina": 10}}, make_event("EXAM_START", {"exam_label": "r1"}))[0].fields
    assert f["exam.hp"].value == 26 and f["exam.remaining_turns"].value == 9
    assert predict({"produce": {**DAY2, "stamina": 10}}, make_event("EXAM_START", {"exam_label": "r2"}))[0].fields["exam.hp"].value == 10


def test_exam_result_selection_and_finals():
    f = _f("EXAM_RESULT", {"exam": 1, "score": 14001})
    assert f["produce.star"].value == 30 + 40  # 选拔① 星满 40（基础值）×1.0
    assert f["produce.params.Da"].min == 357 and f["produce.params.Da"].max > 357
    f = _f("EXAM_RESULT", {"exam": "r1", "score": 135852})
    assert f["produce.star"].min <= 30 + 41 <= f["produce.star"].max and "produce.produce_point" in f


def test_round_interval_buy():
    f = _f("ROUND_INTERVAL_BUY", {"service": "recover"}, stamina=17)
    assert f["produce.stamina"].value == 19 and f["produce.produce_point"].value == 300
    assert _f("ROUND_INTERVAL_BUY", {"service": "card", "card_id": EIEI, "price": 30})["produce.produce_point"].value == 280


def test_unmodeled_change_has_no_prediction():
    assert predict({"produce": DAY2}, make_event("UNMODELED_CHANGE", {"field": "produce.params.Da"})) == []


def test_deck_keys_and_check_value():
    assert deck_keys([{"card_id": "b", "upgrade": 1}, {"card_id": "a"}]) == ["a+0", "b+1"]
    assert check_value({"min": 1, "max": 3, "exact": False}, 3) and not check_value({"value": 5, "exact": True}, None)


def test_returns_contract_types():
    from hiro.contracts import Event, Expect, Prediction

    ev = make_event("REST")
    assert isinstance(ev, Event) and not ev.problems()
    p = predict({"produce": {**DAY2, "stamina": 10}}, ev)[0]
    assert isinstance(p, Prediction) and isinstance(p.fields["produce.stamina"], Expect) and p.fields["produce.stamina"].exact
    assert predict({"produce": DAY2}, ev.to_dict())[0].fields["produce.stamina"].value == 33  # dict 事件也接
    assert predict({"produce": DAY2}, make_event("DECK_REVIEW", {"deck": []}, origin="review")) == []
