"""公开攻略里的 H.I.F 价格 / 效果组规则。"""

from hiro.brain import hif_rules as r


def test_prices():
    assert [r.service_price(i) for i in range(8)] == [100, 125, 150, 175, 200, 250, 250, 250]
    assert [r.interval_refresh_price(i) for i in range(8)] == [10, 10, 20, 30, 40, 50, 50, 50]
    assert [r.interval_upgrade_price(i) for i in range(3)] == [100, 125, 150]
    assert [r.interval_recover_price(h) for h in (0, 1, 2, 3, 7)] == [0, 10, 10, 20, 40]


def test_effect_groups():
    assert (
        r.effect_group("ProduceExamEffectType_ExamCardPlayAggressive")
        == "effect_group-visible-exam_card_play_aggressive-000"
    )
    idol = {"examEffectType": "ProduceExamEffectType_ExamCardPlayAggressive", "planType": "ProducePlanType_Plan2"}
    assert r.is_recommended({"effectGroupIds": ["effect_group-visible-exam_card_play_aggressive-000"]}, idol)
    assert r.PLAN_EFFECTS[idol["planType"]][1] == "ExamCardPlayAggressive"
