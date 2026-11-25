"""DMM 实机检查新设备层 hiro.devices 的 DmmWin32Device（要以管理员身份运行：游戏是管理员，普通进程的点击会被 UIPI 挡掉）。

    python tools/devices_live_check.py shots --out <目录>                 只截图：连接、各截图方式测速、存一张 720x1280 截图
    python tools/devices_live_check.py menu --out <目录> --at X,Y [--n 10] 在主页点 ≡ 菜单开 / 关 n 次，比画面变化、窗口位置

menu 只能在游戏主页跑（≡ 开出来的是菜单浮层，再点一次关掉，不改任何游戏数据）；坐标是 720x1280 逻辑坐标，
先用 shots 存下的截图量出来。每一步都存图，报告写 <目录>/report-<阶段>.json。不 import KAA / kotonebot
（和 KAA 截图逐像素对比的 kaa 阶段 2026-11-24 随 KAA 设备层删了）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from hiro.devices.dmm_win32 import DmmWin32Device

import numpy as np


def window_rect(hwnd):
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(r))
    return [r.left, r.top, r.right, r.bottom]


def cursor_pos():
    import ctypes
    from ctypes import wintypes

    p = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return [p.x, p.y]


def is_admin():
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return None


def save(img, path):
    import cv2

    cv2.imwrite(str(path), img)


def mean_diff(a, b, box=None):
    if box:
        x1, y1, x2, y2 = box
        a, b = a[y1:y2, x1:x2], b[y1:y2, x1:x2]
    return round(float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()), 2)


def shots(out: Path, n: int) -> dict:
    rep = {"admin": is_admin(), "methods": {}}
    for method in ("printwindow", "gdi"):
        dev = DmmWin32Device(capture=method)
        row = {}
        try:
            dev.connect()
            ts = []
            for _ in range(n):
                t = time.perf_counter()
                img = dev.screenshot()
                ts.append((time.perf_counter() - t) * 1000)
            row = {
                "ok": True,
                "logical": list(img.shape[1::-1]),
                "native": list(dev.screen.native),
                "orientation": dev.orientation,
                "ms_median": round(sorted(ts)[len(ts) // 2], 1),
                "ms_max": round(max(ts), 1),
                "mean": round(float(img.mean()), 1),
                "alive": dev.alive(),
                "window": window_rect(dev._hwnd),
            }
            save(img, out / f"shot-{method}.png")
        except Exception as e:  # noqa: BLE001
            row = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            dev.close()
        rep["methods"][method] = row
    return rep


def menu(out: Path, at: tuple[int, int], n: int, settle: float) -> dict:
    dev = DmmWin32Device()
    dev.connect()
    x, y = at
    rep = {"admin": is_admin(), "at": [x, y], "rounds": []}
    home = dev.screenshot()
    save(home, out / "menu-0-home.png")
    rect0 = window_rect(dev._hwnd)
    for i in range(1, n + 1):
        before = dev.screenshot()
        c0 = cursor_pos()
        dev.tap(x, y)
        time.sleep(settle)
        opened = dev.screenshot()
        rect_open = window_rect(dev._hwnd)
        dev.tap(x, y)
        time.sleep(settle)
        closed = dev.screenshot()
        row = {
            "i": i,
            "open_diff": mean_diff(before, opened),
            "close_diff_vs_before": mean_diff(before, closed),
            "window_after_tap": rect_open,
            "window_moved_back": window_rect(dev._hwnd) == rect0,
            "cursor_before": c0,
            "cursor_after": cursor_pos(),
        }
        rep["rounds"].append(row)
        if i <= 2 or row["open_diff"] < 5 or row["close_diff_vs_before"] > 5:
            save(opened, out / f"menu-{i}-open.png")
            save(closed, out / f"menu-{i}-closed.png")
        print(json.dumps(row, ensure_ascii=False), flush=True)
    rep["window_start"] = rect0
    rep["passed"] = sum(1 for r in rep["rounds"] if r["open_diff"] >= 5 and r["close_diff_vs_before"] < 5)
    dev.close()
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=("shots", "menu"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--at", default=None, help="menu：≡ 的逻辑坐标 X,Y")
    ap.add_argument("--settle", type=float, default=1.2)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.stage == "shots":
        rep = shots(out, max(5, a.n))
    else:
        if not a.at:
            raise SystemExit("menu 要 --at X,Y")
        rep = menu(out, tuple(int(v) for v in a.at.split(",")), a.n, a.settle)
    (out / f"report-{a.stage}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "rounds"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
