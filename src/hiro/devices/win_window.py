"""DMM 版游戏窗口（Windows）的客户区尺寸：读和设。规则（用户 11-14「屏幕尺寸根据显示屏的规模来」，github-0a 定比例）：
客户区宽高比 9:16；高 = 主显示器工作区高（去掉任务栏）− 窗口边框和标题栏，宽按 9:16；上限 1080x1920，下限 540x960
（算出来比下限还小，开局检查停下报告）。不改系统缩放 / 分辨率。

窗口小了截图要放大，小字 OCR 和模板分数会掉（541x961 时「残りターン」0.50、牌组按钮 0.78，都在阈值以下）。
游戏以管理员运行，改它的窗口尺寸要以管理员身份调用（UIPI）。尺寸按物理像素算（进程先设成按显示器 DPI 感知），
和 MaaFwWin32Device 原生尺寸截图（screen.native）同一个口径。

    python -m hiro.devices.win_window            # 看现在的客户区尺寸和按显示器算的目标
    python -m hiro.devices.win_window fit        # 调成目标尺寸（要管理员）
    python -m hiro.devices.win_window 600 1067   # 调成指定尺寸（要管理员）
"""

from __future__ import annotations

import sys

MIN_SIZE, MAX_SIZE = (540, 960), (1080, 1920)
WINDOW_CLASS, WINDOW_TITLE = "UnityWndClass", "gakumas"


def _user32():
    import ctypes

    u = ctypes.windll.user32
    try:
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2：拿物理像素
    except Exception:  # noqa: BLE001  老系统没有这个函数
        pass
    return u


def find(title: str = WINDOW_TITLE) -> int | None:
    h = _user32().FindWindowW(WINDOW_CLASS, title)
    return int(h) or None


def client_size(hwnd: int) -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    _user32().GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def frame_size(hwnd: int) -> tuple[int, int]:
    """窗口外框比客户区多出的宽、高（边框 + 标题栏，物理像素）。"""
    import ctypes
    from ctypes import wintypes

    u = _user32()
    win, cli = wintypes.RECT(), wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(win))
    u.GetClientRect(hwnd, ctypes.byref(cli))
    return (win.right - win.left) - (cli.right - cli.left), (win.bottom - win.top) - (cli.bottom - cli.top)


def work_area() -> tuple[int, int, int, int]:
    """主显示器工作区（去掉任务栏）(left, top, right, bottom)，物理像素。"""
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    _user32().SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def fit_size(work_height: int, frame_height: int) -> tuple[int, int] | None:
    """按规则算目标客户区：高 = 工作区高 − 外框高，宽按 9:16，夹到上限；比下限小返回 None。"""
    h = min(work_height - frame_height, MAX_SIZE[1])
    w = round(h * 9 / 16)
    return (w, h) if (w, h) >= MIN_SIZE and h >= MIN_SIZE[1] else None


def target_size(hwnd: int) -> tuple[int, int] | None:
    _, top, _, bottom = work_area()
    return fit_size(bottom - top, frame_size(hwnd)[1])


def set_client_size(hwnd: int, width: int, height: int) -> tuple[int, int]:
    """把客户区设成 width x height；窗口挪到工作区左上角（保证整个窗口在屏幕内）。返回设完的客户区尺寸。"""
    dw, dh = frame_size(hwnd)
    left, top, _, _ = work_area()
    swp_nozorder, swp_noactivate = 0x0004, 0x0010
    _user32().SetWindowPos(hwnd, None, left, top, width + dw, height + dh, swp_nozorder | swp_noactivate)
    return client_size(hwnd)


def center_pos(win_w: int, win_h: int, work: tuple[int, int, int, int]) -> tuple[int, int]:
    """窗口（外框尺寸 win_w x win_h）放在工作区正中的左上角坐标；放不下时贴工作区左上角，保证标题栏在屏幕内。"""
    left, top, right, bottom = work
    return max(left, left + (right - left - win_w) // 2), max(top, top + (bottom - top - win_h) // 2)


def center(title: str = WINDOW_TITLE) -> tuple[int, int] | None:
    """把游戏窗口挪到主显示器工作区正中并提到最前（尺寸不变），让人能看剧情（hiro.loop.unread_story）。
    DMM 后台点击会把窗口挪到点击点落在光标下的位置（hiro.loop.win_desktop），之后点击时会再挪回去，这里不用还原。
    要管理员（游戏以管理员运行）。找不到窗口返回 None。"""
    import ctypes
    from ctypes import wintypes

    hwnd = find(title)
    if hwnd is None:
        return None
    u = _user32()
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = center_pos(r.right - r.left, r.bottom - r.top, work_area())
    swp_nosize, swp_showwindow = 0x0001, 0x0040
    u.SetWindowPos(hwnd, 0, x, y, 0, 0, swp_nosize | swp_showwindow)  # HWND_TOP
    return x, y


def ensure(size: tuple[int, int] | None = None, title: str = WINDOW_TITLE) -> tuple[tuple[int, int], tuple[int, int]]:
    """客户区不是 size（缺省按显示器算的目标）就改；返回（改前, 改后）。找不到窗口 / 目标比下限小抛 RuntimeError。"""
    hwnd = find(title)
    if hwnd is None:
        raise RuntimeError(f"找不到游戏窗口（类 {WINDOW_CLASS}、标题 {title!r}）")
    size = size or target_size(hwnd)
    if size is None:
        raise RuntimeError(f"显示器工作区 {work_area()} 放不下 {MIN_SIZE[0]}x{MIN_SIZE[1]} 的游戏画面")
    before = client_size(hwnd)
    after = before if before == tuple(size) else set_client_size(hwnd, *size)
    return before, after


def main(argv: list[str]) -> int:
    hwnd = find()
    if hwnd is None:
        print("找不到游戏窗口")
        return 2
    if argv:
        want = None if argv[0] == "fit" else (int(argv[0]), int(argv[1]))
        before, after = ensure(want)
        print(f"client {before[0]}x{before[1]} -> {after[0]}x{after[1]}（目标 {want or target_size(hwnd)}）")
        return 0 if after == (want or target_size(hwnd)) else 1
    w, h = client_size(hwnd)
    print(f"client {w}x{h}，work area {work_area()}，frame {frame_size(hwnd)}，target {target_size(hwnd)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
