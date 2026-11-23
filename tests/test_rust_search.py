"""任务 ②（考试出牌改 Rust GNRPA 搜索）第 2、3 部分：gakumas_exam_rs.sample_world / search。

- 世界采样只重排看不到的部分：同一局面采样两次，可见部分完全相同、山札顺序不同；采样后的局面能推进到结束。
- 搜索在预算内给出一手合法动作，每个世界投一票；局面结束时没有动作。
局面用轮间评估的 R2 合成开局（hiro.sim.interval），牌组是第十八局本战的。要模拟器主数据和带 search 的 Rust 后端，缺了就跳过。
"""

import time
from collections import Counter

import pytest

pytestmark = pytest.mark.sim_master

DECK = [("p_card-02-men-1_032", 1), ("p_card-02-act-2_062", 1), ("p_card-02-men-3_040", 0), ("p_card-02-ido-3_105", 1), ("p_card-02-men-2_004", 0),
        ("p_card-02-act-1_037", 1), ("p_card-00-sup-3_152", 1), ("p_card-02-men-2_100", 1), ("p_card-02-act-3_186", 0), ("p_card-02-men-1_031", 0),
        ("p_card-02-men-2_052", 1), ("p_card-02-sup-3_094", 1), ("p_card-02-act-3_045", 0), ("p_card-02-men-3_002", 1), ("p_card-02-men-2_056", 1),
        ("p_card-02-men-2_111", 1), ("p_card-02-act-1_004", 0), ("p_card-02-men-1_006", 0), ("p_card-00-sup-3_093", 1), ("p_card-02-men-2_057", 1),
        ("p_card-02-act-3_039", 1), ("p_card-02-men-1_031", 0)]  # fmt: skip
PERMIL = {"Vo": 37490, "Da": 36100, "Vi": 22860}


@pytest.fixture(scope="module")
def rs():
    rs = pytest.importorskip("gakumas_exam_rs")
    if not (hasattr(rs, "search") and hasattr(rs, "plan_many")):
        pytest.skip("装的 Rust 后端没有 search / plan_many（预编译包是旧版本）")
    return rs


@pytest.fixture()
def rx(rs):
    from hiro.sim import interval as iv

    from gakumas_exam.native_battle import maybe_native

    env = maybe_native(iv._env(iv.R2Setup(deck=DECK, drinks=[], hp=20, max_hp=33, permil=PERMIL, star=None), 7))
    if not hasattr(env, "rx"):
        pytest.skip("没有用上 Rust 后端")
    env.rx.advance()
    return env.rx


def _visible(x):
    return (
        x.zone("hand"),
        sorted(x.zone("grave")),
        sorted(x.zone("lost")),
        x.score,
        x.stamina,
        x.current_turn,
        x.drinks,
    )


def test_sample_world_keeps_visible_and_reshuffles_hidden(rs, rx):
    free = list(range(3, 9))
    a, b = rs.sample_world(rx, 2, free, 1), rs.sample_world(rx, 2, free, 2)
    assert _visible(a) == _visible(b) == _visible(rx)
    da, db, d0 = a.zone("deck"), b.zone("deck"), rx.zone("deck")
    assert da[:2] == db[:2] == d0[:2]  # 已公开的山札顶保留
    assert Counter(da) == Counter(db) == Counter(d0) and da != db  # 同一批牌、顺序不同
    t0 = rx.turn_types
    for t in (a.turn_types, b.turn_types):
        assert [t[i] for i in range(len(t)) if i not in free] == [
            t0[i] for i in range(len(t0)) if i not in free
        ]  # 看得到的回合不动
        assert Counter(t[i] for i in free) == Counter(t0[i] for i in free)  # 配额不变
    assert a.rng_state != b.rng_state
    a.random_rollout(5)
    assert a.ended and a.score > 0


def test_search_returns_legal_first_move_within_budget(rs, rx):
    legal = {tuple(x) for x in rx.clone().legal_actions()}
    t = time.monotonic()
    r = rs.search(rx, 0, list(range(rx.current_turn, rx.limit_turn - 3)), worlds=4, seconds=0.5, threads=2, seed=3)
    assert time.monotonic() - t < 2.0
    assert tuple(r["best"]) in legal
    assert sum(n for _, n, _ in r["votes"]) == 4 and len(r["worlds"]) == 4
    assert all(w[2] > 0 for w in r["worlds"])  # 每个世界都跑了续打
    assert r["votes"][0][1] == max(n for _, n, _ in r["votes"])


def test_search_on_ended_exam_has_no_move(rs, rx):
    rx.random_rollout(1)
    assert rs.search(rx, 0, [], worlds=2, seconds=0.2)["best"] is None


def test_drinks_setter_for_rebuild(rs, rx):
    rx.drinks = ["pdrink_02-3-010"]
    assert rx.drinks == ["pdrink_02-3-010"]
    assert {tuple(a) for a in rx.clone().legal_actions() if a[0] == "drink"} == {("drink", 0)}


def test_set_card_upgrade_adjusts_support_part(rs, rx):
    g = rx.zone("hand")[0]
    _cid, up, _ = rx.card_info(g)
    assert rx.set_card_upgrade(g, up + 1) and rx.card_info(g)[1] == up + 1
    assert rx.set_card_upgrade(g, up) and rx.card_info(g)[1] == up
    assert not rx.set_card_upgrade(g, up)  # 没变化


def test_objective_stars_by_rivals(rs, rx):
    """三星目标（偶像之路）：给了对手分数，每个世界返回终局分和星；名次 = 1 + 分数不超过对手的个数。"""
    r = rs.search(rx, 0, [], worlds=2, seconds=0.3, threads=2, seed=5, rivals=[10**12, 1])
    for w in r["worlds"]:
        raw, stars = w[4], w[5]
        assert stars == 2 and raw > 1  # 赢不了 10¹² 那个、赢得了 1 分那个：第 2 名 → ★2
    r = rs.search(rx, 0, [], worlds=2, seconds=0.3, threads=2, seed=5, rivals=[1, 0], lost_ssr_max=-1)
    assert all(w[5] == 2 for w in r["worlds"])  # 第 1 名但通关条件（除外 SSR ≤ −1）不可能满足：★2


def test_objective_pass_rank(rs, rx):
    """过关名次（初前 3、N.I.A 第 1）：名次在线外一律 0 档。N.I.A 第 2 名不算过。"""
    if "pass_rank" not in (rs.search.__text_signature__ or ""):
        pytest.skip("Rust 后端没有 pass_rank")
    r = rs.search(rx, 0, [], worlds=2, seconds=0.3, threads=2, seed=5, rivals=[10**12, 1], pass_rank=1)
    assert all(w[5] == 0 for w in r["worlds"])  # 第 2 名、要第 1：没过
    r = rs.search(rx, 0, [], worlds=2, seconds=0.3, threads=2, seed=5, rivals=[10**12, 1], pass_rank=3)
    assert all(w[5] == 2 for w in r["worlds"])  # 第 2 名、前 3 就过：照旧 ★2


def test_objective_score_cap_and_lesson(rs, rx):
    """分数封顶（初 REGULAR 中間 forceEndScore、课程满分线）：超过封顶的分数不算；课程（没有对手）先看有没有到合格线。"""
    if "score_cap" not in (rs.search.__text_signature__ or ""):
        pytest.skip("Rust 后端没有 score_cap")
    out = rs.plan_many([rx.clone()], seconds=0.2, threads=1, score_cap=5)
    assert out[0][0] == 5 and out[0][1] > 5  # 目标值封顶在 5，原始分照实
    out = rs.plan_many([rx.clone()], seconds=0.2, threads=1, clear_score=1, score_cap=5)
    assert out[0][0] == 10**10 + 5  # 过了合格线：1 档 + 封顶分
    out = rs.plan_many([rx.clone()], seconds=0.2, threads=1, clear_score=10**12)
    assert out[0][0] < 10**10  # 到不了合格线：0 档


def test_plan_many_returns_best_line(rs, rx):
    out = rs.plan_many([rx.clone(), rx.clone()], seconds=0.2, threads=2, rivals=[1])
    assert len(out) == 2 and all(v >= 3 * 10**10 and raw > 1 and st == 3 for v, raw, st in out)


def test_new_card_for_generated_cards(rs, rx):
    """画面上出现引擎牌组里没有的卡（眠気等生成卡）：new_card 新建、set_zone 放进手牌（11-14 Mac 实机整步报「手牌对不上」）。"""
    if not hasattr(rx, "new_card"):
        pytest.skip("Rust 后端没有 new_card")
    g = rx.new_card("p_card-00-acc-0_002", 0)
    rx.set_zone("hand", list(rx.zone("hand")) + [g])
    assert rx.card_info(g)[0] == "p_card-00-acc-0_002" and g in rx.zone("hand")
    with pytest.raises(KeyError):
        rx.new_card("p_card-nope", 0)
