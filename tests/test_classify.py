"""去 KAA 第 1 步：hiro.vision.classify（纯函数判页，不依赖 KAA）。和 KAA check_scene 那条链的逐帧对照在 .temp 的 parity 脚本里
（tests/fixtures 533 帧 + Mac 两局 trace 848 帧，0 差异）；这里守住接口和几条约定。"""

import os
import subprocess
import sys

from hiro.vision import classify as c

import numpy as np


def test_landscape_is_live_and_no_input_is_unknown():
    assert c.classify_scene(np.zeros((720, 1280, 3), np.uint8)) == c.PageHit("LIVE", "landscape")
    assert c.classify_scene(None) is c.UNKNOWN
    assert c.pixel_fallback(np.zeros((720, 1280, 3), np.uint8)) is None


def test_loading_frame():
    img = np.zeros((1280, 720, 3), np.uint8)
    img[900:, :] = 128  # 下方有内容，上方 35% 纯色
    assert c.loading(img) and c.template_scene(img) == "LOADING"
    img[50:60, 100:110] = (10, 200, 30)
    img[70:80, 100:110] = (200, 10, 30)
    assert not c.loading(img)


def test_option_buttons_counts_white_bars():
    """授業 / 外出 / 开局二选一的选项按钮：白底长条 60–100 高、350–660 宽（同 KAA CommuEventButtonUI）。"""
    img = np.zeros((1280, 720, 3), np.uint8)
    for y in (500, 650, 800):
        img[y : y + 80, 80:620] = 255
    assert c.option_buttons(img) == 3
    assert c.option_buttons(np.zeros((1280, 720, 3), np.uint8)) == 0


def test_old_entry_maps_new_names(monkeypatch):
    """旧主循环（scene.classify_current）拿到的还是旧名字：开局二选一 → IDLE，横屏 → unknown。"""
    from hiro.vision import scene

    monkeypatch.setattr(
        scene, "device", type("D", (), {"screenshot": staticmethod(lambda: np.zeros((1280, 720, 3), np.uint8))})
    )
    monkeypatch.setattr(scene, "classify_scene", lambda img: c.PageHit("INITIAL_CHOICE", "template"))
    assert scene.classify_current().scene == "IDLE"
    monkeypatch.setattr(scene, "classify_scene", lambda img: c.PageHit("LIVE", "landscape"))
    r = scene.classify_current()
    assert (r.scene, r.area) == ("unknown", "unknown")


def test_classify_imports_no_kaa():
    code = (
        "import sys, hiro.vision.classify; print([m for m in sys.modules if m.split('.')[0] in ('kaa', 'kotonebot')])"
    )
    env = {
        k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"
    }  # 设了它 hiro.vision 会给 KAA 指主数据目录（KAA 还在时要）
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()
    assert out == "[]"
