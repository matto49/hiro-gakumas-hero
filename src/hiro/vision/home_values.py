"""首页顶栏读数：PLv、AP（现有 / 上限）、ジュエル（只读，不点）。

720×1280 逻辑画面，顶栏固定位置（2026-11 实机）：左上 PLv 圆标（「PLV」字 + 下面的等级数字）、中间 AP「85/100 +」、
右边ジュエル「15,220 +」。认不出「PLV」字（不在首页、被弹窗 / 模糊遮罩挡住）就返回 None，不去猜被挡住的数字。
读不到的单项是 None。

    python -m hiro.vision.home_values --live [--out 文件]    # 连设备截一张读（控制台 scan-account 任务用）
    python -m hiro.vision.home_values <截图>                  # 离线读一张
"""

from __future__ import annotations

import re

TOP = 115  # 顶栏高度
_AP = re.compile(r"(\d{1,3})\s*/\s*(\d{2,3})")
_NUM = re.compile(r"\d[\d,]*")


def _center(box) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2, (y0 + y1) / 2


def read_home_values(img) -> dict | None:
    from hiro.vision.ocr import ocr_boxes

    h, w = img.shape[:2]
    if w != 720:  # 统一到 720 宽的逻辑坐标
        import cv2

        img = cv2.resize(img, (720, round(h * 720 / w)))
    boxes = ocr_boxes(img[0:TOP, :])
    if not any(t.replace(" ", "").upper() in ("PLV", "PLV.") for t, _, _ in boxes):
        return None
    out: dict = {"plv": None, "ap": None, "ap_max": None, "jewel": None}
    for text, _, box in boxes:
        cx, cy = _center(box)
        t = text.replace(" ", "")
        if 30 <= cx <= 130 and cy >= 70 and t.isdigit():
            out["plv"] = int(t)
        elif 330 <= cx <= 510 and (m := _AP.search(t)):
            out["ap"], out["ap_max"] = int(m.group(1)), int(m.group(2))
        elif 540 <= cx <= 710 and (m := _NUM.search(t)):
            out["jewel"] = int(m.group(0).replace(",", ""))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    import time
    from pathlib import Path

    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true")
    g.add_argument("image", nargs="?")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.live:
        from hiro.tool_device import open_tool_device

        img = open_tool_device().screenshot()
    else:
        import cv2

        img = cv2.imread(a.image)
    vals = read_home_values(img)
    if vals is None:
        print("不在首页，或者首页被弹窗挡住了：先关掉弹窗、回到首页再读", file=sys.stderr)
        return 1
    print(json.dumps(vals, ensure_ascii=False))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), **vals}, ensure_ascii=False),
                               encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
