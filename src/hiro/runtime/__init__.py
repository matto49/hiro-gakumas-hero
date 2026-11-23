"""新主循环（去 KAA 第 6 步）：不 import KAA / kotonebot，设备只用 hiro.devices 的统一接口。

- frame：一拍的画面（截图 + 按需算的文字 / 弹窗读数）
- guard：危险点击拦截（所有点击都过它）
- interrupts：中断层（红线弹窗 / 通信错误 / 维护被踢 / 日切换 / 设置框）
- loop：暂停检查点、wait_until、页面处理器、Runtime（主循环一拍 step、run）

现在还没有调用方：页面处理器（hif.py 的钩子改写）和 run_hif 的接线在后续步骤做。
"""

from hiro.runtime.frame import Frame, Screen
from hiro.runtime.guard import DangerBlocked, GuardedDevice
from hiro.runtime.loop import DONE, STAY, UNKNOWN, Outcome, PageHit, Runtime, stop

__all__ = [
    "DONE",
    "STAY",
    "UNKNOWN",
    "DangerBlocked",
    "Frame",
    "GuardedDevice",
    "Outcome",
    "PageHit",
    "Runtime",
    "Screen",
    "stop",
]
