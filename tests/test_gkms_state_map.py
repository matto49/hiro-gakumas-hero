"""局面 → GKMS 培育记录（spec 4.6）。样例是 11-19 初 PRO 实机第 11 周的选行动页（日程列表探查读过一遍）。"""

import copy
import json
from pathlib import Path

from hiro.contracts import GameState
from hiro.gkms import state_map as sm

RAW = json.loads((Path(__file__).parent / "data" / "gkms-hajime-pro-w11.json").read_text())


def state(**produce) -> GameState:
    d = copy.deepcopy(RAW)
    d["produce"].update(produce)
    return GameState.from_dict(d)


def types(rec, step):
    return next(r["step_types"] for r in rec["schedule"] if r["step_number"] == step)


def test_numbers_and_growth():
    rec, why = sm.record(state())
    assert why is None
    st = rec["occasions"][0]["state"]
    assert (st["vocal"], st["dance"], st["visual"]) == (378, 264, 797)
    assert (st["stamina"], st["max_stamina"], st["produce_point"]) == (22, 36, 419)
    assert (st["vocal_growth_rate_permil"], st["dance_growth_rate_permil"]) == (122, 466)  # 画面 1122 = +12.2%
    assert rec["produce_id"] == "produce-002" and rec["from_step"] == 11
    assert rec["occasions"][0]["items_gained"] == ["pitem_00-3-054-0", "pitem_00-3-053-0", "pitem_02-3-005-0"]


def test_schedule_from_this_week_on():
    rec, _ = sm.record(state())
    names = lambda st: sorted(types(rec, st))  # noqa: E731
    assert names(11) == ["event_activity", "event_school", "refresh", "shop"]  # 本周以画面选项为准
    assert names(12) == ["lesson_da", "lesson_vi", "lesson_vo", "refresh"]
    assert names(15) == ["lesson_da_hard", "lesson_vi_hard", "lesson_vo_hard", "refresh"]  # 追い込み
    assert names(16) == ["audition_final"]
    assert max(r["step_number"] for r in rec["schedule"]) == 16


def test_sp_on_this_week_only():
    d = copy.deepcopy(RAW)
    d["page_fields"]["options"] = [{"option": "training", "attr": "Da", "sp": True}, {"option": "rest"}]
    d["produce"]["step"] = 12
    rec, why = sm.record(GameState.from_dict(d))
    assert why is None
    assert sorted(types(rec, 12)) == ["lesson_da_sp", "refresh"]
    assert "lesson_da_sp" not in types(rec, 13)  # 以后几周读不到 SP：按普通课


def test_missing_week_ahead_defers():
    s = state()
    s.produce["schedule"]["weeks"] = [w for w in s.produce["schedule"]["weeks"] if w["week"] != 13]
    rec, why = sm.record(s)
    assert rec is None and "第 13 周" in why


def test_missing_week_behind_is_fine():
    _rec, why = sm.record(state())  # 样例本来就缺第 6 周（在第 11 周之前）
    assert why is None


def test_legend_lessons():
    rec, _ = sm.record(state(mode="produce-006"))
    assert "legend_lesson_da" in types(rec, 12)


def test_no_step_defers():
    rec, why = sm.record(state(step=None))
    assert rec is None and "第几周" in why


def test_hif_not_mapped():
    rec, why = sm.record(state(mode="produce-008"))
    assert rec is None and "H.I.F" in why


def test_option_for_maps_back():
    opts = [{"option": "rest"}, {"option": "class"}, {"option": "shop"}, {"option": "allowance"},
            {"option": "training", "attr": "Da", "sp": True}]  # fmt: skip
    assert sm.option_for("event_school", opts) == 1
    assert sm.option_for("refresh", opts) == 0
    assert sm.option_for("event_activity", opts) == 3
    assert sm.option_for("lesson_da_sp", opts) == 4
    assert sm.option_for("legend_lesson_da_sp", opts) == 4
    assert sm.option_for("lesson_vo", opts) is None


def test_real_route_maps_back_to_screen(monkeypatch):
    from hiro.gkms import client, locate

    import pytest

    monkeypatch.delenv("HIRO_GKMS", raising=False)  # conftest 默认关
    inst = locate.find()
    if inst is None:
        pytest.skip(f"本机没装 GKMS：{locate.problem()}")
    s = state()
    rec, _ = sm.record(s)
    c = client.Client(inst)
    try:
        r = c.call("route", record=rec, from_step=rec["from_step"])
    finally:
        c.close()
    now = dict(r["plan"]["route"])[11]
    assert sm.option_for(now, s.page_fields["options"]) is not None


def test_i9_no_total_defers():
    s = state()
    s.produce["schedule"].pop("total")
    rec, why = sm.record(s)
    assert rec is None and "总周数" in why


def test_i9_empty_week_defers():
    s = state()
    for w in s.produce["schedule"]["weeks"]:
        if w["week"] == 13:
            w["options"] = []
    rec, why = sm.record(s)
    assert rec is None and "第 13 周" in why
