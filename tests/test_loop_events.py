"""H1：互验拦读错（第六局 #290 Da=70）、事件改牌组并产生预测、Step 带 events / checks。"""

from hiro.contracts import Event, GameState, Observation, TraceWriter
from hiro.contracts.observation import PageObs
from hiro.loop.events import apply_event, check, events_from_step, gate
from hiro.loop.session import Session


def _obs(seq, params, page="ACTION_SELECT"):
    return Observation(
        seq=seq, t=0.0, page=PageObs(page, "produce", 1.0, 2), fields={"params": params, "step": 12}, source="screen"
    )


def test_param_monotonic_blocks_q6_290(tmp_path):
    """第六局第 12 天选行动页 Da 读成 70（前一次 Da≈1000）：param_monotonic 拦下，Da 不进状态（也就不进决策）。"""
    s = Session(
        TraceWriter(tmp_path),
        shoot=lambda: None,
        classify=lambda: ("ACTION_SELECT", "produce"),
        extract=lambda *a: ({}, 0),
    )
    s.observe(
        scene="ACTION_SELECT",
        fields={"params": {"Vo": 900, "Da": 1004, "Vi": 480}, "step": 13},
        ready=True,
        save_frame=False,
    )
    s.observe(
        scene="ACTION_SELECT",
        fields={"params": {"Vo": 910, "Da": 70, "Vi": 486}, "step": 12},
        ready=True,
        save_frame=False,
    )
    assert s.state.produce["params"]["Da"] == 1004 and s.state.produce["params"]["Vo"] == 910
    assert any("读错拦截：produce.params.Da=70" in n for n in s.notes)
    step = s.record()
    assert any(c.field == "produce.params.Da" and c.verdict == "read_error" for c in step.checks)


def test_gate_only_drops_bad_subkey():
    st = GameState(produce={"params": {"Vo": 900, "Da": 1004, "Vi": 480}})
    o = _obs(2, {"Vo": 910, "Da": 70, "Vi": 486})
    cs = check(st, o, [])
    assert gate(o, cs).fields["params"] == {"Vo": 910, "Vi": 486} and gate(o, cs, st).fields["params"]["Da"] == 1004


def test_apply_event_updates_deck_and_ledger():
    st = GameState(produce={"deck": [{"card_id": "a", "upgrade": 0}, {"card_id": "b", "upgrade": 0}]})
    s2, _ = apply_event(st, Event(seq=1, kind="CARD_CHANGE", origin="action", args={"remove_card_id": "a", "card_id": "c"}),
                        predictor=lambda s, e: [])  # fmt: skip
    assert [c["card_id"] for c in s2.produce["deck"]] == ["b", "c"] and len(s2.ledger) == 1 and st.ledger == []


def test_events_from_select_card():
    ev = events_from_step("SELECT_CARD", {"kind": "ActivateEffect", "args": {"pick_indexes": [1]}}, True,
                          {"candidates": [{"card_id": "x"}, {"card_id": "y", "upgrade": 1}]}, None, seq=5)  # fmt: skip
    assert ev[0].kind == "CARD_ACQUIRE" and ev[0].args == {"card_id": "y", "upgrade": 1}


def test_observed_acquire_pairs_with_action(tmp_path):
    """三选一拿卡（动作事件）+ 获得弹窗（识别事件）是同一件事：牌组只加一张，两条都标 confirmed。"""
    s = Session(
        TraceWriter(tmp_path),
        shoot=lambda: None,
        classify=lambda: ("ACTION_SELECT", "produce"),
        extract=lambda *a: ({}, 0),
    )
    s.state.produce = {"deck": []}
    s.emit(Event(seq=10, kind="CARD_ACQUIRE", origin="action", args={"card_id": "x", "upgrade": 0}))
    s.emit(Event(seq=12, kind="CARD_ACQUIRE", origin="observed", args={"card_id": "x", "upgrade": 0}))
    assert [c["card_id"] for c in s.state.produce["deck"]] == ["x"] and all(e.confirmed for e in s._events)
    s.emit(
        Event(seq=30, kind="CARD_ACQUIRE", origin="observed", args={"card_id": "y", "upgrade": 1})
    )  # 没有对应动作（回忆效果等）
    assert [c["card_id"] for c in s.state.produce["deck"]] == ["x", "y"]


def test_deck_review_replaces_deck():
    st = GameState(produce={"deck": [{"card_id": "a", "upgrade": 0}]}, pending_review=["produce.deck"])
    s2, _ = apply_event(st, Event(seq=1, kind="DECK_REVIEW", origin="review", args={"deck": [{"card_id": "b", "upgrade": 1}]}),
                        predictor=lambda s, e: [])  # fmt: skip
    assert (
        s2.produce["deck"] == [{"card_id": "b", "upgrade": 1}]
        and s2.pending_review == []
        and s2.field_status["produce.deck"] == "verified"
    )


def test_attr_permil_keeps_per_attr():
    """倍率按属性记：说明页填初值；本回合属性变了、倍率没读到时不沿用上一个属性的倍率。"""
    from hiro.loop.reduce import reduce

    st = GameState()
    st = reduce(st, Observation(seq=1, t=0, page=PageObs("HIF_EXAM_BRIEFING", "produce", 1.0, 2),
                                fields={"multiplier_pct": {"Vo": 1413, "Da": 1247, "Vi": 1085}}, source="screen"))  # fmt: skip
    st = reduce(st, Observation(seq=2, t=0, page=PageObs("HIF_EXAM", "produce", 1.0, 2), fields={"attr": "Da", "multiplier_permil": 12470},
                                source="screen"))  # fmt: skip
    assert st.exam["attr_permil"] == {"Vo": 14130, "Da": 12470, "Vi": 10850} and st.exam["multiplier_permil"] == 12470
    st = reduce(
        st, Observation(seq=3, t=0, page=PageObs("HIF_EXAM", "produce", 1.0, 3), fields={"attr": "Vi"}, source="screen")
    )
    assert st.exam["multiplier_permil"] == 10850


def test_brief_permil_cleared_after_exam():
    from hiro.loop.reduce import reduce

    st = reduce(GameState(), Observation(seq=1, t=0, page=PageObs("HIF_EXAM_BRIEFING", "produce", 1.0, 2),
                                         fields={"multiplier_pct": {"Vo": 1413, "Da": 1247, "Vi": 1085}}, source="screen"))  # fmt: skip
    st = reduce(
        st, Observation(seq=2, t=0, page=PageObs("HIF_EXAM", "produce", 1.0, 2), fields={"attr": "Da"}, source="screen")
    )
    st = reduce(
        st, Observation(seq=3, t=0, page=PageObs("HIF_EXAM_RESULT", "produce", 1.0, 2), fields={}, source="screen")
    )
    assert st.exam_session.brief_permil is None


def test_card_change_carries_stage1_pick():
    """换卡阶段 2 的 ChangeCard 只带换出卡；换入卡从阶段 1 选中的候选接过来，记账不再每换一次少一张（Windows 第六局记账 17、实际 20）。"""
    events_from_step("SKILL_CARD_CHANGE_1", {"kind": "ChangeCard", "args": {"stage": 1, "pick_index": 1}}, True,
                     {"candidates": [{"card_id": "x"}, {"card_id": "y", "upgrade": 0}]}, None, seq=7)  # fmt: skip
    ev = events_from_step("SKILL_CARD_CHANGE_2", {"kind": "ChangeCard", "args": {"stage": 2, "remove_card_id": "a", "remove_upgrade": 0}},
                          True, {}, None, seq=8)  # fmt: skip
    assert ev[0].kind == "CARD_CHANGE" and ev[0].args["card_id"] == "y"
    st = GameState(produce={"deck": [{"card_id": "a", "upgrade": 0}, {"card_id": "b", "upgrade": 0}]})
    s2, _ = apply_event(st, ev[0], predictor=lambda s, e: [])
    assert [c["card_id"] for c in s2.produce["deck"]] == ["b", "y"]
    # 阶段 2 取消：换入卡作废，下一次换卡不会误用
    events_from_step("SKILL_CARD_CHANGE_1", {"kind": "ChangeCard", "args": {"stage": 1, "pick_index": 0}}, True,
                     {"candidates": [{"card_id": "x"}]}, None, seq=9)  # fmt: skip
    assert (
        events_from_step(
            "SKILL_CARD_CHANGE_2", {"kind": "ChangeCard", "args": {"cancel": True}}, True, {}, None, seq=10
        )
        == []
    )
    ev2 = events_from_step(
        "SKILL_CARD_CHANGE_2", {"kind": "ChangeCard", "args": {"remove_card_id": "b"}}, True, {}, None, seq=11
    )
    assert "card_id" not in ev2[0].args
