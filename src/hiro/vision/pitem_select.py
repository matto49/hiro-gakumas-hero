# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""H.I.F 考试后的「P 道具选择」页（SELECT_PITEM）：标题「獲得するPアイテムを選択してください」，三个彩色背包，下方「受け取る」。

KAA 与签名都认不出这一页（判为 unknown）。背包里是什么道具在选之前看不到，所以只给颜色、位置和选中态。
- 页面判定：标题白色字形模板（与 hif._match_white 同法）。
- 颜色：背包外框的饱和色（红 / 黄 / 绿）；背景是饱和蓝，按色相排除。
- 选中态：「受け取る」按钮不可点（KAA AcquireBtnDisabled 模板）= 还没选，三个都是 False。
  按钮可点时哪一个被选中，暂按「外框明显比另外两个大」判断——还没有选中态的样本，未验证，判不出给 None。
"""

from hiro.vision.hif import _match_white

import cv2
import numpy as np
from cv2.typing import MatLike

TITLE_BOX = (60, 560, 660, 700)
TITLE_MIN = 0.6
BAG_BOXES = ((40, 740, 250, 980), (255, 740, 465, 980), (470, 740, 690, 980))  # 720x1280，三个背包所在的列
COLORS = {"red": ((0, 10), (170, 180)), "yellow": ((20, 35),), "green": ((40, 80),)}
COLOR_MIN_PX = 1500  # 实测外框 3700–5600 像素，其余颜色 ≤ 3
SELECTED_GROW = 1.08  # 选中的背包外框面积至少比另外两个大这么多（未验证）


def is_select_pitem(img: MatLike) -> bool:
    return _match_white(img, "select_pitem_title", TITLE_BOX)[0] >= TITLE_MIN


def read_pitem_select(img: MatLike, *, button_disabled: bool | None = None) -> dict:
    """→ {"options": [{slot, color, rect[x, y, w, h], center[x, y], selected}], "selected": slot | None, "confirm_enabled": bool | None}。

    button_disabled 可由调用方给出；None 时按模板 InProduce.AcquireBtnDisabled 自己查。"""
    if button_disabled is None:
        from hiro.vision.ui_templates import tpl

        button_disabled = tpl("InProduce.AcquireBtnDisabled").exists(img)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    options = []
    for slot, (x1, y1, x2, y2) in enumerate(BAG_BOXES):
        reg = hsv[y1:y2, x1:x2]
        vivid = (reg[:, :, 1] > 120) & (reg[:, :, 2] > 150)
        best, mask = None, None
        for name, ranges in COLORS.items():
            m = vivid & np.logical_or.reduce([(reg[:, :, 0] >= a) & (reg[:, :, 0] <= b) for a, b in ranges])
            if m.sum() >= COLOR_MIN_PX and (mask is None or m.sum() > mask.sum()):
                best, mask = name, m
        if mask is None:
            options.append({"slot": slot, "color": None, "rect": None, "center": None, "selected": None})
            continue
        ys, xs = np.nonzero(mask)
        rect = [int(xs.min() + x1), int(ys.min() + y1), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
        options.append({"slot": slot, "color": best, "rect": rect, "center": [rect[0] + rect[2] // 2, rect[1] + rect[3] // 2],
                        "selected": False if button_disabled else None})  # fmt: skip
    selected = None
    if not button_disabled:
        areas = [o["rect"][2] * o["rect"][3] if o["rect"] else 0 for o in options]
        top = int(np.argmax(areas))
        others = [a for i, a in enumerate(areas) if i != top]
        if others and areas[top] >= SELECTED_GROW * max(others):
            selected = top
            for o in options:
                o["selected"] = o["slot"] == top
    return {"options": options, "selected": selected, "confirm_enabled": not button_disabled}
