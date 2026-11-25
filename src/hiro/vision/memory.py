"""回忆（プロデュースメモリー）选择页的读取：回忆编成选择系统 §2（docs/tasks/回忆编成选择系统.md）。

选择页上半是「当前选中的回忆」的详情框，分两页（下方「1 / 2」「2 / 2」，点左右箭头翻）：
- 第 1 页「獲得可能スキルカード」：入手时机徽章（開始時に獲得 / 中間試験・1次オーディション後に獲得）、卡图标（定制时中下方有绿色数字徽章）、
  卡名（带「+」）、计划限定（センスのみ / ロジックのみ / アノマリーのみ）、效果文字；
- 第 2 页：H.I.F 专用能力（上方蓝框，长的末尾是「もっとみる」）+ 属性词条方块（初期ボーカル上昇+20、ダンスパラメータボーナス+2.1%、
  ビジュアルレッスン終了時、45%の確率で体力回復4 …）。
下半是回忆网格（3 列），点一格即选中、详情框换成那一份。

坐标按 720x1280；OCR 结果用 hiro.vision.pages._texts 的 (文字, 框) 格式。读不到的项给 None（不猜）。
"""

from __future__ import annotations

import re

import cv2
import numpy as np
from cv2.typing import MatLike

PAGER_Y = (505, 560)  # 「1 / 2」页码
PANEL_Y = (165, 515)  # 详情框正文
CARD_NAME_BOX = (180, 210, 720, 270)  # 第 1 页卡名
CARD_ICON = (44, 224, 190, 366)  # 第 1 页卡图标
ABILITY_Y = (170, 275)  # 第 2 页 H.I.F 能力蓝框
TILES_Y = (280, 515)  # 第 2 页属性词条方块
TILE_GAP = 26  # 同一列里两行文字的间距超过这么多就是下一个方块
CUSTOM_BOX = (0.62, 0.90, 0.33, 0.67)  # 定制徽章在卡图标内的相对位置 (y1, y2, x1, x2)
CUSTOM_MIN_FRAC = 0.06

ATTR = {"ボーカル": "Vo", "ダンス": "Da", "ビジュアル": "Vi"}
PLAN = {"センス": "sense", "ロジック": "logic", "アノマリー": "anomaly"}
OCR_FIX = (
    ("ポーカル", "ボーカル"),
    ("ポーナス", "ボーナス"),
    ("バラメータ", "パラメータ"),
    ("ボーナXス", "ボーナス"),
    ("％", "%"),
    ("＋", "+"),
    (" ", ""),
)


def _norm(text: str) -> str:
    for a, b in OCR_FIX:
        text = text.replace(a, b)
    return text


def _cy(b) -> int:
    return (b[1] + b[3]) // 2


def detail_page(boxes) -> int | None:
    """详情框当前是第几页（1 / 2）；页码读不到时按内容判断。"""
    for t, b in boxes:
        # 斜杠 RapidOCR 常读成 1（「1/2」→「112」）：三位且中间是 1 / l / I / | 也按斜杠算
        if PAGER_Y[0] <= _cy(b) <= PAGER_Y[1] and (m := re.fullmatch(r"([12])[/1lI|]2", _norm(t))):
            return int(m.group(1))
    if any("獲得可能スキルカード" in t for t, _ in boxes):
        return 1
    return None


def _customized(img: MatLike) -> bool | None:
    x1, y1, x2, y2 = CARD_ICON
    icon = img[y1:y2, x1:x2]
    if icon.size == 0:
        return None
    h, w = icon.shape[:2]
    by1, by2, bx1, bx2 = CUSTOM_BOX
    hsv = cv2.cvtColor(icon[int(h * by1) : int(h * by2), int(w * bx1) : int(w * bx2)], cv2.COLOR_BGR2HSV)
    green = (hsv[:, :, 0] >= 45) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 120)
    return bool(green.mean() >= CUSTOM_MIN_FRAC)


def read_card_page(img: MatLike, boxes) -> dict:
    """第 1 页 → {timing: start|mid|None, card: {card_id, name, upgrade}|None, card_text, plan, customized}。"""
    from hiro.vision.deck import card_from_name

    texts = [(_norm(t), b) for t, b in boxes if PANEL_Y[0] <= _cy(b) <= PANEL_Y[1]]
    timing = next(
        ("start" if "開始時" in t else "mid" for t, _ in texts if "獲得" in t and ("開始時" in t or "後に" in t)), None
    )
    x1, y1, _x2, y2 = CARD_NAME_BOX
    name = "".join(t for t, b in texts if y1 <= _cy(b) <= y2 and b[0] >= x1)
    card = card_from_name(name, allow_prefix=True) if name else None  # 回忆详情卡名丢尾字（「楽観的」→「楽観」）
    plan = next((v for t, _ in texts if "のみ" in t for k, v in PLAN.items() if k in t), None)
    body = [t for t, b in texts if _cy(b) > y2 + 30 and "のみ" not in t]
    return {"timing": timing, "card": card, "card_name_text": name or None, "plan": plan, "customized": _customized(img) if card else None,
            "card_text": "".join(body) or None}  # fmt: skip


def parse_stat(text: str) -> dict:
    """一个属性词条方块的文字 → 结构化。认不出的类型保留原文（type=passive）。"""
    t = _norm(text)
    if m := re.search(r"初期(ボーカル|ダンス|ビジュアル)上昇\+(\d+)", t):
        return {"type": "initial_param", "attr": ATTR[m.group(1)], "value": int(m.group(2)), "text": t}
    if m := re.search(r"(ボーカル|ダンス|ビジュアル)[パバ]ラメータ[ボポ]ー?ナ.?ス\+(\d+(?:\.\d+)?)%", t):
        return {"type": "param_bonus_pct", "attr": ATTR[m.group(1)], "value": float(m.group(2)), "text": t}
    if m := re.search(r"初期体力上昇\+(\d+)", t):
        return {"type": "initial_stamina", "attr": None, "value": int(m.group(1)), "text": t}
    if m := re.search(r"初期Pポイント\+?(\d+)|Pポイント\+(\d+)", t):
        return {"type": "produce_point", "attr": None, "value": int(m.group(1) or m.group(2)), "text": t}
    plan = re.match(r"【(センス|ロジック|アノマリー)専用】", t)
    body = t[plan.end() :] if plan else t
    out = {
        "type": "passive",
        "attr": next((v for k, v in ATTR.items() if body.startswith(k)), None),
        "value": None,
        "text": t,
        "plan": PLAN[plan.group(1)] if plan else None,
    }
    if m := re.search(r"(\d+)%の確率で(.+?)(\d+)(ターン)?$", body):
        out.update(chance_pct=int(m.group(1)), effect=m.group(2), value=int(m.group(3)), turns=bool(m.group(4)))
    if m := re.match(r"(.+?(?:終了時|開始時|時))、", body):
        out["trigger"] = m.group(1)
    return out


ABILITY_HINTS = ("試験ごとに", "試験ことに", "H.I.F専用", "以降1回まで", "以降2回まで")
LINE_PITCH_MAX = 38  # 同一方块里相邻两行的中心距约 25；相邻方块 ≥ 50


def read_ability_page(img: MatLike, boxes) -> dict:
    """第 2 页 → {ability, ability_truncated, stats: [parse_stat(...) + {active, truncated}…]}。
    较新的回忆上方有 H.I.F 专用能力蓝框，旧回忆没有（词条方块从顶部开始）。灰底「発動不可」方块（【センス専用】等其他计划 / 其他考试的被动）
    在 H.I.F 里不触发：active=False。方块文字被截断（「もっとみる」）时 truncated=True。"""
    texts = [
        (_norm(t), b)
        for t, b in boxes
        if PANEL_Y[0] <= _cy(b) <= PANEL_Y[1] - 5 and not re.fullmatch(r"[12]/2", _norm(t))
    ]
    hint = [b for t, b in texts if _cy(b) <= ABILITY_Y[1] and any(h in t for h in ABILITY_HINTS)]
    ability, truncated, tiles_top = None, None, PANEL_Y[0]
    if hint:
        lines = sorted(
            ((t, b) for t, b in texts if _cy(b) <= ABILITY_Y[1] and b[0] >= 140), key=lambda tb: (_cy(tb[1]), tb[1][0])
        )
        truncated = any("もっとみる" in t for t, _ in lines)
        ability = "".join(t.replace("もっとみる", "") for t, _ in lines) or None
        tiles_top = ABILITY_Y[1] + 5
    tiles: list[list[tuple[str, tuple]]] = []
    for col in (0, 1):
        col_boxes = sorted(((t, b) for t, b in texts if _cy(b) >= tiles_top and ((b[0] + b[2]) // 2 < 360) == (col == 0)
                            and not re.fullmatch(r"H\.?[IL1]\.?F", t)), key=lambda tb: _cy(tb[1]))  # fmt: skip
        cur: list = []
        for t, b in col_boxes:
            if cur and _cy(b) - _cy(cur[-1][1]) > LINE_PITCH_MAX:
                tiles.append(cur)
                cur = []
            cur.append((t, b))
        if cur:
            tiles.append(cur)
    tiles.sort(key=lambda tile: (_cy(tile[0][1]) // 60, tile[0][1][0]))  # 先上后下、先左后右
    stats = []
    for tile in tiles:
        text = "".join(
            t for t, _ in sorted(tile, key=lambda tb: (round(_cy(tb[1]) / 14), tb[1][0]))
        )  # 同一行里红字百分比是单独的框
        inactive = "発動不可" in text
        cut = "もっとみる" in text
        stat = parse_stat(text.replace("発動不可", "").replace("もっとみる", "").replace("...", "").replace("…", ""))
        stats.append({**stat, "active": not inactive, "truncated": cut})
    return {"ability": ability, "ability_truncated": truncated, "stats": stats}


def read_memory_detail(img: MatLike, boxes=None) -> dict | None:
    """回忆选择页详情框 → {page: 1|2, …第 1 页或第 2 页的字段}；不是这个页面时 None。"""
    if boxes is None:
        from hiro.vision.pages import _texts

        boxes = _texts(img)
    page = detail_page(boxes)
    if page is None:
        return None
    return {"page": page, **(read_card_page(img, boxes) if page == 1 else read_ability_page(img, boxes))}


def merge_detail(card_page: dict | None, ability_page: dict | None) -> dict:
    """同一份回忆的两页拼成一条库存记录（memory-inventory.json 的一项）。"""
    out = {
        "timing": None,
        "card": None,
        "plan": None,
        "customized": None,
        "ability": None,
        "ability_truncated": None,
        "stats": None,
    }
    for part in (card_page, ability_page):
        if part:
            out.update({k: v for k, v in part.items() if k in out})
    return out


GRID_X = (140, 360, 580)  # 网格三列中心（与 .temp/memory_scan2.py 一致）
SELECT_CORNER_MIN = 0.03


def selected_cell(img: MatLike, rows_y=(647, 787, 920)) -> tuple[int, int] | None:
    """网格里被橙色角框框住的那一格 (row, col)；没有返回 None。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    orange = (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)
    best, where = 0.0, None
    for r, y in enumerate(rows_y):
        for c, x in enumerate(GRID_X):
            ring = orange[max(0, y - 75) : y + 75, max(0, x - 115) : x + 115].astype(np.float32)
            frac = float(ring.mean())
            if frac > best:
                best, where = frac, (r, c)
    return where if best >= SELECT_CORNER_MIN else None
