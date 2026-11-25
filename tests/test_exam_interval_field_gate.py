"""考试引擎：带场面条件的出牌计数触发，只计出牌开始时条件成立的那几次（#43）。

莉波的 P 道具「憧れ続けた輝き」：好調 8 回合以上时，好調类技能卡每用 4 次发动。实机上好調 6、7 回合时打的好調卡不计数，
引擎以前照样计数，到 T3 提前发动、提前抽走了牌。GKMS rev383（card_play_select，HIF-97）同样只计条件成立时的出牌。
Python / Rust 两个后端都要这样。"""

import json

import hiro.sim  # noqa: F401  把 vendor/gakumas-player 放上 sys.path

import pytest

ITEM = "pitem_01-3-312-0"  # 憧れ続けた輝き
TRIGGER = "e_trigger-exam_play_count_interval-4-parameter_buff_up-8-p_card_search-target-effect_group-visible-exam_parameter_buff-000"
CARD = "p_card-01-act-1_002"  # 軽い足取り：好調类，检索命中
PHASE = 23  # ExamPlayCountInterval


def _exam(kocho):
    from gakumas_exam.master import load
    from gakumas_exam.setup import new_exam

    ex = new_exam(None, load(), 1, deck=[(CARD, 0)] * 20, items=[ITEM], turn_types=[1] * 12, stamina=30, plan_type=1)
    ex.status._try_add("TryAddParameterBuffStatus", turn=kocho)
    assert ex.status.GetParameterBuffTurn(False) == kocho
    return ex


def _py_count(ex):
    from gakumas_exam.status import TriggerEffect

    (s,) = [s for s in ex.status.effects if isinstance(s, TriggerEffect) and s.trigger.get("id") == TRIGGER]
    return s.phase_count.get(PHASE, 0)


def _rs_count(rx):
    effects = json.loads(rx.export_json())["status"]["effects"]
    (s,) = [s for s in effects if s.get("trig") and s["trig"]["trigger"]["id"] == TRIGGER]
    return dict(map(tuple, s["trig"]["phase_count"])).get(PHASE, 0)


@pytest.mark.sim_master  # 真跑模拟：要主数据（fetch_master.py）
@pytest.mark.parametrize(("kocho", "counted"), [(6, 0), (7, 0), (8, 1)])
def test_python_counts_only_plays_with_field_condition(kocho, counted):
    ex = _exam(kocho)
    ex.step(("play", 0))
    assert _py_count(ex) == counted


@pytest.mark.sim_master
@pytest.mark.parametrize(("kocho", "counted"), [(6, 0), (7, 0), (8, 1)])
def test_rust_counts_only_plays_with_field_condition(kocho, counted):
    from gakumas_exam import native

    if not native.available():
        pytest.skip("没装 gakumas_exam_rs")
    rx = native.to_native(_exam(kocho))
    rx.step(("play", 0))
    assert _rs_count(rx) == counted


# 「出牌后」版（ExamPlayCountIntervalAfter）：计数在出牌开始时判条件、发动在出牌后判条件。这次出牌没计数（开始时条件不成立），
# 卡牌效果又把条件抬上去时，不能在同一个计数上再发动一次（#44 review）
AFTER_ITEM = "pitem_01-3-315-0"  # 溢れ出る想い：好調 5 回合以上时，メンタル卡每用 3 次（出牌后）
AFTER_CARD = "p_card-01-men-2_098"  # スリリング：メンタル，加好調


def _after_exam():
    from gakumas_exam.master import load
    from gakumas_exam.setup import new_exam

    ex = new_exam(
        None, load(), 1, deck=[(AFTER_CARD, 0)] * 20, items=[AFTER_ITEM], turn_types=[1] * 12, stamina=200, plan_type=1
    )
    ex.status._try_add("TryAddParameterBuffStatus", turn=6)
    for _ in range(3):
        ex.step(("play", 0))
    assert ex.items[0]["_fireCount"] == 1  # 第 3 次发动
    (kocho,) = [s for s in ex.status.effects if s.cls == "ParameterBuffStatusEffect"]
    kocho.turn = 2  # 下一张出牌开始时好調不到 5：不计数；スリリング 打完好調又够了
    return ex


def _play_until(ex, count, n):
    """一直打第一张手牌，直到触发计数到 n（手牌里有生成的非メンタル卡，打了不计数；行动次数用完就结束回合）。"""
    for _ in range(30):
        if count() >= n:
            return
        ex.advance()  # 结束回合后先开下一回合（抽牌）
        ex.step(("play", 0) if ("play", 0) in ex.legal_actions() else ("end",))
    raise AssertionError("计数没到")


def _py_after_count(ex):
    from gakumas_exam.status import TriggerEffect

    (s,) = [s for s in ex.status.effects if isinstance(s, TriggerEffect) and 44 in (s.trigger.get("phaseTypes") or [])]
    return s.phase_count.get(44, 0)


def _rs_after_count(rx):
    effects = json.loads(rx.export_json())["status"]["effects"]
    (s,) = [s for s in effects if s.get("trig") and 44 in s["trig"]["trigger"]["phaseTypes"]]
    return dict(map(tuple, s["trig"]["phase_count"])).get(44, 0)


@pytest.mark.sim_master
def test_python_interval_after_does_not_refire_on_uncounted_play():
    ex = _after_exam()
    ex.step(("play", 0))
    assert ex.status.GetParameterBuffTurn(False) >= 5
    assert ex.items[0]["_fireCount"] == 1
    assert _py_after_count(ex) == 3
    _play_until(ex, lambda: _py_after_count(ex), 6)  # 之后计到 6 照常发动
    assert ex.items[0]["_fireCount"] == 2


@pytest.mark.sim_master
def test_rust_interval_after_does_not_refire_on_uncounted_play():
    from gakumas_exam import native

    if not native.available():
        pytest.skip("没装 gakumas_exam_rs")
    rx = native.to_native(_after_exam())
    fired = lambda: dict(map(tuple, json.loads(rx.export_json())["items"]))[AFTER_ITEM]  # noqa: E731
    rx.step(("play", 0))
    assert fired() == 1
    assert _rs_after_count(rx) == 3
    _play_until(rx, lambda: _rs_after_count(rx), 6)  # 之后计到 6 照常发动
    assert fired() == 2


COST_CARD = "p_card-01-men-2_037"  # 存在感：メンタル，消耗好調 2


def _next(ex, card_id, kocho):
    """下一张出牌前：好調设成 kocho 回合，把 card_id 放到手牌第一张（测试里直接摆）。"""
    ex.advance()
    (k,) = [s for s in ex.status.effects if s.cls == "ParameterBuffStatusEffect"] or [None]
    if k is None:
        ex.status._try_add("TryAddParameterBuffStatus", turn=kocho)
    else:
        k.turn = kocho
    c = next(c for z in ("hand", "deck", "grave") for c in ex.zones[z] if c.card_id == card_id)
    for z in ("hand", "deck", "grave"):
        if c in ex.zones[z]:
            ex.zones[z].remove(c)
    ex.zones["hand"].insert(0, c)


def _uncounted_after_failed_check():
    """计数到 3 的那张出牌开始时好調 6（计数）、付费后好調 4（出牌后条件不成立，不发动）；
    下一张开始时好調 2（不计数），スリリング 打完好調又够了：计数还是 3，也不能发动。返回下一张之前的局面。"""
    from gakumas_exam.master import load
    from gakumas_exam.setup import new_exam

    ex = new_exam(
        None, load(), 1, deck=[(COST_CARD, 0)] * 10 + [(AFTER_CARD, 0)] * 10, items=[AFTER_ITEM], turn_types=[1] * 12,
        stamina=200, plan_type=1,
    )  # fmt: skip

    _next(ex, AFTER_CARD, 20)
    ex.step(("play", 0))
    _next(ex, AFTER_CARD, 20)
    ex.step(("play", 0))
    _next(ex, COST_CARD, 6)
    ex.step(("play", 0))
    assert _py_after_count(ex) == 3 and ex.status.GetParameterBuffTurn(False) < 5
    assert ex.items[0]["_fireCount"] == 0
    _next(ex, AFTER_CARD, 2)
    return ex


@pytest.mark.sim_master
def test_python_interval_after_skips_count_already_checked():
    ex = _uncounted_after_failed_check()
    ex.step(("play", 0))
    assert _py_after_count(ex) == 3 and ex.status.GetParameterBuffTurn(False) >= 5
    assert ex.items[0]["_fireCount"] == 0


@pytest.mark.sim_master
def test_rust_interval_after_skips_count_already_checked():
    from gakumas_exam import native

    if not native.available():
        pytest.skip("没装 gakumas_exam_rs")
    rx = native.to_native(_uncounted_after_failed_check())
    rx.step(("play", 0))
    assert _rs_after_count(rx) == 3
    assert dict(map(tuple, json.loads(rx.export_json())["items"]))[AFTER_ITEM] == 0


def _after_trigger(ex):
    from gakumas_exam.status import TriggerEffect

    (s,) = [s for s in ex.status.effects if isinstance(s, TriggerEffect) and 44 in (s.trigger.get("phaseTypes") or [])]
    return s


def _limit_restored_then_uncounted():
    """计数 3 发动后次数用完（不可用），这期间照样计到 6、不发动；次数加回来后，下一张不计数的出牌也不能在 6 上补发。"""
    ex = _after_exam()  # 第 3 次已发动；之后开始时好調 2
    _after_trigger(ex).limit_count = 0  # 次数用完
    _next(ex, AFTER_CARD, 20)
    _play_until(ex, lambda: _py_after_count(ex), 6)
    assert ex.items[0]["_fireCount"] == 1
    _after_trigger(ex).limit_count = -1  # 次数加回来（如效果 150）
    _next(ex, AFTER_CARD, 2)
    return ex


@pytest.mark.sim_master
def test_python_interval_after_tracks_count_while_unavailable():
    ex = _limit_restored_then_uncounted()
    ex.step(("play", 0))
    assert _py_after_count(ex) == 6 and ex.status.GetParameterBuffTurn(False) >= 5
    assert ex.items[0]["_fireCount"] == 1


@pytest.mark.sim_master
def test_rust_interval_after_tracks_count_while_unavailable():
    from gakumas_exam import native

    if not native.available():
        pytest.skip("没装 gakumas_exam_rs")
    rx = native.to_native(_limit_restored_then_uncounted())
    rx.step(("play", 0))
    assert _rs_after_count(rx) == 6
    assert dict(map(tuple, json.loads(rx.export_json())["items"]))[AFTER_ITEM] == 1
