"""初 LEGEND（produce-006）的社区规则（GKMS 插件 spec 4.8）。出处见 src/hiro/defaults/hajime_nia_rules.json 的 g8-初L / mk-初L / qa-初L。

社区几条一致的说法：LEGEND 基本不休息、不外出，体力靠早结束课程回；活動支給是主轴；SP 课出现就上；授業补最低的属性。
"""

from hiro.brain.hajime_nia import Status, choose_action, choose_study
from hiro.brain import hajime_nia_rules as hr


def _opt(option, attr=None, sp=None):
    return {"option": option, "attr": attr, "sp": sp}


def _st(**kw):
    base = {"mode": "produce-006", "step": 8, "stamina": 30, "max_stamina": 40, "produce_point": 200,
            "params": {"Vo": 900, "Da": 600, "Vi": 400}}  # fmt: skip
    base.update(kw)
    return Status(**base)


def _pick(st, opts):
    i, reason, rules = choose_action(st, opts)
    return opts[i], reason, rules


def test_legend_tired_still_takes_lesson():
    # 体力少了 18（PRO 规则会让位去休息），LEGEND 照样上课：体力靠早结束课程回
    opts = [_opt("training", "Vo"), _opt("training", "Da"), _opt("training", "Vi"), _opt("rest")]
    o, reason, rules = _pick(_st(stamina=22), opts)
    assert o["option"] == "training"
    assert "hajime.legend_no_rest" in rules and "[g8-初L]" in reason


def test_legend_rests_only_when_lesson_impossible():
    opts = [_opt("training", "Vo"), _opt("rest")]
    o, _, _ = _pick(_st(stamina=5), opts)
    assert o["option"] == "rest"


def test_legend_allowance_over_outing_when_tired():
    opts = [_opt("outing"), _opt("allowance"), _opt("rest")]
    o, reason, rules = _pick(_st(stamina=20), opts)
    assert o["option"] == "allowance"
    assert "hajime.legend_allowance" in rules


def test_legend_sp_first():
    opts = [_opt("training", "Vo", False), _opt("training", "Vi", True)]
    o, _, rules = _pick(_st(), opts)
    assert (o["attr"], o["sp"]) == ("Vi", True) and "hajime.legend_sp_first" in rules


def test_legend_study_fills_lowest_attr():
    choices = [{"index": 0, "attr": "Vo", "cost_hp": 5}, {"index": 1, "attr": "Da", "cost_hp": 5}, {"index": 2, "attr": "Vi", "cost_hp": 5}]
    i, reason, rules = choose_study(_st(), choices)
    assert i == 2 and "hajime.legend_study_lowest" in rules


def test_pro_unchanged_still_rests_when_tired():
    opts = [_opt("training", "Vo"), _opt("rest")]
    o, _, _ = _pick(_st(mode="produce-002", stamina=22), opts)
    assert o["option"] == "rest"


def test_legend_rules_have_sources():
    for rid in ("hajime.legend_no_rest", "hajime.legend_allowance", "hajime.legend_sp_first", "hajime.legend_study_lowest"):
        r = hr.rule(rid)
        assert r["difficulties"] == ["legend"] and r["quote"] and r["source"] in hr.load()["sources"]
