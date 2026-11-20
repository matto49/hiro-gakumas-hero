"""初结算流程（协调 11-17）：True End 条件页点「TAPで次へ」、rt.drive 里的横屏 LIVE（切全屏 / TAP TO START / 演出中不点），
以及低体力课程确认框按最近决策点「開始する / キャンセル」。实机帧在私有 fixtures pages/hajime-*。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

from hiro.contracts import Action, Decision

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _real(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


def _rt(last=None):
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    rt, _ = runtime([fake_frame()])
    rt.session = NS(last_decision=last)
    return rt


def _check(rt, name):
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    return interrupts.check(Frame(_real(name), 0.0), rt)


def test_true_end_page_taps_next():
    rt = _rt()
    assert "TAPで次へ" in _check(rt, "hajime-true-end-dialog.png")
    (x, y), = rt.raw_device.taps
    assert abs(x - 360) < 20 and abs(y - 1147) < 20


def test_tap_next_not_on_cancel_dialogs():
    """有キャンセル的确认框（休み確認 / レッスン開始確認）不归 tap_next_dialog 管。"""
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    rt = _rt()
    assert interrupts.tap_next_dialog(Frame(_real("hajime-lesson-lowhp-confirm.jpg"), 0.0), rt) is None and rt.raw_device.taps == []


def test_lesson_confirm_after_lesson_decision_starts():
    rt = _rt(("ACTION_SELECT", Decision(Action.produce_next("training", attr="Da"), "课程", "rule", 0.8)))
    assert "開始する" in _check(rt, "hajime-lesson-lowhp-confirm.jpg")
    (x, y), = rt.raw_device.taps
    assert abs(x - 515) < 30 and abs(y - 1160) < 20


@pytest.mark.parametrize("last", [None, ("ACTION_SELECT", Decision(Action.produce_next("rest"), "休息", "rule", 0.8))])
def test_lesson_confirm_otherwise_cancels(last):
    rt = _rt(last)
    assert "キャンセル" in _check(rt, "hajime-lesson-lowhp-confirm.jpg")
    (x, y), = rt.raw_device.taps
    assert x < 300 and abs(y - 1160) < 20


def test_class_decision_also_starts():
    rt = _rt(("ACTION_SELECT", Decision(Action.produce_next("class"), "授業", "rule", 0.8)))
    assert "開始する" in _check(rt, "hajime-lesson-lowhp-confirm.jpg")


# ---------------------------------------------------------------- rt.drive 里的横屏 LIVE
class _Guard:
    def __init__(self, active=True):
        self.active, self.fullscreen, self.ticks = active, False, 0

    def tick(self, img):
        self.ticks += 1
        return False

    def in_live(self, img):
        mid = img[img.shape[0] // 8 : img.shape[0] * 7 // 8, img.shape[1] // 8 : img.shape[1] * 7 // 8]
        return self.fullscreen or img.shape[1] > img.shape[0] or float(mid.mean()) < 3.0


def test_drive_taps_tap_to_start_and_does_not_pass_live_to_step():
    """drive 的步骤（结算收尾等竖屏流程）看不到横屏 LIVE 帧：曲目页点 TAP TO START，演出中、黑屏都不交给步骤。"""
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    from hiro.runtime.frame import Frame

    live = _real("hajime-live-tap-to-start.png")
    black = np.zeros((1280, 720, 3), np.uint8)
    done = np.full((1280, 720, 3), 120, np.uint8)
    rt, _ = runtime([fake_frame()])
    frames = iter([Frame(black, 0.0), Frame(live, 0.0), Frame(done, 0.0)])
    rt.screen = NS(shot=lambda: next(frames), frame=None)
    rt.landscape = _Guard()
    seen = []

    def step(f):
        seen.append(f.img.shape)
        return True

    assert rt.drive(step, 30, "结算") is True
    assert seen == [(1280, 720, 3)] and rt.landscape.ticks >= 2
    (x, y), = rt.raw_device.taps
    assert abs(x - 641) < 20 and abs(y - 609) < 20


def test_drive_without_guard_passes_portrait_black_to_step():
    """没有 LandscapeGuard（或没开）的设备：竖屏黑屏照旧交给步骤（不改原行为）。"""
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    from hiro.runtime.frame import Frame

    rt, _ = runtime([fake_frame()])
    rt.screen = NS(shot=lambda: Frame(np.zeros((1280, 720, 3), np.uint8), 0.0), frame=None)
    rt.landscape = _Guard(active=False)
    assert rt.drive(lambda f: "ok", 30, "x") == "ok"


# ---------------------------------------------------------------- LIVE 暂停：全屏里冻住 → 退出全屏 → 「画面タップで再開」
def test_live_paused_resume_tapped():
    rt = _rt()
    assert "画面タップで再開" in _check(rt, "hajime-live-paused.png")
    (x, y), = rt.raw_device.taps
    assert abs(x - 360) < 30 and abs(y - 640) < 20


@pytest.mark.parametrize("name", ["hajime-true-end-dialog.png", "hajime-lesson-lowhp-confirm.jpg", "hajime-live-frozen.png"])
def test_live_resume_not_on_other_pages(name):
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    rt = _rt()
    assert interrupts.live_resume(Frame(_real(name), 0.0), rt) is None


def _frozen_guard(monkeypatch, on: bool):
    from hiro.loop import landscape

    if on:
        monkeypatch.setenv("HIRO_LIVE_FROZEN_EXIT", "1")
    else:
        monkeypatch.delenv("HIRO_LIVE_FROZEN_EXIT", raising=False)
    now = [100.0]
    monkeypatch.setattr(landscape.time, "monotonic", lambda: now[0])
    g = landscape.LandscapeGuard(active=True)
    g.fullscreen, g.entered_at = True, 50.0
    exits = []
    g._exit = lambda: exits.append(now[0]) or True
    return g, now, exits


@pytest.mark.parametrize("on", [True, False])
def test_frozen_fullscreen_live_exits_only_with_switch(monkeypatch, on):
    g, now, exits = _frozen_guard(monkeypatch, on)
    img = _real("hajime-live-frozen.png")
    for _ in range(12):  # 每秒一帧，同一张画面
        g.tick(img)
        now[0] += 1.0
    assert (exits != []) is on
    if on:
        assert exits[0] - 100.0 >= g.FROZEN_S


def test_moving_live_not_frozen(monkeypatch):
    g, now, exits = _frozen_guard(monkeypatch, True)
    a, b = _real("hajime-live-frozen.png"), _real("hajime-live-tap-to-start.png")
    for i in range(20):
        g.tick(a if i % 2 else b)
        now[0] += 1.0
    assert exits == []
