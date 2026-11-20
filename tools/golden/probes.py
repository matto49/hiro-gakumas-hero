"""逐函数对照用的组合探针（probe.py --call probes:<函数>）：一次跑一组东西，免得每个模板 / 每个参数单独起一遍 Python。"""

from __future__ import annotations

import json
from pathlib import Path

from hiro.vision import ui_templates as ui


def all_templates(img):
    """每个界面模板在这张图上的全部命中（缺省阈值），外加不设阈值时的最高分（看分数本身对不对）。"""
    names = sorted(json.loads((ui.PACK / "templates.json").read_text(encoding="utf-8"))["templates"])
    out = {}
    for n in names:
        t = ui.tpl(n)
        hits = t.find_all(img)
        out[n] = [[list(h.rect), round(h.score, 4)] for h in hits]
    return out


def button_states(img):
    """主按钮亮没亮 / 复选框选没选（找到才判）。"""
    out = {}
    for n in ("InProduce.ButtonUse", "Common.ButtonConfirm", "Common.ButtonNext", "Common.CheckboxUnchecked"):
        m = ui.tpl(n).find(img)
        if m is not None:
            out[n] = [ui.primary_button_enabled(img, m), ui.checkbox_checked(img, m)]
    return out


DDDD_BOXES = [(0, 0, 160, 45), (222, 128, 262, 166), (100, 900, 400, 980), (500, 10, 690, 80), (300, 600, 420, 660), (20, 1180, 700, 1260)]


def dddd_crops(img):
    """ddddocr 在几块固定区域上的原始结果：彩色 PNG 一次、灰度 Otsu 二值图一次（同 digits.read_dddd / dddd_digits 的两种喂法）。"""
    import cv2

    from hiro.vision.digits import _dddd

    out = []
    for x1, y1, x2, y2 in DDDD_BOXES:
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            out.append(None)
            continue
        color = _dddd().classification(cv2.imencode(".png", crop)[1].tobytes())
        g = cv2.cvtColor(cv2.resize(crop, None, fx=2, fy=2), cv2.COLOR_BGR2GRAY)
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        gray = _dddd().classification(cv2.imencode(".png", bw)[1].tobytes())
        out.append([color, gray])
    return out
