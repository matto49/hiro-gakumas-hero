"""三选一选卡：从跳转表外的页面（N.I.A 営業话题）进来，页面要防抖几帧；决策前要等候选真的进了 state（11-17 N.I.A：读空停了）。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision  # noqa: F401

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "pages" / "nia-sales-reward-select-card.png"


def test_select_card_waits_for_debounce(tmp_path, monkeypatch):
    if not FIX.exists():
        pytest.skip("需要实机截图 pages/nia-sales-reward-select-card.png")
    import hiro.produce.hif as hif
    from hiro.contracts import TraceWriter
    from hiro.loop.session import Session

    img = cv2.imread(str(FIX))
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: img, click=lambda *a: None))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    session = Session(TraceWriter(name="sel", root=tmp_path), shoot=lambda: img)
    for _ in range(3):  # 前一页：N.I.A 営業话题（稳定）
        session.observe(scene="NIA_SALES_EVENT", img=img, fields={"choices": []}, ready=True)
    seen = []

    class Stop(Exception):
        pass

    def decide(page, state, **kw):
        seen.append([c["card_id"] for c in state.page_fields.get("candidates") or []])
        raise Stop

    core = hif.HifCore.__new__(hif.HifCore)
    core.s, core.deciders, core.rec = session, NS(decide=decide), NS(log=lambda *a, **k: None)
    with pytest.raises(Stop):
        core.on_select_card()
    assert seen and len(seen[-1]) == 3  # 决策时 state 里已经有三张候选


def _residual_core(monkeypatch, ids):
    import time

    import hiro.produce.hif as hif
    import hiro.vision.card_select as cs
    import hiro.vision.extract as ex

    clicks = []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda *a: clicks.append(a)))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(cs, "select_card_options", lambda img: [NS(card=None, rect=NS(center=NS(x=222, y=937)))] * 3)
    monkeypatch.setattr(cs, "recommended_index", lambda img, options: None)
    monkeypatch.setattr(
        ex, "extract", lambda page, img: ({"options": [{"card_id": c, "upgrade": 1} for c in ids]}, None)
    )

    class Stop(Exception):
        pass

    def decide(page, state, **kw):
        raise Stop

    notes = []
    core = hif.HifCore.__new__(hif.HifCore)
    core.deciders, core.rec = NS(decide=decide), NS(log=lambda *a, **k: None)
    core.s = NS(recover=lambda k, d: notes.append((k, d)), observe=lambda **k: None, state=NS(page_fields={}))
    core._last_pick = (("a", "b", "c"), time.monotonic())
    return core, clicks, notes, Stop


def test_select_card_residual_overlay_is_not_picked_again(monkeypatch):
    """mainz3 r3（11-18 04:20）：拿了 イメトレ、获得页过后，「スター性獲得」动画叠在原选卡页上，判成又一次选卡，
    同样三张候选（イメトレ 读成 イメトレ+）又「选」了一次，账本多记一张 イメトレ+。刚拿过、候选完全相同 → 当残留画面，不再选。"""
    core, clicks, notes, _Stop = _residual_core(monkeypatch, ["a", "b", "c"])
    core.on_select_card()
    assert clicks == [core.RESIDUAL_TAP] and notes and notes[0][0] == "page_misjudged"


def test_select_card_different_candidates_still_decides(monkeypatch):
    core, _clicks, _notes, Stop = _residual_core(monkeypatch, ["a", "b", "d"])
    with pytest.raises(Stop):
        core.on_select_card()  # 候选不同：正常决策
