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


def card_matches(img):
    """卡面图库认卡：获得演出中间的卡面 + 三选一每张候选的卡面 → base asset（认不出 None）；外加三选一的「おすすめ」。"""
    from hiro.vision import card_db
    from hiro.vision.card_select import card_letters, recommended_flags, select_card_options
    from hiro.vision.ui_templates import normalize

    img = normalize(img)
    out = [card_db.match(img[556:750, 262:455])]
    for r in card_letters(img):
        x1, y1, x2, y2 = r.center.x - 60, r.y1 - 105, r.center.x + 60, r.y2
        out.append(None if x1 < 0 or y1 < 0 else card_db.match(img[y1:y2, x1:x2]))
    return {"matches": out, "recommended": recommended_flags(img, select_card_options(img))}


def _digit_boxes():
    from hiro.vision import hif

    return [tuple(hif.BOX_TURNS), tuple(hif.BOX_GENKI), tuple(hif.BOX_HP), tuple(hif.BOX_MY_SCORE), tuple(hif.BOX_PITEM_CHARGES),
            (322, 50, 436, 94), (330, 94, 440, 136), (500, 120, 700, 170)]


def digit_reads(img):
    """游戏字体读数：几块 HUD 框上的 read_text（对齐 / 不对齐）、read_int、read_leading_int、read_score（只要值和读法）。"""
    from hiro.vision import digits

    out = []
    for b in _digit_boxes():
        if img.shape[0] < b[3] or img.shape[1] < b[2]:
            out.append(None)
            continue
        t1 = digits.read_text(img, b)
        t2 = digits.read_text(img, b, align=True)
        lead = digits.read_leading_int(img, b)
        v, m = digits.read_score(img, b)
        out.append({"text": list(t1), "aligned": list(t2), "int": digits.read_int(img, b), "lead": list(lead), "score": v, "method": m["method"]})
    return out
