"""DMM 实机检查（接 devices_live_check.py，要管理员）：底部导航贴边点击（アイドル ↔ ホーム 3 次）+ 主页横幅滑动
（停 0.3 秒再松手），每步存图、记画面变化。只能在游戏主页跑。用法：python tools/devices_live_navswipe.py <输出目录>
2026-11-14 实测 3/3 导航、2/2 滑动通过（设备层-MaaFW驱动现状.md）。"""

import json
import sys
import time
from pathlib import Path

from hiro.devices.maafw_win32 import MaaFwWin32Device

import cv2
import numpy as np

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def diff(a, b, box=None):
    if box:
        x1, y1, x2, y2 = box
        a, b = a[y1:y2, x1:x2], b[y1:y2, x1:x2]
    return round(float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()), 2)


dev = MaaFwWin32Device()
dev.connect()
rep = {"nav": [], "swipe": []}
home = dev.screenshot()
cv2.imwrite(str(out / "0-home.png"), home)
for i in range(1, 4):
    dev.tap(240, 1150)
    time.sleep(2.0)
    idol = dev.screenshot()
    dev.tap(356, 1160)
    time.sleep(2.0)
    back = dev.screenshot()
    rep["nav"].append({"i": i, "idol_vs_home": diff(home, idol), "back_vs_home": diff(home, back)})
    cv2.imwrite(str(out / f"{i}-idol.png"), idol)
    cv2.imwrite(str(out / f"{i}-back.png"), back)
banner = (450, 120, 700, 240)
for i, (x1, x2) in enumerate(((660, 470), (470, 660)), 1):
    a = dev.screenshot()
    t = time.perf_counter()
    dev.swipe(x1, 180, x2, 180, duration=0.3, hold=0.3)
    took = round(time.perf_counter() - t, 2)
    time.sleep(1.0)
    b = dev.screenshot()
    rep["swipe"].append(
        {
            "i": i,
            "from": x1,
            "to": x2,
            "seconds": took,
            "banner_diff": diff(a, b, banner),
            "rest_diff": diff(a, b, (0, 300, 720, 1000)),
        }
    )
    cv2.imwrite(str(out / f"swipe{i}-before.png"), a)
    cv2.imwrite(str(out / f"swipe{i}-after.png"), b)
(out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
print(json.dumps(rep, ensure_ascii=False))
