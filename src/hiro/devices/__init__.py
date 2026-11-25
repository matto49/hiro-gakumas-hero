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


def open_device(name: str | None = None) -> Device:
    """按名字（缺省 HIRO_DEVICE，auto 在 macOS 上是 maatools）建设备，不连接。"""
    key = (name or os.environ.get("HIRO_DEVICE") or "auto").strip().lower()
    if key == "auto":
        if sys.platform != "darwin":
            raise ValueError("这台机器不是 macOS，请设 HIRO_DEVICE：dmm、adb、mumu、leidian")
        key = "maatools"
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
