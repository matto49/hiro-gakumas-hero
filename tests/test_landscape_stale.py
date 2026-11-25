"""LandscapeGuard：上一段进程留下的全屏（DeviceGone 停机后 resume）。新进程 fullscreen=False，画面黑且横向 →
当成「横屏 LIVE 黑屏」去点「进入全屏幕」，菜单里只有「退出全屏幕」，每 2 秒失败一次、永远不退（11-25 账号 3）。"""

from hiro.loop import landscape

import numpy as np
import pytest


@pytest.mark.synthetic
def test_stale_fullscreen_black_exits(monkeypatch):
    calls = []
    now = [100.0]
    monkeypatch.setattr(landscape.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(landscape, "_game_pid", lambda: 123)
    monkeypatch.setattr(landscape, "_front_app", lambda: None)
    monkeypatch.setattr(landscape, "_click_menu", lambda pid, items: calls.append(items[0]) or items == landscape.EXIT)
    g = landscape.LandscapeGuard()
    black = np.zeros((720, 1280, 3), np.uint8)
    for _ in range(12):
        g.tick(black)
        now[0] += 2.0
    assert landscape.EXIT[0] in calls  # 进不去全屏就退全屏
    assert calls.count(landscape.ENTER[0]) <= 1  # 不再每拍去点「进入全屏幕」
    assert g.fullscreen is False


@pytest.mark.synthetic
def test_black_landscape_window_still_enters(monkeypatch):
    """原来的场景（窗口不在全屏、横屏 LIVE 黑屏）照旧进全屏。"""
    calls = []
    now = [100.0]
    monkeypatch.setattr(landscape.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(landscape, "_game_pid", lambda: 123)
    monkeypatch.setattr(landscape, "_front_app", lambda: None)
    monkeypatch.setattr(landscape, "_click_menu", lambda pid, items: calls.append(items[0]) or items == landscape.ENTER)
    g = landscape.LandscapeGuard()
    black = np.zeros((720, 1280, 3), np.uint8)
    for _ in range(12):
        g.tick(black)
        now[0] += 2.0
    assert calls[0] == landscape.ENTER[0] and g.fullscreen is True


@pytest.mark.synthetic
def test_empty_frame_budget_outlasts_fullscreen_exit():
    """进全屏后 MaaTools 一直空帧（设备层补黑帧）：LandscapeGuard 要「进全屏 30 秒后 + 黑屏 EXIT_BLACK_S」才退全屏，
    空帧上限原来也是 30 秒，DeviceGone 先到、整局停机（11-25 账号 3 ENDLESS DANCE 结束 LIVE）。空帧上限要比退全屏晚。"""
    from hiro.devices import maatools

    assert maatools.EMPTY_MAX_S > 30 + landscape.LandscapeGuard.EXIT_BLACK_S + 10
