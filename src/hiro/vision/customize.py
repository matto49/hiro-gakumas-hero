# 来源：digital-hiro src/hiro/vision/customize.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""特別指導（技能卡定制）页：SPECIAL_GUIDANCE（docs/pages-hif.md §4）。按 q10 第 19 天实机帧（reports/live/special-guidance/）写。

两种画面，左上角行动标签都是「特別指導」：
- 选卡（view=select）：上方提示「カスタマイズするスキルカードを1つ選んでください」，选中一张后换成卡详情 + 「カスタマイズメニュー」预览；
  下方卡格 4 列（字母 x 113+160k、y 761+160k，卡格 141，行距 160，可滚动），只列可定制的卡，不能定制的压暗；
  底部「カスタマイズ可能スキルカード あと N枚」、「カスタマイズ」(360,1083)、「終了」(653,1083)。
- 定制项（view=menu，点「カスタマイズ」后）：上下两个卡详情（当前 / 实行后），「カスタマイズメニュー」横排 2–3 个彩色块，
  每块「あと N回」（这一项还能做几级）、名称、价格（当前这一级的价）、做满时「カスタマイズ上限」；「合計あと N回」（这张卡还能做几次）、
  「一覧に戻る」(213,1083)、「実行する」(497,1083)、「終了」(653,977)；卡数用完时「一覧に戻る」上方「あと N枚」。
定制项块从左到右的顺序 = 主数据 ProduceCard.produceCardCustomizeIds 的顺序（紙吹雪+：やる気コスト値- / 元気分パラメータ+ / 好印象追加，
价格 40 / 40→70 / 70 与主数据一致），所以按位置对应定制 ID。
"""

from __future__ import annotations

import json
import re
from functools import cache

import cv2
import numpy as np
from cv2.typing import MatLike

GRID_REGION = (0, 600, 720, 1000)
SCROLL_REGION, SCROLL_X = (0, 625, 720, 1000), 702  # 卡格右侧滑块：刚进来 636–763（在顶），滚动后 730–858
LABEL_BOX = (20, 20, 210, 80)  # 左上角行动标签「特別指導」
MENU_COLS = ((30, 250), (250, 470), (470, 690))
DETAIL_NAME_Y = (165, 220)  # 选中卡 / 当前卡的卡名
CURSOR_MIN_PX = 500  # 选中格四角的橙色角框（141 像素卡格）


@cache
def _customize_rows() -> dict[str, list[dict]]:
    from kaa.db.sqlite import select_many

    out: dict[str, list[dict]] = {}
    for r in select_many("SELECT id, customizeCount, producePoint FROM ProduceCardCustomize"):
        out.setdefault(r["id"], []).append({"count": r["customizeCount"], "price": r["producePoint"]})
    return out


@cache
def _card_customize_ids(card_id: str, upgrade: int) -> tuple[str, ...]:
    from kaa.db.sqlite import select_many

    rows = select_many("SELECT produceCardCustomizeIds FROM ProduceCard WHERE id = ? AND upgradeCount = ?", card_id, upgrade)
    return tuple(json.loads(rows[0]["produceCardCustomizeIds"] or "[]")) if rows else ()


def master_offers(card_id: str | None, upgrade: int | None) -> list[dict]:
    """主数据里这张卡（这个强化等级）的定制项：[{id, item_index, price（第 1 级的价）, levels（每级价格）, available: None, source: "master"}]。"""
    if not card_id or upgrade is None:
        return []
    out = []
    for k, cid in enumerate(_card_customize_ids(card_id, upgrade)):
        rows = sorted(_customize_rows().get(cid, []), key=lambda r: r["count"])
        out.append({"id": cid, "item_index": k, "price": rows[0]["price"] if rows else None, "levels": [r["price"] for r in rows],
                    "available": None, "source": "master"})  # fmt: skip
    return out


def _cy(b) -> int:
    return (b[1] + b[3]) // 2


def _cx(b) -> int:
    return (b[0] + b[2]) // 2


def is_special_guidance(boxes) -> bool:
    """左上角行动标签「特別指導」，且画面上有「カスタマイズ」字样（选卡提示 / 菜单 / 按钮）。"""
    x1, y1, x2, y2 = LABEL_BOX
    label = any("特別指導" in t and x1 <= _cx(b) <= x2 and y1 <= _cy(b) <= y2 for t, b in boxes)
    return label and any("カスタマイズ" in t for t, _ in boxes)


def _orange(hsv) -> np.ndarray:
    return (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)


def _cursor(grid: list[dict], orange: np.ndarray, selected_card: dict | None) -> int | None:
    """选中格：四角橙色角框。选中格的角框会伸进右边 / 下边相邻格的检测窗（DMM dmm2 特別指導：选中 0 号 1210 px，
    右邻 597、下邻 558 都过门槛，以前取最后一个过门槛的 → 4 号）。过门槛的格子里按上方详情的卡（selected_card）认：
    取同卡里角框最多的；详情卡不在这些格子里（选中的是滚到一半、不整格可见的卡，邻格被角框带过门槛）→ None。
    没有详情卡时取角框最多的。"""
    hits = []
    for c in grid:
        x, y, w, h = c["rect"]
        px = _corner_px(orange, x, y, x + w, y + h)
        if px >= CURSOR_MIN_PX:
            hits.append((px, c))
    if not hits:
        return None
    if selected_card and selected_card.get("card_id"):
        same = [(px, c) for px, c in hits if c["card_id"] == selected_card["card_id"]
                and (selected_card.get("upgrade") is None or c["upgrade"] is None or c["upgrade"] == selected_card["upgrade"])]  # fmt: skip
        return max(same, key=lambda t: t[0])[1]["index"] if same else None
    return max(hits, key=lambda t: t[0])[1]["index"]


def _corner_px(orange: np.ndarray, x1: int, y1: int, x2: int, y2: int, pad: int = 14, arm: int = 30) -> int:
    """矩形四角外侧的橙色角框像素数。"""
    h, w = orange.shape
    total = 0
    for cx, cy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
        xa, xb = max(0, min(cx - pad, cx + pad - arm)), min(w, max(cx + pad, cx - pad + arm))
        ya, yb = max(0, min(cy - pad, cy + pad - arm)), min(h, max(cy + pad, cy - pad + arm))
        total += int(orange[ya:yb, xa:xb].sum())
    return total


def read_menu(img: MatLike, boxes) -> dict | None:
    """「カスタマイズメニュー」→ {items: [{item_index, name, price, left, maxed, available, selected, center}], total_left}；没有菜单时 None。"""
    hdr = next((b for t, b in boxes if "カスタマイズメニュー" in t), None)
    if hdr is None:
        from hiro.vision.ocr import engine
        from hiro.vision.pages import _contains_near

        if engine() != "vision":  # RapidOCR 常错一个字
            hdr = next((b for t, b in boxes if _contains_near(t.replace(" ", ""), "カスタマイズメニュー")), None)
    if hdr is None:
        return None
    hy = _cy(hdr)
    orange = _orange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
    items = []
    for k, (x1, x2) in enumerate(MENU_COLS):
        col = sorted(((t.replace(" ", ""), b) for t, b in boxes if x1 <= _cx(b) < x2 and hy + 10 <= _cy(b) <= hy + 165), key=lambda tb: (_cy(tb[1]), tb[1][0]))
        if not col:
            continue
        left = next((int(m.group(1)) for t, _ in col if (m := re.search(r"あと(\d+)回", t))), None)
        maxed = any("上限" in t for t, _ in col)
        price = next((int(t) for t, b in col if re.fullmatch(r"\d{2,3}", t) and _cy(b) >= hy + 85), None)
        name = "".join(t for t, b in col if not re.search(r"あと\d+回|上限|^\d+$|^P$", t) and _cy(b) < hy + 100) or None
        if name is None and price is None:
            continue
        top, bottom = hy + 22, hy + 140
        items.append({"item_index": len(items), "name": name, "price": price, "left": left, "maxed": maxed,
                      "available": (not maxed) if (maxed or left is not None or price is not None) else None,
                      "corner_px": _corner_px(orange, x1 + 8, top, x2 - 8, bottom), "center": [(x1 + x2) // 2, hy + 75]})  # fmt: skip
    # 选中项：橙色角框像素最多的一块（相邻块的角框会漏进旁边的检测区，不能各自按阈值判）
    best = max(items, key=lambda it: it["corner_px"], default=None)
    for it in items:
        it["selected"] = it is best and it["corner_px"] >= 400
        del it["corner_px"]
    total = next((int(m.group(1)) for t, _ in boxes if (m := re.search(r"合計あと(\d+)回", t.replace(" ", "")))), None)
    return {"items": items, "total_left": total, "y": hy}


def read_special_guidance(img: MatLike, boxes=None) -> dict:
    """→ {view: select|menu, cards, selected, selected_card, menu, cards_left, scrollable, complete}。

    cards：选卡画面的卡格 [{index, row, col, card_id, name, upgrade, customized, available, center, customizations}]（index 是本屏格子顺序，
      只含可定制卡，不是牌组下标；按 center 点）。customizations 默认按主数据（source="master"），选中的那张换成画面上菜单的实价（source="screen"）。
    selected_card：画面上方详情的卡名（选中的卡 / 定制项页正在定制的卡）{card_id, name, upgrade}。
    menu：read_menu，每项另带主数据 id（按位置对应）。cards_left：还能定制几张卡（「あと N枚」）。"""
    from hiro.vision.deck import card_from_name, dialog_scroll, read_card_grid

    if boxes is None:
        from hiro.vision.pages import _texts

        boxes = _texts(img)
    texts = [t.replace(" ", "") for t, _ in boxes]
    view = "menu" if any("一覧に戻る" in t for t in texts) else "select"
    name = "".join(t for t, b in boxes if DETAIL_NAME_Y[0] <= _cy(b) <= DETAIL_NAME_Y[1] and b[0] >= 180).replace("＋", "+").replace(" ", "")
    selected_card = card_from_name(name) if name else None
    menu = read_menu(img, boxes)
    if menu is not None and selected_card is not None:
        offers = master_offers(selected_card["card_id"], selected_card["upgrade"])
        for it in menu["items"]:
            it["id"] = offers[it["item_index"]]["id"] if len(offers) == len(menu["items"]) else None
    cards, selected = [], None
    if view == "select":
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        orange = _orange(hsv)
        grid = read_card_grid(img, GRID_REGION, "guidance")
        selected = _cursor(grid, orange, selected_card)
        for c in grid:
            dim = c["dimmed"]  # 压暗 = 不能定制（read_card_grid 已提亮后再判「+」）
            card = {k: c[k] for k in ("index", "row", "col", "card_id", "name", "upgrade", "customized")}
            card.update(available=not dim, center=list(c["center"]))
            same = selected_card and selected == c["index"] and selected_card["card_id"] == c["card_id"]
            if same and menu is not None:
                card["customizations"] = [{"id": it.get("id"), "item_index": it["item_index"], "price": it["price"], "available": it["available"],
                                           "source": "screen"} for it in menu["items"]]  # fmt: skip
            else:
                card["customizations"] = [] if dim else master_offers(c["card_id"], c["upgrade"])
            cards.append(card)
    left = next((int(m.group(1)) for t in texts if (m := re.search(r"あと(\d+)枚", t))), None)
    scroll = dialog_scroll(img, SCROLL_REGION, SCROLL_X) if view == "select" else {"scrollable": None, "at_top": None}
    from hiro.vision.pages import read_hud_resources

    hud = read_hud_resources(img)  # q11：决策拿不到 P（「P None」）就一项都不定制；左上 P 栏与选行动页同一位置
    return {**hud, "view": view, "cards": cards if view == "select" else None, "selected": selected, "selected_card": selected_card,
            "menu": menu, "cards_left": left, "scrollable": scroll["scrollable"], "at_top": scroll["at_top"],
            "complete": (not scroll["scrollable"]) if view == "select" else None}  # fmt: skip


def read_customize_result(text: str) -> dict:
    """「きらきら紙吹雪+をカスタマイズしました」→ {name, card_id, upgrade}。"""
    from hiro.vision.deck import card_from_name

    name = text.replace(" ", "").replace("＋", "+").split("をカスタマイズ")[0]  # 气泡里会折行（「…しまし / た」）
    card = card_from_name(name)
    return {"name": name, "card_id": card["card_id"] if card else None, "upgrade": card["upgrade"] if card else None}
