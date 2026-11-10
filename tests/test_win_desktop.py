"""hiro.loop.win_desktop：DMM 后台点击读不到光标（拒绝访问）时等桌面恢复再重做（2026-11-10 dmm1m 21:16 UAC 撞上点击）。"""

import pytest

from hiro.loop import win_desktop as wd


class _Denied(Exception):
    def __init__(self):
        super().__init__(5, "GetCursorPos", "拒绝访问。")


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_access_denied_detects_winerror_5():
    assert wd.access_denied(_Denied())
    assert not wd.access_denied(RuntimeError("x"))


def test_click_waits_for_desktop_then_retries(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(wd.time, "monotonic", clock)
    state = {"denied": 3, "clicks": 0}

    def probe():
        if state["denied"] > 0:
            state["denied"] -= 1
            raise _Denied()

    def click(self, x, y):
        state["clicks"] += 1
        if state["clicks"] == 1:
            raise _Denied()  # 第一次点击撞上 UAC
        return (x, y)

    g = wd.guard(click, probe, name="点击", sleep=clock.sleep, clock=clock)
    assert g(None, 10, 20) == (10, 20)
    assert state["clicks"] == 2 and state["denied"] == 0


def test_gives_up_with_clear_reason():
    clock = _Clock()

    def probe():
        raise _Denied()

    def click(self, x, y):
        raise _Denied()

    g = wd.guard(click, probe, name="点击", timeout=10, sleep=clock.sleep, clock=clock)
    with pytest.raises(wd.DesktopUnavailable, match="UAC"):
        g(None, 1, 2)


def test_other_errors_pass_through():
    def click(self, x, y):
        raise ValueError("别的错")

    g = wd.guard(click, lambda: None, name="点击")
    with pytest.raises(ValueError):
        g(None, 1, 2)


def test_apply_is_noop_off_windows():
    import sys

    if sys.platform == "win32":
        pytest.skip("只在非 Windows 上验证「不装」")
    assert wd.apply() is False


def test_supervised_resume_stops_on_desktop_unavailable(monkeypatch):
    """等满还不行（DesktopUnavailable）：续跑看守不再当崩溃重试，直接 ResumeBlocked 讲清原因。"""
    from hiro import run_hif
    from hiro.loop import resume

    runs = {"n": 0}

    def fake_run(*a, **k):
        runs["n"] += 1
        raise wd.DesktopUnavailable("Windows 桌面 600 秒都不可用（UAC 确认框 …）")

    monkeypatch.setattr(resume, "run", fake_run)
    with pytest.raises(resume.ResumeBlocked, match="UAC"):
        run_hif._supervised_resume("main", "idol", object(), Strategy=object)
    assert runs["n"] == 1
