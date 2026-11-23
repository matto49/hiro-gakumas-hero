"""设备统一接口（去 KAA，设计稿第 4 节）：不依赖 kotonebot / KAA 的三个设备实现。

    from hiro.devices import open_device
    dev = open_device()          # 按 HIRO_DEVICE 选：maatools（Mac）、dmm（MaaFramework）、adb / mumu / leidian（adb）
    dev.connect()
    img = dev.screenshot()       # 720x1280 BGR（横屏 1280x720）
    dev.tap(360, 640)

现在还没有调用方：主循环仍走 hiro.device.connect()（kotonebot 设备）。新主循环接上之前这里不改变任何行为。
接口说明见 hiro.devices.base。
"""

from __future__ import annotations

import os
import sys

from .base import LANDSCAPE, PORTRAIT, Device, DeviceError, DeviceGone, DeviceUnavailable, LogicalScreen  # noqa: F401

NAMES = {"maatools": "maatools", "dmm": "maafw-win32", "adb": "adb", "mumu": "adb", "leidian": "adb"}


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
    if impl == "maafw-win32":
        from .maafw_win32 import MaaFwWin32Device

        return MaaFwWin32Device()
    from .adb import AdbDevice

    return AdbDevice()
