"""支援卡相关画面（用户 2026-11-02：支援卡不再用おまかせ）。样本 reports/live/rental/（540x960，放大到 720x1280 再读）。

- 支援卡卡片（read_tile）：横版插画，左下「Lv 60」+ 4 个突破标记（已突破 = 亮的粉白花形，未突破 = 半透明暗色），右下属性圆标
  （粉 = Vo、蓝 = Da、橙 = Vi）。卡本身用插画上半部分匹配素材包（hiro.vision.support 同一套库）→ support_card_id / name / type / rarity。
- 编成页（read_support_formation）：「サポートカード編成 N」页（view=formation，5 格）与开局第 2 步「2.サポート選択」（view=step2，5 格 + レンタル枠）。
- 租借列表（read_rental_list）：开局第 2 步点レンタル枠后的「フォロー / サークル」玩家列表，每行 玩家名 / PLv / 留言 / 标签 / 卡片；
  底部「プラン適正」勾选、刷新、筛选（Lv）、排序、キャンセル / 決定。
坐标都是 720x1280。读不到的项给 None（不猜）。
"""

from __future__ import annotations

import re
from collections import Counter

import cv2
import numpy as np
from cv2.typing import MatLike

TILE_W = 211  # 编成页 / 第 2 步 / 租借列表的卡片宽度（720 下）
LEVEL_BOX = (
    3,
    76,
    46,
    103,
)  # 相对卡片左上角：「Lv」下方的大号数字（两位数；一位数时右缘到 30，再往右是第一个突破标记）
LEVEL_X2_ONE_DIGIT = 30
BREAK_X0, BREAK_X0_ONE_DIGIT, BREAK_DX, BREAK_Y, BREAK_R = (
    61,
    42,
    25,
    95,
    9,
)  # 突破标记中心（相对左上角）：等级一位数时整排左移
BREAK_BRIGHT_MIN = 0.4  # 标记内「亮且低饱和」像素占比：已突破 0.69–0.83，未突破 ≤ 0.07
ATTR_BOX = (183, 80, 201, 98)  # 右下属性圆标（下方紧挨着彩虹条，不能取大）
ATTR_HUE = (
    ("Vi", 5, 35),
    ("HP", 40, 85),
    ("Da", 90, 120),
    ("Vo", 150, 180),
)  # 实测 Vi 19–20、Da 104、Vo 165；绿色叶子 = 体力（支援）
TYPE_ATTR = {
    "SupportCardType_Vocal": "Vo",
    "SupportCardType_Dance": "Da",
    "SupportCardType_Visual": "Vi",
    "SupportCardType_Assist": "HP",
}


def _match(img: MatLike, x: int, y: int, w: int = TILE_W) -> tuple[float, float, str, tuple[int, int]]:
    """插画上半部分 vs 支援卡素材库（±8 像素、±6 宽度）→ (分数, 领先第二名, 资源名, 实际左上角)。"""
    from hiro.vision.support import ART_TOP, _library, _vec

    keys, matrix, _ = _library()
    tries, vecs = [], []
    for ww in (w - 6, w - 3, w, w + 3, w + 6):
        for dy in range(-8, 9, 2):
            for dx in range(-8, 9, 2):
                crop = img[max(0, y + dy) : y + dy + int(ww / 2 * ART_TOP), max(0, x + dx) : x + dx + ww]
                if crop.size and crop.shape[1] >= ww - 1:
                    tries.append((x + dx, y + dy))
                    vecs.append(_vec(crop))
    if not vecs:
        return 0.0, 0.0, "", (x, y)
    scores = matrix @ np.stack(vecs, axis=1)
    i, j = np.unravel_index(int(scores.argmax()), scores.shape)
    col = np.sort(scores[:, j])
    return float(scores[i, j]), float(col[-1] - col[-2]), keys[i], tries[j]


def _one_digit(img: MatLike, x: int, y: int) -> bool:
    """等级是不是一位数（决定等级框宽度和突破标记位置）：ddddocr 读整个两位数框，它会跳过右边的突破标记。"""
    from hiro.vision.digits import read_dddd

    v = read_dddd(img, (x + LEVEL_BOX[0], y + LEVEL_BOX[1], x + LEVEL_BOX[2], y + LEVEL_BOX[3]))
    return v is not None and v < 10


def _level(img: MatLike, x: int, y: int, one_digit: bool = False) -> int | None:
    """「Lv」下方的大号白字：ddddocr / 白字 OCR / 直接 OCR，至少两票一致且在 1–70 内才给（第 2 步页左侧翻页箭头会盖住首位）。"""
    from hiro.vision.digits import read_dddd
    from hiro.vision.ocr import ocr, ocr_digits

    x1, y1, y2 = x + LEVEL_BOX[0], y + LEVEL_BOX[1], y + LEVEL_BOX[3]
    x2 = x + (LEVEL_X2_ONE_DIGIT if one_digit else LEVEL_BOX[2])
    crop = img[y1:y2, x1:x2]
    if one_digit:
        # 一位数 Apple Vision 基本读不出（空串），ddddocr 在不同框宽下都稳：宽框 / 窄框两次 ddddocr + 白字 OCR
        wide = read_dddd(img, (x1, y1, x + LEVEL_BOX[2], y2))
        votes = [
            wide if wide is not None and wide < 10 else None,
            read_dddd(img, (x1, y1, x2, y2)),
            ocr_digits(crop, white_min=220),
        ]
    else:
        plain = re.sub(r"\D", "", "".join(t for t, _ in ocr(crop, scale=4, languages=("en-US",))))
        votes = [ocr_digits(crop, white_min=235), read_dddd(img, (x1, y1, x2, y2)), int(plain) if plain else None]
    valid = [v for v in votes if v is not None and 1 <= v <= 70]
    if not valid:
        return None
    best, n = Counter(valid).most_common(1)[0]
    return best if n >= 2 else None


def _breaks(img: MatLike, x: int, y: int, one_digit: bool = False) -> int | None:
    """突破数：4 个标记里亮的个数（亮的一定在左边连续）。"""
    lit = []
    for k in range(4):
        cx, cy = x + (BREAK_X0_ONE_DIGIT if one_digit else BREAK_X0) + BREAK_DX * k, y + BREAK_Y
        hsv = cv2.cvtColor(img[cy - BREAK_R : cy + BREAK_R, cx - BREAK_R : cx + BREAK_R], cv2.COLOR_BGR2HSV)
        lit.append(float(((hsv[:, :, 2] >= 215) & (hsv[:, :, 1] <= 70)).mean()) >= BREAK_BRIGHT_MIN)
    n = sum(lit)
    return n if lit == [True] * n + [False] * (4 - n) else None


def _attr_icon(img: MatLike, x: int, y: int) -> str | None:
    x1, y1, x2, y2 = ATTR_BOX
    hsv = cv2.cvtColor(img[y + y1 : y + y2, x + x1 : x + x2], cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 150)
    if m.sum() < 30:
        return None
    hue = int(np.median(hsv[:, :, 0][m]))
    return next((a for a, lo, hi in ATTR_HUE if lo <= hue <= hi), None)


def read_tile(img: MatLike, x: int, y: int, w: int = TILE_W, anchored: bool = False) -> dict:
    """一张支援卡卡片（左上角大致位置）→ {support_card_id, name, type, attr, rarity, level, breaks, attr_icon, score, margin, rect, center}。
    attr 以素材库类型为准（attr_icon 是右下圆标的颜色，作核对）；素材库认不出（分数 < 0.7）时 support_card_id / name 为 None。"""
    from hiro.vision.support import MIN_SCORE, _library

    _, _, index = _library()
    score, margin, key, (cx, cy) = _match(img, x, y, w)
    if anchored:
        cx, cy = (
            x,
            y,
        )  # 位置已由属性圆标精确定出（列表页）：等级 / 突破 / 圆标按它读，素材库匹配的 ±8 像素偏移会把等级框移歪
    info = index.get(key) if score >= MIN_SCORE else None
    icon = _attr_icon(img, cx, cy)
    one = _one_digit(img, cx, cy)
    return {"support_card_id": info["id"] if info else None, "name": info["name"] if info else None,
            "type": info["type"] if info else None, "attr": TYPE_ATTR.get(info["type"]) if info else icon,
            "rarity": info.get("rarity") if info else None, "level": _level(img, cx, cy, one), "breaks": _breaks(img, cx, cy, one),
            "attr_icon": icon, "score": round(score, 3), "margin": round(margin, 3),
            "rect": [cx, cy, w, 118], "center": [cx + w // 2, cy + 59]}  # fmt: skip


# ---------------------------------------------------------------- 编成页
FORMATION_TILES = (
    (31, 675),
    (255, 675),
    (478, 677),
    (143, 806),
    (367, 806),
)  # 「サポートカード編成 N」页 5 格（上 3 下 2）
STEP2_TILES = ((29, 715), (254, 713), (481, 715), (29, 847), (256, 847))  # 开局「2.サポート選択」5 格
STEP2_RENTAL = (483, 847)  # 第 6 格：レンタル枠（空时「レンタルのみ +」）
ARROWS = {"formation": {"prev": [40, 635], "next": [680, 635]}, "step2": {"prev": [20, 833], "next": [700, 833]}}


def _texts(img: MatLike, boxes):
    if boxes is None:
        from hiro.vision.pages import _texts as t

        boxes = t(img)
    # 「サポート」的「ポ」常被 OCR 读成「ボ」（11-16 Mac 账号 2 第 2 步标题「2.サボート選択」，整页认不出、自动编成失败），这里统一回「ポ」
    return [(t.replace(" ", "").replace("サボート", "サポート"), b) for t, b in boxes]


_PAGER_SLASHES = "1lI|｜／!"  # RapidOCR 常把分页栏的「/」读成这些（11-18 Windows MuMu：「1/2」读成「112」，整页认不出、库存扫描停下）


def _pager(texts) -> re.Match | None:
    """分页栏「N/M」（y 500–560）。斜杠读对时照常认；读错时只认居中（x 中心 300–420）、1 ≤ N ≤ M ≤ 9 的读数：
    斜杠读成 1 / l / | 等（「112」），或者整个漏掉（「12」，11-18 Windows MuMu 滚动后的同一页）。"""
    for t, b in texts:
        if not 500 <= b[1] <= 560:
            continue
        if m := re.fullmatch(r"(\d)/(\d)", t):
            return m
        if not 300 <= (b[0] + b[2]) // 2 <= 420:
            continue
        m = re.fullmatch(r"(\d)(\d)", t) or (
            re.fullmatch(r"(\d).(\d)", t) if len(t) == 3 and t[1] in _PAGER_SLASHES else None
        )
        if m and 1 <= int(m.group(1)) <= int(m.group(2)):
            return re.fullmatch(r"(\d)/(\d)", f"{m.group(1)}/{m.group(2)}")
    return None


def _center(b) -> list[int]:
    return [(b[0] + b[2]) // 2, (b[1] + b[3]) // 2]


def read_support_formation(img: MatLike, boxes=None) -> dict | None:
    """支援卡编成 → {view: formation|step2, name, number, total, slots[read_tile + slot], rental, arrows, buttons}；不是这两种页面时 None。
    name：编成名（「サポートカード編成1」）；number / total：「1/20」；rental（只有 step2）：{empty, tile|None, center}。"""
    texts = _texts(img, boxes)
    if any("サポート選択" in t for t, b in texts if b[1] < 90):
        view, tiles = "step2", STEP2_TILES
    elif any(t.startswith("サポートカード編成") for t, b in texts if b[1] < 160) or (
        # 用户改过名的编成（第十六局后实机：编成 4 叫「No vocal」）：标题不是「サポートカード編成 N」，
        # 改认底部「サポート編成」页签 + 左下「N/20」编号 + 「おまかせ」按钮
        any("サポート編成" in t for t, b in texts if b[1] > 1040)
        and any(re.fullmatch(r"\d+/20", t) for t, _ in texts)
        and any("おまかせ" in t for t, _ in texts)
    ):
        view, tiles = "formation", FORMATION_TILES
    else:
        return None
    name = next((t for t, _ in texts if t.startswith("サポートカード編成")), None) or next(
        (
            t
            for t, b in texts
            if 60 <= b[1] <= 160 and 150 <= (b[0] + b[2]) // 2 <= 570 and not re.fullmatch(r"[+\d.%]+", t)
        ),
        None,
    )
    # 编成号在左侧；第 2 步页右上还有「親愛度 37/37」。macOS 27.2.1 的 Apple Vision 会把左边的小图标一起读成「- 1/20」（A16）
    page = next(
        (m for t, b in texts if b[0] < 250 and (m := re.fullmatch(r"\D{0,3}?(\d+)\s*/\s*(\d+)", t.strip()))), None
    )
    slots = [{"slot": i, **read_tile(img, x, y)} for i, (x, y) in enumerate(tiles)]
    out = {"view": view, "name": name, "number": int(page.group(1)) if page else None, "total": int(page.group(2)) if page else None,
           "slots": slots, "arrows": ARROWS[view],
           "buttons": {k: _center(b) for t, b in texts for k, w in (("reset", "リセット"), ("auto", "おまかせ"), ("detail", "編成詳細")) if w in t}}  # fmt: skip
    if view == "step2":
        empty = any("レンタルのみ" in t for t, _ in texts)
        x, y = STEP2_RENTAL
        out["rental"] = {
            "empty": empty,
            "tile": None if empty else read_tile(img, x, y),
            "center": [x + TILE_W // 2, y + 59],
        }
    return out


# ---------------------------------------------------------------- 租借列表
ROW_TILE_DX, ROW_TILE_DY = 44, -13  # 卡片左上角：x 固定 44，y = 「PLv」中心 − 13
ROW_COVERED_Y = 990  # 底部控件（プラン適正 / 編成詳細 / 筛选排序）从 y≈1000 起盖住列表
CONTROLS = {
    "plan_fit": [551, 1016],
    "refresh": [400, 1071],
    "filter": [524, 1071],
    "sort": [653, 1071],
    "cancel": [220, 1157],
    "decide": [497, 1157],
    "detail": [130, 1064],
}
PLAN_FIT_BOX = (545, 1006, 566, 1027)  # 「プラン適正」勾选框：勾上时是实心灰方块 + 白对勾
PLAN_FIT_GREY_MIN = 0.4


def read_rental_list(img: MatLike, boxes=None) -> dict | None:
    """开局租借支援卡列表 → {rows: [{player, plv, comment, tag: follow|circle|None, card: read_tile, center, covered, selected}],
    plan_fit: {checked, center}, controls, pager}；不是这一页时 None。
    rows 只含「PLv」露出来的行；covered=True：卡片下半被底部控件盖住（卡片读数可能不全，先滚一下再选）。"""
    texts = _texts(img, boxes)
    plvs = sorted(((m, b) for t, b in texts if (m := re.match(r"P[LI]V(\S+)", t, re.I))), key=lambda mb: mb[1][1])
    if not plvs or not any("決定" in t for t, _ in texts):
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    orange = (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)
    rows = []
    for m, b in plvs:
        cy = (b[1] + b[3]) // 2
        digits = re.sub(r"\D", "", m.group(1).replace("ろ", "3").replace("O", "0"))
        player = next((t for t, bb in texts if bb[0] < 300 and -50 <= (bb[1] + bb[3]) // 2 - cy <= -25), None)
        comment = next((t for t, bb in texts if 360 <= bb[0] <= 400 and 15 <= (bb[1] + bb[3]) // 2 - cy <= 40), None)
        tag_text = next((t for t, bb in texts if bb[0] >= 560 and -65 <= (bb[1] + bb[3]) // 2 - cy <= -35), "")
        tag = "follow" if "フォロー" in tag_text else "circle" if "サークル" in tag_text else None
        x, y = ROW_TILE_DX, cy + ROW_TILE_DY
        covered = y + 118 > ROW_COVERED_Y
        top = y - 45
        corners = int(
            orange[max(0, top - 10) : top + 25, 15:50].sum() + orange[max(0, top - 10) : top + 25, 670:705].sum()
        )
        rows.append({"player": player, "plv": int(digits) if digits else None, "comment": comment, "tag": tag,
                     "card": read_tile(img, x, y) if not covered else None, "center": [360, y + 30], "covered": covered,
                     "selected": corners >= 200})  # fmt: skip
    x1, y1, x2, y2 = PLAN_FIT_BOX
    box = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    grey = float(((box[:, :, 2] >= 150) & (box[:, :, 2] <= 215) & (box[:, :, 1] < 40)).mean())
    checked = grey >= PLAN_FIT_GREY_MIN  # 没勾的样本还没有（预计是白底空框），阈值待核对
    page = _pager(texts)
    return {"rows": rows, "plan_fit": {"checked": checked, "center": CONTROLS["plan_fit"]}, "controls": dict(CONTROLS),
            "pager": {"page": int(page.group(1)) if page else None, "pages": int(page.group(2)) if page else None,
                      "prev": [233, 533], "next": [485, 533]}}  # fmt: skip


# ---------------------------------------------------------------- 「選択」列表（支援卡全量）
# 编成页点空格进入：3 列网格（卡片同编成页），按 Lv 排序，顶部是选中卡的详情，分页「1 / 2」+ 左右箭头，底部筛选 / 排序、キャンセル / 決定。
# 网格会上下滚动，行的位置不固定：用每列右下属性圆标（饱和色小圆，x = 列左缘 + 192）定行，卡片左上角 = 圆标中心 − (192, 89)。
LIST_COLS = (33, 255, 476)
LIST_TOP = 555  # 分页栏下缘：卡片顶在它上面 = 插画被挡（partial，不认卡，留给上一屏）
LIST_BOTTOM = 1060  # 底部按钮区（編成詳細 / 筛选 / キャンセル / 決定）以下不读
ICON_DX, ICON_DY = 192, 89
NEW_PINK_MIN = 40  # 卡片左上「NEW」粉字像素
LIST_CONTROLS = {"cancel": [220, 1157], "decide": [497, 1157], "filter": [524, 1071], "sort": [653, 1071], "detail": [130, 1064],
                 "prev": [233, 533], "next": [485, 533]}  # fmt: skip


def _icon_rows(hsv: np.ndarray, col_x: int) -> list[int]:
    """一列里属性圆标的中心 y（饱和色、宽 ≥ 8 像素的竖向连续段 12–30 像素）。"""
    cx = col_x + ICON_DX
    sub = hsv[:, cx - 10 : cx + 10]
    on = ((sub[:, :, 1] >= 120) & (sub[:, :, 2] >= 150)).sum(axis=1) >= 8
    out, start = [], None
    for y in range(LIST_TOP - ICON_DY + 40, LIST_BOTTOM):
        if on[y] and start is None:
            start = y
        elif not on[y] and start is not None:
            if 12 <= y - start <= 30:
                out.append((start + y) // 2)
            start = None
    return out


def read_support_list(img: MatLike, boxes=None) -> dict | None:
    """支援卡「選択」列表 → {cards: [{col, row_y, support_card_id, name, type, attr, rarity, level, breaks, attr_icon, new, selected, partial,
    center, score, margin}], page, pages, controls}；不是这一页时 None（租借列表有「PLv」，不算）。
    partial=True：卡片上半被分页栏挡住，不认卡（support_card_id=None），只给等级 / 突破 / 属性圆标。"""
    if img.shape[1] != 720:
        img = cv2.resize(img, (720, 1280), interpolation=cv2.INTER_CUBIC)
    texts = _texts(img, boxes)
    if not any("決定" in t for t, _ in texts) or any(re.match(r"P[LI]V", t, re.I) for t, _ in texts):
        return None
    page = _pager(texts)
    if page is None:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    orange = (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)
    pink = ((hsv[:, :, 0] >= 165) | (hsv[:, :, 0] <= 5)) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 180)
    cards = []
    for col, x in enumerate(LIST_COLS):
        for cy in _icon_rows(hsv, x):
            y = cy - ICON_DY
            partial = y < LIST_TOP - 4
            if partial:
                one = _one_digit(img, x, y)
                tile = {"support_card_id": None, "name": None, "type": None, "attr": _attr_icon(img, x, y), "rarity": None,
                        "level": _level(img, x, y, one), "breaks": _breaks(img, x, y, one), "attr_icon": _attr_icon(img, x, y),
                        "score": None, "margin": None, "rect": [x, y, TILE_W, 118], "center": [x + TILE_W // 2, y + 59]}  # fmt: skip
            else:
                tile = read_tile(img, x, y, anchored=True)
            ty = tile["rect"][1]
            new = int(pink[max(0, ty - 16) : ty + 4, x : x + 55].sum()) >= NEW_PINK_MIN
            corners = int(
                orange[max(0, ty - 12) : ty + 20, max(0, x - 12) : x + 20].sum()
                + orange[ty + 95 : ty + 130, max(0, x - 12) : x + 20].sum()
            )
            cards.append({"col": col, "row_y": ty, **tile, "new": new, "corners": corners, "partial": partial})

    # 卡面插画里的饱和色块也会被当成圆标：同一行（±6）至少 2 列都有圆标，或素材库认得出（≥0.7），才算一格
    def row_mates(c):
        return sum(1 for o in cards if o is not c and abs(o["row_y"] - c["row_y"]) <= 6)

    cards = [c for c in cards if row_mates(c) >= 1 or (c["score"] or 0) >= 0.7]
    best = max(cards, key=lambda c: c["corners"], default=None)
    for c in cards:
        c["selected"] = c is best and c["corners"] >= 150  # 选中框只有一个；相邻格会分到它的一个角
        del c["corners"]
    cards.sort(key=lambda c: (c["row_y"] // 40, c["col"]))
    return {"cards": cards, "page": int(page.group(1)), "pages": int(page.group(2)), "controls": dict(LIST_CONTROLS)}
