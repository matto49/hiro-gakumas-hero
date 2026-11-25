"""学マス不在前台（GKMS 第五局 06:45：内存吃紧时进程被杀，前台是 MuMu 安卓桌面）：认出来、按开关拉起、回首页续跑。"""

from types import SimpleNamespace as NS

from hiro.loop import game_app as ga

import numpy as np

LAUNCHER = "  mCurrentFocus=Window{f0866f8 u0 app.lawnchair/app.lawnchair.LawnchairLauncher}\n"
GAME = "  mCurrentFocus=Window{66b76c5 u0 com.bandainamcoent.idolmaster_gakuen/com.google.firebase.MessagingUnityPlayerActivity}\n"


def test_focus_package_parses_dumpsys():
    assert ga.focus_package(LAUNCHER) == "app.lawnchair"
    assert ga.focus_package(GAME) == ga.PACKAGE
    assert ga.focus_package("") is None


def test_foreground_unknown_without_adb():
    assert ga.foreground(None) is None
    assert ga.foreground(lambda cmd: LAUNCHER) is False
    assert ga.foreground(lambda cmd: GAME) is True

    def broken(cmd):
        raise RuntimeError("adb offline")

    assert ga.foreground(broken) is None  # adb 出错：当作不知道，不误判成被杀


def test_relaunch_uses_resolved_activity():
    cmds, state = [], {"focus": LAUNCHER}

    def shell(cmd):
        cmds.append(cmd)
        if cmd.startswith("cmd package resolve-activity"):
            return "priority=0\ncom.bandainamcoent.idolmaster_gakuen/com.google.firebase.MessagingUnityPlayerActivity\n"
        if cmd.startswith("am start"):
            state["focus"] = GAME
            return "Status: ok\nLaunchState: WARM\n"
        return state["focus"]

    assert ga.relaunch(shell) is True
    assert any(
        c == "am start -W -n com.bandainamcoent.idolmaster_gakuen/com.google.firebase.MessagingUnityPlayerActivity"
        for c in cmds
    )
    assert ga.relaunch(lambda cmd: "No activity found") is False


def test_is_landscape():
    assert ga.is_landscape(np.zeros((900, 1600, 3), np.uint8))
    assert not ga.is_landscape(np.zeros((1280, 720, 3), np.uint8))
    assert not ga.is_landscape(None)


def test_enabled_switch_default_off(monkeypatch):
    monkeypatch.delenv("HIRO_RELAUNCH_GAME", raising=False)
    assert not ga.enabled()
    monkeypatch.setenv("HIRO_RELAUNCH_GAME", "1")
    assert ga.enabled()


def test_fallback_landscape_does_not_crop():
    """横屏桌面帧走像素兜底：以前按竖屏坐标裁出空图，cvtColor 断言崩溃。"""
    from hiro.vision.classify import pixel_fallback

    assert pixel_fallback(np.zeros((720, 1280, 3), np.uint8)) is None


def test_recover_to_home_taps_no_boost():
    """拉起后 MuMu 弹「是否为《学マス》开启 MuMu 加速服务?」：点「不加速」，不点「立即加速」。"""
    from hiro.loop import date_change as dc

    clicks, t = [], {"now": 0.0}
    frames = iter([0, 1, 2])
    screens = {0: [("是否为《学マス》开启 MuMu 加速服务？", 1.0, (100, 680, 620, 720)), ("不加速", 1.0, (180, 890, 300, 925)),
                   ("立即加速", 1.0, (440, 890, 580, 925))], 1: [], 2: []}  # fmt: skip
    cur = {"i": 0}

    def shoot():
        cur["i"] = next(frames, 2)
        return cur["i"]

    ok = dc.recover_to_home(shoot, lambda x, y: clicks.append((x, y)), lambda i: screens[i], lambda i: i == 2,
                            clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))  # fmt: skip
    assert ok and clicks[0] == (240, 907)


def test_dmm_window_alive(monkeypatch):
    assert ga.dmm_window_alive(lambda title: 0x1234) is True
    assert ga.dmm_window_alive(lambda title: 0) is False

    def broken(title):
        raise OSError("no user32")

    assert ga.dmm_window_alive(broken) is None


def test_alive_dispatches_by_backend(monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    monkeypatch.setattr(ga, "dmm_window_alive", lambda find=None: False)
    assert ga.is_dmm() and ga.alive() is False
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    assert not ga.is_dmm() and ga.alive() is None  # 没给 device（没有 adb shell）：不知道
    assert ga.alive(NS(shell=lambda cmd: GAME)) is True
