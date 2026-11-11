# 来源：digital-hiro src/hiro/vision/deck.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""牌组：培育中「所持スキルカード」一览页（DECK_LIST）与相談削除 / 強化、换卡等同款卡格网格。

入口：培育画面底栏的文档图标（选行动页、三选一、相談页都有），打开后默认是「スキルカード(N)」标签。
画面（720x1280）：卡格 4 列、按获得顺序排（= 协议 deckProduceCardIndex），超过约 3 行要上下滑；底部标签写着总张数 N。

每格：
- 定位：卡缩略图底部的类别字母 A / M / T（KAA 模板 R.InProduce.A / M / T），按字母推卡格。
  一览页格宽 141、横向间距 161；相談削除 / 强化 / 换卡弹窗格宽 118、间距 147，按字母间距判断是哪种。
- 定卡：KAA match_card_region（三选一、相談货架同一套缩略图库）。认不出 card_id 为 None。
- 强化：卡格右侧中部的红黄「+」（素材 ProduceAtlas/icon_enhanced），数高饱和红色像素。
  3 张样本 36 格：有「+」71–169 像素（被效果图标盖住一半时 122），没有 ≤3；橙色插图在宽松阈值下会到 107，所以只用严格阈值。
  介于两者之间（压暗、遮挡）→ None。二次及以上强化（++）的缩略图还没有样本：看到「+」一律记 1，field_meta 注明。
- 定制：缩略图底部中央 CustomIcon 的绿色圆形数字徽章（样本 2 张 413–438 像素，其余 0）→ customized=True。

只认「整格可见」的卡（卡格上缘不在视口上方、字母不被底部标签栏挡住）；一屏装不下时 complete=False，
由操作层滑到底再读一屏，用 merge_deck_pages 按行号拼接。
"""

from functools import cache
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

TEMPLATES = Path(__file__).with_name("templates")

BUTTON_STRIP = (380, 1130, 720, 1270)  # 底栏右侧：礼盒图标在选行动 / 相談 / 选饮料约 (558,1213)、换卡 / 三选一弹窗约 (654,1200)
BUTTON_MIN = 0.8  # 白色字形相关：有图标 0.86–0.92，没有 ≤0.31
TITLE_BOX = (40, 25, 300, 90)  # 「所持スキルカード」白色标题条
TITLE_MIN = 0.75
TAB_BOX = (60, 1015, 340, 1075)  # 「スキルカード(N)」标签
TAB_ACTIVE_MIN_PX = 200  # 选中的标签文字是橙色（实测 738 个高饱和像素，未选中 0）
LIST_VIEW = (0, 395, 720, 1010)  # 一览页卡格视口（标题下的详情框以下、标签栏以上）

# 卡格几何：按类别字母左上角 → 卡格左上角的偏移与格宽
GEOMETRY = {
    "list": {"dx": -62, "dy": -124, "cell": 141, "x0": 48, "pitch": 161, "row_pitch": 153.3},  # 所持スキルカード
    "dialog": {"dx": -51, "dy": -100, "cell": 118, "x0": 81, "pitch": 147, "row_pitch": 147.5},  # 相談削除 / 強化、换卡
    "guidance": {"dx": -64, "dy": -124, "cell": 141, "x0": 49, "pitch": 160, "row_pitch": 160},  # 特別指導选卡（q10 实机：字母 x 113+160k、y 761+160k）
}
PITCH_SPLIT = 154
ROW_TOL = 20  # 同一行字母的 y 容差

DIM_V_MAX = 160  # 格内平均亮度：正常 200–241，压暗 103–117
DIM_TARGET_V, DIM_GAIN_MAX = 225, 2.5
PLUS_BOX = (0.33, 0.66, 0.74, 1.0)  # 「+」在卡格内的相对位置 (y1, y2, x1, x2)
PLUS_MIN_PX_125 = 40  # 125 像素卡格时的最少红色像素（与相談货架同一阈值），按面积缩放；低于 1/4 记为没有
CUSTOM_BOX = (0.62, 0.86, 0.36, 0.64)  # 定制徽章 (y1, y2, x1, x2)
CUSTOM_MIN_PX_125 = 120
COL_TOL = 8  # 卡格左缘 / 上缘与网格的最大偏差（实测 ≤2）；超出的是标签栏 / 按钮上的误检字母


@cache
def _tpl(name: str) -> MatLike:
    return cv2.imread(str(TEMPLATES / f"{name}.png"))


def _white(img: MatLike) -> MatLike:
    return cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), (0, 0, 200), (180, 60, 255))


def find_deck_button(img: MatLike) -> tuple[int, int] | None:
    """培育画面底栏打开「所持スキルカード」一览的礼盒图标（KAA InProduce.OwnedCardsButton「右下角购物包按钮」）中心；没有时 None。
    退出：一览底部「閉じる」（约 (360,1160)，KAA OwnedSkillCardsDialog.ButtonClose）。
    注意：左边的文档图标是行动记录（「1日目」日志页，qualify7 实机误开过），不是牌组。"""
    x1, y1, x2, y2 = BUTTON_STRIP
    tpl = _white(_tpl("owned_cards_button"))
    res = cv2.matchTemplate(_white(img[y1:y2, x1:x2]), tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(res)
    if score < BUTTON_MIN:
        return None
    return x1 + x + tpl.shape[1] // 2, y1 + y + tpl.shape[0] // 2


def is_deck_list(img: MatLike) -> bool:
    """「所持スキルカード」一览页，且当前是「スキルカード」标签（不是 P アイテム）。"""
    x1, y1, x2, y2 = TITLE_BOX
    res = cv2.matchTemplate(img[y1:y2, x1:x2], _tpl("deck_list_title"), cv2.TM_CCOEFF_NORMED)
    if float(res.max()) < TITLE_MIN:
        return False
    return _tab_active(img)


def _tab_active(img: MatLike) -> bool:
    x1, y1, x2, y2 = TAB_BOX
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    orange = (hsv[:, :, 0] >= 5) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 180)
    return int(orange.sum()) >= TAB_ACTIVE_MIN_PX


def read_deck_size(img: MatLike) -> int | None:
    """底部标签「スキルカード(16)」里的张数。"""
    import re

    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = TAB_BOX
    text = "".join(t for t, _ in ocr(img[y1:y2, x1:x2])).replace("（", "(").replace("）", ")")
    m = re.search(r"\((\d+)\)", text) or re.search(r"カード\D*(\d+)", text)
    return int(m.group(1)) if m else None


def _marks(img: MatLike, region: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """region 内类别字母 A / M / T 的左上角，按行再按 x 排序。直接在 img 上匹配（KAA 模板与默认阈值），不读设备截图。"""
    from kaa.tasks import R
    from kotonebot.backend.image import find_all
    from kotonebot.primitives import Rect

    x1, y1, x2, y2 = region
    rect = Rect(x1, y1, x2 - x1, y2 - y1)
    found = []
    for prefab in (R.InProduce.A, R.InProduce.M, R.InProduce.T):
        hits = find_all(img, prefab.template.pixels, rect=rect, threshold=prefab.threshold, colored=prefab.colored, debug_output=False)
        found += [(r.rect.x1, r.rect.y1) for r in hits]
    found.sort(key=lambda p: (p[1], p[0]))
    rows: list[list[tuple[int, int]]] = []
    for p in found:
        if rows and abs(rows[-1][0][1] - p[1]) <= ROW_TOL:
            rows[-1].append(p)
        else:
            rows.append([p])
    return [p for row in rows for p in sorted(row)]


def _geometry(marks: list[tuple[int, int]]) -> dict:
    xs = sorted({x for x, _ in marks})
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 60]
    if not gaps:
        return GEOMETRY["list"]
    return GEOMETRY["list"] if min(gaps) >= PITCH_SPLIT else GEOMETRY["dialog"]


def _hsv(cell: MatLike, box: tuple[float, float, float, float]):
    n = cell.shape[0]
    y1, y2, x1, x2 = (int(round(v * n)) for v in box)
    hsv = cv2.cvtColor(cell[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2], (n / 125) ** 2


PLUS_MAX_H, PLUS_MAX_W = 0.17, 0.19  # 「+」红色部分（下半截渐变成黄）最大连通块高 / 宽占格宽：实测 ≤0.15 / ≤0.18


def plus_shape_ok(red: np.ndarray, cell_size: int) -> bool:
    """红色像素的最大连通块像不像「+」的红色上半截：又扁又小。偶像插画卡的红色块（红伞，数据确认 enhance-kaa 第 7 格）高 0.21。"""
    k, _, st, _ = cv2.connectedComponentsWithStats(red.astype(np.uint8), 8)
    if k <= 1:
        return False
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    return st[i][cv2.CC_STAT_HEIGHT] <= PLUS_MAX_H * cell_size and st[i][cv2.CC_STAT_WIDTH] <= PLUS_MAX_W * cell_size


def _plus(cell: MatLike) -> int | None:
    """强化次数：看得清红黄「+」→ 1，明显没有 → 0，介于两者之间、或红色够多但形状不像「+」（插画里的红色）→ None。"""
    h, s, v, area = _hsv(cell, PLUS_BOX)
    mask = ((h <= 12) | (h >= 170)) & (s >= 140) & (v >= 150)
    red = int(mask.sum())
    need = PLUS_MIN_PX_125 * area
    if red >= need:
        return 1 if plus_shape_ok(mask, cell.shape[0]) else None
    return 0 if red < need / 4 else None


def _customized(cell: MatLike) -> bool:
    h, s, v, area = _hsv(cell, CUSTOM_BOX)
    green = int(((h >= 45) & (h <= 85) & (s >= 120) & (v >= 100)).sum())
    return green >= CUSTOM_MIN_PX_125 * area


DIM_VOTE_GAINS = (0.8, 0.9, 1.0)  # 压暗格按这几档提亮各认一次（乘在 gain 上），至少 2 次一致才算


def _dimmed_card(crop: MatLike, gain: float, match):
    """压暗的格子（强化对话框里已强化的卡）：原图直接认会认成别的卡（Windows 第五局「えいえいおー」→「潜在能力」），
    提亮过头也会（「アイドル宣言」→「ハートの合図」/「準備運動」）。几档提亮投票，多数一致才用，否则 None（拼接时当通配）。"""
    from collections import Counter

    hits = [c for k in DIM_VOTE_GAINS if (c := match(cv2.convertScaleAbs(crop, alpha=gain * k))) is not None]
    if not hits:
        return None
    best, n = Counter(c._id for c in hits).most_common(1)[0]
    return next(c for c in hits if c._id == best) if n >= 2 else None


def read_card_grid(img: MatLike, region: tuple[int, int, int, int], geometry: str | None = None) -> list[dict]:
    """region 内整格可见的卡：[{index, row, col, card_id, name, upgrade, customized, rect, center}]，从左到右、从上到下。

    index 是本屏内的顺序；row 从 0 起，是本屏可见的第几行（不是牌组里的绝对行号）。
    """
    from kaa.game_ui.skill_card_select import match_card_region

    marks = _marks(img, region)
    if not marks:
        return []
    g = GEOMETRY[geometry] if geometry else _geometry(marks)
    cell, out = g["cell"], []
    row_ys: list[int] = []
    for mx, my in marks:
        x, y = mx + g["dx"], my + g["dy"]
        col = round((x - g["x0"]) / g["pitch"])
        if y < region[1] - 4 or y + cell > region[3] + 8 or not 0 <= col <= 3 or abs(x - g["x0"] - col * g["pitch"]) > COL_TOL:
            continue  # 卡格不整格在视口内（滚动到一半的行）或不在网格列上（误检）
        if row_ys:
            k = round((y - row_ys[0]) / g["row_pitch"])
            if abs(y - row_ys[0] - k * g["row_pitch"]) > COL_TOL:
                continue  # 不在网格行上
        if not row_ys or abs(row_ys[-1] - y) > ROW_TOL:
            row_ys.append(y)
        crop = img[y : y + cell, x : x + cell]
        v_mean = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 2].mean())
        dimmed = v_mean < DIM_V_MAX
        # 压暗的格子（强化对话框里不能再强化的卡、特別指導里不能定制的卡）：「+」与定制徽章的颜色跟着变暗，先按亮度比例提亮再判
        #（main10 相談強化：届いて！+ / デイドリーミング+ / 紙吹雪+ 压暗后 upgrade 全读成 0）
        gain = min(DIM_GAIN_MAX, DIM_TARGET_V / max(v_mean, 1.0))
        look = cv2.convertScaleAbs(crop, alpha=gain) if dimmed else crop
        card = _dimmed_card(crop, gain, match_card_region) if dimmed else match_card_region(crop)
        upgrade = _plus(look) if card is not None else None
        out.append({
            "index": len(out), "row": len(row_ys) - 1, "col": int(col),
            "card_id": card._id if card else None, "name": card.name if card else None,
            "upgrade": upgrade, "customized": _customized(look) if card is not None else None, "dimmed": dimmed,
            "rect": (int(x), int(y), int(cell), int(cell)), "center": (int(x + cell // 2), int(y + cell // 2)),
        })  # fmt: skip
    return out


def read_deck_list(img: MatLike) -> dict:
    """所持スキルカード一览页（一屏）。

    返回 {deck_size, cards, rows_visible, complete, deck, problems}：
    - deck_size：标签上的总张数（读不到 None）；
    - cards：本屏整格可见的卡（见 read_card_grid）；
    - complete / deck：本屏看到了全部 deck_size 张、每张都认出且强化次数确定时，deck 为
      [{card_id, upgrade, customized}]（ProduceState.deck 格式，一览顺序 = deckProduceCardIndex），否则 None；
    - problems：deck 为 None 的原因。
    """
    size = read_deck_size(img)
    cards = read_card_grid(img, LIST_VIEW, "list")  # 一览页固定 list 几何：标签栏 / 卡面图案上的误检字母会把自动推断带偏成 dialog（第二十局实机一屏只认出 4 张）
    out = {"deck_size": size, "cards": cards, "rows_visible": (cards[-1]["row"] + 1) if cards else 0}
    out.update(_finish(cards, size, []))
    return out


def _finish(cards: list[dict], size: int | None, problems: list[str]) -> dict:
    problems = list(problems)
    if size is None:
        problems.append("没有读到总张数")
    elif len(cards) < size:
        problems.append(f"只看到 {len(cards)} / {size} 张（要滑动再读）")
    elif len(cards) > size:
        problems.append(f"看到 {len(cards)} 张，多于标签的 {size} 张")
    bad = [i for i, c in enumerate(cards) if c["card_id"] is None]
    if bad:
        problems.append(f"第 {[i + 1 for i in bad]} 张认不出")
    vague = [i for i, c in enumerate(cards) if c["card_id"] is not None and c["upgrade"] is None]
    if vague:
        problems.append(f"第 {[i + 1 for i in vague]} 张强化标记看不清")
    ok = not problems
    return {"complete": ok, "deck": [_deck_card(c) for c in cards] if ok else None, "problems": problems}


def _deck_card(c: dict) -> dict:
    return {"card_id": c["card_id"], "upgrade": c["upgrade"], "customized": c["customized"]}


def merge_deck_pages(top: dict, bottom: dict) -> dict:
    """一屏装不下时：top 为刚打开（滚动在最上）时的 read_deck_list，bottom 为滑到底后的，按行号拼成完整牌组。

    总行数 = ceil(deck_size / 4)。top 的可见行是第 1..a 行，bottom 的可见行是最后 b 行。
    中间有行两屏都没看到、或重叠的行两屏读得不一样时，deck 为 None，problems 说明原因。
    返回与 read_deck_list 同格式（cards 的 index 为牌组下标，row 为牌组里的绝对行号）。
    """
    size = top.get("deck_size") or bottom.get("deck_size")
    problems: list[str] = []
    if not size:
        return {"deck_size": None, "cards": [], "rows_visible": 0, **_finish([], None, [])}
    n_rows = -(-size // 4)

    def by_row(read: dict) -> list[list[dict]]:
        got: list[list[dict]] = []
        for c in read.get("cards") or []:
            while len(got) <= c["row"]:
                got.append([])
            got[c["row"]].append(c)
        return got

    rows = dict(enumerate(by_row(top)))
    bottom_rows = by_row(bottom)
    if bottom_rows and len(bottom_rows[-1]) != (size - 4 * (n_rows - 1)):
        problems.append(f"底屏最后一行 {len(bottom_rows[-1])} 张，按总张数应为 {size - 4 * (n_rows - 1)} 张（可能没滑到底）")
    first = n_rows - len(bottom_rows)
    for j, row in enumerate(bottom_rows):
        i = first + j
        if i < 0:
            continue
        if i in rows and [_key(c) for c in rows[i]] != [_key(c) for c in row]:
            problems.append(f"第 {i + 1} 行两屏读得不一样")
        rows.setdefault(i, row)
    missing = [i + 1 for i in range(n_rows) if i not in rows]
    if missing:
        problems.append(f"第 {missing} 行两屏都没看到")
    cards = [dict(c, row=i) for i in range(n_rows) for c in rows.get(i, [])]
    cards = [dict(c, index=k) for k, c in enumerate(cards)]
    return {"deck_size": size, "cards": cards, "rows_visible": n_rows - len(missing), **_finish(cards, size, problems)}


def _key(c: dict) -> tuple:
    return c["card_id"], c["upgrade"]


def deck_diff(expected: list[dict], observed: list[dict]) -> dict:
    """推算的牌组 vs 牌组页读到的卡（多重集比较，不看顺序和定制）→ 复核结论。

    observed 可以是 read_deck_list / merge_deck_pages 的 cards（含认不出的卡：card_id 或 upgrade 为 None）。
    返回 {same, missing, extra, upgraded, unknown}：
    - missing：推算里有、画面上没有的 {card_id, upgrade}；extra：画面上有、推算里没有的；
    - upgraded：同一张卡等级不同（missing / extra 里各一张、card_id 相同）→ {card_id, expected, observed}，已从 missing / extra 里去掉；
    - unknown：画面上认不出的张数；
    - same：True 完全一致；False 有对不上的（认不出的卡也解释不了）；None 差异可能只是认不出的那几张。
    """
    from collections import Counter

    exp = Counter((c["card_id"], c["upgrade"]) for c in expected)
    known = [c for c in observed if c.get("card_id") is not None and c.get("upgrade") is not None]
    got = Counter((c["card_id"], c["upgrade"]) for c in known)
    unknown = len(observed) - len(known)
    missing, extra = list((exp - got).elements()), list((got - exp).elements())
    upgraded = []
    for cid, up in list(extra):
        hit = next((m for m in missing if m[0] == cid), None)
        if hit is not None:
            missing.remove(hit)
            extra.remove((cid, up))
            upgraded.append({"card_id": cid, "expected": hit[1], "observed": up})
    if not missing and not extra and not upgraded and unknown == 0:
        same = True
    elif extra or upgraded or len(missing) > unknown or len(observed) != len(expected):
        same = False
    else:
        same = None
    as_cards = lambda xs: [{"card_id": c, "upgrade": u} for c, u in xs]  # noqa: E731
    return {"same": same, "missing": as_cards(missing), "extra": as_cards(extra), "upgraded": upgraded, "unknown": unknown}


# ---------------------------------------------------------------- 选卡弹窗：删除 / 强化 / 换卡第 2 步的选中项
CURSOR_EXPAND, CURSOR_CORNER = 16, 30  # 选中格的橙色角框在卡格外侧约 16 像素、每角约 30 像素
CURSOR_MIN_PX_118 = 600  # 118 像素卡格：选中 1103–1251，未选 ≤190（含橙色插图）
DETAIL_BOX = (170, 95, 720, 160)  # 顶部详情栏第一行（卡名）


@cache
def _cards_by_full_name() -> dict[str, list[tuple[str, int]]]:
    """主数据卡名（带「+」，如 距離感の基本+）→ [(card_id, upgrade)]。"""
    from kaa.db.skill_card import SkillCard

    out: dict[str, list[tuple[str, int]]] = {}
    for c in SkillCard.all():
        out.setdefault(c.name, []).append((c._id, int(c.upgrade_count)))
    return out


# 统一长音（RapidOCR 常把「ー」读成「一」）和半角 / 全角感叹号、问号（「届いて!」）后再查卡名
_DASHES = str.maketrans({"一": "ー", "－": "ー", "-": "ー", "―": "ー", "ｰ": "ー", "!": "！", "?": "？"})


@cache
def _cards_by_dash_name() -> dict[str, tuple[str, list[tuple[str, int]]]]:
    """长音符号 / 感叹号问号统一后的卡名 → (主数据原名, [(card_id, upgrade)])；两张卡统一后撞名的不收（取不定）。"""
    out: dict[str, tuple[str, list[tuple[str, int]]]] = {}
    clash: set[str] = set()
    for name, hits in _cards_by_full_name().items():
        key = name.translate(_DASHES)
        if key in out and out[key][0] != name:
            clash.add(key)
        out[key] = (name, hits)
    return {k: v for k, v in out.items() if k not in clash}


def card_from_name(text: str, candidates: set[str] | None = None) -> dict | None:
    """卡名（OCR，带「+」）精确命中主数据 → {card_id, name, upgrade}；同名多张时按 candidates（如本页卡格认出的 id）取，取不定给 None。
    精确没命中时把长音符号统一再查（RapidOCR 常把「ー」读成「一」：DMM dmm2 特別指導「えいえいお一+」，Windows 回归集 sg-dmm2-2c）。"""
    name = text.replace("＋", "+").replace(" ", "").strip()
    hits = _cards_by_full_name().get(name) or []
    if not hits and (alt := _cards_by_dash_name().get(name.translate(_DASHES))) is not None:
        name, hits = alt
    if candidates:
        hits = [h for h in hits if h[0] in candidates] or hits
    if len({h[0] for h in hits}) != 1:
        return None
    card_id, upgrade = hits[0]
    return {"card_id": card_id, "name": name.rstrip("+"), "upgrade": upgrade}


def _cursor_px(hsv: MatLike, rect: tuple[int, int, int, int]) -> int:
    x, y, n, _ = rect
    e, size = CURSOR_EXPAND, CURSOR_CORNER
    orange = (hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 22) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 180)
    total = 0
    for cx, cy in ((x - e, y - e), (x + n + e - size, y - e), (x - e, y + n + e - size), (x + n + e - size, y + n + e - size)):
        total += int(orange[max(0, cy) : cy + size, max(0, cx) : cx + size].sum())
    return total


# 相談「削除 / 強化」、换卡第 2 步的选卡对话框卡格区域（720x1280）。削除：详情框下 y≈398 起，底部按钮从 y≈1120 起，第 5 行（986–1104）整格可见；
# 強化：上方两个详情框，卡格 y≈698 起，只露出约 3 行（右侧有滚动条）。与 kaa_patches.CARD_OP_REGION 一致
REMOVAL_GRID = (0, 380, 720, 1110)
ENHANCE_GRID = (0, 690, 720, 1115)
SCROLL_X = 683  # 卡格右侧滚动条的滑块列：比左右两侧亮 ≥ 25
SCROLL_THUMB_MIN = 40


def dialog_scroll(img: MatLike, region: tuple[int, int, int, int], x: int = SCROLL_X) -> dict:
    """选卡对话框的滚动条 → {scrollable, thumb: [y1, y2] | None, at_top}。没有滑块 = 整副牌组都在这一屏。
    到底与否以「滑一次没读到新行」为准（merge_dialog_screens 的 new_rows == 0），滑块位置只作参考（还没有滚动中的实机样本）。"""
    _, y1, _, y2 = region
    v = img[y1:y2, x - 4 : min(img.shape[1], x + 5)].astype(int).max(axis=2)
    if v.shape[1] < 9:
        return {"scrollable": False, "thumb": None, "at_top": True}
    diff = v[:, 4] - (v[:, 0] + v[:, 8]) / 2
    on = (diff >= 25) & (v[:, 4] >= 120)
    best, start = None, None
    for i, flag in enumerate(list(on) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= SCROLL_THUMB_MIN and (best is None or i - start > best[1] - best[0]):
                best = (start, i)
            start = None
    if best is None:
        return {"scrollable": False, "thumb": None, "at_top": True}
    return {"scrollable": True, "thumb": [y1 + best[0], y1 + best[1]], "at_top": best[0] <= 15}


def _cell_match(a: dict, b: dict) -> bool | None:
    """两屏同一格：任一边没认出 → None（通配）；都认出 → 卡相同（强化有一边不确定也算同）。"""
    if a.get("card_id") is None or b.get("card_id") is None:
        return None
    return a["card_id"] == b["card_id"] and (a.get("upgrade") is None or b.get("upgrade") is None or a["upgrade"] == b["upgrade"])


BLOCK_CONFLICT_MIN_ROWS = 2  # 至少几行的重叠块才容许一格两屏认得不一样


def _block_match(ra: list[list[dict]], rb: list[list[dict]]) -> bool:
    """两块行对得上：每行张数相同、每格相同或通配，且两边都认出且相同的格数 ≥ 行数（全是通配的块不作对齐依据；
    第十四局第 1 屏末行两格都没认出，按行要求会整块对不上）。
    ≥ 2 行的块容许一格两屏认得不一样（卡图识别偶尔认错一格；Windows 第四局 MuMu 换卡阶段 2 每次都因此「第 3 屏没有重叠的行」，
    拼出 27–28 张），但其余格要几乎全对上（相同格 ≥ 3×行数 − 1）。"""
    hits = conflicts = 0
    for x, y in zip(ra, rb, strict=True):
        if len(x) != len(y):
            return False
        res = [_cell_match(a, b) for a, b in zip(x, y, strict=True)]
        hits += res.count(True)
        conflicts += res.count(False)
    if conflicts == 0:
        return hits >= len(ra)
    return conflicts == 1 and len(ra) >= BLOCK_CONFLICT_MIN_ROWS and hits >= 3 * len(ra) - 1


DECK_SIZE_TOL = 2  # 拼出的张数与记账张数最多差几张（记账会漏记少数事件）


def merge_dialog_screens(screens: list[list[dict]], expected: int | None = None) -> dict:
    """选卡对话框滚动读的多屏卡格（每屏 read_card_grid / read_card_dialog 的 cards，从上往下滑）→ 整副牌组。

    相邻两屏按行对齐：取「前一屏末尾 k 行 ≈ 这一屏开头 k 行」的最大 k（没认出的格当通配，但每行至少一格两边都认出且相同），
    后面的行是新的（整块里认出且相同的格 ≥ 行数）；对齐的行里，前一屏没认出的格用这一屏认出的补上。
    返回 {cards: [{deck_index, row, col, card_id, name, upgrade, customized, dimmed, screen}], new_rows, problems, complete}：
    deck_index = 牌组里的全局序号（对话框按牌组顺序排）、row 为全局行号；dimmed = 压暗（强化对话框里 = 满级不能强化）；
    screen = 这张卡（最终采用的那格）来自第几屏（往回滑找卡时参考）；某屏 new_rows == 0 说明已经滑到底（滑了但画面没变也是 0）。
    complete=False：两屏对不上（滑过了头）、末行之后还有行、或与 expected（记账张数）差超过 DECK_SIZE_TOL —— 这时不要交给决策。
    没认全的卡另列 unknown（也写进 problems），不影响 complete。
    第十四局相談删除：18 张一屏放得下，滑了一次画面没变，两屏没认出的格不同 → 旧版对不上、整屏接在后面，拼成 36 张。"""
    rows: list[list[dict]] = []
    new_rows, problems, problems_soft = [], [], []  # problems_soft：只提示，不影响 complete
    for n, cards in enumerate(screens):
        by_row: dict[int, list] = {}
        for c in cards:
            by_row.setdefault(c["row"], []).append({**c, "screen": n})
        screen_rows = [sorted(by_row[r], key=lambda c: c["col"]) for r in sorted(by_row)]
        k = 0
        for size in range(min(len(rows), len(screen_rows)), 0, -1):
            if _block_match(rows[len(rows) - size :], screen_rows[:size]):
                k = size
                break
        for i in range(k):  # 对齐的行：用这一屏认出的补上前一屏没认出的格；两屏认得不一样的格不知道信哪边，记成没认全
            old = rows[len(rows) - k + i]
            for j, (x, y) in enumerate(zip(old, screen_rows[i], strict=True)):
                if _cell_match(x, y) is False:
                    old[j] = {**x, "card_id": None, "name": None, "upgrade": None}
                    problems_soft.append(f"第 {n + 1} 屏第 {i + 1} 行第 {j + 1} 格和上一屏认得不一样（{x.get('name')} / {y.get('name')}），记成没认全")
                elif (x.get("card_id") is None or x.get("upgrade") is None) and y.get("card_id") is not None and y.get("upgrade") is not None:
                    old[j] = y
        if rows and k == 0 and screen_rows:
            problems.append(f"第 {n + 1} 屏和上一屏没有重叠的行（滑过了头？），按接在后面处理")
        if any(len(r) < 4 for r in rows) and screen_rows[k:]:
            problems.append(f"第 {n + 1} 屏：前面已出现不满 4 张的末行，后面却还有新行")
        rows.extend(screen_rows[k:])
        new_rows.append(len(screen_rows) - k)
    out = []
    for r, row in enumerate(rows):
        for c in row:
            out.append({"deck_index": len(out), "row": r, "col": c["col"], "card_id": c["card_id"], "name": c.get("name"),
                        "upgrade": c["upgrade"], "customized": c.get("customized"), "dimmed": c.get("dimmed"), "screen": c["screen"]})  # fmt: skip
    if expected is not None and abs(len(out) - expected) > DECK_SIZE_TOL:
        problems.append(f"拼出 {len(out)} 张，记账 {expected} 张，差太多")
    unknown = [c["deck_index"] for c in out if c["card_id"] is None or c["upgrade"] is None]
    complete = not problems  # 个别卡没认全不算拼接错（决策不会选认不出的卡），另列 unknown
    problems.extend(problems_soft)
    if unknown:
        problems.append(f"第 {unknown} 张没认全")
    return {"cards": out, "new_rows": new_rows, "problems": problems, "unknown": unknown, "complete": complete}


def merge_deck_list(screens: list) -> dict:
    """「所持スキルカード」一览页从上往下滑的多屏读数（每屏 read_deck_list，第一屏 = 刚打开、滚动在最上）→ 整副牌组（DECK_LIST 字段）。

    本战开局写 produce.deck 用（总控 2026-11-03：第十九局保护卡 / 必拿按空牌组算）。按重叠行拼接（merge_dialog_screens，
    expected = 标签张数）；只有拼出的张数恰好等于标签、每张都认出且强化次数确定时才给 deck，否则 deck=None、problems 说明原因。
    重叠拼接不成立时退回 merge_deck_pages（首屏 + 末屏按行号拼，适合两屏正好盖满的 22 张）。
    screens 的元素可以是截图（720x1280，这里调 read_deck_list），也可以是已读好的 read_deck_list 结果。
    滑动建议每次约 2 行（行距约 154px，滑 300px）：相邻两屏重叠 1 行才能互相核对；滑 450px（约 2.9 行）两屏没有整行重叠，
    22 张还能靠首屏 + 末屏拼，25 张以上中间会漏行。
    返回 read_deck_list 同格式，另加 screens（屏数）；cards 带全局 deck_index / row。"""
    screens = [s if isinstance(s, dict) else read_deck_list(s) for s in screens]
    size = next((r.get("deck_size") for r in screens if r.get("deck_size")), None)
    merged = merge_dialog_screens([r.get("cards") or [] for r in screens], expected=size)
    cards = [{**c, "index": c["deck_index"]} for c in merged["cards"]]
    problems = [p for p in merged["problems"] if "没认全" not in p]  # 认不全由 _finish 统一报
    if size is not None and len(cards) != size:
        problems.append(f"拼出 {len(cards)} 张，标签 {size} 张")
    out = {"deck_size": size, "cards": cards, "rows_visible": (cards[-1]["row"] + 1) if cards else 0, "screens": len(screens)}
    out.update(_finish(cards, size, problems))
    if not out["complete"] and len(screens) >= 2:
        alt = merge_deck_pages(screens[0], screens[-1])
        if alt.get("complete"):
            return {**alt, "screens": len(screens), "problems": [f"重叠拼接不成立（{'；'.join(out['problems'])}），按首屏 + 末屏行号拼"]}
    return out


def read_card_dialog(img: MatLike, region: tuple[int, int, int, int] = REMOVAL_GRID) -> dict:
    """相談削除 / 强化、换卡第 2 步等「从牌组里选一张」的弹窗。

    返回 {cards, selected, selected_card, detail, agree}：
    - cards：卡格（read_card_grid）；selected：带橙色角框的格子 index（没有选中 None）；
    - detail：顶部详情栏卡名命中主数据的结果 {card_id, name, upgrade}（没选中时栏里是「スキルカードが選択されていません」→ None）；
    - selected_card：选中的卡。卡格和详情栏都读到时要一致（agree=True），以详情栏为准（带强化次数）；
      只有一边读到时用那一边；两边不一致时 agree=False、selected_card=None（不猜）。
    """
    from hiro.vision.ocr import ocr_boxes

    cards = read_card_grid(img, region)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    px = [_cursor_px(hsv, c["rect"]) for c in cards]
    need = CURSOR_MIN_PX_118 * ((cards[0]["rect"][2] / 118) ** 2 if cards else 1)
    hot = [i for i, v in enumerate(px) if v >= need]
    selected = hot[0] if len(hot) == 1 else None
    x1, y1, x2, y2 = DETAIL_BOX
    ids = {c["card_id"] for c in cards if c["card_id"]}
    detail = next((hit for t, _, _ in ocr_boxes(img[y1:y2, x1:x2]) if (hit := card_from_name(t, ids))), None)
    cell = cards[selected] if selected is not None else None
    agree = None
    if cell is not None and detail is not None:
        agree = cell["card_id"] == detail["card_id"] and cell["upgrade"] in (detail["upgrade"], None)
    if agree is False:
        chosen = None
    elif detail is not None:
        chosen = detail
    elif cell is not None and cell["card_id"] and cell["upgrade"] is not None:
        chosen = {"card_id": cell["card_id"], "name": cell["name"], "upgrade": cell["upgrade"]}
    else:
        chosen = None
    return {"cards": cards, "selected": selected, "selected_card": chosen, "detail": detail, "agree": agree}


# ---------------------------------------------------------------- 确认弹窗：相談「交換確認」、「スキルカード削除」
CONFIRM_KINDS = {"exchange": "confirm_exchange", "delete": "confirm_delete"}  # 标题白字模板
CONFIRM_STRIP = (30, 440, 380, 760)  # 标题所在范围（弹窗高度随内容变，标题 y 在 527–636 之间）
CONFIRM_MIN = 0.75
CONFIRM_BODY = (0, 560, 720, 1200)


def _match_white_tpl(img: MatLike, name: str, box: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = box
    res = cv2.matchTemplate(_white(img[y1:y2, x1:x2]), _white(_tpl(name)), cv2.TM_CCOEFF_NORMED)
    return float(np.nan_to_num(res).max())


def confirm_kind(img: MatLike) -> str | None:
    """交換確認 → exchange，スキルカード削除 → delete，都不是 → None。"""
    scores = {k: _match_white_tpl(img, t, CONFIRM_STRIP) for k, t in CONFIRM_KINDS.items()}
    kind, score = max(scores.items(), key=lambda kv: kv[1])
    return kind if score >= CONFIRM_MIN else None


@cache
def _drinks_by_name() -> dict[str, str]:
    from kaa.db.drink import Drink

    return {d.name: d.id for d in Drink.all()}


def read_confirm_dialog(img: MatLike) -> dict | None:
    """相談购买 / 删卡的确认弹窗：{kind, item: {kind: card|drink, card_id|drink_id, name, upgrade?}, p_before, p_after, price, sale}。
    不是这类弹窗时 None。读不到的项为 None；price = p_before - p_after（画面上的实付价，SALE 时是折后价）。"""
    import re

    from hiro.vision.ocr import ocr_boxes

    kind = confirm_kind(img)
    if kind is None:
        return None
    x1, y1, x2, y2 = CONFIRM_BODY
    boxes = [(t.replace(" ", ""), (bx1 + x1, by1 + y1, bx2 + x1, by2 + y1)) for t, _, (bx1, by1, bx2, by2) in ocr_boxes(img[y1:y2, x1:x2])]
    item = None
    for t, _ in boxes:
        if (card := card_from_name(t)) is not None:
            item = {"kind": "card", **card}
            break
        if t in _drinks_by_name():
            item = {"kind": "drink", "drink_id": _drinks_by_name()[t], "name": t}
            break
    # 「270→221」「116 → 16」在大号 P 硬币图标右侧，可能拆成几个框；效果说明里的图标也会被认成「→」，
    # 所以以 P 图标（高 ≥40）所在行为准，找不到再用最后一个带「→」的框
    # RapidOCR 读不出「→」、P 图标框也偏小（高 35，Vision 54；小号价格图标约 26）
    coin = next((b for t, b in boxes if t == "P" and b[3] - b[1] >= 30), None)
    arrow = coin or next((b for t, b in reversed(boxes) if "→" in t), None)
    if arrow is None:  # 都找不到：取最靠下、右半边有两个纯数字的一行（「116」「16」）
        digit_rows = sorted(((b[1] + b[3]) // 2, b) for t, b in boxes if t.isdigit() and b[0] >= 250)
        for cy, b in reversed(digit_rows):
            if sum(abs(cy2 - cy) <= 12 for cy2, _ in digit_rows) >= 2:
                arrow = b
                break
    before = after = None
    if arrow is not None:
        cy = (arrow[1] + arrow[3]) // 2
        row = sorted((b[0], t) for t, b in boxes if abs((b[1] + b[3]) // 2 - cy) <= 12 and b[0] >= 250)
        nums = [int(n) for _, t in row for n in re.findall(r"\d+", t)]
        if len(nums) >= 2:
            before, after = nums[0], nums[1]
    price = before - after if before is not None and after is not None and before >= after else None
    return {"kind": kind, "item": item, "p_before": before, "p_after": after, "price": price,
            "sale": any(t == "SALE" for t, _ in boxes)}  # fmt: skip


# ---------------------------------------------------------------- 获得弹窗：卡 / P 饮料 / P 道具的获得演出
ACQUIRE_BAR = (40, 840, 680, 910)  # 画面中下部的名称栏（メモリー効果、支援卡事件、活动支给、P 道具获得等同一布局）
ACQUIRE_ART = (262, 556, 455, 750)  # 获得卡时中间缩略图的卡面部分
ACQUIRE_CENTER_X, ACQUIRE_CENTER_TOL = 370, 25


@cache
def _pitems_by_name() -> dict[str, list[str]]:
    from kaa.db.sqlite import select_many

    out: dict[str, list[str]] = {}
    for row in select_many("SELECT id, name FROM ProduceItem;"):
        out.setdefault(row["name"], []).append(row["id"])
    return out


def _item_from_name(text: str) -> dict | None:
    """名称栏文字 → 卡 / 饮料 / P 道具。名称前的图标常被 OCR 成一个字（「団」「ど」），精确匹配不到时去掉首字再试。"""
    text = text.replace(" ", "").replace("＋", "+")
    for t in (text, text[1:]):
        if not t:
            continue
        if (card := card_from_name(t)) is not None:
            return {"kind": "card", **card}
        if t in _drinks_by_name():
            return {"kind": "drink", "drink_id": _drinks_by_name()[t], "name": t}
        if t in _pitems_by_name():
            ids = _pitems_by_name()[t]
            return {"kind": "pitem", "pitem_id": ids[0] if len(ids) == 1 else None, "pitem_ids": ids, "name": t}
    from hiro.vision.ocr import engine

    if engine() != "vision":  # RapidOCR 等常错一个字（「冒険心」→「目険心」）：只差一个字且唯一时按主数据名称再认一次
        for t in (text, text[1:]):
            if (near := _near_name(t)) is not None:
                return _item_from_name(near)
    return None


def _near_name(text: str) -> str | None:
    """主数据里与 text 等长（≥3 字）、只差一个字的名称；恰好一个才返回（卡名含「+」）。"""
    if len(text) < 3:
        return None
    names = {n for n in _cards_by_full_name()} | set(_drinks_by_name()) | set(_pitems_by_name())
    hits = {n for n in names if len(n) == len(text) and sum(a != b for a, b in zip(n, text, strict=True)) == 1}
    return hits.pop() if len(hits) == 1 else None


def read_acquire_popup(img: MatLike, art: bool = True) -> dict | None:
    """获得演出的名称栏：{kind: card|drink|pitem, card_id/drink_id/pitem_id, name, upgrade?, art_card_id?, agree?, text}。

    名称栏文字居中、且精确命中主数据才算（所以也可当页面判定用：命中不了返回 None，约 80–200 ms 一次 OCR）。
    卡：再用中间缩略图认卡（KAA match_card_region）核对，agree=False 时 card_id 仍以名称栏为准（带强化次数），由调用方决定是否采信。
    「H.I.Fワッペン」在 P 道具表里（pitem_00-3-265-0）。同名 P 道具有多个 id 时 pitem_id 为 None、pitem_ids 全列出。
    """
    from hiro.vision.ocr import ocr_boxes

    x1, y1, x2, y2 = ACQUIRE_BAR
    # 名称栏只有一段居中的文字（中心 x 实测 360–380，名称前带图标）。同一高度上的其他页面：选饮料页瓶身标签「初星水」
    # 偏在两侧；考试「Pドリンク詳細」的饮料名中心 321、右侧还有「捨てる」——有偏离中心的文字就不算
    # 名称前的图标有时被单独切成一个单字框（「と」），不参与判断
    boxes = [(t, (b[0] + b[2]) / 2 + x1) for t, _, b in ocr_boxes(img[y1:y2, x1:x2]) if len(t.strip()) >= 2]
    if not boxes or any(abs(cx - ACQUIRE_CENTER_X) > ACQUIRE_CENTER_TOL for _, cx in boxes):
        return None
    text = "".join(t for t, _ in boxes)
    item = _item_from_name(text)
    if item is None:
        return None
    item["text"] = text
    if item["kind"] == "card" and art:  # art=False：只判页（hiro.vision.quick），不用卡面核对（首次加载卡图库要 1–2 秒）
        from kaa.game_ui.skill_card_select import match_card_region

        ax1, ay1, ax2, ay2 = ACQUIRE_ART
        art = match_card_region(img[ay1:ay2, ax1:ax2])
        item["art_card_id"] = art._id if art else None
        item["agree"] = (art._id == item["card_id"]) if art else None
    return item


# ---------------------------------------------------------------- 换卡第 1 步（SKILL_CARD_CHANGE_1）：换进哪张
# 布局同三选一：中间一行 3 张候选（类别字母 x≈213 / 352 / 491，间距 139，与删卡页 147、一览 161 都不同），
# 卡下「おすすめ」，左下「あと N回 カード除去」、右下「あと N回 再抽選」（KAA 参考图 dialog_card_change_1.png）。
CHANGE1_MARKS = (40, 820, 680, 1020)  # 同 hiro.loop.kaa_patches.CHANGE_LETTERS_BOX
CHANGE1_DX, CHANGE1_DY, CHANGE1_CELL = -53, -110, 124
CHANGE1_TEXT = (0, 940, 720, 1130)


CHANGE1_MIN_GAP = 100  # 候选字母间距 139；更近的是同一张卡上的效果图标被当成字母


def _change1_row(marks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """候选字母都在卡底同一行：只留与中位数 y 相差 ≤ ROW_TOL 的，横向太近的只留一个。
    qualify6-r2 #29：第 2 张卡左侧效果图标列（y 比字母高约 40）被认成「M」，同一张卡出现在两个 slot。"""
    if not marks:
        return []
    ys = sorted(y for _, y in marks)
    mid = ys[len(ys) // 2]
    out: list[tuple[int, int]] = []
    for p in sorted(p for p in marks if abs(p[1] - mid) <= ROW_TOL):
        if not out or p[0] - out[-1][0] >= CHANGE1_MIN_GAP:
            out.append(p)
    return out


def read_change_candidates(img: MatLike) -> dict:
    """→ {candidates: [{index, slot, card_id, name, upgrade, recommended, center}], recommended, rerolls_left, excludes_left, complete}。
    complete：找到 3 张且都认出（含强化次数）。没有实机样本，只在 KAA 参考图上核对过。"""
    import re

    from kaa.game_ui.skill_card_select import match_card_region

    from hiro.vision.ocr import ocr_boxes

    marks = _change1_row(_marks(img, CHANGE1_MARKS))
    x1, y1, x2, y2 = CHANGE1_TEXT
    boxes = [(t.replace(" ", ""), (a + x1, b + y1, c + x1, d + y1)) for t, _, (a, b, c, d) in ocr_boxes(img[y1:y2, x1:x2])]
    cands = []
    for i, (mx, my) in enumerate(marks):
        x, y = mx + CHANGE1_DX, my + CHANGE1_DY
        crop = img[max(0, y) : y + CHANGE1_CELL, max(0, x) : x + CHANGE1_CELL]
        card = match_card_region(crop)
        cx = x + CHANGE1_CELL // 2
        rec = any("おすすめ" in t and abs((b[0] + b[2]) // 2 - cx) <= 60 for t, b in boxes)
        cands.append({"index": i, "slot": i, "card_id": card._id if card else None, "name": card.name if card else None,
                      "upgrade": _plus(crop) if card else None, "recommended": rec, "center": [int(cx), int(y + CHANGE1_CELL // 2)]})  # fmt: skip

    def left_count(xr) -> int | None:
        col = [(t, b) for t, b in boxes if xr[0] <= (b[0] + b[2]) // 2 <= xr[1]]
        if m := re.search(r"あと(\d+)回", "".join(t for t, _ in col)):
            return int(m.group(1))
        # 整块 OCR 把数字读丢（RapidOCR「あと回」，DMM qualifydmm2-00237）：只裁这一行再读一次
        from hiro.vision.ocr import ocr

        for t, (a, b, c, d) in col:
            if "あと" in t and "回" in t:
                line = "".join(t for t, _ in ocr(img[max(0, b - 10) : d + 10, max(0, a - 10) : c + 10], scale=2))
                if m := re.search(r"あと(\d+)回", line.replace(" ", "")):
                    return int(m.group(1))
        return None

    complete = len(cands) == 3 and all(c["card_id"] and c["upgrade"] is not None for c in cands)
    return {"candidates": cands, "recommended": next((c["index"] for c in cands if c["recommended"]), None),
            "excludes_left": left_count((0, 260)), "rerolls_left": left_count((460, 720)), "complete": complete}  # fmt: skip
