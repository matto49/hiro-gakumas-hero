"""工作派遣选时长：模板认不出时按字点「12時間」（11-27 DMM 实跑：缺省选中带橙色角框，模板 30 秒没认出，整项失败）。"""

from types import SimpleNamespace as NS

from hiro.daily import assignment
from hiro.runtime.frame import Frame
from hiro.vision.ocr import Box, Text

import numpy as np


def _frame(*texts):
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    f.__dict__["texts"] = [Text(t, Box(x - 60, y - 20, x + 60, y + 20)) for t, x, y in texts]
    return f


def test_tap_hours_falls_back_to_text(monkeypatch):
    taps = []
    monkeypatch.setattr(assignment.nav, "find", lambda f, name: None)
    f = _frame(("ミニライブ Lv12", 140, 130), ("4時間", 137, 930), ("8時間", 360, 930), ("12時間", 584, 930))
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), drive=lambda step, timeout, what, every=0.5: step(f))
    assignment._tap_hours(rt, 12)
    assert taps == [(584, 930)]


def test_pass_stops_after_first_empty_screen_once_claimed(monkeypatch):
    """通行证：领到过以后碰到一整屏没有可领的就停，不再拖到底（11-27 用户）。"""
    from hiro.daily import mission_reward as mr

    screens = [[], [1, 1], [], [1], []]  # 第 2 屏能领 2 个，第 3 屏没有：停在这里，第 4 屏那个不该碰（实际不会出现）
    state = {"i": 0, "left": list(screens[0]), "scrolls": 0}

    def claim(rt):
        if state["left"]:
            state["left"].pop()
            return True
        return False

    def scroll(rt, rect, bar, step):
        state["i"] += 1
        state["scrolls"] += 1
        state["left"] = list(screens[state["i"]])

    monkeypatch.setattr(mr, "_enter", lambda *a: None)
    monkeypatch.setattr(mr, "_claim_one_pass", claim)
    monkeypatch.setattr(mr.common, "frame", lambda rt: NS(img=None))
    monkeypatch.setattr(mr.common, "scrollbar", lambda img, rect: NS(position=state["i"] / 10))
    monkeypatch.setattr(mr.common, "scroll_by", scroll)
    rt = NS(sleep=lambda s: None)
    assert mr.claim_pass(rt) == 2 and state["scrolls"] == 2
