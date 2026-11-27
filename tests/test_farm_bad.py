"""刷回忆：「初」BAD 育成（HIRO_FARM=bad，hiro.brain.farm）：13 周固定日程、练习课刚好不通关、相談删掉这局拿到的卡。"""

from types import SimpleNamespace as NS

from hiro.brain import farm

import pytest

CRIT = {"Vo": 0.2, "Da": 0.5, "Vi": 0.3}  # 审查基准：Da 第 1、Vi 第 2


def _opt(option, attr=None, sp=None, oikomi=None):
    return {"option": option, "attr": attr, "sp": sp, "oikomi": oikomi}


def test_switch():
    assert farm.mode({}) is None and farm.mode({"HIRO_FARM": "bad"}) == "bad" and farm.mode({"HIRO_FARM": "x"}) is None
    assert farm.margin_ratio({}) == farm.DEFAULT_MARGIN and farm.margin_ratio({"HIRO_FARM_BAD_MARGIN": "0.2"}) == 0.2


def test_lesson_week_takes_sp_of_top_criteria():
    opts = [_opt("training", "Vo", True), _opt("training", "Da", False), _opt("training", "Da", True), _opt("rest")]
    i, why = farm.pick_action(1, opts, CRIT)
    assert opts[i]["attr"] == "Da" and opts[i]["sp"] and "故意不通关" in why


def test_lesson_index_zero_is_found():
    opts = [_opt("training", "Vo", False), _opt("rest")]
    assert farm.pick_action(3, opts, None)[0] == 0  # 审查基准没读到：唯一一节课在第 0 个


def test_oikomi_takes_second_criteria():
    opts = [_opt("training", a, oikomi=True) for a in ("Vo", "Da", "Vi")]
    i, _ = farm.pick_action(5, opts, CRIT)
    assert opts[i]["attr"] == "Vi"


def test_rest_weeks_and_week7():
    opts = [_opt("rest"), _opt("outing"), _opt("shop"), _opt("training", "Da", True)]
    for week in (2, 4, 8, 11):
        assert opts[farm.pick_action(week, opts, CRIT)[0]]["option"] == "rest"
    assert opts[farm.pick_action(7, opts, CRIT, has_junk=True)[0]]["option"] == "shop"
    assert opts[farm.pick_action(7, opts, CRIT, has_junk=False)[0]]["option"] == "outing"
    assert opts[farm.pick_action(7, opts, CRIT, has_junk=True, shop_ok=False)[0]]["option"] == "outing"


def test_unknown_week_or_missing_option_falls_back():
    assert farm.pick_action(None, [_opt("rest")], CRIT) is None
    assert farm.pick_action(6, [_opt("rest")], CRIT) is None  # 考试周不在日程里
    assert farm.pick_action(2, [_opt("training", "Da", True)], CRIT) is None  # 休息周没有休息可选


def test_junk_cards(monkeypatch):
    import hiro.brain.deciders as d

    monkeypatch.setattr(d, "_is_basic", lambda cid: cid.endswith("basic"))
    monkeypatch.setattr(d, "_is_trouble_card", lambda cid: cid == "sleepy")
    cards = [{"index": 0, "card_id": "a-basic"}, {"index": 1, "card_id": "p_card-01-ido-3_1"}, {"index": 2, "card_id": "target"},
             {"index": 3, "card_id": "picked"}, {"index": 4, "card_id": "sleepy"}, {"index": 5, "card_id": None},
             {"index": 6, "card_id": "custom", "customized": True}]  # fmt: skip
    picked = {"a-basic": 1, "p_card-01-ido-3_1": 1, "target": 1, "picked": 1, "sleepy": 1, "custom": 1}
    assert [c["card_id"] for c in farm.junk_cards(cards, {"target"}, picked)] == ["sleepy", "picked"]


def test_target_cards_env():
    assert farm.target_cards({"HIRO_FARM_TARGET": "p_card-01-men-3_036, p_card-x"}) == {
        "p_card-01-men-3_036",
        "p_card-x",
    }


def test_bad_lesson_advisor_skips_near_clear():
    from hiro.contracts import Action, ActionKind

    inner = NS(decide=lambda *a: "inner")
    live = {}
    adv = farm.BadLessonAdvisor(inner, live, lambda: 100, 0.3)
    legal = [Action.play_card(0), Action.skip()]
    assert adv.decide(None, None, None, legal) == "inner"  # 还没读到离通关多少
    live["left"] = 45
    assert adv.decide(None, None, None, legal) == "inner"  # 还差 45 > 30：照常出牌
    live["left"] = 25
    d = adv.decide(None, None, None, legal)
    assert d.action.kind is ActionKind.SKIP and "刚好没过" in d.reason
    assert adv.decide(None, None, None, [Action.play_card(0)]) == "inner"  # 不能跳过时照常


@pytest.mark.master
def test_action_select_uses_schedule(monkeypatch):
    """接进初的选行动：HIRO_FARM=bad 时第 2 周休息（原来的规则会去上课）。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    monkeypatch.setenv("HIRO_FARM", "bad")
    prod = {"idol_card_id": "i_card-fktn-3-000", "mode": "produce-001", "step": 2, "stamina": 30, "max_stamina": 30, "produce_point": 0,
            "params": {"Vo": 100, "Da": 100, "Vi": 100}, "growth_permil": {}, "deck": []}  # fmt: skip
    opts = [_opt("training", "Da", True), _opt("rest")]
    d = ProduceDeciders(profile_for("i_card-fktn-3-000"), None).decide(
        "ACTION_SELECT", GameState(produce=prod, page_fields={"options": opts})
    )
    assert d.action.args.get("option") == "rest" and "刷回忆" in d.reason


def test_target_accepts_card_names(monkeypatch):
    """11-27 实机：界面里填的是卡名；id 原样，名字按资料库找（完全一致优先），找不到的丢掉。"""
    from hiro.brain import farm
    from hiro.cli import catalog

    def search(q, kind=None, lang="ja"):
        return [{"id": "p_card-02-act-3_050", "name": "輝くキミへ"}] if q == "輝くキミへ" and lang == "ja" else []

    monkeypatch.setattr(catalog, "search", search)
    got = farm.target_cards({"HIRO_FARM_TARGET": "輝くキミへ+，p_card-02-act-3_186,没有这张"})
    assert got == {"p_card-02-act-3_050", "p_card-02-act-3_186"}


def test_impression_passive_gain_counts_in_margin():
    """11-27 实机：好印象流停手后每回合还自动加分，差 5 停手照样过线。余量算上之后好印象自动加的分。"""
    from types import SimpleNamespace as NS

    from hiro.brain import farm
    from hiro.contracts import Action, ActionKind

    assert farm.impression_gain(5, 3) == 5 + 4 + 3
    assert farm.impression_gain(2, 5) == 2 + 1
    assert farm.impression_gain(None, 3) == 0

    class Inner:
        def decide(self, *a):
            return "inner"

    legal = [Action.skip(), Action(ActionKind.PLAY_CARD, {"slot": 0})]
    adv = farm.BadLessonAdvisor(Inner(), {"left": 30}, 50, 0.4)  # 只按比例：余量 20，差 30 照常出牌
    assert adv.decide(NS(impression=None, remaining_turns=3), None, None, legal) == "inner"
    d = adv.decide(NS(impression=5, remaining_turns=3), None, None, legal)  # 20 + 12×1.2 = 34.4 ≥ 30：停手
    assert d.action.kind is ActionKind.SKIP and "好印象" in d.reason


def test_week7_goes_shop_when_deck_unread_but_cards_picked(monkeypatch):
    """11-27 DMM：牌组按钮认不出、牌组为空 → 以为没卡可删去了外出。牌组读不到时按这局拿过的卡判断。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-02-act-3_050")
    picked = {"p_card-02-act-1_010", "p_card-02-act-3_050"}
    junk = farm.junk_cards([{"card_id": c} for c in picked], farm.target_cards(), dict.fromkeys(picked, 1))
    assert [c["card_id"] for c in junk] == ["p_card-02-act-1_010"]  # 目标卡不算要删的


def test_remove_card_page_never_deletes_target(monkeypatch):
    """CodeRabbit #198：删卡页（相談没带 card_id / 删卡事件）刷回忆时也只删这局拿到的卡，转写目标不删。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-02-act-3_186")
    prod = {"idol_card_id": "i_card-hski-3-000", "mode": "produce-001", "step": 7, "deck": []}
    cards = [
        {"index": 0, "card_id": "p_card-02-act-3_186", "upgrade": 0},  # 目标
        {"index": 1, "card_id": "p_card-02-act-1_010", "upgrade": 0},  # 这局拿的
    ]
    dec = ProduceDeciders(profile_for("i_card-hski-3-000"), None)
    dec.farm_picked.update({"p_card-02-act-1_010": 1, "p_card-02-act-3_186": 1})  # 这局领到过的（目标也领到过一张）
    d = dec.decide("SKILL_CARD_REMOVAL", GameState(produce=prod, page_fields={"cards": cards}))
    assert d.action.args.get("index") == 1 and d.action.args.get("card_id") == "p_card-02-act-1_010", d
    assert dec.farm_picked["p_card-02-act-1_010"] == 0  # 决定删了：名单里减掉
    d = dec.decide("SKILL_CARD_REMOVAL", GameState(produce=prod, page_fields={"cards": cards}))
    assert d.action.args.get("cancel"), d  # 只剩目标卡（和名单里已经删过的那张）：取消，不删


def test_consult_delete_carries_card_id(monkeypatch):
    """CodeRabbit #198：刷回忆相談删卡带上 card_id（删卡页按它选，不让 remove_card 重选），也不选转写目标。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-02-act-3_186")
    deck = ["p_card-02-act-3_186", "p_card-02-act-1_010"]
    prod = {"idol_card_id": "i_card-hski-3-000", "mode": "produce-001", "step": 7, "stamina": 20, "max_stamina": 30,
            "produce_point": 300, "params": {"Vo": 1, "Da": 1, "Vi": 1}, "growth_permil": {}, "rerolls_left": 0,
            "excludes_left": 0, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    pf = {"shelf": [], "services": {"delete": {"price": 100, "available": True}}}
    dec = ProduceDeciders(profile_for("i_card-hski-3-000"), None)
    dec.farm_picked.update({"p_card-02-act-1_010": 1})
    d = dec.decide("CONSULT", GameState(produce=prod, page_fields=pf))
    assert d.action.args.get("service") == "delete" and d.action.args.get("card_id") == "p_card-02-act-1_010", d


def test_unrecorded_cards_are_never_deleted(monkeypatch):
    """CodeRabbit #198：删卡候选只能是本局确认领到的卡（正向名单）。借用回忆带进来的卡不在任何名单里，原来也会被删；
    现在没有记账的卡一律不删。同一张卡名单里记了几张最多删几张。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import ActionKind, GameState

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-02-act-3_186")
    deck = ["p_card-02-act-3_186", "p_card-02-act-2_001"]  # 目标 + 借用回忆带进来的卡（没有记账）
    prod = {"idol_card_id": "i_card-hski-3-000", "mode": "produce-001", "step": 7, "stamina": 20, "max_stamina": 30,
            "produce_point": 300, "params": {"Vo": 1, "Da": 1, "Vi": 1}, "growth_permil": {}, "rerolls_left": 0,
            "excludes_left": 0, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    pf = {"shelf": [], "services": {"delete": {"price": 100, "available": True}}}
    dec = ProduceDeciders(profile_for("i_card-hski-3-000"), None)
    d = dec.decide("CONSULT", GameState(produce=prod, page_fields=pf))
    assert d.action.kind is ActionKind.SHOP_END, d
    cards = [
        {"index": 0, "card_id": "p_card-x"},
        {"index": 1, "card_id": "p_card-x"},
        {"index": 2, "card_id": "p_card-y"},
    ]
    # 牌组里两份 x、只记了一张：分不清哪份是本局领的，一份都不删（CodeRabbit #198）；y 没记账也不删
    assert [c["index"] for c in farm.junk_cards(cards, set(), {"p_card-x": 1})] == []
    assert [c["index"] for c in farm.junk_cards(cards, set(), {"p_card-x": 2, "p_card-y": 1})] == [0, 1, 2]


def test_picked_cards_from_trace():
    """续跑：删卡正向名单从 trace 里选卡领取成功的步骤恢复，减去之后删掉的；牌组快照（含借用回忆的卡）不算。"""
    from hiro.loop.ledger import picked_cards

    def pick(cid, ok=True):
        return {"state": {"page": {"name": "SELECT_CARD"}, "page_fields": {"candidates": [{"card_id": cid}]}},
                "decision": {"action": {"kind": "ActivateEffect", "args": {"pick_indexes": [0]}}}, "execution": {"ok": ok}}  # fmt: skip

    deck = {"observation": {"page": {"name": "DECK_LIST"}, "fields": {"deck": [{"card_id": "p_card-rental"}]}},
            "execution": {"ok": True}}  # fmt: skip
    delete = {"state": {"page": {"name": "CONSULT"}},
              "decision": {"action": {"kind": "ShopBuy", "args": {"service": "delete", "card_id": "p_card-a"}}},
              "execution": {"ok": True}}  # fmt: skip
    got = picked_cards([pick("p_card-a"), pick("p_card-b"), pick("p_card-c", ok=False), deck, delete])
    assert dict(got) == {"p_card-b": 1}


def _boxes(*items):
    from types import SimpleNamespace as NS

    return [NS(text=t, rect=NS(center=NS(x=x, y=y))) for t, x, y in items]


RESULT = ("獲得可能スキルカード", 163, 685), ("中間試験/1次オーディション後に獲得", 485, 690), ("リズミカル", 270, 730), \
    ("あと1回可能", 85, 1115), ("再生成", 130, 1160), ("次へ", 360, 1160)  # fmt: skip


def test_regen_once_when_not_target(monkeypatch):
    """11-27 实机：回忆带「リズミカル」不是目标「輝くキミへ」，旁边写着再生成还能 1 次，程序直接点了次へ。现在再生成一次。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    assert farm.memory_result(_boxes(*RESULT))["name"] == "リズミカル"
    assert farm.regen_step(_boxes(*RESULT)) == (130, 1160)  # 点再生成
    dlg = _boxes(
        ("メモリー再生成", 360, 500),
        ("メモリー再生成チケットを1枚使用します", 360, 640),
        ("キャンセル", 218, 1000),
        ("再生成する", 500, 1000),
    )
    assert farm.regen_step(dlg) == (500, 1000)  # 确认（用券，没有钻石）
    assert farm.regen_step(_boxes(*RESULT)) is None  # 只再生成一次：照常点次へ


def test_regen_never_spends_jewels(monkeypatch):
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    farm.regen_step(_boxes(*RESULT))
    dlg = _boxes(
        ("ジュエルを50個使用して再生成しますか", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000)
    )
    assert farm.regen_step(dlg) == (218, 1000)  # 有钻石：取消


def test_regen_skipped_when_hit_or_off(monkeypatch):
    from hiro.brain import farm

    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    monkeypatch.setattr(farm, "_target_names", lambda: {"リズミカル"})
    monkeypatch.setenv("HIRO_FARM", "bad")
    assert farm.regen_step(_boxes(*RESULT)) is None  # 刷到了
    monkeypatch.setenv("HIRO_FARM", "")
    assert farm.regen_step(_boxes(*RESULT)) is None  # 没开刷回忆


def test_regen_ignores_unrelated_dialog(monkeypatch):
    """点了再生成没弹确认框（直接重抽）：之后别的弹窗哪怕有「決定」也不替它点。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    now = [0.0]
    monkeypatch.setattr(farm, "_clock", lambda: now[0])
    assert farm.regen_step(_boxes(*RESULT)) == (130, 1160)
    other = _boxes(("フォトを保存しますか", 360, 640), ("キャンセル", 218, 1000), ("決定", 500, 1000))
    assert farm.regen_step(other) == (218, 1000)  # 不替它点「決定」：点キャンセル
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT  # 刚点了再生成：框可能还会弹，先等
    now[0] = 5
    assert farm.regen_step(_boxes(*RESULT)) is None  # 回到结果页：不再等确认框，也不再生成第二次
    assert farm._regen["pending"] is False


def test_regen_dialog_needs_ticket_or_free(monkeypatch):
    """白名单：确认框里没读到「チケット」「無料」（钻石只显示成图标 + 数字）就キャンセル；
    框是半透明的、底下结果页的字还读得到时也由这里管，不交回通用分支去点「決定」。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    farm.regen_step(_boxes(*RESULT))
    dlg = _boxes(
        *RESULT,
        ("メモリーを再生成しますか", 360, 600),
        ("50", 400, 650),
        ("キャンセル", 218, 1000),
        ("決定", 500, 1000),
    )
    assert farm.regen_step(dlg) == (218, 1000)


def _regen_started(monkeypatch, now):
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    monkeypatch.setattr(farm, "_clock", lambda: now[0])
    assert farm.regen_step(_boxes(*RESULT)) == (130, 1160)
    return farm


def test_regen_dialog_without_cancel_waits_then_stops(monkeypatch):
    """CodeRabbit #198：等确认框时认不出キャンセル（只读到费用和「決定」）：不交回通用分支（会点決定），等着；20 秒还这样就停下。"""
    from hiro.loop.errors import ResumeBlocked

    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    dlg = _boxes(*RESULT, ("50", 400, 650), ("決定", 500, 1000))
    assert farm.regen_step(dlg) == farm.WAIT
    now[0] = 25
    with pytest.raises(ResumeBlocked, match="再生成"):
        farm.regen_step(dlg)


def test_regen_confirm_must_be_on_cancel_row(monkeypatch):
    """CodeRabbit #198：框里认不出确认按钮时，不能把底下结果页的「再生成」（y 1160）当确认按钮。"""
    farm = _regen_started(monkeypatch, [0.0])
    dlg = _boxes(*RESULT, ("再生成チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000))
    assert farm.regen_step(dlg) == (218, 1000)


def test_regen_keeps_watching_after_cancel(monkeypatch):
    """CodeRabbit #198：点了キャンセル框还开着：照样按白名单处理（不放给通用分支）；框关了回到干净的结果页才结束。"""
    farm = _regen_started(monkeypatch, [0.0])
    dlg = _boxes(
        ("メモリーを再生成しますか", 360, 600), ("50", 400, 650), ("キャンセル", 218, 1000), ("決定", 500, 1000)
    )
    assert farm.regen_step(dlg) == (218, 1000)
    assert farm.regen_step(dlg) == (218, 1000)  # 还开着：再点キャンセル，绝不点決定
    now = farm._clock()
    monkeypatch.setattr(farm, "_clock", lambda: now + 5)
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT  # 取消过的框：要连着两帧没框才算关了
    assert farm.regen_step(_boxes(*RESULT)) is None and not farm._regen["pending"]


def test_bad_wrapper_goes_outside_strategy_plugin(monkeypatch):
    """CodeRabbit #198：strategy.json 换了出牌插件时，BAD 停手包在最后选定的出招外面（原来被放进 GuardedAdvisor 的退路里，插件出牌时不停手）。"""
    from hiro.exec import exam as ex

    plugin_adv, given = object(), object()
    monkeypatch.setattr(ex, "_plugin", lambda kind: None)  # 没有局面来源：包完之后报错停下，正好看包的是谁
    monkeypatch.setattr(ex, "pick_advisor", lambda exam, mode, given=None: plugin_adv)
    seen = []

    def wrap(a):
        seen.append(a)
        return a

    with pytest.raises(RuntimeError):
        ex.run_exam(None, "hajime-lesson-da", None, lambda shot: True, advisor=given, wrap=wrap)
    assert len(seen) == 1 and isinstance(seen[0], ex.GuardedAdvisor)


def test_regen_late_dialog_is_still_handled(monkeypatch):
    """自检：点了再生成后第一帧还是干净的结果页（框还没弹）：先等，框晚弹出来照样按白名单处理，不交给通用分支。"""
    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    now[0] = 1.0
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT
    dlg = _boxes(
        *RESULT,
        ("メモリーを再生成しますか", 360, 600),
        ("50", 400, 650),
        ("キャンセル", 218, 1000),
        ("決定", 500, 1000),
    )
    now[0] = 2.0
    assert farm.regen_step(dlg) == (218, 1000)


def test_regen_after_confirm_hands_back_when_no_dialog(monkeypatch):
    """自检：确认以后重抽的演出画面（不是结果页、也没有框）：不等到超时，交回收尾照常走。"""
    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    dlg = _boxes(
        ("メモリー再生成チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000)
    )
    assert farm.regen_step(dlg) == (500, 1000)
    assert farm.regen_step(_boxes(("TAP", 360, 1200))) is None and not farm._regen["pending"]


def test_free_regen_whitelist():
    from hiro.brain import farm

    assert farm._free_regen(["メモリー再生成チケットを1枚使用します"])
    assert farm._free_regen(["本日1回無料で使用できます"])
    assert not farm._free_regen(["本日1回無料"])  # CodeRabbit #198：单独的「無料」不算，要和「使用 / 消費」同一行
    assert not farm._free_regen(["無料", "メモリーを再生成しますか"])
    assert not farm._free_regen(["メモリーを再生成しますか", "所持チケット 0"])
    assert not farm._free_regen(["チケットが不足しています"])
    assert not farm._free_regen(["メモリーを再生成しますか", "50"])


def test_free_regen_ticket_count_ten_is_not_zero():
    """审查 #198 第 6 条：券数 10 / 20 / 30 张不能判成 0 张（原来 .*? 吃掉前面的 1，整数倍券时白名单无声失效）。"""
    from hiro.brain import farm

    for n in ("10", "20", "30"):
        assert farm._free_regen(["メモリー再生成チケットを1枚使用します", f"所持チケット{n}"]), n
        assert farm._free_regen(["メモリー再生成チケットを1枚使用します", f"所持チケット{n}枚"]), n
    assert not farm._free_regen(["メモリー再生成チケットを1枚使用します", "所持チケット0"])
    assert not farm._free_regen(["メモリー再生成チケットを1枚使用します", "所持チケット：0枚"])


def _late_dialog(*extra):
    return _boxes(*RESULT, *extra)


def test_regen_dialog_after_grace_never_goes_to_generic_branch(monkeypatch):
    """审查 #198 第 1 条：点了再生成 5.5 秒才弹出带ジュエル的框（pending 已经结束）：原来返回 None，交给通用分支点「決定」花钻石。
    现在 farm 开着时照样由这里接管：有钻石取消。"""
    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    now[0] = 4.5
    assert farm.regen_step(_boxes(*RESULT)) is None and not farm._regen["pending"]  # 4 秒没框：交回收尾
    now[0] = 5.5
    dlg = _late_dialog(
        ("ジュエルを50個使用して再生成しますか", 360, 640), ("キャンセル", 218, 1000), ("決定", 500, 1000)
    )
    assert farm.regen_step(dlg) == (218, 1000)
    assert farm.regen_step(dlg) == (218, 1000)  # 还开着：再点キャンセル，绝不点決定


def test_regen_late_free_dialog_is_confirmed_once(monkeypatch):
    """审查 #198 第 1 条：晚弹的框是用券的（白名单）：照样确认；确认后淡出那几帧等着，不交回通用分支。"""
    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    now[0] = 4.5
    assert farm.regen_step(_boxes(*RESULT)) is None
    now[0] = 5.5
    dlg = _boxes(
        ("メモリー再生成チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000)
    )
    assert farm.regen_step(dlg) == (500, 1000)
    assert farm.regen_step(dlg) == farm.WAIT  # 淡出中：不再点
    assert farm.regen_step(_boxes(("TAP", 360, 1200))) is None and not farm._regen["pending"]
    assert farm.regen_step(dlg) == (218, 1000)  # 已经确认过一次：再弹一次的框取消（REGEN_MAX）


def test_regen_dialog_after_restart_is_handled(monkeypatch):
    """审查 #198 第 1 条：进程在框开着时重启（_regen 清空、pending=False），框后面结果页读不全：照样按白名单处理，
    有钻石取消；之后不会再点第二次再生成。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    monkeypatch.setattr(farm, "_clock", lambda: 0.0)
    dlg = _boxes(
        ("メモリーを再生成しますか", 360, 600),
        ("ジユエル 50", 400, 650),
        ("キャンセル", 218, 1000),
        ("決定", 500, 1000),
    )
    assert farm.regen_step(dlg) == (218, 1000)
    monkeypatch.setattr(farm, "_clock", lambda: 10.0)
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT  # 取消过的框：要连着两帧没框才算关了
    assert farm.regen_step(_boxes(*RESULT)) is None  # 框关了：回到结果页，不再生成第二次
    assert farm._regen["done"] == 1


def test_unrelated_cancel_dialog_without_pending_is_left_alone(monkeypatch):
    """没在等再生成、画面上的框和再生成 / 钻石无关（照片保存确认）：不归这里管，交回收尾（结果页的「再生成」按钮不算）。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})  # 结果页的「リズミカル」不是目标
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    other = _boxes(*RESULT, ("フォトを保存しますか", 360, 640), ("キャンセル", 218, 1000), ("決定", 500, 1000))
    assert farm.regen_step(other) is None  # CodeRabbit #198：别的框开着时不读框后面的结果页、不点被遮住的「再生成」
    assert farm._regen["done"] == 0 and not farm._regen["pending"] and not farm._regen["reported"]
    # 结果页底下「あと1回再生成可能」这类说明（不在框里）也不算再生成框
    other = _boxes(*RESULT, ("あと1回再生成可能", 360, 1120), ("フォトを保存しますか", 360, 640),
                   ("キャンセル", 218, 1000), ("決定", 500, 1000))  # fmt: skip
    assert farm.regen_step(other) is None
    monkeypatch.setenv("HIRO_FARM", "")
    jewel = _boxes(("ジュエルを50個使用して再生成しますか", 360, 640), ("キャンセル", 218, 1000), ("決定", 500, 1000))
    assert farm.regen_step(jewel) is None  # 刷回忆没开：完全不管


def test_regen_whitelist_only_reads_dialog_area(monkeypatch):
    """审查 #198 第 7 条：白名单只看确认框区域（キャンセル那一行往上）。框外（屏幕顶上）的「無料」不能把付费框当成免费。"""
    farm = _regen_started(monkeypatch, [0.0])
    dlg = _boxes(("本日1回無料で使用", 360, 120), ("メモリーを再生成しますか", 360, 600), ("50", 400, 650),
                 ("キャンセル", 218, 1000), ("決定", 500, 1000))  # fmt: skip
    assert farm.regen_step(dlg) == (218, 1000)


def test_regen_jewel_variants_anywhere_cancel(monkeypatch):
    """审查 #198 第 7 条：ジュエル看整屏、认 OCR 变体；框里写「50個」（钻石只显示成图标）也不算免费。"""
    from hiro.brain import farm as f

    for jewel in ("シュエル 50", "ジュェル×50"):
        farm = _regen_started(monkeypatch, [0.0])
        dlg = _boxes((jewel, 360, 60), ("メモリー再生成チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000),
                     ("再生成する", 500, 1000))  # fmt: skip
        assert farm.regen_step(dlg) == (218, 1000), jewel
    assert not f._free_regen(["チケットを使用して再生成", "50個消費します"])
    assert f._free_regen(["メモリー再生成チケットを1枚使用します", "所持数 3個"])  # 持有数不算付费


def test_target_match_is_conservative():
    """审查 #198 第 5 条：卡名比较有容错，读得不确定时不再生成（宁可留下非目标）。"""
    from hiro.brain import farm

    t = {"輝くキミへ"}
    assert farm.target_match("輝くキミへ", t) == "hit"
    assert farm.target_match("輝くキミヘ", t) == "hit"  # 平假名「へ」读成片假名「ヘ」
    assert farm.target_match("輝くキミへ+", t) == "hit"
    assert farm.target_match("輝くキミ", t) == "hit"  # 只读出前几个字
    assert farm.target_match("耀くキミへ", t) == "unsure"  # 错一个字：像目标，不再生成
    assert farm.target_match("輝", t) == "unsure"  # 太短：读不准
    assert farm.target_match("リズミカル", t) == "miss"
    assert farm.target_match("リズミカル", set()) == "unsure"


def test_regen_skipped_when_name_looks_like_target(monkeypatch):
    """审查 #198 第 5 条：结果页卡名 OCR 错了一个字（其实是目标）：不点再生成，照常点次へ。"""
    from hiro.brain import farm

    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_target_names", lambda: {"輝くキミへ"})
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    page = [b if b[0] != "リズミカル" else ("耀くキミへ", 270, 730) for b in RESULT]
    assert farm.regen_step(_boxes(*page)) is None
    page = [b if b[0] != "リズミカル" else ("輝くキミヘ", 270, 730) for b in RESULT]
    assert farm.regen_step(_boxes(*page)) is None


def _fake_catalog(monkeypatch):
    from hiro.brain import farm
    from hiro.cli import catalog

    calls = []

    def search(q, kind=None, lang="ja"):
        calls.append(q)
        return [{"id": "p_card-02-act-3_050", "name": "輝くキミへ"}] if q == "輝くキミへ" and lang == "ja" else []

    monkeypatch.setattr(catalog, "search", search)
    known = {"p_card-02-act-3_050": {}, "p_card-02-act-3_186": {}}
    monkeypatch.setattr(catalog, "load", lambda lang="ja": {"cards": known})
    monkeypatch.setattr(farm, "_TARGET_CACHE", {})
    return farm, calls


def test_check_targets_rejects_blank_and_unknown_ids(monkeypatch):
    """CodeRabbit #198：只填了「，,」（显式但全空）不开始；填的编号要在资料库里精确查到，写错的编号同样停下。"""
    from hiro.loop.errors import ResumeBlocked

    farm, _ = _fake_catalog(monkeypatch)
    for raw in ("，,", " , ，"):
        with pytest.raises(ResumeBlocked, match="一张卡都没有"):
            farm.check_targets({"HIRO_FARM_TARGET": raw})
    with pytest.raises(ResumeBlocked, match=r"p_card-02-act-3_999.*资料库"):
        farm.check_targets({"HIRO_FARM_TARGET": "p_card-02-act-3_999"})
    with pytest.raises(ResumeBlocked, match="p_card-02-act-3_999"):
        farm.check_targets({"HIRO_FARM_TARGET": "輝くキミへ,p_card-02-act-3_999"})
    assert farm.check_targets({"HIRO_FARM_TARGET": "p_card-02-act-3_186"}) == {"p_card-02-act-3_186"}


def test_check_targets_id_when_catalog_missing(monkeypatch):
    """资料库读不到时编号也查不到：开局前停下（提示检查资料库），不当成有效目标。"""
    from hiro.cli import catalog
    from hiro.loop.errors import ResumeBlocked

    farm, _ = _fake_catalog(monkeypatch)

    def missing(lang="ja"):
        raise RuntimeError("catalog_missing")

    monkeypatch.setattr(catalog, "load", missing)
    with pytest.raises(ResumeBlocked, match="资料库"):
        farm.check_targets({"HIRO_FARM_TARGET": "p_card-02-act-3_186"})


def test_check_targets_blocks_on_unresolved(monkeypatch):
    """审查 #198 第 2 条：填了的转写目标有一项解析不出（片假名写错 / 资料库没下载）：开局前 ResumeBlocked，不静默丢掉。"""
    from hiro.loop.errors import ResumeBlocked

    farm, _ = _fake_catalog(monkeypatch)
    with pytest.raises(ResumeBlocked, match=r"輝くキミヘ.*卡名"):
        farm.check_targets({"HIRO_FARM_TARGET": "輝くキミヘ"})
    with pytest.raises(ResumeBlocked, match="没有这张"):
        farm.check_targets({"HIRO_FARM_TARGET": "輝くキミへ,没有这张"})  # 多个目标只有一个解析不出也停
    assert farm.check_targets({"HIRO_FARM_TARGET": "輝くキミへ，p_card-02-act-3_186"}) == {
        "p_card-02-act-3_050",
        "p_card-02-act-3_186",
    }


def test_check_targets_blocks_when_no_target(monkeypatch):
    from hiro import formation_env
    from hiro.loop.errors import ResumeBlocked

    farm, _ = _fake_catalog(monkeypatch)
    monkeypatch.setattr(formation_env, "MEMORY_CARDS", [])
    with pytest.raises(ResumeBlocked, match="没有转写目标"):
        farm.check_targets({})


def test_target_resolution_is_cached(monkeypatch):
    """审查 #198 第 2 条：每次决策都调 target_cards，解析结果按原文缓存，不再每次查资料库。"""
    farm, calls = _fake_catalog(monkeypatch)
    env = {"HIRO_FARM_TARGET": "輝くキミへ"}
    assert farm.target_cards(env) == farm.target_cards(env) == {"p_card-02-act-3_050"}
    assert calls == ["輝くキミへ"]


def test_hajime_run_stops_before_device_on_bad_target(monkeypatch, capsys):
    """审查 #198 第 2 条：初开局（new / resume 都一样）在连设备之前核对转写目标，解析不出退出码 3。"""
    from types import SimpleNamespace as NS

    from hiro import formation_env
    from hiro.brain import guides
    from hiro.runtime import hajime

    _fake_catalog(monkeypatch)
    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setenv("HIRO_FARM_TARGET", "輝くキミヘ")
    monkeypatch.setattr(guides, "refresh_if_stale", lambda: None)
    monkeypatch.setattr(formation_env, "apply_formation_plan", lambda idol: None)
    opened = []
    monkeypatch.setattr("hiro.devices.open_device", lambda: opened.append(1))
    assert hajime.run(NS(idol="i_card-hski-3-000", command="resume")) == 3
    assert not opened and "輝くキミヘ" in capsys.readouterr().out


def _consult(monkeypatch, farm_on: bool, deck: list):
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    monkeypatch.setenv("HIRO_FARM", "bad" if farm_on else "")
    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-02-act-3_186")
    prod = {"idol_card_id": "i_card-hski-3-000", "mode": "produce-001", "step": 7, "stamina": 20, "max_stamina": 30,
            "produce_point": 300, "params": {"Vo": 1, "Da": 1, "Vi": 1}, "growth_permil": {}, "rerolls_left": 0,
            "excludes_left": 0, "deck": deck}  # fmt: skip
    pf = {"shelf": [], "services": {"delete": {"price": 100, "available": True}}}
    dec = ProduceDeciders(profile_for("i_card-hski-3-000"), None)
    return dec, dec.decide("CONSULT", GameState(produce=prod, page_fields=pf))


def test_consult_deck_unread_still_opens_delete_dialog(monkeypatch):
    """审查 #198 第 3 条 / CodeRabbit deciders.py:2403：牌组没读到时，选行动按这局拿过的卡判断去了相談，
    进去却直接结束不删卡（白花一周、垃圾卡进抽选池）。刷回忆时改为打开删卡对话框，在对话框里由 remove_card 选。"""
    from hiro.contracts import ActionKind

    dec, d = _consult(monkeypatch, True, [])
    assert d.action.kind is ActionKind.SHOP_BUY and d.action.args == {"service": "delete"}, d
    assert "delete" in dec.consult_used  # 一次相談只删一张：对话框里取消了也不再开


def test_consult_deck_unread_normal_mode_unchanged(monkeypatch):
    from hiro.contracts import ActionKind

    _, d = _consult(monkeypatch, False, [])
    assert d.action.kind is ActionKind.SHOP_END and "牌组没识别" in d.reason, d


def test_bad_lesson_advisor_reads_left_at_decision_time():
    """审查 #198 第 4 条 / CodeRabbit hajime.py:276：live 是循环开头那张截图读的（出牌后还在分数动画里、偏大），
    决策时现读一次；现读读不到 / 出错再退回 live 的值。"""
    from hiro.contracts import Action, ActionKind

    inner = NS(decide=lambda *a: "inner")
    legal = [Action.play_card(0), Action.skip()]
    live = {"left": 60}  # 动画帧：还差 60，按它会继续出牌
    fresh = [20]
    adv = farm.BadLessonAdvisor(inner, live, lambda: 100, 0.3, read_left=lambda: fresh[0])
    d = adv.decide(None, None, None, legal)
    assert d.action.kind is ActionKind.SKIP and "20" in d.reason and live["left"] == 20
    fresh[0] = None  # 现读读不到：退回 live（新一节课的实例：停手状态不带过来）
    live["left"] = 45
    adv = farm.BadLessonAdvisor(inner, live, lambda: 100, 0.3, read_left=lambda: fresh[0])
    assert adv.decide(None, None, None, legal) == "inner"
    live["left"] = 25
    assert adv.decide(None, None, None, legal).action.kind is ActionKind.SKIP

    def boom():
        raise RuntimeError("ocr")

    adv = farm.BadLessonAdvisor(inner, {"left": 25}, lambda: 100, 0.3, read_left=boom)
    assert adv.decide(None, None, None, legal).action.kind is ActionKind.SKIP
    calls = []
    adv = farm.BadLessonAdvisor(inner, {}, lambda: 100, 0.3, read_left=lambda: calls.append(1))
    assert adv.decide(None, None, None, [Action.play_card(0)]) == "inner" and not calls  # 不能跳过时不多截图


def test_lesson_clear_left_only_on_clear_goal():
    from hiro.runtime.hajime import lesson_clear_left

    hv = NS(lesson_goal=lambda shot: shot["goal"], read_lesson_hud=lambda shot: {"left": shot.get("left")})
    assert lesson_clear_left({"goal": "clear", "left": 12}, hv) == 12
    assert lesson_clear_left({"goal": "perfect", "left": 12}, hv) is None
    assert lesson_clear_left({"goal": "clear"}, hv) is None
    assert lesson_clear_left({"goal": None, "left": 3}, hv) is None


def test_late_dialog_without_cancel_waits_then_stops(monkeypatch):
    """CodeRabbit #198：pending 清掉以后才弹的框、キャンセル没读到：不交回通用分支（会点「決定」），WAIT；20 秒还读不出就停下。"""
    from hiro.loop.errors import ResumeBlocked

    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    now[0] = 4.5
    assert farm.regen_step(_boxes(*RESULT)) is None and not farm._regen["pending"]
    now[0] = 6.0
    dlg = _boxes(*RESULT, ("メモリーを再生成しますか", 360, 600), ("50", 400, 650), ("決定", 500, 1000))
    assert farm.regen_step(dlg) == farm.WAIT and farm._regen["pending"]
    now[0] = 10.0
    assert farm.regen_step(dlg) == farm.WAIT
    # キャンセル读出来了：按白名单取消
    assert farm.regen_step(_boxes(*RESULT, ("メモリーを再生成しますか", 360, 600), ("50", 400, 650), ("キャンセル", 218, 1000),
                                  ("決定", 500, 1000))) == (218, 1000)  # fmt: skip
    now[0] = 30.0
    with pytest.raises(ResumeBlocked):
        farm.regen_step(dlg)


def test_late_jewel_dialog_without_cancel_waits(monkeypatch):
    farm = _regen_started(monkeypatch, [0.0])
    farm._regen["pending"] = False
    dlg = _boxes(("ジュエル", 300, 640), ("50個使用します", 420, 640), ("OK", 500, 1000))
    assert farm.regen_step(dlg) == farm.WAIT


def test_no_cancel_reward_screens_are_not_regen_dialogs():
    """读不到キャンセル时的口径不能拦下正常收尾：只有ジュエル的领奖画面、干净的结果页（「再生成可能」说明）都不算。"""
    from hiro.brain import farm

    assert not farm._is_regen_dialog_no_cancel(["ジュエル×30", "獲得しました", "OK"])
    assert not farm._is_regen_dialog_no_cancel(["獲得可能スキルカード", "あと1回再生成可能", "再生成", "次へ"])
    assert not farm._is_regen_dialog_no_cancel(["メモリーを再生成しますか"])  # 没有确认按钮：不是框
    assert farm._is_regen_dialog_no_cancel(["メモリーを再生成しますか", "決定"])


def test_cancel_not_responding_stops_after_limit(monkeypatch):
    """CodeRabbit #198：点了キャンセル框还一直在（没反应）：20 秒上限，超时停下交给人，不无限点。"""
    from hiro.loop.errors import ResumeBlocked

    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    dlg = _boxes(
        ("メモリーを再生成しますか", 360, 600), ("50", 400, 650), ("キャンセル", 218, 1000), ("決定", 500, 1000)
    )
    for t in (1.0, 5.0, 15.0):
        now[0] = t
        assert farm.regen_step(dlg) == (218, 1000)
    now[0] = 25.0
    with pytest.raises(ResumeBlocked, match="关不掉"):
        farm.regen_step(dlg)


def test_stray_dialog_after_confirm_stops_after_limit(monkeypatch):
    """CodeRabbit #198：已经确认过一次，又弹出的框点キャンセル一直关不掉：同样 20 秒停下。"""
    from hiro.loop.errors import ResumeBlocked

    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    farm._regen.update(pending=False, confirmed=True)
    dlg = _boxes(("メモリーを再生成しますか", 360, 600), ("キャンセル", 218, 1000), ("決定", 500, 1000))
    now[0] = 100.0
    assert farm.regen_step(dlg) == (218, 1000)
    now[0] = 110.0
    assert farm.regen_step(dlg) == (218, 1000)
    now[0] = 125.0
    with pytest.raises(ResumeBlocked, match="关不掉"):
        farm.regen_step(dlg)


def test_bad_lesson_advisor_stays_stopped():
    """CodeRabbit #198：一节课停过手以后，只要能跳过就一直跳过（好印象层数下降让余量变小时，原来会恢复出牌、过线掉卡）；
    不能跳过时照常交给原顾问；新一节课（新实例）重置。"""
    from hiro.contracts import Action, ActionKind

    inner = NS(decide=lambda *a: "inner")
    legal = [Action.play_card(0), Action.skip()]
    live = {"left": 30}
    adv = farm.BadLessonAdvisor(inner, live, 50, 0.4)  # 余量 20
    assert (
        adv.decide(NS(impression=5, remaining_turns=3), None, None, legal).action.kind is ActionKind.SKIP
    )  # 20+14.4 ≥ 30
    # 下一回合好印象掉了，余量只剩 20 < 30：仍然跳过，不恢复出牌
    d = adv.decide(NS(impression=None, remaining_turns=2), None, None, legal)
    assert d.action.kind is ActionKind.SKIP and "已经停手" in d.reason
    assert adv.decide(None, None, None, [Action.play_card(0)]) == "inner"  # 不能跳过时照常
    fresh = farm.BadLessonAdvisor(inner, {"left": 30}, 50, 0.4)  # 新一节课
    assert fresh.decide(NS(impression=None, remaining_turns=2), None, None, legal) == "inner"


@pytest.mark.parametrize(
    ("ok", "fast", "slow", "want"),
    [
        (True, True, None, True),  # 快速提交成功
        (False, True, None, False),  # 页面校验没过
        (True, False, True, True),  # 慢速路径「受け取る」按下了
        (True, False, False, False),  # 慢速路径一直是灰的：没领成（_act 照样按已生效返回 ok）
    ],
)
def test_picked_card_recorded_only_when_acquired(monkeypatch, ok, fast, slow, want):
    """CodeRabbit #198：选卡记账（牌组读不到时刷回忆第 7 周靠它判断去不去相談删卡）只在领取成功时记，没领成不记。"""
    import hiro.produce.hif as hif
    import hiro.vision.card_select as cs
    import hiro.vision.extract as ex
    from hiro.contracts import Action, Decision

    import numpy as np

    blank = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: blank, click=lambda *a: None))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    card = NS(_id="p_card-02-act-1_010", name="x", upgrade_count=0, effect_display_text="")
    monkeypatch.setattr(cs, "select_card_options", lambda img: [NS(card=card, rect=NS(center=NS(x=222, y=937)))] * 3)
    monkeypatch.setattr(cs, "recommended_index", lambda img, options: None)
    monkeypatch.setattr(ex, "extract", lambda page, img: ({"options": [{"card_id": c} for c in "abc"]}, None))
    from collections import Counter

    deciders = NS(
        decide=lambda page, state, **kw: Decision(Action.activate_effect(0), "拿", "rule", 0.9),
        picked_musts=set(),
        farm_picked=Counter(),
    )
    core = hif.HifCore.__new__(hif.HifCore)
    core.deciders, core.rec = deciders, NS(log=lambda *a, **k: None)
    recorded = []
    core.s = NS(observe=lambda **k: None, record=lambda *a, **k: recorded.append(k["execution"]),
                state=NS(page_fields={"candidates": [1]}))  # fmt: skip
    core._rerolls_this_page = 0
    monkeypatch.setattr(core, "_note_fallback", lambda *a: None, raising=False)
    monkeypatch.setattr(core, "_pick_index", lambda *a: 0, raising=False)
    import hiro.produce.card_ops as card_ops

    monkeypatch.setattr(hif.ops, "fast_acquire", lambda center: fast)
    monkeypatch.setattr(card_ops, "card_select_commit", lambda rect: slow)
    monkeypatch.setattr(core, "_settings_dialog", lambda where: False, raising=False)
    monkeypatch.setattr(core, "_confirm_acquire", lambda: 0, raising=False)

    from hiro.contracts import ExecutionResult

    def act(fn, action, **k):
        fn()
        return ExecutionResult(action=action, ok=ok, error=None if ok else "没领成")

    monkeypatch.setattr(core, "_act", act, raising=False)
    core.on_select_card()
    assert deciders.picked_musts == ({"p_card-02-act-1_010"} if want else set())
    assert dict(deciders.farm_picked) == ({"p_card-02-act-1_010": 1} if want else {})  # 删卡正向名单同样只记领到的
    # CodeRabbit #198：没领到时 trace 里这一步也记成没生效（续跑记账不把它当已有）
    assert recorded[-1].ok is want


def test_blank_target_falls_back_to_formation_in_both_entries(monkeypatch):
    """CodeRabbit #198：目标栏只有空格时，check_targets 和 target_cards 口径一致：都回退到回忆编成；非空才覆盖编成。"""
    from hiro import formation_env

    farm, _ = _fake_catalog(monkeypatch)
    monkeypatch.setattr(formation_env, "MEMORY_CARDS", ("p_card-02-act-3_186",))
    for raw in ("", "   ", "\t "):
        env = {"HIRO_FARM_TARGET": raw}
        assert farm.check_targets(env) == farm.target_cards(env) == {"p_card-02-act-3_186"}, repr(raw)
    env = {"HIRO_FARM_TARGET": " 輝くキミへ "}
    assert farm.check_targets(env) == farm.target_cards(env) == {"p_card-02-act-3_050"}


def test_target_names_isolate_language_failures(monkeypatch):
    """CodeRabbit #198：中文资料库报错不影响日文名；有哪张目标卡一个名字都没取到就返回空（regen_step 不再生成）。"""
    from hiro.brain import farm
    from hiro.cli import catalog

    monkeypatch.setenv("HIRO_FARM_TARGET", "p_card-a,p_card-b")
    monkeypatch.setattr(farm, "_TARGET_CACHE", {})
    ja = {"p_card-a": {"name": "輝くキミへ"}, "p_card-b": {"name": "リズミカル+"}}

    def load(lang="ja"):
        if lang == "zh":
            raise RuntimeError("zh broken")
        return {"cards": ja}

    monkeypatch.setattr(catalog, "load", load)
    assert farm._target_names() == {"輝くキミへ", "リズミカル"}
    del ja["p_card-b"]  # 第二张目标卡一个名字都没有：不判 miss
    assert farm._target_names() == set()
    monkeypatch.setenv("HIRO_FARM", "bad")
    monkeypatch.setattr(farm, "_regen", {"done": 0, "pending": False, "reported": set()})
    page = [b if b[0] != "リズミカル" else ("全然違う名前", 270, 730) for b in RESULT]
    assert farm.regen_step(_boxes(*page)) is None  # 名字不全：不再生成


def test_card_id_prefers_exact_in_any_language(monkeypatch):
    """CodeRabbit #198：先看两种语言的完全一致，都没有才接受唯一的模糊结果。
    原来日文里模糊唯一（「闪耀」只是名字的一部分）就返回那张，中文里完全一致的另一张轮不到。"""
    from hiro.brain import farm
    from hiro.cli import catalog

    def search(q, kind=None, lang="ja"):
        if lang == "ja":
            return [{"id": "p_card-ja-fuzzy", "name": "閃耀する日々"}]
        return [{"id": "p_card-zh-exact", "name": "闪耀"}, {"id": "p_card-zh-other", "name": "闪耀之后"}]

    monkeypatch.setattr(catalog, "search", search)
    assert farm.card_id("闪耀") == "p_card-zh-exact"

    def search2(q, kind=None, lang="ja"):
        return (
            [{"id": "p_card-one", "name": "輝くキミへ"}] if lang == "ja" else [{"id": "p_card-one", "name": "闪耀的你"}]
        )

    monkeypatch.setattr(catalog, "search", search2)
    assert farm.card_id("輝く") == "p_card-one"  # 两种语言模糊都只对上同一张：接受

    def search3(q, kind=None, lang="ja"):
        if lang == "ja":
            raise RuntimeError("ja broken")
        return [{"id": "p_card-a", "name": "闪耀A"}, {"id": "p_card-b", "name": "闪耀B"}]

    monkeypatch.setattr(catalog, "search", search3)
    assert farm.card_id("闪耀") is None  # 模糊对上不止一张：不猜


def test_junk_cards_keeps_memory_cards(monkeypatch):
    """CodeRabbit #198：回忆编成带进来的卡（MEMORY_CARDS）也不删，和目标卡一样留着。"""
    import hiro.brain.deciders as d
    from hiro import formation_env

    monkeypatch.setattr(d, "_is_basic", lambda cid: False)
    monkeypatch.setattr(d, "_is_trouble_card", lambda cid: False)
    monkeypatch.setattr(formation_env, "MEMORY_CARDS", ("p_card-mem",))
    cards = [
        {"index": 0, "card_id": "p_card-mem"},
        {"index": 1, "card_id": "p_card-picked"},
        {"index": 2, "card_id": "p_card-target"},
    ]
    picked = {"p_card-mem": 1, "p_card-picked": 1, "p_card-target": 1}
    assert [c["card_id"] for c in farm.junk_cards(cards, {"p_card-target"}, picked)] == ["p_card-picked"]


def test_cancel_decision_sticks_until_dialog_closes(monkeypatch):
    """CodeRabbit #198：同一个没关掉的框已经因为有ジュエル点过キャンセル，下一帧漏读了ジュエル（看着像用券的白名单框）：
    照样点キャンセル，不重判；框关了以后这个记录清掉。"""
    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    paid = _boxes(("ジュエルを50個使用します", 360, 600), ("チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000),
                  ("再生成する", 500, 1000))  # fmt: skip
    looks_free = _boxes(("チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000))
    assert farm.regen_step(paid) == (218, 1000)
    now[0] = 1.0
    assert farm.regen_step(looks_free) == (218, 1000)  # 漏读ジュエル的一帧：不点确认
    now[0] = 2.0
    farm.regen_step(_boxes(*RESULT))
    farm.regen_step(_boxes(*RESULT))  # 框关了（连着两帧有字、没框）
    assert not farm._regen["cancelled"]


def test_cancel_decision_survives_blank_frames(monkeypatch):
    """CodeRabbit #198：已决定取消的框，空帧 / 一帧没框不能清掉这个决定（框再露出来、漏读ジュエル时会按白名单点确认）；
    空帧继续 WAIT、计入 20 秒超时；连着两帧有字、没有框才清。"""
    from hiro.loop.errors import ResumeBlocked

    now = [0.0]
    farm = _regen_started(monkeypatch, now)
    paid = _boxes(("ジュエルを50個使用します", 360, 600), ("キャンセル", 218, 1000), ("再生成する", 500, 1000))
    looks_free = _boxes(
        ("メモリー再生成チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000)
    )
    assert farm.regen_step(paid) == (218, 1000)
    now[0] = 1.0
    assert farm.regen_step([]) == farm.WAIT and farm._regen["cancelled"]  # 空帧：不清
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT and farm._regen["cancelled"]  # 只一帧有字没框：还不清
    assert farm.regen_step(looks_free) == (218, 1000)  # 框又露出来、漏读ジュエル：照样取消
    assert farm.regen_step(_boxes(*RESULT)) == farm.WAIT
    farm.regen_step(_boxes(*RESULT))  # 连着两帧有字、没框：清
    assert not farm._regen["cancelled"]

    farm = _regen_started(monkeypatch, now)
    now[0] = 0.0
    farm._regen["since"] = 0.0
    assert farm.regen_step(paid) == (218, 1000)
    now[0] = 25.0
    with pytest.raises(ResumeBlocked):
        farm.regen_step([])  # 一直空帧：20 秒停下


def test_whitelist_text_must_be_inside_dialog_box(monkeypatch):
    """CodeRabbit #198：白名单文字要在确认框边界内：框标题（含再生成的那行）和キャンセル那一行之间，x 在按钮范围外扩以内。
    框标题上方 / 框外左右的「チケットを使用」不算；找不到框标题就不确认。"""
    farm = _regen_started(monkeypatch, [0.0])
    above = _boxes(("チケットを1枚使用します", 360, 450), ("メモリーを再生成しますか", 360, 600), ("キャンセル", 218, 1000),
                   ("再生成する", 500, 1000))  # fmt: skip
    assert farm.regen_step(above) == (218, 1000)  # 券的字在框标题上面：框外
    farm = _regen_started(monkeypatch, [0.0])
    side = _boxes(("メモリーを再生成しますか", 360, 600), ("チケットを1枚使用", 20, 700), ("キャンセル", 218, 1000),
                  ("再生成する", 500, 1000))  # fmt: skip
    assert farm.regen_step(side) == (218, 1000)  # 券的字在框左边外面
    farm = _regen_started(monkeypatch, [0.0])
    no_title = _boxes(("チケットを1枚使用します", 360, 640), ("キャンセル", 218, 1000), ("再生成する", 500, 1000))
    assert farm.regen_step(no_title) == (218, 1000)  # 认不出框标题：不确认
    farm = _regen_started(monkeypatch, [0.0])
    good = _boxes(("メモリーを再生成しますか", 360, 600), ("チケットを1枚使用します", 360, 700), ("キャンセル", 218, 1000),
                  ("再生成する", 500, 1000))  # fmt: skip
    assert farm.regen_step(good) == (500, 1000)


def test_failed_pick_not_counted_on_resume():
    """CodeRabbit #198：没领到的选卡步骤记成 execution.ok=False，续跑记账（_picked_cards_from_trace → owned_cards）不把它当已有。"""
    from hiro.loop.ledger import owned_cards

    def step(ok):
        return {"state": {"page": {"name": "SELECT_CARD"}, "page_fields": {"candidates": [{"card_id": "p_card-x"}]}},
                "decision": {"action": {"kind": "ActivateEffect", "args": {"pick_indexes": [0]}}},
                "execution": {"ok": ok}}  # fmt: skip

    assert owned_cards([step(False)]) == {}
    assert owned_cards([step(True)])["p_card-x"] == 1


def test_junk_cards_skips_ambiguous_copies():
    """牌组里同一张卡两份、本局只领到一张：分不清哪份是借用回忆带进来的（可能是强化过的），一份都不删（CodeRabbit #198）。"""
    from hiro.brain import farm

    cards = [{"index": 0, "card_id": "p_card-02-act-1_010", "upgrade": 1}, {"index": 1, "card_id": "p_card-02-act-1_010"},
             {"index": 2, "card_id": "p_card-02-act-1_020"}]  # fmt: skip
    got = farm.junk_cards(cards, set(), {"p_card-02-act-1_010": 1, "p_card-02-act-1_020": 1})
    assert [c["index"] for c in got] == [2]
    got = farm.junk_cards(cards, set(), {"p_card-02-act-1_010": 2})  # 两份都是本局领的：照删
    assert [c["index"] for c in got] == [0, 1]
