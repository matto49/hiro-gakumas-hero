"""P 道具账本 produce.items（8a3a1e7 / 协调 11-18：实机账本一直是空的，搜索引擎里没有 P 道具）：事件真的写进局面、开局放固有道具、
初 / N.I.A 的 P 道具三选一记获得；另：支援能力横幅「Pアイテム獲得時…」不再被判成 SELECT_PITEM。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import Event, GameState
from hiro.loop.events import apply_event

import cv2
import pytest


def _ev(kind, seq=1, origin="observed", **args):
    return Event(seq=seq, kind=kind, origin=origin, args=args)


def test_acquire_written_to_items():
    st, _ = apply_event(GameState(produce={"mode": "produce-002"}), _ev("PITEM_ACQUIRE", pitem_id="pitem_00-3-001-0"))
    assert st.produce["items"] == ["pitem_00-3-001-0"] and st.sources["produce.items"].origin == "screen"
    st, _ = apply_event(st, _ev("PITEM_ACQUIRE", seq=2, origin="action", item_id="pitem_00-3-001-0"))
    assert st.produce["items"] == ["pitem_00-3-001-0"]  # 同一件不重复记
    st, _ = apply_event(st, _ev("PITEM_ACQUIRE", seq=3, pitem_id=None, pitem_ids=["a", "b"]))
    assert st.produce["items"] == ["pitem_00-3-001-0"]  # 同名分不清：不猜


def test_customize_replaces_previous_stage():
    st = GameState(produce={"items": ["pitem_02-3-076-0", "customize_pitem-red"]})
    st, _ = apply_event(
        st, _ev("PITEM_CUSTOMIZE", origin="action", item_id="customize_pitem-red-bear", replaces="customize_pitem-red")
    )
    assert st.produce["items"] == ["pitem_02-3-076-0", "customize_pitem-red-bear"]


def test_named_pitem_pick_emits_acquire():
    from hiro.loop.events import events_from_step

    pf = {
        "pitems": [
            {"name": "はつぼしブレスレット", "pitem_id": "pitem_00-3-001-0"},
            {"name": "はつぼしマスコット", "pitem_id": "pitem_00-3-002-0"},
        ]
    }
    evs = events_from_step(
        "SELECT_PITEM", {"kind": "ActivateEffect", "args": {"pick_indexes": [0]}}, True, pf, {}, seq=5
    )
    assert [(e.kind, e.args.get("item_id")) for e in evs] == [("PITEM_ACQUIRE", "pitem_00-3-001-0")]


@pytest.mark.sim_master
def test_seed_idol_unique_item():
    from hiro.produce.hif import HifCore

    core = NS(profile=NS(idol_id="i_card-shro-3-007"), s=NS(state=GameState(produce={"mode": "produce-002"})))
    HifCore._seed_idol_item(core)
    assert core.s.state.produce["items"] == ["pitem_02-3-076-0"]  # 広（Campus mode!!）的固有道具
    core.s.state.produce["items"] = ["x"]
    HifCore._seed_idol_item(core)
    assert core.s.state.produce["items"] == ["x"]  # 已有账本（续跑打底）不动


def test_banner_not_select_pitem():
    p = Path(__file__).parent / "fixtures" / "pages" / "hajime-pitem-banner-dialogue.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/hajime-pitem-banner-dialogue.jpg")
    from hiro.vision.classify import classify
    from hiro.vision.offline import load

    assert classify(load(cv2.imread(str(p)))).name != "SELECT_PITEM"
