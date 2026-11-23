"""初 / N.I.A 选行动（hiro.brain.hajime_nia.choose_action）：通关档的社区规则，每条规则至少一个用例。
设计稿 （作者本机，未发布） 第四节；规则文件 src/hiro/defaults/hajime_nia_rules.json。"""

from hiro.brain.hajime_nia import Status, choose_action

import pytest


def _opt(option, attr=None, sp=None, hp_cost=None):
    return {"option": option, "attr": attr, "sp": sp, "hp_cost": hp_cost}


def _st(**kw):
    base = {
        "mode": "produce-001",
        "step": 3,
        "stamina": 30,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 300, "Da": 200, "Vi": 100},
    }
    base.update(kw)
    return Status(**base)


def _pick(st, opts):
    i, reason, rules = choose_action(st, opts)
    return opts[i], reason, rules


# ---------------------------------------------------------------- 初


def test_hajime_lesson_week_takes_sp_lesson():
    opts = [_opt("training", "Vo", False), _opt("training", "Da", True), _opt("training", "Vi", False), _opt("rest")]
    o, reason, rules = _pick(_st(), opts)
    assert (o["option"], o["attr"], o["sp"]) == ("training", "Da", True)
    assert "hajime.lesson_first" in rules and "[g8-PRO]" in reason


def test_hajime_lesson_without_sp_takes_main_attr():
    opts = [_opt("training", "Vo", False), _opt("training", "Da", False), _opt("training", "Vi", False)]
    o, _, _ = _pick(_st(main_attr="Vi"), opts)
    assert o["attr"] == "Vi"


def test_hajime_class_before_other_actions():
    opts = [_opt("class", "Vo"), _opt("allowance"), _opt("outing"), _opt("rest")]
    o, _, rules = _pick(_st(stamina=25), opts)
    assert o["option"] == "class" and "hajime.lesson_first" in rules


def test_hajime_tired_outing_with_points():
    """体力少了 15 以上、没有课程 / 授業：P 点 ≥ 100 去外出。"""
    opts = [_opt("allowance"), _opt("outing"), _opt("rest")]
    o, _, rules = _pick(_st(stamina=12, produce_point=120), opts)
    assert o["option"] == "outing" and {"hajime.outing_when", "hajime.outing_vs_rest"} <= set(rules)


def test_hajime_tired_rest_without_points():
    opts = [_opt("allowance"), _opt("outing"), _opt("rest")]
    o, _, rules = _pick(_st(stamina=12, produce_point=40), opts)
    assert o["option"] == "rest" and "hajime.outing_vs_rest" in rules


def test_hajime_not_tired_takes_allowance():
    opts = [_opt("allowance"), _opt("outing"), _opt("rest")]
    o, _, _ = _pick(_st(stamina=28), opts)
    assert o["option"] == "allowance"


def test_hajime_rest_blocked_when_full():
    """体力满了不能休息：MASTER 只剩休息和外出（外出排最后）时，休息被拦下，选外出。"""
    opts = [_opt("rest"), _opt("outing")]
    o, _, rules = _pick(_st(mode="produce-003", step=9, stamina=30), opts)
    assert o["option"] == "outing" and "hajime.rest_full" in rules


def test_hajime_master_no_rest_before_week6():
    opts = [_opt("rest"), _opt("shop")]
    o, _, rules = _pick(_st(mode="produce-003", step=4, stamina=10, produce_point=0), opts)
    assert o["option"] == "shop" and "hajime.master_rest_week6" in rules


def test_hajime_master_avoids_outing():
    opts = [_opt("outing"), _opt("allowance")]
    o, _, rules = _pick(_st(mode="produce-003", step=9, stamina=10, produce_point=150), opts)
    assert o["option"] == "allowance" and "hajime.master_outing_avoid" in rules


def test_hajime_lesson_week_very_low_stamina_rests():
    """课程周体力太低（< 8）时休息，不硬上课程。"""
    opts = [_opt("training", "Vo", True), _opt("rest")]
    o, _, _ = _pick(_st(stamina=5), opts)
    assert o["option"] == "rest"


# ---------------------------------------------------------------- N.I.A


def _nia(**kw):
    base = {
        "mode": "produce-004",
        "step": 3,
        "stamina": 20,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 300, "Da": 200, "Vi": 150},
        "rest_left": 4,
    }
    base.update(kw)
    return _st(**base)


def test_nia_always_sp_lesson_lowest_attr():
    opts = [
        _opt("training", "Vo", True, 8),
        _opt("training", "Vi", True, 8),
        _opt("training", "Da", False, 6),
        _opt("sales"),
    ]
    o, _, rules = _pick(_nia(), opts)
    assert (o["attr"], o["sp"]) == ("Vi", True) and "nia.lesson_sp" in rules


def test_nia_lesson_stamina_at_least_cost():
    """体力 ≥ 实际消耗就能上（客户端 IsSelfLessonStaminaSufficient，协调 11-17 逆向）：体力 8 能上 SP（耗 8）；体力 7 上不了 SP，改上普通课程（耗 6）。"""
    opts = [_opt("training", "Vo", True, 8), _opt("training", "Da", False, 6)]
    o, _, rules = _pick(_nia(stamina=8), opts)
    assert (o["attr"], o["sp"]) == ("Vo", True)
    o, _, rules = _pick(_nia(stamina=7), opts)
    assert (o["attr"], o["sp"]) == ("Da", False) and "nia.lesson_stamina" in rules
    o, _, _ = _pick(_nia(stamina=8), [_opt("gift"), _opt("rest")])
    assert o["option"] == "gift"  # 没课的周体力 8：下周 SP 照样能上，不休息


def test_nia_sales_when_no_lesson():
    opts = [_opt("sales"), _opt("gift"), _opt("rest")]
    o, _, _rules = _pick(_nia(), opts)
    assert o["option"] == "sales"


def test_nia_master_prefers_outing_over_gift():
    opts = [_opt("gift"), _opt("outing"), _opt("shop")]
    o, _, rules = _pick(_nia(mode="produce-005"), opts)
    assert o["option"] == "outing" and "nia.outing_default" in rules


def test_nia_rest_limit():
    """休息次数用完不能再休。"""
    opts = [_opt("rest"), _opt("gift")]
    o, _, rules = _pick(_nia(stamina=5, rest_left=0), opts)
    assert o["option"] == "gift" and "nia.rest_limit" in rules


def test_nia_low_stamina_rests_when_possible():
    opts = [_opt("training", "Vo", True, 8), _opt("training", "Da", False, 6), _opt("rest")]
    o, _, _ = _pick(_nia(stamina=5), opts)
    assert o["option"] == "rest"


def test_nia_special_guidance_with_points():
    """特別指導：P 点超过主数据的提醒线（ProduceSetting.stepCustomizeStartAlertProducePointThreshold）就进。"""
    opts = [_opt("special_guidance"), _opt("gift")]
    o, _, _ = _pick(_nia(produce_point=120), opts)
    assert o["option"] == "special_guidance"
    o, _, _ = _pick(_nia(produce_point=10), opts)
    assert o["option"] == "gift"


def test_teacher_suggestion_ignored():
    """不跟随老师建议（用户定）：建议的选项不加分。"""
    opts = [_opt("training", "Vo", False), _opt("training", "Da", True)]
    opts[0]["recommended"] = True
    o, _, _ = _pick(_st(), opts)
    assert o["attr"] == "Da"


def test_unknown_family_raises():
    with pytest.raises(ValueError):
        choose_action(_st(mode="produce-007"), [_opt("rest")])


def test_deciders_route_hajime_nia_to_strategy():
    """ProduceDeciders.action_select：初 / N.I.A 走策略表（来源 rule，理由带出处），不碰 H.I.F 的日程预演。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    st = GameState(produce={"mode": "produce-004", "step": 3, "stamina": 20, "max_stamina": 30, "produce_point": 0,
                            "params": {"Vo": 300, "Da": 200, "Vi": 150}, "idol_card_id": "i_card-shro-3-007"},
                   page_fields={"options": [_opt("training", "Vo", True, 8), _opt("training", "Vi", True, 8), _opt("sales")]})  # fmt: skip
    d = ProduceDeciders(CAMPUS_HIRO, None).action_select(st)
    assert d.source == "rule" and d.action.args["option"] == "training" and d.action.args["attr"] == "Vi"
    assert "[g8-NIA]" in d.reason and d.decider == "action_select"


# ---------------------------------------------------------------- 外出 / 授業

from hiro.brain.hajime_nia import choose_outing, choose_study  # noqa: E402


def _ch(i, cost_p=0, cost_hp=0, trouble=False, text="", **kw):
    return {"index": i, "cost_p": cost_p, "cost_hp": cost_hp, "trouble": trouble, "text": text, **kw}


def test_nia_pro_outing_middle():
    i, _, rules = choose_outing(_nia(produce_point=100), [_ch(0, 60), _ch(1, 40), _ch(2, 20)])
    assert i == 1 and "nia.outing_middle" in rules


def test_nia_master_outing_bottom_first_and_affordable():
    st = _nia(mode="produce-005", produce_point=100)
    i, _, rules = choose_outing(st, [_ch(0, 60), _ch(1, 40), _ch(2, 20)])
    assert i == 2 and "nia.outing_order" in rules
    i, _, _ = choose_outing(_nia(mode="produce-005", produce_point=50), [_ch(0, 60), _ch(1, 40), _ch(2, 80)])
    assert i == 1  # 下买不起：中（削除）


def test_nia_master_middle_change_is_last():
    st = _nia(mode="produce-005", produce_point=70)
    i, _, _ = choose_outing(st, [_ch(0, 60), _ch(1, 40, text="スキルカードをチェンジ"), _ch(2, 80)])
    assert i == 0  # 下买不起、中是换卡：上（强化）


def test_hajime_outing_tired_spends_most_affordable():
    i, _, rules = choose_outing(_st(stamina=10, produce_point=120), [_ch(0, 0), _ch(1, 50), _ch(2, 150)])
    assert i == 1 and "hajime.outing_spend" in rules


def test_hajime_outing_not_tired_cheapest():
    i, _, _ = choose_outing(_st(stamina=25, produce_point=300), [_ch(0, 100), _ch(1, 50), _ch(2, 0)])
    assert i == 2


def test_outing_skips_disabled():
    """平台 20ad0e5：画面上灰掉的档（choices[].disabled）点不了。P 点读多了也不选灰档；N.I.A 按位置选时同样跳过。"""
    chs = [_ch(0, 100, disabled=True), _ch(1, 50, disabled=True), _ch(2, 0)]
    i, _, _ = choose_outing(_st(stamina=10, produce_point=300), chs)  # 累了本该选最贵的，但前两档灰了
    assert i == 2
    i, _, _ = choose_outing(_nia(produce_point=100), [_ch(0, 60), _ch(1, 40, disabled=True), _ch(2, 20)])
    assert i != 1


def test_hajime_study_main_attr_avoids_trouble():
    chs = [
        _ch(0, text="ボーカル", attr="Vo"),
        _ch(1, text="ビジュアル", attr="Vi"),
        _ch(2, trouble=True, text="トラブル追加"),
    ]
    i, _, rules = choose_study(_st(main_attr="Vi", mode="produce-002"), chs)
    assert i == 1 and "hajime.study_avoid_trouble" in rules


def test_hajime_study_trouble_when_no_stamina():
    chs = [_ch(0, cost_hp=5, attr="Vo"), _ch(1, cost_hp=5, attr="Vi"), _ch(2, trouble=True)]
    i, _, _ = choose_study(_st(stamina=4, mode="produce-002"), chs)
    assert i == 2


def test_deciders_outing_study_route_by_family():
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-004",
        "step": 5,
        "stamina": 20,
        "max_stamina": 30,
        "produce_point": 100,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
    }
    d = ProduceDeciders(CAMPUS_HIRO, None).outing(
        GameState(produce=prod, page_fields={"choices": [_ch(0, 60), _ch(1, 40), _ch(2, 20)]})
    )
    assert d.action.args["select"] == 1 and "[g8-NIA]" in d.reason
    prod = dict(prod, mode="produce-002", main_attr="Vo")
    chs = [_ch(0, attr="Vo"), _ch(1, attr="Vi"), _ch(2, trouble=True)]
    d = ProduceDeciders(CAMPUS_HIRO, None).study(GameState(produce=prod, page_fields={"choices": chs}))
    assert d.action.args["select"] == 0


# ---------------------------------------------------------------- N.I.A 营业 / 试镜选档

from hiro.brain.hajime_nia import choose_audition, choose_sales_event, choose_sales_place  # noqa: E402


def _pl(i, kind, hp_cost=0):
    return {"index": i, "kind": kind, "hp_cost": hp_cost}


def test_sales_place_yellow_or_purple_when_stamina_ok():
    i, _, rules = choose_sales_place(
        _nia(stamina=25), [_pl(0, "resort", 0), _pl(1, "local", 5), _pl(2, "commercial", 5)]
    )
    assert i in (1, 2) and "nia.sales_yellow_purple" in rules


def test_sales_place_purple_when_points_enough_yellow_when_short():
    places = [_pl(0, "local", 5), _pl(1, "commercial", 5)]
    assert choose_sales_place(_nia(stamina=25, produce_point=200), places)[0] == 1
    assert choose_sales_place(_nia(stamina=25, produce_point=20), places)[0] == 0


def test_sales_place_resort_when_stamina_would_block_lesson():
    """营业后体力会不到 SP 课程要的 8 以上：去绿（リゾート）回体力。"""
    i, _, rules = choose_sales_place(
        _nia(stamina=12), [_pl(0, "resort", 0), _pl(1, "local", 5), _pl(2, "commercial", 5)]
    )
    assert i == 0 and "nia.sales_green_low" in rules


def test_sales_place_skips_unaffordable_and_reports_expected_votes():
    """去得了 = P 点 ≥ P 点消耗 且 体力 ≥ 体力消耗（客户端 CheckIfGoBusiness）；理由里带期望票数 = 票数 ×（1 + 0.3 × 大成功概率）。"""
    places = [
        dict(_pl(0, "local", 5), p_cost=0, votes=3000, great_rate=0.5),
        dict(_pl(1, "commercial", 5), p_cost=150, votes=3000, great_rate=1.0),
    ]
    i, reason, _ = choose_sales_place(_nia(stamina=25, produce_point=120), places)  # P 点 ≥ 100 本来去紫，但紫要 150P
    assert i == 0 and "期望票数 3450" in reason
    unknown = [{"index": 0, "hp_cost": 3, "votes": 2000}, {"index": 1, "hp_cost": 5, "votes": 2000, "great_rate": 0.5}]
    i, reason, _ = choose_sales_place(_nia(stamina=25), unknown)
    assert i == 1 and "期望票数最高" in reason


@pytest.mark.sim_master
def test_sales_place_expected_votes_from_master():
    """营业票数查主数据（段 × 场所，协调 11-17）：N.I.A PRO 1 次前 黄 2400 / 大成功 3120、绿 1700 / 2210。
    期望票数 = 普通 ×（1 − p）+ 大成功 × p，× 营业票数加成；p 只有展开那张卡读得到（平台 d07e8bf great_success_pct），没读到按 0。"""
    st = _nia(stamina=25, produce_point=20, stage="mid1", idol="i_card-shro-3-007")
    places = [dict(_pl(0, "local", 5), great_success_pct=90, great_success_votes=840), _pl(1, "commercial", 5)]
    i, reason, _ = choose_sales_place(st, places)
    assert i == 0 and "期望票数 3048" in reason and "大成功 90%" in reason  # 2400×0.1 + 3120×0.9
    st = _nia(stamina=12, stage="final", idol="i_card-shro-3-007", business_vote_permil=100)
    i, reason, _ = choose_sales_place(st, [_pl(0, "resort", 0), _pl(1, "local", 5)])
    assert i == 0 and "期望票数 3080" in reason  # 绿 最终前 2800 × 1.1
    i, reason, _ = choose_sales_place(_nia(stamina=25), [_pl(0, "local", 5)])
    assert "期望票数" not in reason  # 下一场是哪场不知道：不查


def test_sales_place_corporate_when_low_stamina_and_no_resort():
    i, _, _ = choose_sales_place(
        _nia(stamina=10), [_pl(0, "corporate", 0), _pl(1, "local", 5), _pl(2, "commercial", 5)]
    )
    assert i == 0


def test_sales_event_fills_weakest_attr():
    chs = [{"index": 0, "attr": "Vo"}, {"index": 1, "attr": "Vi"}, {"index": 2, "attr": "Da"}]
    i, _, rules = choose_sales_event(_nia(params={"Vo": 300, "Da": 250, "Vi": 120}), chs)
    assert i == 1 and "nia.sales_choice_fill" in rules


def test_audition_highest_unlocked_tier():
    tiers = [{"index": 0, "number": 1, "vote_need": 0, "locked": False}, {"index": 1, "number": 2, "vote_need": 4000, "locked": False},
             {"index": 2, "number": 3, "vote_need": 9000, "locked": True}]  # fmt: skip
    i, number, reason, _ = choose_audition(_nia(), 6000, tiers)
    assert (i, number) == (1, 2) and "4,000" in reason


def test_audition_lower_after_failure():
    """失败重来时降一档（N.I.A 失败可以降档，协调 11-17）。"""
    tiers = [
        {"index": 0, "number": 1, "vote_need": 0, "locked": False},
        {"index": 1, "number": 2, "vote_need": 4000, "locked": False},
    ]
    i, number, reason, _ = choose_audition(_nia(), 6000, tiers, failed=1)
    assert (i, number) == (0, 1) and "降" in reason


def test_deciders_nia_pages_registered():
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-004",
        "step": 9,
        "stamina": 25,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
        "votes": 5000,
    }
    pd = ProduceDeciders(CAMPUS_HIRO, None)
    d = pd.decide(
        "NIA_SALES_PLACE", GameState(produce=prod, page_fields={"places": [_pl(0, "resort"), _pl(1, "local", 5)]})
    )
    assert d.action.args["select"] == 1 and d.decider == "sales_place"
    d = pd.decide(
        "NIA_AUDITION_SELECT",
        GameState(
            produce=prod,
            page_fields={
                "votes": 5000,
                "tiers": [
                    {"index": 0, "number": 1, "vote_need": 0, "locked": False},
                    {"index": 1, "number": 2, "vote_need": 4000, "locked": False},
                ],
            },
        ),
    )
    assert d.action.args == {"select": 1, "audition_number": 2} and d.decider == "audition_select"
    d = pd.decide(
        "NIA_SALES_EVENT",
        GameState(produce=prod, page_fields={"choices": [{"index": 0, "attr": "Vo"}, {"index": 1, "attr": "Da"}]}),
    )
    assert d.action.args["select"] in (0, 1) and d.decider == "sales_event"


# ---------------------------------------------------------------- 再挑戦（协调 11-17：只用免费 / 已有的票，每场 ≤ 3 次）


def _retry(prod_mode="produce-001", **pf):
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    return ProduceDeciders(CAMPUS_HIRO, None).decide(
        "EXAM_RETRY", GameState(produce={"mode": prod_mode}, page_fields=pf)
    )


@pytest.mark.parametrize("pf, confirm", [
    ({"stage": "result", "attempts": 0}, True),
    ({"stage": "result", "attempts": 3}, False),  # 每场最多 3 次
    ({"stage": "result", "attempts": 1, "retries_left": 0}, False),  # 游戏说没次数了
    ({"stage": "confirm", "attempts": 1, "cost": "free"}, True),
    ({"stage": "confirm", "attempts": 1, "cost": "ticket"}, True),
    ({"stage": "confirm", "attempts": 1, "cost": "jewel"}, False),  # 绝不花ジュエル
    ({"stage": "confirm", "attempts": 1}, False),  # 读不出花什么：不点
])  # fmt: skip
def test_exam_retry_rules(pf, confirm):
    d = _retry(**pf)
    assert d.action.args["kind"] == "exam_retry" and d.action.args["confirm"] is confirm and d.decider == "exam_retry"


def test_nia_retry_says_reselect():
    d = _retry("produce-004", stage="confirm", attempts=0, cost="free")
    assert d.action.args["confirm"] and d.action.args["reselect"] is True and "降" in d.reason


def test_hajime_oikomi_takes_highest_exam_criteria():
    """追い込みレッスン（初 REGULAR 中間まで 1週 实机帧 00886）：选审查基准最高的属性 Vo [g8-PRO]，不是数值最高的 Da。"""
    opts = [_opt("training", "Vo", False), _opt("training", "Da", False), _opt("training", "Vi", False)]
    for o in opts:
        o["oikomi"] = True
    st = _st(params={"Vo": 252, "Da": 260, "Vi": 180}, criteria={"Vo": 0.45, "Da": 0.35, "Vi": 0.20}, stamina=34)
    o, _reason, rules = _pick(st, opts)
    assert o["attr"] == "Vo" and "hajime.oikomi_bonus" in rules


@pytest.mark.sim_master
def test_exam_criteria_from_master():
    """审查基准 = 下一场考试战斗配置的三属性权重（主数据）。"""
    from hiro.brain.hajime_nia import exam_criteria

    c = exam_criteria("produce-001", "i_card-shro-3-007", "mid1")
    assert c and set(c) == {"Vo", "Da", "Vi"} and abs(sum(c.values()) - 1) < 1e-6


def test_drinks_full_flag_caps_capacity():
    """「Pドリンク所持上限」红标（page_fields.drinks_full）：饮料容量按现在持有的瓶数算（p15 账号 2 只有 3 格，识别读成 4）。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "idol_card_id": "i_card-shro-3-007",
        "mode": "produce-007",
        "step": 12,
        "stamina": 6,
        "max_stamina": 39,
        "produce_point": 278,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
        "growth_permil": {},
        "star": 0,
        "drinks": ["a", "b", "c"],
        "drink_capacity": 4,
    }
    pd = ProduceDeciders(CAMPUS_HIRO, None)
    assert pd.produce_state(GameState(produce=prod)).drink_capacity == 4
    assert pd.produce_state(GameState(produce=prod, page_fields={"drinks_full": True})).drink_capacity == 3


def test_action_select_skips_options_without_name():
    """识别没认出名字的选项（没有 option 键）跳过，不让决策器抛 KeyError。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-004",
        "step": 5,
        "stamina": 20,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
    }
    pd = ProduceDeciders(CAMPUS_HIRO, None)
    d = pd.decide(
        "ACTION_SELECT",
        GameState(produce=prod, page_fields={"options": [{"kind": "??", "center": [1, 1]}, _opt("sales")]}),
    )
    assert d.action.args["option"] == "sales" and d.source == "rule"
    d = pd.decide("ACTION_SELECT", GameState(produce=prod, page_fields={"options": [{"kind": "??"}]}))
    assert d.action.kind.value == "Wait"


def test_nia_pitem_rules():
    """N.I.A 试镜后的 P 道具：PRO 选ワッペン [ww-NIA]、MASTER 选パスケース [ww-NIAM]；攻略没写的交回原有规则（None）。"""
    from hiro.brain.hajime_nia import choose_nia_pitem

    pro = [
        {"index": 0, "name": "N.I.Aトートバッグ"},
        {"index": 1, "name": "N.I.Aワッペン"},
        {"index": 2, "name": "N.I.Aスマホリング"},
    ]
    i, _, rules = choose_nia_pitem(_nia(), pro, "ProducePlanType_Plan2")
    assert i == 1 and "nia.pitem_wappen" in rules
    assert choose_nia_pitem(_nia(), pro, "ProducePlanType_Plan3")[0] is None  # アノマリー除外
    master = [
        {"index": 0, "name": "N.I.Aトートバッグ"},
        {"index": 1, "name": "N.I.Aスマホリング"},
        {"index": 2, "name": "N.I.Aパスケース"},
    ]
    i, _, rules = choose_nia_pitem(_nia(mode="produce-005"), master)
    assert i == 2 and "nia.pitem_passcase" in rules
    assert choose_nia_pitem(_nia(mode="produce-005"), [{"index": 0, "name": "はつぼしブレスレット"}])[0] is None


def test_select_pitem_routes_nia():
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-005",
        "step": 10,
        "stamina": 20,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
        "idol_card_id": "i_card-shro-3-007",
    }
    items = [
        {"index": 0, "name": "N.I.Aトートバッグ"},
        {"index": 1, "name": "N.I.Aスマホリング"},
        {"index": 2, "name": "N.I.Aパスケース"},
    ]
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "SELECT_PITEM", GameState(produce=prod, page_fields={"pitems": items})
    )
    assert d.action.args["pick_indexes"] == [2] and "[ww-NIAM]" in d.reason


def test_nia_action_without_step_is_rule():
    """N.I.A 读不到 step：规则照常选，来源记 rule 不记 fallback。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-004",
        "stamina": 20,
        "max_stamina": 30,
        "produce_point": 0,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
    }
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "ACTION_SELECT", GameState(produce=prod, page_fields={"options": [_opt("sales")]})
    )
    assert d.source == "rule" and not d.warnings


def test_hajime_tired_lesson_week_rests():
    """初 REGULAR 实机（协调 11-17）：中間まで 3 週、体力 9/30，可选 Vo / Da SP / Vi SP 课、授業、休む——累了（少了一半 / 15 以上）
    课程、授業让位给外出或休息，没有外出就休息 [g8-外出]（原来选了 Da SP 课，游戏弹「体力が低下しています」停 180 秒）。"""
    opts = [
        _opt("training", "Vo", False),
        _opt("training", "Da", True),
        _opt("training", "Vi", True),
        _opt("class"),
        _opt("rest"),
    ]
    o, _, rules = _pick(_st(stamina=9, max_stamina=30, produce_point=40), opts)
    assert o["option"] == "rest" and "hajime.outing_when" in rules


def test_hajime_tired_lesson_week_outing_with_points():
    opts = [_opt("training", "Da", True), _opt("outing"), _opt("rest")]
    o, _, _ = _pick(_st(stamina=12, max_stamina=30, produce_point=150), opts)
    assert o["option"] == "outing"


def test_hajime_not_tired_still_lessons():
    opts = [_opt("training", "Da", True), _opt("rest")]
    o, _, _ = _pick(_st(stamina=20, max_stamina=30), opts)
    assert o["option"] == "training"


def test_event_choice_pure_dialogue_picks_first():
    """开场纯台词二选一（协调 11-17）：识别读过、确认没有效果（effect 为空字符串或 no_effect）时直接选第一个，不停。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    pd = ProduceDeciders(CAMPUS_HIRO, None)
    prod = {"mode": "produce-001", "stamina": 30, "max_stamina": 30, "params": {"Vo": 1, "Da": 1, "Vi": 1}}
    for chs in (
        [{"index": 0, "text": "よろしく", "effect": ""}, {"index": 1, "text": "がんばろう", "effect": ""}],
        [{"index": 0, "text": "A", "no_effect": True}, {"index": 1, "text": "B", "no_effect": True}],
    ):
        d = pd.decide("EVENT_CHOICE", GameState(produce=prod, page_fields={"choices": chs}))
        assert d.action.args == {"select": 0} and d.source == "rule" and "没有效果" in d.reason
    # 平台的 page_fields.dialogue_only（ef07f2b）：选项只有文字、没读 effect，也直接选第一个；false 时照旧走粗估 / 停机
    chs = [{"index": 0, "text": "よろしく"}, {"index": 1, "text": "がんばろう"}]
    d = pd.decide("EVENT_CHOICE", GameState(produce=prod, page_fields={"choices": chs, "dialogue_only": True}))
    assert d.action.args == {"select": 0} and d.source == "rule" and "纯台词" in d.reason
    d = pd.decide("EVENT_CHOICE", GameState(produce=prod, page_fields={"choices": chs, "dialogue_only": False}))
    assert "纯台词" not in d.reason


def test_hajime_full_stamina_prefers_shop_over_outing():
    """初实机（协调 11-17）：体力 30/30、没课的周，原来选了外出。满体力外出回体力用不上：P 点够删卡时相談排在外出前。"""
    opts = [_opt("outing"), _opt("shop"), _opt("rest")]
    o, _reason, _ = _pick(_st(stamina=30, max_stamina=30, produce_point=150), opts)
    assert o["option"] == "shop"


def test_hajime_shop_only_when_affordable():
    """初第二局第 4 周（协调 11-17）：外出 / 相談、体力 29/30、P 30 选了相談，游戏弹「P ポイントが少ない」。
    相談要 P 点 ≥ 删卡价（局面给了 consult_delete_price 就用它，否则 100P）才去；做不成就外出，体力几乎满也一样。"""
    o, reason, _ = _pick(_st(stamina=29, max_stamina=30, produce_point=30), [_opt("outing"), _opt("shop")])
    assert o["option"] == "outing" and "相談 P 点不够（30P < 100P）" in reason
    o, _, _ = _pick(_st(stamina=29, max_stamina=30, produce_point=80, consult_price=75), [_opt("outing"), _opt("shop")])
    assert o["option"] == "shop"
    o, _, _ = _pick(_st(stamina=29, max_stamina=30, produce_point=30), [_opt("shop")])
    assert o["option"] == "shop"  # 只剩相談：没得选
    o, _, _ = _pick(_nia(stamina=25, produce_point=30), [_opt("shop"), _opt("rest")])
    assert o["option"] == "rest"  # N.I.A 同样：相談做不成事排最后


def test_declined_option_not_chosen_again():
    """平台点掉相談開始確認（P 点太少）后回到选行动页，page_fields.declined_options=["shop"]：这一周不再选相談。"""
    from hiro.contracts import GameState

    prod = {
        "mode": "produce-001",
        "stamina": 29,
        "max_stamina": 30,
        "produce_point": 120,
        "params": {"Vo": 1, "Da": 1, "Vi": 1},
    }
    opts = [{"option": "shop"}, {"option": "outing"}]
    d = _pd().decide("ACTION_SELECT", GameState(produce=prod, page_fields={"options": opts}))
    assert d.action.args.get("option") == "shop"
    d = _pd().decide(
        "ACTION_SELECT", GameState(produce=prod, page_fields={"options": opts, "declined_options": ["shop"]})
    )
    assert d.action.args.get("option") == "outing"
    o, _, _ = _pick(_st(stamina=30, max_stamina=30), [_opt("outing"), _opt("rest")])
    assert o["option"] == "outing"  # 满体力不能休息、只剩外出
    o, _, _ = _pick(_st(stamina=22, max_stamina=30, produce_point=60), [_opt("outing"), _opt("shop"), _opt("rest")])
    assert o["option"] == "outing"  # 少了 8（不算满、也不算累）：外出照旧排在 P 不足时的相談前


# ---- 初 / N.I.A 选卡（协调 11-17「N.I.A 必拿卡表」；实机 11-17：H.I.F 必拿表让每页为 せのびの魔法 刷新 / 禁用用光） ----
TELEVI, GENKI_AISATSU, YURUFUWA, KAIKA = (
    "p_card-00-men-3_012",
    "p_card-02-act-1_004",
    "p_card-02-act-1_028",
    "p_card-02-act-3_038",
)
LOVELY_WINK, MINNA, HIGH_TENSION, KIRAKIRA = (
    "p_card-02-act-2_046",
    "p_card-02-act-2_049",
    "p_card-00-men-2_014",
    "p_card-02-act-2_062",
)
RESTART, SHIKIRI, GAMUSHARA = "p_card-02-men-1_030", "p_card-00-men-3_005", "p_card-02-act-1_071"


def _picked(d):
    return (d.action.args.get("pick_indexes") or [None])[0]


def _card_pick(mode, cands, deck=(), rerolls=4, excludes=2):
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {"idol_card_id": "i_card-shro-3-007", "mode": mode, "step": 4, "stamina": 20, "max_stamina": 30, "produce_point": 50,
            "params": {"Vo": 200, "Da": 200, "Vi": 200}, "growth_permil": {}, "star": 0, "rerolls_left": rerolls, "excludes_left": excludes,
            "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    pd = ProduceDeciders(CAMPUS_HIRO, None)
    return pd.decide(
        "SELECT_CARD",
        GameState(produce=prod, page_fields={"candidates": [{"card_id": c, "upgrade": 0} for c in cands]}),
    )


@pytest.mark.master
@pytest.mark.sim_master
@pytest.mark.parametrize("mode", ["produce-004", "produce-001"])
def test_card_pick_no_fishing_for_hif_musts(mode):
    """候选里有一次性、不冲突的卡：直接拿，不为 H.I.F 必拿（せのびの魔法）刷新。"""
    d = _card_pick(mode, [RESTART, GENKI_AISATSU, LOVELY_WINK])
    assert d.action.kind.name == "ACTIVATE_EFFECT" and _picked(d) == 1, d.reason
    assert "せのび" not in d.reason and d.source == "rule"


@pytest.mark.master
@pytest.mark.sim_master
def test_card_pick_prefers_signature_buff_then_value():
    """一次性的卡里：社区 S / A 先；同档时加やる気的先 [g8-試験]。"""
    d = _card_pick("produce-004", [TELEVI, YURUFUWA, GENKI_AISATSU])
    assert _picked(d) == 1 and "やる気" in d.reason
    d = _card_pick("produce-004", [YURUFUWA, SHIKIRI, KAIKA])
    assert _picked(d) == 1  # 仕切り直し：社区 S
    d = _card_pick("produce-001", [KAIKA, GENKI_AISATSU, GAMUSHARA])
    assert (
        _picked(d) == 0
    )  # 都是一次性、加やる気：SSR 開花 在 R がむしゃら（卡表里只是换卡保留）前（11-17 初实机这一页）


@pytest.mark.master
@pytest.mark.sim_master
def test_nia_card_pick_prefers_owned_once_copy():
    """N.I.A：同档的一次性卡，牌组里已经有的先 [ww-NIA]；初不看这条。"""
    deck = [GENKI_AISATSU, "p_card-02-act-1_003"]
    d = _card_pick("produce-004", [YURUFUWA, KAIKA], deck=[*deck, KAIKA])
    assert _picked(d) == 1 and "已有 1 张" in d.reason and "ww-NIA" in d.reason


@pytest.mark.master
@pytest.mark.sim_master
def test_card_pick_rerolls_when_all_unwanted_then_least_polluting():
    """三张都不该拿（冲突 / 可复用非循环卡）：先刷新；刷新用完拿最不污染的（可复用 > 冲突）。"""
    d = _card_pick("produce-004", [RESTART, LOVELY_WINK, MINNA])
    assert d.action.kind.name == "REROLL"
    d = _card_pick("produce-004", [RESTART, HIGH_TENSION, MINNA], rerolls=0)
    assert d.action.kind.name == "EXCLUDE_CARD"  # 刷新用完、禁用还有：先禁用（协调 11-18）
    d = _card_pick("produce-004", [RESTART, HIGH_TENSION, MINNA], rerolls=0, excludes=0)
    # 都冲突：みんな大好き（好印象の90%分）主打好印象排最后；リスタート 可复用、ハイテンション 一次性 → 一次性的污染小
    assert _picked(d) == 1 and "不污染" in d.reason


@pytest.mark.master
@pytest.mark.sim_master
def test_card_pick_loop_part_until_kept():
    """循环卡（きらきら紙吹雪）还没有时拿；牌组里已有 1 张（保留数 1）后算「已够」，不再拿。"""
    d = _card_pick("produce-004", [KIRAKIRA, RESTART], deck=[GENKI_AISATSU])
    assert _picked(d) == 0 and "循环" in d.reason
    d = _card_pick("produce-004", [KIRAKIRA, TELEVI], deck=[GENKI_AISATSU, KIRAKIRA])
    assert _picked(d) == 1
    eieio, imetore = "p_card-02-men-1_031", "p_card-02-men-2_008"
    d = _card_pick("produce-001", [eieio, imetore, GAMUSHARA], deck=[GENKI_AISATSU])
    assert (
        _picked(d) == 0
    )  # えいえいおー（固有 P 道具的触发牌，保留 2 张）还缺：排在一次性的 イメトレ 前（11-17 初实机这一页）


@pytest.mark.master
@pytest.mark.sim_master
def test_hif_card_pick_unchanged():
    """H.I.F 不走这条路：仍按必拿还缺 せのびの魔法 刷新。"""
    d = _card_pick("produce-007", [RESTART, GENKI_AISATSU, LOVELY_WINK])
    assert "せのび" in d.reason


@pytest.mark.master
@pytest.mark.sim_master
def test_card_pick_live_produce_without_step_star():
    """实机局面（11-17 N.I.A trace）没有 step / star、多一个 deck_size：仍按 N.I.A 规则选，牌组张数照算。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {"mode": "produce-004", "stamina": 20, "max_stamina": 30, "produce_point": 50, "params": {"Vo": 200, "Da": 200, "Vi": 200},
            "growth_permil": {}, "drinks": [], "param_cap": 1000, "deck_size": 2,
            "deck": [{"card_id": KAIKA, "upgrade": 0}, {"card_id": GENKI_AISATSU, "upgrade": 0}]}  # fmt: skip
    pd = ProduceDeciders(CAMPUS_HIRO, None)
    d = pd.decide(
        "SELECT_CARD",
        GameState(
            produce=prod, page_fields={"candidates": [{"card_id": RESTART}, {"card_id": YURUFUWA}, {"card_id": KAIKA}]}
        ),
    )
    assert _picked(d) == 2 and "已有 1 张" in d.reason and "せのび" not in d.reason


# ---- 初 / N.I.A 的局面（协调 11-17 ②：没有 star 是真值、step 缺失按未知）----
LIVE_NIA_DECK = [
    "p_card-02-ido-3_105",
    "p_card-02-act-1_037",
    "p_card-00-act-0_001",
    "p_card-02-men-1_031",
    "p_card-02-men-2_004",
]


def _live_state(mode="produce-004", deck=LIVE_NIA_DECK, **pf):
    from hiro.contracts import GameState

    prod = {"mode": mode, "stamina": 20, "max_stamina": 30, "produce_point": 160, "params": {"Vo": 200, "Da": 200, "Vi": 200},
            "growth_permil": {}, "drinks": [], "deck_size": len(deck), "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    return GameState(produce=prod, page_fields=pf)


def _pd():
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO

    return ProduceDeciders(CAMPUS_HIRO, None)


def test_produce_state_hajime_nia_without_step_star():
    """实机局面没有 step / star：初 / N.I.A 照样构造出局面，没有星星（star 留 None，用户 11-17：完全不用处理），step 记为未观测；
    H.I.F 缺 star 仍构造不出。"""
    from hiro.contracts import GameState

    pd = _pd()
    for mode in ("produce-004", "produce-001"):
        st = _live_state(mode)
        ps = pd.produce_state(st)
        assert ps is not None and ps.mode == mode and ps.star is None and len(ps.deck) == 5
        assert "step" in pd.unknown_fields(st, ps)
    assert pd.produce_state(_live_state("produce-007")) is None
    full = dict(_live_state("produce-007").produce, idol_card_id="i_card-shro-3-007", step=3)
    assert GameState(produce=full).produce_state() is None  # H.I.F：只缺 star 也不构造
    assert GameState(produce=dict(full, star=120)).produce_state().star == 120


@pytest.mark.master
@pytest.mark.sim_master
def test_hajime_nia_texts_have_no_star():
    """初 / N.I.A 给 LLM 的局面文本、选卡预演、提示词里都没有星星（也没有 H.I.F 的ワッペン / 选拔 / 本战）。"""
    import json

    from hiro.brain import hajime_nia_rules as hr
    from hiro.brain.llm import load_prompt
    from hiro.state.context import preview_card_pick, situation

    pd = _pd()
    for mode in ("produce-004", "produce-001"):
        st = _live_state(mode)
        ps = pd.produce_state(st)
        text = json.dumps(situation(ps, pd.unknown_fields(st, ps)), ensure_ascii=False)
        assert not any(w in text for w in ("星", "ワッペン", "选拔", "本战")), text
        pick = preview_card_pick(ps, [{"card_id": KAIKA}, {"card_id": TELEVI}], unknown=pd.unknown_fields(st, ps))
        assert "ワッペン星星" not in pick and all("star" not in r["delta"] for r in pick["候选"])
    for fam, diff in (("hajime", "regular"), ("hajime", "master"), ("nia", "pro"), ("nia", "master")):
        system = load_prompt("hajime_nia_choice.md") + hr.guide(fam, diff)
        assert "星" not in system and "H.I.F" not in system


@pytest.mark.master
@pytest.mark.sim_master
def test_hajime_nia_llm_choice_uses_own_prompt(monkeypatch):
    """初 / N.I.A 的 LLM 选择（事件选项等）用 hajime_nia_choice.md + 规则清单，不带 hif_produce.md 和广的 H.I.F 打法。"""
    from hiro.brain.deciders import Action, Decision

    seen = {}

    class LLM:
        def ask_json(self, system, user):
            from hiro.brain.llm import LLMResult

            seen["system"], seen["user"] = system, user
            return LLMResult(data={"choice": 1, "reason": "test"}, raw="", seconds=0.0)

    monkeypatch.setenv("HIRO_PRODUCE_LLM", "all")
    pd = _pd()
    pd.llm = LLM()
    rule = Decision(Action.event_select(0), "规则", "rule", 0.5, decider="event_choice")
    d = pd._ask_choice("event_choice", rule, [(Action.event_select(0), {"选项": "a"}), (Action.event_select(1), {"选项": "b"})], "q", "why",
                       _live_state("produce-004"))  # fmt: skip
    assert d.source == "llm" and d.action.args == {"select": 1}
    assert "N.I.A PRO 的社区规则" in seen["system"] and "H.I.F" not in seen["system"] and "星" not in seen["system"]
    assert "星" not in seen["user"]


@pytest.mark.master
@pytest.mark.sim_master
def test_nia_consult_deletes_basic_card():
    """N.I.A 相談：删「○○の基本」（不走 H.I.F 选拔的 P 点预算）；没有该删的、牌组没识别、P 不够时不买。"""
    pd = _pd()
    svc = {"delete": {"price": 100, "available": True}}
    d = pd.decide("CONSULT", _live_state(shelf=[], services=svc))
    assert d.action.kind.name == "SHOP_BUY" and d.action.args == {"service": "delete", "card_index": 2}, d.reason
    assert "アピールの基本" in d.reason and "g8-NIAM" in d.reason
    keep = [c for c in LIVE_NIA_DECK if c != "p_card-00-act-0_001"]
    assert _pd().decide("CONSULT", _live_state(deck=keep, shelf=[], services=svc)).action.kind.name == "SHOP_END"
    poor = {"delete": {"price": 200, "available": True}}
    assert _pd().decide("CONSULT", _live_state(shelf=[], services=poor)).action.kind.name == "SHOP_END"
    d = _pd().decide("CONSULT", _live_state("produce-001", shelf=[], services=svc))
    assert d.action.args.get("card_index") == 2 and "g8-PRO" in d.reason


def test_hajime_nia_drink_no_hif_sim():
    """初 / N.I.A 的饮料比较不跑 H.I.F 选拔考试的模拟。"""
    pd = _pd()
    assert pd._drink_sim_context(pd.produce_state(_live_state()), "selection") is None


# ---- 主打好印象的卡（协调 11-18 N.I.A 第二局：拿了 キラメキ / キセキの魔法 / ファンシーチャーム）----
KIRAMEKI, KISEKI, FANCY, TEBYOSHI = (
    "p_card-02-act-2_048",
    "p_card-02-act-3_052",
    "p_card-02-men-2_058",
    "p_card-02-act-1_003",
)
PASTEL, SHIAWASE, OTOME, HONBAN, OMOIDASHI = (
    "p_card-02-men-1_033",
    "p_card-02-men-1_035",
    "p_card-02-men-2_060",
    "p_card-02-men-2_054",
    "p_card-02-men-1_032",
)


@pytest.mark.master
@pytest.mark.sim_master
def test_impression_main_cards_detected():
    """卡面「好印象のN%分パラメータ上昇」「好印象がN以上の場合、使用可」「以降…好印象+」算主打好印象；
    ゆめみごこち / あのときの約束（只消費好印象）、成長痛 / 止められない想い（顺带加好印象）不算。"""
    from hiro.brain.deciders import _impression_main

    assert all(_impression_main(c) for c in (KIRAMEKI, KISEKI, FANCY, TEBYOSHI))
    assert not any(
        _impression_main(c)
        for c in ("p_card-02-men-2_057", "p_card-02-act-3_045", "p_card-02-men-2_111", "p_card-02-men-2_056", KAIKA)
    )


@pytest.mark.master
@pytest.mark.sim_master
def test_impression_main_not_taken_even_if_once():
    """打法匹配先于一次性：キラメキ（一次性、主打好印象）不拿；刷新用完先禁用；禁用也用完才在三张里挑，主打好印象的排最后。"""
    d = _card_pick("produce-004", [KIRAMEKI, PASTEL, SHIAWASE], rerolls=0, excludes=0)
    assert _picked(d) == 1  # パステル気分：可复用、不冲突，比主打好印象 / 纯好印象的都强
    d = _card_pick("produce-004", [FANCY, HONBAN, OMOIDASHI], rerolls=0, excludes=2)
    assert d.action.kind.name == "EXCLUDE_CARD" and d.action.args["pick_index"] == 0 and "主打好印象" in d.reason
    d = _card_pick("produce-004", [OTOME, TEBYOSHI, KISEKI], rerolls=0, excludes=0)
    assert _picked(d) == 0  # 都冲突：不拿主打好印象的 手拍子 / キセキの魔法


@pytest.mark.master
def test_consult_one_delete_per_visit():
    """一次相談只能删一张（协调 11-18 初 PRO：删完按钮变「交換済」，价格显示 125P，又按它删了 3 次停机）：
    删过一次就结束；回到选行动页后下一次相談可以再删；画面给了 used 时也照它。"""
    pd = _pd()
    svc = {"delete": {"price": 100, "available": True}}
    d = pd.decide("CONSULT", _live_state("produce-002", shelf=[], services=svc))
    assert d.action.kind.name == "SHOP_BUY"
    after = {"delete": {"price": 125, "available": True}}
    d = pd.decide("CONSULT", _live_state("produce-002", shelf=[], services=after))
    assert d.action.kind.name == "SHOP_END" and "已经删过" in d.reason
    from hiro.contracts import GameState

    pd.decide("ACTION_SELECT", GameState(produce={"mode": "produce-002", "stamina": 20, "max_stamina": 30, "produce_point": 300},
                                         page_fields={"options": [{"option": "shop"}, {"option": "rest"}]}))  # fmt: skip
    assert pd.decide("CONSULT", _live_state("produce-002", shelf=[], services=after)).action.kind.name == "SHOP_BUY"
    used = {"delete": {"price": 125, "available": True, "used": True}}
    assert _pd().decide("CONSULT", _live_state("produce-004", shelf=[], services=used)).action.kind.name == "SHOP_END"


# ---------------------------------------------------------------- 属性上限（11-23 Mac 初 REGULAR：Vo 1000/1000 还选 Vo 课，游戏弹「レッスンパラメータ上限」卡住）


def test_hajime_lesson_skips_capped_attr():
    opts = [_opt("training", "Vo", True), _opt("training", "Da", False), _opt("training", "Vi", True)]
    o, _, _ = _pick(_st(main_attr="Vo", params={"Vo": 1000, "Da": 288, "Vi": 275}), opts)
    assert o["attr"] == "Vi" and o["sp"]  # Vo 封顶：剩下的里 SP 优先


def test_hajime_oikomi_skips_capped_attr():
    opts = [
        {**_opt("training", "Vo"), "oikomi": True},
        {**_opt("training", "Da"), "oikomi": True},
        {**_opt("training", "Vi"), "oikomi": True},
    ]
    st = _st(main_attr="Vo", params={"Vo": 1000, "Da": 393, "Vi": 313}, criteria={"Vo": 0.4, "Da": 0.35, "Vi": 0.25})
    o, _, _ = _pick(st, opts)
    assert o["attr"] == "Da"  # Vo 封顶：审查基准第二高的


def test_hajime_lesson_all_capped_falls_back():
    opts = [_opt("training", "Vo", True), _opt("training", "Da", False)]
    o, _, _ = _pick(_st(main_attr="Vo", params={"Vo": 1000, "Da": 1000, "Vi": 1000}), opts)
    assert o["attr"] == "Vo"  # 都封顶了：照原来的规则选，不卡住
