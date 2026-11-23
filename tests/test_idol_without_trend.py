"""knowledge.IDOL_TREND 只收了 篠澤広。别的偶像（11-20 DMM 有村麻央 標）不能让选卡 / 选行动决策器崩：没有流行顺序就跳过相关估算。"""

from types import SimpleNamespace as NS

from hiro.state import context


def test_trend_or_none():
    assert context._trend_or_none("i_card-shro-3-007") is not None
    assert context._trend_or_none("i_card-amao-3-018") is None
    assert context._trend_or_none(None) is None


def test_likely_sub_guesses_without_trend():
    sub, note = context._likely_sub(NS(idol_card_id="i_card-amao-3-018"), "Vo")
    assert sub in ("Da", "Vi") and "没有流行顺序" in note


def test_r1_bonus_gain_none_without_trend(monkeypatch):
    monkeypatch.setattr(context, "params_complete", lambda p: True)
    st = NS(mode=context.FINALS, params={"Vo": 1, "Da": 1, "Vi": 1}, idol_card_id="i_card-amao-3-018", param_cap=3000)
    assert context.r1_bonus_gain(st, {"Vo": 10}) is None
