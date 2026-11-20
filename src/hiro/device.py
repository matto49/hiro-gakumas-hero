# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""连接游戏设备，统一给出 720x1280 逻辑坐标的 kotonebot Device。

后端由 HIRO_DEVICE 选（见 resolve_backend / docs）：
- maatools：macOS + hguandl 分支 PlayCover（本模块自己的实现，作者实机验证过的路线）；
- adb / mumu / leidian / dmm：交给 KAA 的设备工厂（kaa.main.kaa.KaaDeviceFactory），与 KAA 支持的平台对齐；
  KAA 后端配置由 hiro.kaa_env.init_kaa 按 HIRO_DEVICE / HIRO_ADB / HIRO_SCREENSHOT / HIRO_EMULATOR_INSTANCE 写入。

Mac 只支持 hguandl/PlayCover 分支：它的 PlayTools 在游戏进程内开 MaaTools TCP 服务（游戏的 PlayCover 设置里打开
maaTools / maaToolsPort），进程内截图、以 UITouch 注入触摸，不要求游戏在前台，也不动鼠标。协议见 hguandl/PlayTools 的
MaaTools.swift，客户端在 hiro.devices.maatools。原版 PlayCover（没有 MaaTools）不再支持：2026-11-13 用户定，
原来退回 Quartz 截图 + CGEventPostToPid 的路线已删除；MaaTools 连不上时报错讲清原因、停下。
"""

from __future__ import annotations

import logging
import os
import sys

from kotonebot.client.device import Device
from kotonebot.client.scaler import PortraitGameScaler
from kotonebot.config.config import conf
from kotonebot.primitives.geometry import Size

# MaaTools 客户端和它的常数挪到了 hiro.devices.maatools（设备统一接口也用它），这里照旧导入，原有用法不变
from hiro.devices.maatools import (  # noqa: F401
    CATALYST_IDIOM_SCALE,
    CLICK_HOLD_SECONDS,
    MAATOOLS_PORT,
    RECONNECT_INTERVAL,
    RECONNECT_TIMEOUT,
    SOCKET_TIMEOUT,
    SWIPE_FPS,
    MaaToolsImpl,
)

logger = logging.getLogger(__name__)


PLAYCOVER_BACKENDS = ("maatools",)
KAA_BACKENDS = {"adb": "custom", "mumu": "mumu12v5", "leidian": "leidian", "dmm": "dmm"}  # HIRO_DEVICE → KAA lifecycle.type
BACKENDS = ("auto", *PLAYCOVER_BACKENDS, *KAA_BACKENDS)
BACKEND: str | None = None  # connect() 实际选中的后端；PlayCover 专用的退路（横屏全屏、真实鼠标点击）看它决定开不开
DEVICE = None  # connect() 建的设备（hiro.loop.game_app 从里面取 adb shell 查前台应用 / 拉起游戏）


def requested_backend() -> str:
    """HIRO_DEVICE（缺省 auto）；写错直接报错，列出可选值。"""
    name = (os.environ.get("HIRO_DEVICE") or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"HIRO_DEVICE={name!r} 不认识；可选：{', '.join(BACKENDS)}")
    return name


def resolve_backend(name: str | None = None, *, platform: str | None = None) -> str:
    """把 auto 落到具体后端：macOS 上是 maatools；其他平台不猜（模拟器 / DMM / 真机都可能），要求显式设 HIRO_DEVICE。"""
    name = name or requested_backend()
    if name != "auto":
        return name
    if (platform or sys.platform) == "darwin":
        return "maatools"
    raise RuntimeError("这台机器不是 macOS，请设 HIRO_DEVICE 选设备：adb（安卓设备 / 其他模拟器）、mumu、leidian、dmm。"
                       "见 AGENTS.md「设备」一节")  # fmt: skip


def is_playcover() -> bool:
    """当前连的是不是 PlayCover（maatools）。还没 connect 时按 HIRO_DEVICE 和平台推断。"""
    if BACKEND is not None:
        return BACKEND in PLAYCOVER_BACKENDS
    try:
        name = requested_backend()
    except ValueError:
        return False
    return name in PLAYCOVER_BACKENDS or (name == "auto" and sys.platform == "darwin")


def fallback_click():
    """控件点了没反应时的兜底点击（produce.ops.Run.real_click）：PlayCover 返回真实鼠标点击（MaaTools 偶尔点不进饮料详情等弹窗），
    其他设备 None（adb / DMM 的点击没有这个问题）。平台判断放在设备层，执行层只问「有没有兜底」。"""
    if not is_playcover():
        return None
    from hiro import real_click

    return real_click.click


def _kaa_config(name: str):
    """KAA 设备工厂要的配置：已 init_kaa 就用当前配置（后端已按环境变量写好）；
    否则（tap 等工具直接 connect）现建一份，后端按环境变量写。"""
    from kaa.config.schema import KaaConfig

    from hiro.kaa_env import kaa_backend_from_env

    try:
        from kaa.kaa_context import conf as kaa_conf

        config = kaa_conf()
        if config.backend.lifecycle.type == KAA_BACKENDS[name]:
            return config
    except Exception:  # noqa: BLE001  KAA 上下文还没初始化
        config = KaaConfig()
    config.backend = kaa_backend_from_env()
    return config


def _keep_swipe_duration(device) -> None:
    """模拟器后端的滑动保留时长。KotoneBot 的 AdbImpl.swipe 会丢掉 duration（日志「Swipe duration is not supported
    with AdbDevice」），快速划过去列表带惯性多滚；Windows + MuMu 第二局每次换卡翻牌组都这样。
    有 MuMu 的 nemu_ipc 触摸就交给它（按 duration 分步移动），否则用 adb 自带的「input touchscreen swipe … 毫秒」。"""
    parts = [getattr(device, a, None) for a in ("_touch", "_multitouch", "commands")]
    nemu = next((c for c in parts if type(c).__name__ == "NemuIpcImpl"), None)
    adb = next((c for c in parts if type(c).__name__ == "AdbImpl"), None)
    if nemu is None and adb is None:
        return
    plain = device.swipe

    def swipe(x1: int, y1: int, x2: int, y2: int, duration: float | None = None, **kw) -> None:
        if not duration:
            return plain(x1, y1, x2, y2, duration, **kw)
        (a1, b1), (a2, b2) = device.scaler.logic_to_physical((x1, y1)), device.scaler.logic_to_physical((x2, y2))
        a1, b1, a2, b2 = int(a1), int(b1), int(a2), int(b2)
        if nemu is not None:
            nemu.swipe(a1, b1, a2, b2, duration)
        else:
            adb.adb.shell(f"input touchscreen swipe {a1} {b1} {a2} {b2} {max(1, round(duration * 1000))}")

    device.swipe = swipe


def _dmm_driver() -> str:
    from hiro.device_maafw import driver

    return driver()


def connect(backend: str | None = None) -> Device:
    """创建并启动连接游戏的设备，逻辑分辨率 720x1280。

    backend：缺省读 HIRO_DEVICE（auto / maatools / adb / mumu / leidian / dmm）。
    adb / mumu / leidian / dmm 交给 KAA 的设备工厂，要求先 hiro.kaa_env.init_kaa（KAA 配置里有后端设置）。
    dmm 另有 HIRO_DMM_DRIVER=maafw（实验，缺省关）：截图 / 点击换成 MaaFramework 的 Win32 控制器（hiro.device_maafw）。
    """
    global BACKEND, DEVICE
    from hiro.vision.ocr import engine as ocr_engine

    if ocr_engine() == "simd":
        from hiro.vision.simd_ocr import install_kotonebot

        install_kotonebot()
    name = resolve_backend(backend)
    conf().device.default_scaler_factory = lambda: PortraitGameScaler()
    conf().device.default_logic_resolution = Size(720, 1280)
    if name == "dmm" and _dmm_driver() == "maafw":
        # 实验（缺省关）：DMM 版换成 MaaFramework 的 Win32 控制器截图 / 点击，见 hiro.device_maafw
        from kotonebot.client.device import WindowsDevice

        from hiro.device_maafw import MaaFwWin32Impl

        device = WindowsDevice()
        impl = MaaFwWin32Impl(device)
        device.setup(screenshot=impl, touch=impl)
    elif name in KAA_BACKENDS:
        from kaa.main.kaa import KaaDeviceFactory

        device = KaaDeviceFactory().create_device_for_config(_kaa_config(name))
        _keep_swipe_duration(device)
    else:
        from kotonebot.client.device import MacOSDevice

        if name != "maatools":
            raise ValueError(f"HIRO_DEVICE={name!r} 不支持")
        device = MacOSDevice()
        maatools = MaaToolsImpl(device)
        device.setup(screenshot=maatools, touch=maatools)
    BACKEND, DEVICE = name, device
    logger.info("设备后端：%s", name)
    try:
        device.start()
    except Exception as e:  # kotonebot 把连不上包成它自己的 DeviceConnectRefusedError
        if name != "maatools":
            raise
        raise RuntimeError(f"连不上 MaaTools（127.0.0.1:{MAATOOLS_PORT}）：Mac 只支持 hguandl 分支的 PlayCover，"
                           "要在游戏的 PlayCover 设置里打开 maaTools，并且第一次从 PlayCover 界面启动游戏。"
                           f"原版 PlayCover 不支持（{e}）") from e
    device.screenshot()  # 缩放器要靠首张截图确定物理分辨率，之后才能换算点击坐标
    return device
