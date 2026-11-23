"""手动导航：在 720x1280 逻辑坐标点击，等待后截图保存，便于逐步走页面。

PYTHONPATH=src .venv/bin/python tools/tap.py 360 1161 [--wait 1.5] [--out reports/live/tap.png]
PYTHONPATH=src .venv/bin/python tools/tap.py --shot-only
"""

import argparse
import time

from hiro import imgio
from hiro.tool_device import open_tool_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("x", type=int, nargs="?")
    parser.add_argument("y", type=int, nargs="?")
    parser.add_argument("--wait", type=float, default=1.5)
    parser.add_argument("--out", default="reports/live/tap.png")
    parser.add_argument("--shot-only", action="store_true")
    args = parser.parse_args()
    d = open_tool_device()
    if not args.shot_only:
        d.tap(args.x, args.y)  # 点在 ジュエル / 購入 等危险文字上会被拦下（hiro.runtime.guard）
        time.sleep(args.wait)
    imgio.imwrite(args.out, d.screenshot())
    print(args.out)


if __name__ == "__main__":
    main()
