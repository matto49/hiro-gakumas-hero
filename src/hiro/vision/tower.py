"""偶像之路（Idol Road）考试页的识别：顶部 6 人名次条（1st–6th）上自己的名次和分数、各人的实时分数。

和 H.I.F 的区别：选拔赛顶部是突破线 + 多人分数，本战是 3 人名次条；偶像之路是 6 人名次条，3rd 和 4th 之间有一道分隔线
（星按名次给：★ 3 位以上、★★ 2 位以上、★★★ 1 位，8a3a1e7 11-14）。自己那一列比 NPC 宽、青色底、名次字更大，
位置随名次变；屏幕中间的大数字是本回合加分的动画，不是总分（Mac 15 层实机：读成 5,442，实际 34,368）。

名次条的布局是固定的（Mac 实机帧量出来，720x1280 坐标）：右缘约 x=710，NPC 列宽 69、自己那列 127（含白框外沿），
列间 2，3rd / 4th 之间多 13。所以自己排第 r 名时 6 列的位置都定了：r 取 1–6 各试一次，「自己那列」青色底最多的就是 r。
分数在每列底部，NPC 的五位数会往右溢出到下一列约 10 像素，所以读数框不跨到下一列数字开始的地方。
读出来的分数必须从左到右不增（名次条按分数排）：违反的那一格（被特效线挡住少读一位等）给 None，不猜。
"""

from __future__ import annotations

import cv2
import numpy as np

from hiro.vision.digits import read_number

RIGHT, NPC_W, ME_W, GAP, DIVIDER = 710, 69, 127, 2, 13
SCORE_Y = (126, 168)
ME_CYAN_MIN = 0.15  # 自己那列青色底比例：对的位置 0.18–0.20，其余 ≤ 0.13（Mac 实机 65 帧）；H.I.F 本战压暗帧 0.134
ROWS = 6


def layout(me: int) -> list[tuple[int, int, int]]:
    """自己排第 me 名时 6 列的 [(名次, 左, 右)]，从 1st 到 6th。"""
    cols, right = [], RIGHT
    for rank in range(ROWS, 0, -1):
        w = ME_W if rank == me else NPC_W
        cols.append((rank, right - w, right))
        right -= w + GAP + (DIVIDER if rank == 4 else 0)
    return sorted(cols)


def _cyan(hsv: np.ndarray, left: int, right: int) -> float:
    b = hsv[50:120, left + 4 : right - 40]
    return float(((b[..., 0] >= 85) & (b[..., 0] <= 105) & (b[..., 1] >= 120) & (b[..., 2] >= 150)).mean())


def my_rank(img: np.ndarray) -> tuple[int | None, float]:
    """自己的名次（1–6）和那一列的青色底比例；哪一列都不够青返回 (None, 最大比例)。"""
    hsv = cv2.cvtColor(img[:170], cv2.COLOR_BGR2HSV)
    best = max((_cyan(hsv, *layout(r)[r - 1][1:]), r) for r in range(1, ROWS + 1))
    return (best[1] if best[0] >= ME_CYAN_MIN else None), best[0]


def read_tower_ranking(img: np.ndarray) -> dict | None:
    """名次条 → {my_rank, my_score, scores[6]（1st→6th，读不出 / 不可信为 None）, top_score}；不是偶像之路名次条返回 None。"""
    me, _ = my_rank(img)
    if me is None:
        return None
    y1, y2 = SCORE_Y
    scores: list[int | None] = []
    for rank, left, right in layout(me):
        box = (left + 2, y1, min(720, right + 8), y2) if rank == me else (left + 6, y1, min(720, left + 80), y2)
        scores.append(read_number(img, box))
    for i in range(ROWS - 1):  # 名次条按分数排：比右边一格（名次更低）还小的读数不可信
        if scores[i] is not None and scores[i + 1] is not None and scores[i] < scores[i + 1]:
            scores[i] = None
    return {"my_rank": me, "my_score": scores[me - 1], "scores": scores, "top_score": scores[0]}


TURNS_MIN = 0.45  # 「残りターン」模板：偶像之路考试帧 0.58–0.9（Mac 实机 65 帧），非考试画面最高 0.21


def is_tower_exam(img: np.ndarray) -> bool:
    """偶像之路考试页：左上「残りターン」+ 没有突破线、不是本战 3 人名次条 + 6 人名次条里认得出自己那一列。"""
    from hiro.vision import hif

    if hif._match(img, "hif_exam_remaining_turns", (0, 0, 160, 45))[0] < TURNS_MIN:
        return False
    if hif.is_finals_strip(img) or hif._match_white(img, "hif_exam_breakthrough", hif.TOP_STRIP)[0] >= hif.BREAKTHROUGH_MIN:
        return False
    return my_rank(img)[0] is not None


# ---------------------------------------------------------------- 偶像之路的非考试页面（Mac 实机采样 11-14，（作者本机，未发布）
# 判据都是整帧 OCR 的固定字样；字段只读画面上的，读不到给 None / 不写，不猜。boxes：[(去空格的文字, (x1, y1, x2, y2))]。
ATTRS = ("Vo", "Da", "Vi")


def _find(boxes, word: str, y=(0, 1280)):
    return next((b for t, b in boxes if word in t and y[0] <= (b[1] + b[3]) // 2 <= y[1]), None)


def _center(b) -> list[int]:
    return [int((b[0] + b[2]) // 2), int((b[1] + b[3]) // 2)]


def _int(text: str) -> int | None:
    import re

    m = re.search(r"\d[\d,]*", text)
    return int(m.group().replace(",", "")) if m else None


def _list_fields(boxes) -> dict:
    """路线列表：每张卡从上到下是「STAGE n」、偶像名（约 +215）、进度「x/81」（约 +275）。
    以进度「x/81」为锚：名字在它上面约 60 像素，STAGE 在上面约 270 像素。STAGE 上有闪光动画时 OCR 读不出（g5b 11-17：
    手毬那张读成「STA」「¥15」，整张卡漏掉，「列表里没找到月村手毬」），这时 stage 记 None，名字和点击位置照样有。
    只露出 STAGE、名字在屏幕外的卡（第二排）也列出来，name / progress 为 None。点击位置用名字（没有名字用 STAGE）的中心。"""
    import re

    idols, used = [], set()
    progs = sorted(((t, b) for t, b in boxes if re.fullmatch(r"\d+/\d+", t) and _center(b)[1] > 600), key=lambda x: (_center(x[1])[1] // 40, _center(x[1])[0]))
    for pt, pb in progs:
        px, py = _center(pb)
        name = next(((t, b) for t, b in boxes if abs(_center(b)[0] - px) < 90 and 30 <= py - _center(b)[1] <= 100 and len(t) >= 2
                     and not re.search(r"\d", t)), None)  # fmt: skip
        st = next(((t, b) for t, b in boxes if re.fullmatch(r"STAGE(\d+)", t) and abs(_center(b)[0] - px) < 110 and 230 <= py - _center(b)[1] <= 320), None)
        if st is not None:
            used.add(id(st[1]))
        anchor = name[1] if name is not None else (st[1] if st is not None else None)
        center = list(_center(anchor)) if anchor is not None else [px, py - 60]
        idols.append({"stage": int(st[0][5:]) if st else None, "name": name[0] if name else None, "progress": pt, "center": center})
    for t, b in boxes:  # 名字在屏幕外的卡：只有 STAGE
        if (m := re.fullmatch(r"STAGE(\d+)", t)) and id(b) not in used:
            idols.append({"stage": int(m.group(1)), "name": None, "progress": None, "center": list(_center(b))})
    return {"idols": idols}


def _formation_fields(boxes, img: np.ndarray | None = None) -> dict:
    """编成页（挑戦する那页）：层号、回合数、三属性倍率 → 千分比（HIRO_TOWER_LOADOUT 的 score_bonus_permils）、体力上限、按钮。
    三属性倍率从上到下是 Vo / Da / Vi（ボーカル / ダンス / ビジュアル 那三行右侧的「2309%」）。"""
    import re

    out: dict = {}
    if (st := next((t for t, _ in boxes if re.fullmatch(r"STAGE\d+", t)), None)) is not None:
        out["layer"] = int(st[5:])
    turn_label = _find(boxes, "ターン", (60, 130))
    if turn_label is not None:
        tx = _center(turn_label)[0]
        out["turns"] = next((int(t) for t, b in boxes if t.isdigit() and abs(_center(b)[0] - tx) < 30 and 30 <= _center(b)[1] <= 80), None)
    pcts = sorted(((_center(b)[1], int(m.group(1))) for t, b in boxes if (m := re.fullmatch(r"\D?(\d{2,5})%", t)) and _center(b)[0] > 560), key=lambda x: x[0])
    if len(pcts) == 3:
        out["score_bonus_permils"] = [p * 10 for _, p in pcts]
        # 同一行左边的属性值（「2348」「•1701」「A611」：前面的评价记号 OCR 常读成杂字，只取数字）
        params = [next((v for t, b in boxes if 480 <= _center(b)[0] <= 600 and abs(_center(b)[1] - y) <= 20
                        and (v := _int(t)) is not None and v >= 10), None) for y, _ in pcts]  # fmt: skip
        if all(v is not None for v in params):
            out["params"] = params
    name = next((t for t, b in boxes if 390 <= _center(b)[1] <= 425 and 180 <= _center(b)[0] <= 400), None)
    title = next((t for t, b in boxes if 360 <= _center(b)[1] <= 390 and 180 <= _center(b)[0] <= 330), None)
    if name and title:
        out["idol_name"], out["idol_card_title"] = name, title  # 「月村手毬」「一匹狼」：查 IdolCard 得主回忆的偶像卡
    # 主 / サブ1 / サブ2 三张回忆的评价值（y≈903 一行，x 约 183 / 412 / 650）：编成签名用（hiro.runtime.tower_cache.signature）
    ratings = [next((v for t, b in boxes if 880 <= _center(b)[1] <= 925 and lo <= _center(b)[0] < hi
                     and re.fullmatch(r"\d{3,6}", t) and (v := int(t))), None)
               for lo, hi in ((60, 300), (300, 530), (530, 720))]  # fmt: skip
    if all(ratings):
        out["memory_ratings"] = ratings
    hp = _find(boxes, "体力", (500, 600))
    if hp is not None:
        out["max_stamina"] = next((int(t) for t, b in boxes if t.isdigit() and abs(_center(b)[0] - _center(hp)[0]) < 30 and 15 <= _center(b)[1] - _center(hp)[1] <= 50), None)
    for key, word in (("challenge", "挑戦する"), ("omakase", "おまかせ"), ("reset", "リセット")):
        if (b := _find(boxes, word, (1000, 1280))) is not None:
            out[key] = _center(b)  # おまかせ 只记位置，绝不点（用户 11-14：编成走我们的决策）
    # 第 1 / 2 页（「1/2」「2/2」，两边是左右翻页箭头，间距约 97）：第 2 页整副牌组直接显示
    if (pg := next(((t, b) for t, b in boxes if re.fullmatch(r"([12])/2", t) and 600 <= _center(b)[1] <= 720), None)) is not None:
        out["page"] = int(pg[0][0])
        cx, cy = _center(pg[1])
        out["next_page"], out["prev_page"] = [cx + 97, cy], [cx - 97, cy]
    return out


STAR_X = (250, 360, 470)
STAR_PINK_MIN = 0.2
LAYER_BOX = (280, 385, 440, 470)


# 编成页第 2/2 页的牌组（协调 11-14 第二批采样 38 号帧）：3 排，第 1 排主回忆 6 张（x 70 起、间隔 90），第 2、3 排是サブ1 / サブ2
#（前面一个「+」，x 195 起）。灰掉的（「重複」「制限」）不进考试（8a3a1e7 拿 GKMS 6 局开局对过），按亮度跳过：灰 102–141、亮 181–239。
# 强化看右上角的红「+」（卡中心右上约 +25..+40、-20..0）；左上角的数字是等级不是强化。基本 2 张页面上不显示，由中转层按偶像补。
DECK_ROWS = ((415, (70, 160, 250, 340, 430, 520)), (515, (195, 285, 375, 465, 555, 645)), (607, (195, 285, 375, 465, 555, 645)))
DECK_HALF = 42
DECK_DIM_V = 160  # 卡面平均亮度低于这个算灰掉（重複 / 制限）
DECK_PLUS_MIN = 15  # 右上角红色像素：强化 20–60，没强化 ≤ 8


DECK_MIN_VOTES = 2  # 编成页的卡图只有 84 像素，特征点少：卡格认卡没把握时，最高票 ≥ 2 且严格多于第二名也算


def _deck_card(crop: np.ndarray):
    """编成页小卡图认卡：先用卡格缩略图的规则（deck.match_grid_card）；没把握时，最高票 ≥ DECK_MIN_VOTES 且严格多于第二名就认
    （38 号帧带「12」等级和「7」角标的 スタンドプレー+：act-2_032 两票、其余一票，8a3a1e7 对过考试帧）。"""
    from hiro import master
    from hiro.vision import card_db
    from hiro.vision.deck import base_asset, match_grid_card

    if (card := match_grid_card(crop)) is not None:
        return card
    votes: dict[str, int] = {}
    for h in card_db.query(crop):
        a = base_asset(h.key)
        votes[a] = votes.get(a, 0) + h.votes
    ranked = sorted(votes.values(), reverse=True)
    if not ranked or ranked[0] < DECK_MIN_VOTES or (len(ranked) > 1 and ranked[0] == ranked[1]):
        return None
    return master.card_by_asset(max(votes, key=votes.get))


def read_formation_deck(img: np.ndarray) -> dict:
    """编成页第 2/2 页 → {"cards": [「卡ID」或「卡ID+1」…]（亮着的、认得出的）, "rows": [[{name, card_id, upgrade, dim}…]×3], "unread": 亮着但认不出的张数}。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    rows, cards, unread = [], [], 0
    for y, xs in DECK_ROWS:
        row = []
        for x in xs:
            cell = hsv[y - DECK_HALF : y + DECK_HALF, x - DECK_HALF : x + DECK_HALF]
            dim = float(cell[..., 2].mean()) < DECK_DIM_V
            corner = hsv[y - 22 : y + 2, x + 15 : x + 45]
            upgrade = int(int((((corner[..., 0] <= 8) | (corner[..., 0] >= 172)) & (corner[..., 1] >= 150) & (corner[..., 2] >= 170)).sum()) >= DECK_PLUS_MIN)
            card = None if dim else _deck_card(img[y - DECK_HALF : y + DECK_HALF, x - DECK_HALF : x + DECK_HALF])
            row.append({"name": card.name if card else None, "card_id": card._id if card else None, "upgrade": upgrade, "dim": dim})
            if not dim:
                if card is None:
                    unread += 1
                else:
                    cards.append(card._id + ("+1" if upgrade else ""))
        rows.append(row)
    return {"cards": cards, "rows": rows, "unread": unread}


RESULT_TITLES = ("CLEAR", "PERFECT")  # 过关结果页的大标题：三星（1 位）是「PERFECT」（Mac 手毬 14 层 11-15，原来判成 unknown）


def _result_title(boxes):
    return next((b for w in RESULT_TITLES if (b := _find(boxes, w, (150, 320))) is not None), None)


def _result_fields(img: np.ndarray, boxes) -> dict:
    """结果页：过关（CLEAR / 三星时 PERFECT）、层号（大字）、本次星数（三颗星里粉色的个数）、按钮。"""
    hsv = cv2.cvtColor(img[550:660], cv2.COLOR_BGR2HSV)
    stars = 0
    for x in STAR_X:  # 三颗星：拿到的粉色（约 0.46–0.50），没拿到的灰色（0）
        cell = hsv[:, x - 45 : x + 45]
        stars += int((((cell[..., 0] >= 150) | (cell[..., 0] <= 8)) & (cell[..., 1] >= 80) & (cell[..., 2] >= 150)).mean() >= STAR_PINK_MIN)
    layer = read_number(img, LAYER_BOX)  # 「CLEAR」下面的大号层号
    out = {"cleared": _result_title(boxes) is not None, "layer": layer, "stars": stars}
    for key, word in (("retry", "再挑戦"), ("back", "一覧に戻る"), ("next", "次へ")):
        if (b := _find(boxes, word, (1080, 1260))) is not None:
            out[key] = _center(b)
    return out


NODE_STAR_X = (382, 462, 540)  # 层选择页居中放大那一层的三颗星（y≈795）；下面 y≥840 是粉色的报酬水晶，不算
NODE_STAR_Y = (765, 825)
LOCK_ORANGE_MIN = 0.55  # 「挑戦する」按钮橙色比例：能打 0.74，锁着（灰、带锁）0.37


def _layers_fields(img: np.ndarray, boxes) -> dict:
    """层选择页：偶像名（左上）、居中放大那一层的层号（大数字）、它的星数、「挑戦する」位置和是不是锁着的。
    左栏的「STAGE n 報酬」是最前沿的报酬，滑动后不跟着变，不能当层号。
    居中的是最前沿以上的层时没有「挑戦する」（实机帧 25：19 层）：challenge=None、locked=True。"""
    b = _find(boxes, "挑戦する", (900, 1100))
    name = next((t for t, bb in boxes if _center(bb)[1] < 200 and _center(bb)[0] < 220 and len(t) >= 3), None)
    layer = next((int(t) for t, bb in boxes if t.isdigit() and 690 <= _center(bb)[1] <= 745 and 380 <= _center(bb)[0] <= 560), None)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    y1, y2 = NODE_STAR_Y
    stars = 0
    for x in NODE_STAR_X:
        cell = hsv[y1:y2, x - 30 : x + 30]
        stars += int((((cell[..., 0] >= 150) | (cell[..., 0] <= 8)) & (cell[..., 1] >= 90) & (cell[..., 2] >= 150)).mean() >= STAR_PINK_MIN)
    if b is None:
        return {"idol": name, "stage": layer, "stars": stars, "locked": True, "challenge": None}
    cx, cy = _center(b)
    btn = hsv[cy - 20 : cy + 20, max(0, cx - 120) : cx + 80]
    orange = float(((btn[..., 0] >= 5) & (btn[..., 0] <= 22) & (btn[..., 1] >= 150)).mean())
    return {"idol": name, "stage": layer, "stars": stars, "locked": orange < LOCK_ORANGE_MIN, "challenge": [cx, cy]}


PT_RE = r"(\d{1,3}(?:,\d{3})+|\d+)[Pp][tTlI1]?"  # 「105,438Pt」「54,009pt」；高亮那行（自己）的 Pt 常读成「P1」（Mac 不合格页 11-14）
RANK_RE = r"([1-6IlS])(st|nd|rd|th)"  # 名次标签：「1st」常读成「Ist」、「5th」读成「Sth」


def _ranking_fields(boxes) -> dict:
    """考完的名次页（左上「3位」徽章 / 「不合格」+ 6 行「名次 名字 分数Pt」+「次へ」或 再挑戦 / 一覧に戻る）：各行（按 y 排）、
    自己的名次（徽章数字）。名次取同一行左边的「1st」「2nd」标签，标签没读到按行序。
    分数是 OCR 读的，偶尔错一位（105,438 → 105,458），只做记录；星数以结果页为准。"""
    import re

    pts = sorted(((b[1], int(m.group(1).replace(",", ""))) for t, b in boxes if (m := re.fullmatch(PT_RE, t))), key=lambda x: x[0])
    labels = [(_center(b)[1], {"I": 1, "l": 1, "S": 5}.get(m.group(1)) or int(m.group(1))) for t, b in boxes
              if _center(b)[0] < 150 and (m := re.fullmatch(RANK_RE, t))]  # fmt: skip
    names = sorted(((b[1], t) for t, b in boxes if 200 <= _center(b)[0] <= 400 and not re.fullmatch(PT_RE, t) and not re.search(r"\d", t)
                    and _center(b)[1] > 300), key=lambda x: x[0])  # fmt: skip
    rows = []
    for i, (y, score) in enumerate(pts):
        name = next((t for ny, t in names if 0 < y - ny < 70), None)
        rank = next((r for ly, r in labels if -60 < ly - y < 30), i + 1)
        rows.append({"rank": rank, "name": name, "score": score})
    badge = next((int(t) for t, b in boxes if t.isdigit() and len(t) == 1 and _center(b)[1] < 260 and _center(b)[0] < 200), None)
    nxt = _find(boxes, "次へ", (1080, 1260))
    return {"my_rank": badge, "rows": rows, "next": _center(nxt) if nxt else None}


def tower_dialog(dialog: dict, boxes) -> tuple[str, dict] | None:
    """偶像之路的弹窗：层详情「条件」（每颗星的条件原文，8a3a1e7 按原文查表）、再挑戦确认（选项 / 按钮 / 消耗词）。"""
    from hiro.runtime.tower import retry_cost

    title, body = dialog.get("title", ""), dialog.get("body", "")
    if "条件" in title and "位" in body + "".join(t for t, _ in boxes):
        import re

        # 星按名次给：1 位 ★★★、2 位以上 ★★、3 位以上 ★（8a3a1e7 11-14）；条件原文交 8a3a1e7 按表查（26 / 27 层★3 另有通关时条件）
        conds = sorted(((b[1], t.lstrip("・•").strip()) for t, b in boxes if "クリア" in t), key=lambda x: x[0])
        rows = [{"star": 4 - int(m.group(1)) if (m := re.search(r"(\d)位", t)) else None, "text": t} for _, t in conds]
        return "TOWER_LAYER_INFO", {"conditions": rows,
                                     "close": _center(c) if (c := _find(boxes, "閉じる", (1080, 1260))) else None}  # fmt: skip
    if "再挑戦" in title and "ステージを再挑戦" in body + "".join(t for t, _ in boxes):
        out = {"cost": retry_cost(dialog)}
        for key, word in (("rebuild", "編成して再挑戦"), ("same", "同じ編成で再挑戦"), ("cancel", "キャンセル"), ("ok", "OK")):
            if (b := _find(boxes, word, (900, 1260))) is not None:
                out[key] = _center(b)
        return "TOWER_RETRY_CONFIRM", out
    return None


def tower_page(img: np.ndarray, boxes) -> tuple[str, dict] | None:
    """偶像之路的非弹窗页面（考试页由 is_tower_exam 判）。"""
    if _find(boxes, "アイドルへの道", (0, 120)) and _find(boxes, "STAGE", (600, 1100)):
        return "TOWER_LIST", _list_fields(boxes)
    if _find(boxes, "ステージ別ランキング") and (_find(boxes, "挑戦する", (900, 1100)) or _find(boxes, "STAGE", (660, 720))):
        # 「STAGE」小字在居中大节点的层号上面（y≈690）；左栏「STAGE15 報酬」在 y≈745，不算
        return "TOWER_LAYERS", _layers_fields(img, boxes)
    if _find(boxes, "応援/トラブル", (0, 60)) and _find(boxes, "審査基準") and _find(boxes, "ターン", (60, 130)):
        f = _formation_fields(boxes, img)
        if f.get("page") == 2:
            f["deck"] = read_formation_deck(img)
        return "TOWER_FORMATION", f
    if _find(boxes, "編成中") and _find(boxes, "審査基準"):
        dec = next((b for t, b in boxes if "決定" in t and _center(b)[1] > 1050), None)
        return "TOWER_MEMORY_SELECT", {"decide": _center(dec) if dec else None}
    if _find(boxes, "次へ", (1080, 1260)) and _find(boxes, "位", (130, 260)) and sum(1 for t, _ in boxes if t.lower().endswith("pt")) >= 3:
        return "TOWER_RANKING", _ranking_fields(boxes)
    if _find(boxes, "再挑戦確認") or _find(boxes, "ステージを再挑戦"):
        return None  # 再挑戦确认框正在弹出（标题条还没认出来）：结果页在框后面还看得见，不能当结果页再处理一次（实机 r2）
    if (_find(boxes, "不合格", (100, 260)) and sum(1 for t, _ in boxes if __import__("re").fullmatch(PT_RE, t)) >= 3
            and (_find(boxes, "一覧に戻る", (1080, 1260)) or _find(boxes, "再挑戦", (1080, 1260)))):  # fmt: skip
        # 没过关的名次页（左上「不合格」，4–6 位）：没有「次へ」也没有 CLEAR 结果页，再挑戦 / 一覧に戻る 直接在这一页（Mac 稳定性测试 11-14）
        f = _ranking_fields(boxes)
        f["failed"] = True
        for key, word in (("retry", "再挑戦"), ("back", "一覧に戻る")):
            if (b := _find(boxes, word, (1080, 1260))) is not None:
                f[key] = _center(b)
        return "TOWER_RANKING", f
    if _result_title(boxes) and (_find(boxes, "一覧に戻る", (1080, 1260)) or _find(boxes, "再挑戦", (1080, 1260))):
        return "TOWER_RESULT", _result_fields(img, boxes)
    return None
