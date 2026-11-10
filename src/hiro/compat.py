# 来源：digital-hiro hiro/compat.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""让 kotonebot / KAA 在 macOS 上可以导入。

kotonebot 0.20.x 在模块顶层引用了 Windows 专属对象：
- `ctypes.WinDLL` 出现在类方法注解里（nemu_ipc/external_renderer_ipc.py），类定义时就会求值；
- `kotonebot.interop.win._mouse` 无条件 `import mouse`，该库在 Darwin 上直接抛 OSError。
两者在 PlayCover 后端都用不到，这里只做占位，必须在导入 kotonebot 之前执行。
"""

import ctypes
import sys
import types



def default_sim_workers() -> int:
    """模拟进程池的平台缺省上限（0 = 不限；HIRO_SIM_WORKERS 优先，见 hiro.sim.interval.sim_workers）。平台差异只放在这里：
    Windows 默认 1（不开进程池）：spawn 起的子进程会重新导入主模块（python -m hiro.run_hif → KAA、OCR、控制器整套），
    4 个子进程加上 MuMu 把 16GB 内存吃满，Windows 第十五局 MemoryError、MuMu 崩溃（device offline）。Mac / Linux 用 fork，子进程共享内存，不受限。"""
    return 1 if sys.platform == "win32" else 0


if sys.platform == "darwin":
    if not hasattr(ctypes, "WinDLL"):
        ctypes.WinDLL = ctypes.CDLL
    sys.modules.setdefault("mouse", types.ModuleType("mouse"))
