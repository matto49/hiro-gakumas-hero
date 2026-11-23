"""培育中跳过剧情（支援事件等）：游戏不弹获得页，事件给的卡进不了牌组账本 → 把牌组标成待复核，下次选行动页读一次牌组。
（DMM mainverify2 11-17：支援卡 s_card-3-0094 事件给「頭上に注意！」，账本漏记，R1 搜索整场跟丢。）"""

from types import SimpleNamespace as NS

from hiro.contracts import GameState


def _rt(produce):
    events = []
    st = GameState(produce=produce)
    session = NS(state=st, trace=NS(next_seq=lambda: 7), emit=lambda ev: events.append(ev))
    taps = []
    rt = NS(session=session, device=NS(tap=lambda *a: taps.append(a)), _quick_wait=None)
    return rt, events, taps


def test_skipped_commu_in_produce_flags_deck_review():
    from hiro.runtime.play import _quick_tap

    rt, events, taps = _rt({"deck": [{"card_id": "c0", "upgrade": 0}], "step": 2})
    assert _quick_tap(rt, {"page": "commu", "tap": (225, 1200), "fields": {}}, 0.0)
    assert taps == [(225, 1200)]
    assert [e.kind for e in events] == ["UNMODELED_CHANGE"] and events[0].args["field"] == "produce.deck"


def test_commu_flag_once_and_not_outside_produce():
    from hiro.runtime.play import _quick_tap

    rt, events, _ = _rt({"deck": [{"card_id": "c0", "upgrade": 0}]})
    rt.session.state.pending_review = ["produce.deck"]
    _quick_tap(rt, {"page": "commu", "tap": (225, 1200), "fields": {}}, 0.0)
    assert events == []  # 已经在待复核：不重复发
    rt2, events2, _ = _rt({})
    _quick_tap(rt2, {"page": "commu", "tap": (225, 1200), "fields": {}}, 0.0)
    assert events2 == []  # 不在培育里（首页剧情等）：不管
    rt3, events3, _ = _rt({"deck": [{"card_id": "c0", "upgrade": 0}]})
    _quick_tap(rt3, {"page": "ACQUIRE", "tap": (12, 12), "fields": {}}, 0.0)
    assert events3 == []  # 获得页本身会记账


def test_unmodeled_deck_event_sets_pending_review():
    from hiro.contracts import Event
    from hiro.loop.events import apply_event

    st = GameState(produce={"deck": [{"card_id": "c0", "upgrade": 0}]})
    ev = Event(
        seq=1,
        kind="UNMODELED_CHANGE",
        origin="inferred",
        args={"field": "produce.deck", "why": "跳过剧情"},
        basis="observe_only",
    )
    st2, _ = apply_event(st, ev)
    assert "produce.deck" in st2.pending_review and st2.produce["deck"] == st.produce["deck"]
