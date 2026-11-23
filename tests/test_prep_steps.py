"""编成第 2 / 3 步翻编成号（hiro.produce.prep_steps.select_set，替代 KAA _select_set）。读编成号打桩，不跑 OCR。"""

from hiro.produce import prep_steps
from hiro.vision.ui_templates import point


def rig(monkeypatch, start, *, moves=True, readable=True):
    state = {"n": start, "now": 0.0}
    nxt, prv = tuple(point("Produce.PointProduceNextSet")), tuple(point("Produce.PointProducePrevSet"))
    taps = []

    def tap(x, y):
        taps.append((x, y))
        if moves:
            state["n"] += 1 if (x, y) == nxt else -1

    monkeypatch.setattr(prep_steps, "set_number", lambda img: state["n"] if readable else None)
    kw = {
        "shoot": lambda: None,
        "tap": tap,
        "sleep": lambda s: state.__setitem__("now", state["now"] + s),
        "clock": lambda: state["now"],
    }
    return kw, taps, nxt, prv


def test_select_set_forward_backward_and_stay(monkeypatch):
    kw, taps, nxt, prv = rig(monkeypatch, 2)
    assert prep_steps.select_set(5, **kw) and taps == [nxt] * 3
    kw, taps, nxt, prv = rig(monkeypatch, 7)
    assert prep_steps.select_set(4, **kw) and taps == [prv] * 3
    kw, taps, *_ = rig(monkeypatch, 4)
    assert prep_steps.select_set(4, **kw) and taps == []


def test_select_set_gives_up(monkeypatch):
    kw, taps, *_ = rig(monkeypatch, 1, readable=False)
    assert not prep_steps.select_set(3, **kw) and taps == []  # 读不出：超时放弃（KAA 原版会一直等）
    kw, taps, _nxt, _ = rig(monkeypatch, 1, moves=False)
    assert not prep_steps.select_set(3, **kw) and len(taps) == 2 * prep_steps.RETRIES  # 点了没反应：重试 3 轮放弃
