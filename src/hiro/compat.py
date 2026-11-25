"""平台缺省值。原来还给 kotonebot / KAA 在 macOS 上补 ctypes.WinDLL、mouse 占位；去 KAA 后不再需要。"""

import sys


def default_sim_workers() -> int:
    """模拟进程池的平台缺省上限（0 = 不限；HIRO_SIM_WORKERS 优先，见 hiro.sim.interval.sim_workers）。平台差异只放在这里：
    Windows 默认 1（不开进程池）：spawn 起的子进程会重新导入主模块（python -m hiro.run_hif → OCR、控制器整套），
    4 个子进程加上 MuMu 把 16GB 内存吃满，Windows 第十五局 MemoryError、MuMu 崩溃（device offline）。Mac / Linux 用 fork，子进程共享内存，不受限。"""
    return 1 if sys.platform == "win32" else 0
