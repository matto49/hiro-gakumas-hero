"""在 Windows 上试 MaaFramework 的 Win32 截图方式能不能截到 DMM 版学マス（缺省只截图，不点）。

    python tools/maafw_probe.py [输出目录]                                    # 每种截图方式各截 5 张，报尺寸、平均亮度、耗时
    python tools/maafw_probe.py 输出目录 --click 976,1816 --input SendMessageWithWindowPos   # 点一下（坐标按原始窗口像素）

    python tools/maafw_probe.py 输出目录 --harness --input SendMessageWithWindowPos  # 不碰游戏：点自己开的测试窗口，量落点精度

点击会真的点到游戏，只在知道这一下点了什么时用（比如打开再关上一个菜单）；游戏以管理员运行，点击要以管理员身份跑。
--harness 开一个 540×960 的 Win32 测试窗口（普通权限即可），用指定输入方式点 15 个已知位置，每次记下窗口收到的消息坐标和那一刻
真实光标的位置（DMM 版游戏认的是真实光标），报两者相对目标的偏差（像素）。会挪测试窗口、可能动光标：别在有对局时跑。
要先 pip install maafw pillow。结果写 输出目录/report.json，每种方式的截图写 <方式>.png（全黑说明这种方式截不到）。
"""

import argparse
import ctypes
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

METHODS = ("FramePool", "PrintWindow", "GDI", "ScreenDC", "DXGI_DesktopDup_Window", "DXGI_DesktopDup")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="maafw-probe")
    ap.add_argument("--click", default=None, help="x,y（原始窗口像素）：点一下，前后各截一张")
    ap.add_argument("--input", default="SendMessageWithWindowPos")
    ap.add_argument("--screencap", default="FramePool", help="点击测试时用的截图方式")
    ap.add_argument("--harness", action="store_true", help="点测试窗口量落点精度，不碰游戏")
    ap.add_argument(
        "--targets",
        default="grid",
        choices=("grid", "edges"),
        help="grid：窗口中部 15 个点；edges：贴四边 / 四角的点（离边 2–20 像素）",
    )
    ap.add_argument(
        "--cursor-at",
        default="",
        help="每点一下前先把真实光标放到屏幕的这些位置轮流试（x,y;x,y…，屏幕物理像素），看挪窗口对准光标时窗口会不会被系统限位",
    )
    a = ap.parse_args()
    if a.harness:
        return harness(a)
    from hiro.device_maafw import find_window, input_method, screencap_flags

    from maa.controller import Win32Controller
    from maa.define import MaaWin32InputMethodEnum as I
    from maa.define import MaaWin32ScreencapMethodEnum as S
    from maa.toolkit import Toolkit
    from PIL import Image

    os.makedirs(a.out, exist_ok=True)
    Toolkit.init_option(a.out)
    win = find_window(Toolkit)
    rep = {"admin": bool(ctypes.windll.shell32.IsUserAnAdmin()), "hwnd": int(win.hwnd), "methods": {}}

    def ctrl(screencap, inp):
        c = Win32Controller(
            win.hwnd,
            screencap_method=screencap_flags(screencap, S),
            mouse_method=input_method(inp, I),
            keyboard_method=input_method(inp, I),
        )
        c.set_screenshot_use_raw_size(True)
        return c, bool(c.post_connection().wait().succeeded)

    def save(img, name):
        Image.fromarray(img[:, :, ::-1]).save(os.path.join(a.out, name))

    if a.click:
        x, y = (int(v) for v in a.click.split(","))
        c, ok = ctrl(a.screencap, a.input)
        before = c.post_screencap().wait().get()
        t = time.perf_counter()
        job = c.post_click(x, y).wait()
        ms = (time.perf_counter() - t) * 1000
        time.sleep(1.5)
        after = c.post_screencap().wait().get()
        save(before, "click_before.png")
        save(after, "click_after.png")
        diff = float(abs(after.astype(int) - before.astype(int)).mean())
        rep["click"] = {
            "input": a.input,
            "xy": [x, y],
            "connected": ok,
            "succeeded": bool(job.succeeded),
            "ms": round(ms, 1),
            "diff": round(diff, 2),
        }
        print(json.dumps(rep["click"], ensure_ascii=False))
    else:
        for name in METHODS:
            r = {}
            try:
                c, r["connected"] = ctrl(name, "SendMessage")
                ts, img = [], None
                for _ in range(5):
                    t = time.perf_counter()
                    j = c.post_screencap().wait()
                    ts.append((time.perf_counter() - t) * 1000)
                    if j.succeeded:
                        img = j.get()
                if img is not None:
                    r.update(shape=list(img.shape), mean=round(float(img.mean()), 1), ms=round(sorted(ts)[2], 1))
                    save(img, f"{name}.png")
            except Exception as e:  # noqa: BLE001
                r["error"] = f"{type(e).__name__}: {e}"[:200]
            rep["methods"][name] = r
            print(name, json.dumps(r, ensure_ascii=False), flush=True)
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)


def harness(a):
    """测试窗口：纯 Win32 窗口（Tk 不认合成的鼠标消息，换成自己的窗口过程），收到按下时记消息坐标（lParam）和那一刻
    真实光标相对客户区的位置。消息循环在主线程，MaaFramework 在另一个线程里点。"""
    import threading
    from ctypes import wintypes

    from hiro.device_maafw import input_method, screencap_flags

    from maa.controller import Win32Controller
    from maa.define import MaaWin32InputMethodEnum as I
    from maa.define import MaaWin32ScreencapMethodEnum as S
    from maa.toolkit import Toolkit

    os.makedirs(a.out, exist_ok=True)
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    # 按物理像素建窗口：不声明的话 150% 缩放下窗口按逻辑像素算，MaaFramework 按物理像素点，坐标差 1.5 倍
    u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    WM_DESTROY, WM_LBUTTONDOWN, WM_POINTERDOWN = 0x0002, 0x0201, 0x0246
    W, H = 540, 960
    hits = []

    def cursor_client(hwnd):
        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.ScreenToClient(hwnd, ctypes.byref(pt))
        return [pt.x, pt.y]

    def proc(hwnd, msg, wp, lp):
        if msg == WM_LBUTTONDOWN:
            x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
            hits.append({"kind": "mouse", "msg": [x, y], "cursor": cursor_client(hwnd)})
            return 0
        if msg == WM_POINTERDOWN:  # 屏幕坐标
            pt = wintypes.POINT(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
            u32.ScreenToClient(hwnd, ctypes.byref(pt))
            hits.append({"kind": "pointer", "msg": [pt.x, pt.y], "cursor": cursor_client(hwnd)})
        if msg == WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    wndproc = WNDPROC(proc)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    hinst = k32.GetModuleHandleW(None)
    wc = WNDCLASSW(
        0, wndproc, 0, 0, hinst, None, u32.LoadCursorW(None, 32512), ctypes.c_void_p(6), None, "HiroMaafwHarness"
    )
    u32.RegisterClassW(ctypes.byref(wc))
    rc = wintypes.RECT(0, 0, W, H)
    style = 0x00CF0000 | 0x10000000  # WS_OVERLAPPEDWINDOW | WS_VISIBLE
    u32.AdjustWindowRect(ctypes.byref(rc), style, False)
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    hwnd = u32.CreateWindowExW(
        0x00000008,
        "HiroMaafwHarness",
        "hiro-maafw-harness",
        style,
        200,
        40,  # WS_EX_TOPMOST
        rc.right - rc.left,
        rc.bottom - rc.top,
        None,
        None,
        hinst,
        None,
    )
    if a.targets == "edges":
        targets = (
            [(d, d) for d in (2, 6, 12, 20)]
            + [(W - 1 - d, d) for d in (2, 12)]
            + [(d, H - 1 - d) for d in (2, 12)]
            + [(W - 1 - d, H - 1 - d) for d in (2, 12)]
            + [(W // 2, 3), (W // 2, H - 4), (3, H // 2), (W - 4, H // 2)]
        )
    else:
        targets = [(x, y) for y in (60, 300, 480, 700, 900) for x in (40, 270, 500)]
    cursors = [tuple(int(v) for v in c.split(",")) for c in a.cursor_at.split(";") if c.strip()] or [None]
    rep = {"input": a.input, "hwnd": int(hwnd or 0), "targets": targets, "cursors": cursors, "hits": []}

    def window_rect():
        r = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        return [r.left, r.top, r.right, r.bottom]

    def run():
        try:
            time.sleep(1.0)
            Toolkit.init_option(a.out)
            c = Win32Controller(
                hwnd,
                screencap_method=screencap_flags("FramePool|GDI", S),
                mouse_method=input_method(a.input, I),
                keyboard_method=input_method(a.input, I),
            )
            c.set_screenshot_use_raw_size(True)
            rep["connected"] = bool(c.post_connection().wait().succeeded)
            for cur in cursors:
                for x, y in targets:
                    if cur is not None:
                        u32.SetCursorPos(*cur)
                        time.sleep(0.15)
                    n = len(hits)
                    ok = c.post_click(x, y).wait().succeeded
                    t0 = time.time()
                    while len(hits) == n and time.time() - t0 < 1.0:
                        time.sleep(0.02)
                    rep["hits"].append(
                        {
                            "target": [x, y],
                            "cursor_at": list(cur) if cur else None,
                            "ok": bool(ok),
                            "got": hits[n] if len(hits) > n else None,
                            "window": window_rect(),
                        }
                    )
        except Exception as e:  # noqa: BLE001
            rep["error"] = f"{type(e).__name__}: {e}"
        finally:
            u32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    threading.Thread(target=run, daemon=True).start()
    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))
    errs = {k: [] for k in ("msg", "cursor")}
    for h in rep["hits"]:
        if h["got"]:
            for k in errs:
                gx, gy = h["got"][k]
                errs[k].append(max(abs(gx - h["target"][0]), abs(gy - h["target"][1])))
    rep["worst"] = sorted(
        ([h["target"], h["cursor_at"], h["got"]] for h in rep["hits"] if h["got"]),
        key=lambda t: -max(abs(t[2]["cursor"][0] - t[0][0]), abs(t[2]["cursor"][1] - t[0][1])),
    )[:5]
    rep["summary"] = {
        "received": sum(1 for h in rep["hits"] if h["got"]),
        "of": len(rep["hits"]),
        **{f"{k}_err_max": (max(v) if v else None) for k, v in errs.items()},
        **{f"{k}_err_mean": (round(sum(v) / len(v), 2) if v else None) for k, v in errs.items()},
        **({"error": rep["error"]} if rep.get("error") else {}),
    }
    print(json.dumps(rep["summary"], ensure_ascii=False))
    with open(os.path.join(a.out, "harness.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
