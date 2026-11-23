"""偶像之路编成决策（TOWER_FORMATION / TOWER_RETRY）：考试 × 主回忆用模拟评估排序，副回忆按倍率公式贪心挑；接近交 LLM；失败重试按差距。
层设置、倍率公式、评估函数都换成假的（只验证决策逻辑；真跑 bench 的对照在离线脚本里）。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.llm import MockLLM
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

LEX = {"tower_id": "tower_001-hmsz", "layer": 26, "mechanic": "exam_lesson_buff", "turns": 16, "border": 500000,
       "exam": {"produce_exam_battle_config_id": "cfg", "produce_exam_gimmick_effect_group_id": "g-0001",
                "produce_exam_battle_npc_group_id": "npc", "produce_item_ids": ["pitem_tower_001-exam_lesson_buff-1-stage_026"]}}  # fmt: skip


@pytest.fixture(autouse=True)
def _fake_tower(monkeypatch):
    from hiro.bench_bridge import tower as tw

    monkeypatch.setenv("HIRO_PRODUCE_LLM", "off")
    monkeypatch.setattr(tw, "layer_mechanics", lambda t, l: ["exam_lesson_buff"])  # noqa: E741
    monkeypatch.setattr(tw, "layer_exam", lambda t, l, m=None: dict(LEX))  # noqa: E741
    monkeypatch.setattr(tw, "score_bonus_permils", lambda cfg, params: [1000 + p for p in params])


def _cands(n=4):
    return [{"index": i, "memory_id": f"m{i}", "params": [1000 + 100 * i, 800, 600], "max_stamina": 60,
             "deck": [f"p_card-01-act-1_00{i}"], "items": ["pitem_00-3-010-0"], "drinks": []} for i in range(n)]  # fmt: skip


def _state(n=4, sub_slots=1):
    return GameState(
        page_fields={
            "tower_id": "tower_001-hmsz",
            "layer": 26,
            "idol_card_id": "i_card-hmsz-3-020",
            "sub_slots": sub_slots,
            "candidates": _cands(n),
        }
    )


CALLS = []


def _eval(table, noise=1000, star_line=530000):
    """假评估：分数 = 表里的值 + 波动；≥ 过关线算第 1，≥ star_line 算三星。"""

    def run(loadouts, seeds, detail=False, workers=1, **kw):
        CALLS.append(len(loadouts) * len(seeds))
        out = []
        for lo in loadouts:
            row = []
            for _i, sd in enumerate(seeds):
                sc = table[lo["produce_cards"][0][-1]] + ((sd * 7) % 5 - 2) * noise
                row.append(
                    {
                        "score": sc,
                        "first": sc >= LEX["border"],
                        "stars": 3 if sc >= star_line else 2 if sc >= LEX["border"] else 1,
                    }
                    if detail
                    else sc
                )
            out.append(row)
        return out

    return run


def test_clear_winner_picked_by_sim(monkeypatch):
    monkeypatch.setattr(
        ProduceDeciders, "TOWER_EVAL", staticmethod(_eval({"0": 300000, "1": 520000, "2": 450000, "3": 200000}))
    )
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", _state())
    assert d.source == "sim" and d.action.kind is ActionKind.SELECT_FORMATION
    a = d.action.args
    assert a["main"] == 1 and a["mechanic"] == "exam_lesson_buff" and a["subs"] and 1 not in a["subs"]
    assert a["subs"] == [3]  # 副回忆按倍率和挑：属性最高的（主回忆除外）
    assert "三星" in d.reason


def test_three_star_rate_beats_higher_mean(monkeypatch):
    """1 号平均分高（90 万 / 30 万交替，均 60 万）但三星只有一半；2 号每局 54.5 万稳定三星：按三星概率选 2 号。"""

    def run(loadouts, seeds, detail=False, workers=1, **kw):
        out = []
        for lo in loadouts:
            m = lo["produce_cards"][0][-1]
            row = []
            for _i, sd in enumerate(seeds):
                sc = {"1": 900000 if sd % 2 else 300000, "2": 545000}.get(m, 100000)
                row.append({"score": sc, "first": sc >= LEX["border"], "stars": 3 if sc >= 540000 else 1})
            out.append(row)
        return out

    monkeypatch.setattr(ProduceDeciders, "TOWER_EVAL", staticmethod(run))
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", _state())
    assert d.action.args["main"] == 2 and d.source == "sim"


def test_cache_reused_until_inventory_changes(monkeypatch):
    monkeypatch.setattr(
        ProduceDeciders, "TOWER_EVAL", staticmethod(_eval({"0": 300000, "1": 520000, "2": 450000, "3": 200000}))
    )
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    CALLS.clear()
    dec.decide("TOWER_FORMATION", _state())
    n = len(CALLS)
    dec.decide("TOWER_FORMATION", _state())
    assert len(CALLS) == n  # 同一层、回忆没变：不重算
    st = _state()
    st.page_fields["candidates"][0]["params"] = [9999, 9999, 9999]  # 回忆强化了
    dec.decide("TOWER_FORMATION", st)
    assert len(CALLS) > n


def test_close_call_goes_to_llm(monkeypatch):
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "tower_formation")
    monkeypatch.setattr(
        ProduceDeciders,
        "TOWER_EVAL",
        staticmethod(_eval({"0": 540000, "1": 541000, "2": 100000, "3": 100000}, noise=40000)),
    )
    llm = MockLLM(replies=[{"choice": 1, "reason": "牌组更合这一层的机关"}])
    d = ProduceDeciders(CAMPUS_HIRO, llm).decide("TOWER_FORMATION", _state())
    assert d.source == "llm" and llm.calls


def test_eval_unavailable_falls_back_by_bonus(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("没有 GKMS")

    monkeypatch.setattr(ProduceDeciders, "TOWER_EVAL", staticmethod(boom))
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", _state())
    assert d.source == "fallback" and d.action.args["main"] == 3 and "模拟评估不可用" in d.reason


def test_missing_candidates_waits():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "TOWER_FORMATION", GameState(page_fields={"tower_id": "x", "layer": 1, "candidates": []})
    )
    assert d.action.kind is ActionKind.WAIT


def test_retry_rules(monkeypatch):
    monkeypatch.setattr(
        ProduceDeciders, "TOWER_EVAL", staticmethod(_eval({"0": 300000, "1": 520000, "2": 450000, "3": 200000}))
    )
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    first = dec.decide("TOWER_FORMATION", _state()).action.args
    base = {"tower_id": "tower_001-hmsz", "layer": 26, "border": 500000, "last_pick": first}
    near = dec.decide("TOWER_RETRY", GameState(page_fields={**base, "attempt": 1, "last_score": 480000})).action.args
    assert near["confirm"] and near["pick"] == first  # 差得不多：同编成再打
    far = dec.decide("TOWER_RETRY", GameState(page_fields={**base, "attempt": 2, "last_score": 200000})).action.args
    assert far["confirm"] and far["pick"]["main"] == 2  # 差得多：换评估排第二的
    stop = dec.decide("TOWER_RETRY", GameState(page_fields={**base, "attempt": 3, "last_score": 480000})).action.args
    assert stop == {"kind": "tower_retry", "confirm": False}


def test_condition_rate_breaks_tie_when_uinet_never_gets_three_stars(monkeypatch):
    """27 层式：UINet 从不刻意满足条件，实测三星都是 0。1 号拿第 1 的概率高但条件从不满足；2 号第 1 少一点但条件一半满足：
    估计三星率（第 1 × 条件）2 号高，选 2 号。"""
    from hiro.bench_bridge import tower as tw

    lex = dict(LEX, star_conditions=[{"text": "除外 SSR ≤ 5", "field": "lost_ssr", "op": "<=", "value": 5}])
    monkeypatch.setattr(tw, "layer_exam", lambda t, l, m=None: dict(lex))  # noqa: E741

    def run(loadouts, seeds, detail=False, workers=1, **kw):
        out = []
        for lo in loadouts:
            m = lo["produce_cards"][0][-1]
            row = []
            for sd in seeds:
                first = {"1": True, "2": sd % 3 != 0}.get(m, False)
                lost = {"1": 8, "2": 4 if sd % 2 else 7}.get(m, 9)
                ok = lost <= 5
                row.append(
                    {
                        "score": 600000 if first else 300000,
                        "first": first,
                        "stars": 2 if first else 1,
                        "conditions": [{"text": "除外 SSR ≤ 5", "field": "lost_ssr", "got": lost, "ok": ok}],
                    }
                )
            out.append(row)
        return out

    monkeypatch.setattr(ProduceDeciders, "TOWER_EVAL", staticmethod(run))
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", _state())
    assert d.action.args["main"] == 2, d.reason


def test_lost_ssr_is_soft_penalty_not_filter(monkeypatch):
    """「除外 SSR ≤ 5」：牌组里打出即除外的 SSR 多的编成照样进模拟（协调 11-14：评估是能控牌的 GNRPA，自己判断）。
    模拟里 1 号（8 张）三星最多 → 选 1 号。"""
    from hiro.bench_bridge import tower as tw
    from hiro.brain import tower as tf

    lex = dict(LEX, star_conditions=[{"text": "除外 SSR ≤ 5", "field": "lost_ssr", "op": "<=", "value": 5}])
    monkeypatch.setattr(tw, "layer_exam", lambda t, l, m=None: dict(lex))  # noqa: E741
    monkeypatch.setattr(tf, "lost_ssr_count", lambda deck: 8 if deck[0].endswith("1") else 3)  # 1 号牌组 8 张
    monkeypatch.setattr(
        ProduceDeciders, "TOWER_EVAL", staticmethod(_eval({"0": 300000, "1": 900000, "2": 450000, "3": 200000}))
    )
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", _state())
    assert d.action.args["main"] == 1 and "不筛" in d.reason, d.reason


def test_lost_ssr_breaks_tie(monkeypatch):
    """模拟结果一样时，打出即除外的 SSR 少的编成排前面（软惩罚）。"""
    from hiro.brain import tower as tf

    a = {"n": 8, "p3": 0.5, "est3": 0.5, "first": 1.0, "margin": None, "mean": 600000, "static_margin": -3}
    b = dict(a, static_margin=2)
    assert tf.rank_key(b) > tf.rank_key(a)


def test_current_formation_uses_shown_bonus(monkeypatch):
    """回忆选择页没接上之前只给当前编成一组：倍率 / 体力按编成页画面读到的，选它，直接挑战。"""
    seen = []

    def run(loadouts, seeds, **kw):
        seen.extend(lo["score_bonus_permils"] for lo in loadouts)
        return [[{"score": 520000, "first": True, "stars": 3}] * len(seeds) for _ in loadouts]

    monkeypatch.setattr(ProduceDeciders, "TOWER_EVAL", staticmethod(run))
    st = _state(1)
    st.page_fields["candidates"][0]["current"] = True
    st.page_fields.update(shown_bonus=[23090, 11980, 2600], shown_stamina=46)
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("TOWER_FORMATION", st)
    assert d.action.args["main"] == 0 and d.source == "sim" and seen[0] == [23090, 11980, 2600]
    assert "倍率按画面" in d.reason
