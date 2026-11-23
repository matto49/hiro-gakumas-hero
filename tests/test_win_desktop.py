"""hiro.loop.win_desktop：DMM 后台点击读不到光标（拒绝访问）时等桌面恢复再重做（2026-11-10 dmm1m 21:16 UAC 撞上点击）。"""

from hiro.loop import win_desktop as wd

import pytest


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


class _Wrapper:
    def __init__(self):
        self.log = []

    def _send_activate(self):
        self.log.append("activate")

    def _align_window(self, x, y):
        pass

    def _send_mouse_button(self, x, y, button, down):
        self.log.append(("down" if down else "up", x, y))
        return True

    def _send_mouse_move(self, x, y, button=None):
        self.log.append(("move", x, y))
        return True


class _Impl:
    def __init__(self):
        self.w = _Wrapper()

    def _require_wrapper(self):
        return self.w


def test_swipe_holds_at_end_before_release():
    """dmm1m 本战相談删卡：拖到终点立刻松手 → 滚动列表惯性多滑、两屏没重叠。改成终点原地停一会儿再松手。"""
    impl = _Impl()
    wd.swipe_hold(impl, 360, 900, 360, 740, duration=1.0, hold=0.3, sleep=lambda s: None)
    log = impl.w.log
    assert log[1] == ("down", 360, 900) and log[-1] == ("up", 360, 740)
    moves = [e for e in log if e[0] == "move"]
    assert moves[59] == ("move", 360, 740)  # 1 秒 60 步匀速移到终点
    tail = moves[60:]
    assert len(tail) == 18 and set(tail) == {("move", 360, 740)}  # 再原地停 0.3 秒（18 帧）才松开


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
def test_post_message_ignores_stale_last_error():
    """11-21：PostMessage 成功时不清错误码，原版按 GetLastError()==0 判成功会误报失败（点击丢「松开」、滑动抛错）。"""
    import win32api
    import win32gui

    class W:
        hwnd = win32gui.GetDesktopWindow()

    win32api.SetLastError(1400)
    assert wd.post_message(W(), 0, 0, 0) is True  # WM_NULL 投给桌面窗口：成功，旧错误码不影响

    class Bad:
        hwnd = 0x7FFFFFF0

    import pywintypes

    with pytest.raises(pywintypes.error):
        wd.post_message(Bad(), 0, 0, 0)  # 真失败：pywin32 自己抛 pywintypes.error
