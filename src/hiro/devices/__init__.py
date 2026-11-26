"""设备统一接口（去 KAA，设计稿第 4 节）：不依赖 kotonebot / KAA 的三个设备实现。

    from hiro.devices import open_device
    dev = open_device()          # 按 HIRO_DEVICE 选：maatools（Mac）、dmm（自己调 Win32）、adb / mumu / leidian（adb）
    dev.connect()
    img = dev.screenshot()       # 720x1280 BGR（横屏 1280x720）
    dev.tap(360, 640)

主循环（hiro.runtime.play）开局时用它建设备；旧的 kotonebot 设备 hiro.device 2026-11-24 删了。
接口说明见 hiro.devices.base。
"""

from __future__ import annotations

import os
import sys

from .base import LANDSCAPE, PORTRAIT, Device, DeviceError, DeviceGone, DeviceUnavailable, LogicalScreen  # noqa: F401

NAMES = {"maatools": "maatools", "dmm": "dmm-win32", "adb": "adb", "mumu": "adb", "leidian": "adb"}


def device_name(name: str | None = None, env=None) -> str | None:
    """要用的设备名：name，没有就 HIRO_DEVICE，都没有（或 auto）时按系统定——macOS 是 maatools（PlayCover），
    Windows 是 dmm（DMM 版）；别的系统认不出返回 None。
    G3（11-26 Windows 小白验收）：原来 auto 只认 macOS，桌面版在 Windows 上不设 HIRO_DEVICE 时试连显示「已连上」（doctor 把 auto 当 dmm），
    扫描、开局却抛「这台机器不是 macOS」，小白绕不过去。各处判断「是不是 DMM」都用这里。"""
    env = env if env is not None else os.environ
    key = (name or env.get("HIRO_DEVICE") or "auto").strip().lower()
    if key == "auto":
        return {"darwin": "maatools", "win32": "dmm"}.get(sys.platform)
    return key


def open_device(name: str | None = None) -> Device:
    """按名字（缺省 HIRO_DEVICE；auto 见 device_name）建设备，不连接。"""
    key = device_name(name)
    if key is None:
        raise ValueError("这个系统认不出用哪种游戏设备：请设 HIRO_DEVICE（maatools、dmm、adb、mumu、leidian）")
    impl = NAMES.get(key)
    if impl is None:
        raise ValueError(
            f"HIRO_DEVICE={key!r} 新设备层不支持；可选：{', '.join(NAMES)}（原版 PlayCover 的 quartz 已不支持）"
        )
    if impl == "maatools":
        from .maatools import MaaToolsDevice

        return MaaToolsDevice()
    if impl == "dmm-win32":
        from .dmm_win32 import DmmWin32Device

        return DmmWin32Device()
    from .adb import AdbDevice

    return AdbDevice()
