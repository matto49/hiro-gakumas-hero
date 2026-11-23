"""hiro.tool_device：工具脚本用的设备（统一接口 + 危险点击拦截），以及改过来的工具不再碰 kotonebot / KAA。"""

import os
import re
import subprocess
import sys
from pathlib import Path

from hiro.runtime import guard
from hiro.runtime.frame import Frame
from hiro.runtime.guard import DangerBlocked
from hiro.tool_device import ToolDevice, open_tool_device

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.calls, self.shots = [], 0

    def connect(self):
        self.calls.append(("connect",))

    def screenshot(self):
        self.shots += 1
        return np.full((1280, 720, 3), self.shots, np.uint8)

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def swipe(self, *a, **kw):
        self.calls.append(("swipe", a, kw))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def dev(monkeypatch):
    fake, clock = FakeDevice(), Clock()
    monkeypatch.setattr("hiro.tool_device.open_device", lambda name=None: fake)
    seen = []

    def word_at(frame, x, y):  # 真的 check_tap，只把「点击点上是什么字」换成记下它看的是哪一帧
        seen.append((frame.img[0, 0, 0], x, y))

    monkeypatch.setattr(guard, "word_at", word_at)
    monkeypatch.setattr(Frame, "has_dialog", False)
    d = open_tool_device(clock=clock)
    return d, fake, clock, seen


def test_screenshot_updates_the_frame_the_guard_checks(dev):
    d, fake, _, seen = dev
    assert isinstance(d, ToolDevice) and d.name == "fake" and fake.calls[0] == ("connect",)
    img = d.screenshot()
    d.tap(10, 20)
    assert seen == [(img[0, 0, 0], 10, 20)] and fake.shots == 1  # 画面够新：不补拍，查的就是刚截的那张
    assert fake.calls[-1] == ("tap", 10, 20)


def test_tap_reshoots_when_frame_is_missing_or_stale(dev):
    d, fake, clock, seen = dev
    d.tap(1, 1)  # 还没截过：先补拍
    assert fake.shots == 1
    clock.t = 5.0
    d.tap(2, 2)  # 太旧（超过 guard.FRESH_S）：再补拍
    assert fake.shots == 2 and [s[0] for s in seen] == [1, 2]


def test_blocked_tap_never_reaches_the_device(dev, monkeypatch):
    d, fake, _, _ = dev
    monkeypatch.setattr(guard, "word_at", lambda frame, x, y: "ジュエルで購入")
    d.screenshot()
    with pytest.raises(DangerBlocked):
        d.tap(100, 100)
    assert not any(c[0] == "tap" for c in fake.calls)


def test_swipe_passes_through_with_hold(dev):
    d, fake, _, _ = dev
    d.swipe(360, 900, 360, 450, duration=0.8, hold=0.3)
    assert fake.calls[-1] == ("swipe", (360, 900, 360, 450), {"duration": 0.8, "hold": 0.3})


MIGRATED = [
    "tools/tap.py",
    "tools/check_settings.py",
    "tools/memory_inventory.py",
    "tools/support_inventory.py",
    "tools/play_game.py",
    "src/hiro/memory_scan.py",
    "src/hiro/collect.py",
]


@pytest.mark.parametrize("path", MIGRATED)
def test_migrated_tools_use_the_new_device_layer(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "hiro.device import" not in src and "from hiro import device" not in src
    assert not re.search(r"^\s*(from|import) (kotonebot|kaa)\b", src, re.MULTILINE)
    assert "init_context" not in src and "manual_context" not in src


def test_tool_device_does_not_import_kaa():
    code = (
        "import sys, hiro.tool_device;"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('kotonebot', 'kaa'));"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    env = {
        k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"
    }  # 设了它 hiro.vision 会给 KAA 指主数据目录（KAA 还在时要）
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
