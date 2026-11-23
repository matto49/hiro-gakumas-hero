"""选拔没过后程序要知道培育结束了（11-20 麻央：第 3 次免费再挑戦没生效、LLM 兜底点了次へ、回到首页后程序一直点）。"""

from types import SimpleNamespace as NS

import hiro  # noqa: F401  兼容层（先于 kaa）
from hiro.loop.controller import HiroController


def _box(text, y, x=360):
    return NS(text=text, rect=NS(center=NS(x=x, y=y)))


HOME = [
    _box("プロデュース", 1000),
    _box("コミュ", 1124),
    _box("ガシャ", 1125),
    _box("アイドル", 1176),
    _box("コンテスト", 1176),
    _box("ホーム", 1194),
]


def _ctl(boxes, clock):
    c = object.__new__(HiroController)
    c._boxes = boxes
    return c


def test_home_needs_to_stay(monkeypatch):
    import hiro.loop.controller as ctl

    t = [0.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: t[0])
    c = _ctl(HOME, t)
    assert not c._home_mid_produce()  # 第一次看到：开始计时
    t[0] = 5.0
    assert not c._home_mid_produce()
    t[0] = 9.0
    assert c._home_mid_produce()


def test_not_home_when_resume_dialog_or_in_produce(monkeypatch):
    import hiro.loop.controller as ctl

    t = [0.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: t[0])
    c = _ctl(HOME + [_box("プロデュース再開", 600)], t)  # 日期变更后的再開框：还在培育里
    assert not c._home_mid_produce()
    t[0] = 20.0
    assert not c._home_mid_produce()
    c = _ctl([_box("レッスン", 1000), _box("体力", 60)], t)
    assert not c._home_mid_produce()


def test_retry_that_did_not_start_falls_back(monkeypatch):
    """点了再挑戦、15 秒后结算页的「再挑戦」还在 = 没生效。"""
    import hiro.produce.hif as hif

    st = object.__new__(hif.HiroHifStrategy)
    notes = []
    st.s = NS(note=notes.append)
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(hif, "screen_texts", lambda img: [_box("再挑戦", 1100)])
    assert st._retry_took_effect("qualify-2", wait=0.01) is False and notes
    monkeypatch.setattr(hif, "screen_texts", lambda img: [_box("NOW LOADING", 1200)])
    assert st._retry_took_effect("qualify-2", wait=5) is True
