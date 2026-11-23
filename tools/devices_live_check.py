"""DMM 实机检查新设备层 hiro.devices 的 MaaFwWin32Device（要以管理员身份运行：游戏是管理员，普通进程的点击会被 UIPI 挡掉）。

    python tools/devices_live_check.py shots --out <目录>                 只截图：连接、各截图方式测速、存一张 720x1280 截图
    python tools/devices_live_check.py menu --out <目录> --at X,Y [--n 10] 在主页点 ≡ 菜单开 / 关 n 次，比画面变化、窗口位置
    python tools/devices_live_check.py kaa --out <目录> [--n 5]            同一画面新设备层和 KAA 各截 n 张，比像素、OCR、模板分数

menu 只能在游戏主页跑（≡ 开出来的是菜单浮层，再点一次关掉，不改任何游戏数据）；坐标是 720x1280 逻辑坐标，
先用 shots 存下的截图量出来。每一步都存图，报告写 <目录>/report-<阶段>.json。shots / menu 不 import KAA / kotonebot。

kaa 只截图不点击，要在静止的画面上跑（首页、菜单这类没有动画的页；有动画的页像素差会偏大）：新设备层（MaaFwWin32Device，
GDI 和 FramePool）和 KAA 的 DMM 截图（hiro.device.connect("dmm")，HIRO_DMM_DRIVER 不设，即 KAA 自己的 Windows 截图）
交替各截 n 张，报告每对的平均像素差 / 最大差 / PSNR、两边整帧 OCR 文字的差集、几个首页模板的匹配分数。
游戏局进行中不要跑（两个进程同时截同一个窗口）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from hiro.devices.maafw_win32 import MaaFwWin32Device

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
    for method in ("GDI", "FramePool"):
        dev = MaaFwWin32Device(screencap=method)
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


PARITY_TEMPLATES = (
    "Daily.ButtonHomeCurrent",
    "Common.ButtonToolbarMenu",
    "Daily.ButtonMission",
    "Daily.ButtonPresentsPartial",
)


def _psnr(a, b) -> float | None:
    mse = float(((a.astype(np.float32) - b.astype(np.float32)) ** 2).mean())
    return None if mse == 0 else round(10 * float(np.log10(255.0**2 / mse)), 2)


def _texts(img) -> list[str]:
    from hiro.vision.ocr import screen_texts

    return sorted(t.text for t in screen_texts(img))


def _scores(img) -> dict:
    from hiro.vision.ui_templates import tpl

    out = {}
    for name in PARITY_TEMPLATES:
        m = tpl(name).find(img, threshold=0.0)
        out[name] = None if m is None else round(m.score, 4)
    return out


def kaa_parity(out: Path, n: int) -> dict:
    """同一静止画面：新设备层（GDI / FramePool）和 KAA 的 DMM 截图交替各截 n 张，逐对比较。"""
    from hiro import device as old_device

    import cv2
    from kaa.config.schema import KaaConfig
    from kaa.kaa_context import init as kaa_init

    kaa_init(KaaConfig(), "hiro-parity")
    kaa = old_device.connect("dmm")
    rep = {"admin": is_admin(), "methods": {}}
    try:
        for method in ("GDI", "FramePool"):
            dev = MaaFwWin32Device(screencap=method)
            rows = []
            try:
                dev.connect()
                for i in range(n):
                    a = dev.screenshot()
                    b = kaa.screenshot()
                    b = b if b.shape[:2] == (1280, 720) else cv2.resize(b, (720, 1280))
                    save(a, out / f"kaa-{method}-{i}-own.png")
                    save(b, out / f"kaa-{method}-{i}-kaa.png")
                    row = {"mean_diff": mean_diff(a, b), "max_diff": int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max()),
                           "psnr": _psnr(a, b), "mean_own": round(float(a.mean()), 1), "mean_kaa": round(float(b.mean()), 1), "native": list(dev.screen.native)}  # fmt: skip
                    if i == 0:  # OCR / 模板只比第一对（OCR 慢）
                        ta, tb = _texts(a), _texts(b)
                        row["ocr_only_own"] = sorted(set(ta) - set(tb))
                        row["ocr_only_kaa"] = sorted(set(tb) - set(ta))
                        row["ocr_same"] = len(set(ta) & set(tb))
                        row["tpl_own"], row["tpl_kaa"] = _scores(a), _scores(b)
                    rows.append(row)
                    time.sleep(0.3)
                rep["methods"][method] = {"ok": True, "pairs": rows,
                                          "mean_diff_median": sorted(r["mean_diff"] for r in rows)[len(rows) // 2]}  # fmt: skip
            except Exception as e:  # noqa: BLE001
                rep["methods"][method] = {"ok": False, "error": f"{type(e).__name__}: {e}", "pairs": rows}
            finally:
                dev.close()
    finally:
        try:
            kaa.stop()
        except Exception as e:  # noqa: BLE001  收尾失败不影响报告
            rep["kaa_stop_error"] = repr(e)
    return rep


def menu(out: Path, at: tuple[int, int], n: int, settle: float) -> dict:
    dev = MaaFwWin32Device()
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
    ap.add_argument("stage", choices=("shots", "menu", "kaa"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--at", default=None, help="menu：≡ 的逻辑坐标 X,Y")
    ap.add_argument("--settle", type=float, default=1.2)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.stage == "shots":
        rep = shots(out, max(5, a.n))
    elif a.stage == "kaa":
        rep = kaa_parity(out, max(1, a.n))
    else:
        if not a.at:
            raise SystemExit("menu 要 --at X,Y")
        rep = menu(out, tuple(int(v) for v in a.at.split(",")), a.n, a.settle)
    (out / f"report-{a.stage}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "rounds"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
