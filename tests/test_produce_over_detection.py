"""选拔没过后程序要知道培育结束了（11-20 麻央：第 3 次免费再挑戦没生效、LLM 兜底点了次へ、回到首页后程序一直点）。
测旧主循环「培育中回到首页」的两个测试 2026-11-24 随 hiro.loop.controller 删了。"""

from types import SimpleNamespace as NS

import hiro  # noqa: F401  兼容层（先于 kaa）


def _box(text, y, x=360):
    return NS(text=text, rect=NS(center=NS(x=x, y=y)))


def test_retry_that_did_not_start_falls_back(monkeypatch):
    """点了再挑戦、15 秒后结算页的「再挑戦」还在 = 没生效。"""
    import hiro.produce.hif as hif

    st = object.__new__(hif.HifCore)
    notes = []
    st.s = NS(note=notes.append)
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(hif, "screen_texts", lambda img: [_box("再挑戦", 1100)])
    assert st._retry_took_effect("qualify-2", wait=0.01) is False and notes
    monkeypatch.setattr(hif, "screen_texts", lambda img: [_box("NOW LOADING", 1200)])
    assert st._retry_took_effect("qualify-2", wait=5) is True
