# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""用游戏自带字体读数字。

HUD、状态栏、分数等数字是 Campus-Alphanumeric-SemiBold（客户端 data.unity3d 里的原始字体，
由 tools/assets/build_pack 导出到素材包 font/）白字加深色描边。做法：
1. 区域内取白色字形，按连通域切成单个字符。白底、浅青底等亮背景也会被取到，
   所以先切掉贴着深色描边的像素，再只保留外圈大多是深色描边的连通域；
2. 每个字符按高度缩放后，与用原字体渲染的 0–9、% 模板比较重合度（IoU），取最高者。
比通用 OCR 稳：不会把 23 读成 25，也不会漏读单个数字。
"""

import re
from functools import cache

from hiro.vision import assets

import cv2
import numpy as np
from cv2.typing import MatLike

FONT = "Campus-Alphanumeric-SemiBold.ttf"
CHARS = "0123456789%"
GLYPH_H = 40  # 比较时统一的字形高度
WHITE = ((0, 0, 190), (180, 70, 255))
DARK_V = 140  # 描边亮度上限
# 缩放过的画面（DMM 1080 宽窗口缩到 720）描边被白字和青底混浅（亮度 140–170），外圈「深色描边」占比掉到 0.2–0.5，
# 字形全被当成背景丢掉、read_text 返回空（9580c0c 11-17，genki_dmm 9 张）。第一遍按 DARK_V 切不出字或有字认不准时，
# 按 DARK_V_SOFT 再切一遍，那一遍所有字都认得出才用。原尺寸（Mac / 720 宽）第一遍就读出，不受影响。
DARK_V_SOFT = 170
RING_DARK_MIN = 0.55  # 字符外圈中深色描边的占比下限；背景色块外圈不是描边
MIN_SCORE = 0.7  # 实测正确字符 0.81–0.93；数字间露出的背景块被认成「1」时约 0.57
MIN_HEIGHT_RATIO = 0.6  # 比最高字符矮得多的连通域是噪点或「%」之外的小符号
GROUP_GAP = 0.45  # 字符间距超过字高的这个比例，视为另一个数


@cache
def _templates() -> dict[str, np.ndarray]:
    path = assets.font_path()
    if path is None:
        return {}  # 没有游戏数字字体：字形比对关闭，read_number 全走 ddddocr
    from PIL import Image, ImageDraw, ImageFont  # 只有渲染字体模板时用（Pillow 随可选组 play 装）

    font = ImageFont.truetype(str(path), 160)
    out = {}
    for ch in CHARS:
        im = Image.new("L", (240, 240), 0)
        ImageDraw.Draw(im).text((20, 20), ch, font=font, fill=255)
        a = np.array(im)
        ys, xs = np.where(a > 127)
        glyph = a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        out[ch] = _scale(glyph > 127)
    return out


def _best_char(glyph: np.ndarray) -> tuple[float, str | None]:
    """字形（_scale 过的掩码）与字体模板比对的 (最高分, 字符)；没有字体时为 (0.0, None)。"""
    tpl = _templates()
    return max((_iou(glyph, t), c) for c, t in tpl.items()) if tpl else (0.0, None)


def _scale(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    width = max(1, round(w * GLYPH_H / h))
    return cv2.resize(mask.astype(np.uint8) * 255, (width, GLYPH_H), interpolation=cv2.INTER_AREA) > 127


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    w = max(a.shape[1], b.shape[1])
    pa = np.zeros((GLYPH_H, w), bool)
    pb = np.zeros((GLYPH_H, w), bool)
    pa[:, (w - a.shape[1]) // 2 : (w - a.shape[1]) // 2 + a.shape[1]] = a
    pb[:, (w - b.shape[1]) // 2 : (w - b.shape[1]) // 2 + b.shape[1]] = b
    union = np.logical_or(pa, pb).sum()
    return float(np.logical_and(pa, pb).sum() / union) if union else 0.0


MARGIN = 14  # 框外多取的像素：手量的框常切到首尾字符，按整字比对需要完整字形


def read_text(img: MatLike, box: tuple[int, int, int, int], *, align: bool = False) -> tuple[str, bool]:
    """box 为 (x1, y1, x2, y2)；返回 (从左到右识别出的字符串, 是否有字符没认出)。字符串只含 0–9 与 %。
    在框外扩 MARGIN 找字符，只保留与原框有重叠的，所以框切到首尾字符也能读全。"""
    text, uncertain, _ = _read_glyphs(img, box, align=align)
    return text, uncertain


def _read_glyphs(img: MatLike, box: tuple[int, int, int, int], *, align: bool = False) -> tuple[str, bool, dict | None]:
    """_read_glyphs_at(DARK_V)；切不出字或有字认不准时按 DARK_V_SOFT 再切一遍（缩放后描边变浅的画面），那一遍全认出才用。"""
    text, uncertain, info = _read_glyphs_at(img, box, align=align, dark_v=DARK_V)
    if (not text or uncertain) and _templates():
        t2, u2, i2 = _read_glyphs_at(img, box, align=align, dark_v=DARK_V_SOFT)
        if t2 and not u2:
            return t2, u2, i2
    return text, uncertain, info


def _read_glyphs_at(
    img: MatLike, box: tuple[int, int, int, int], *, align: bool = False, dark_v: int = DARK_V
) -> tuple[str, bool, dict | None]:
    """read_text 的实现；另返回所取那组字形的信息 {span: (x1, y1, x2, y2) 原图坐标, glyphs: 字形个数, occluded: 是否疑似漏字}。
    没有数字字体（开源默认素材）时照常按描边切出字形、给出 span 和字形个数，只是每个字都记为没认出（整串「不确定」），
    由调用方交给 ddddocr；read_score 用字形个数核对 OCR 的位数。"""
    bx1, by1, bx2, by2 = box
    x1, y1 = max(0, bx1 - MARGIN), max(0, by1 - MARGIN // 2)
    x2, y2 = min(img.shape[1], bx2 + MARGIN), min(img.shape[0], by2 + MARGIN // 2)
    crop = img[y1:y2, x1:x2]
    scale = 3  # 小号字先放大，连通域与字形边缘更稳
    inner = ((bx1 - x1) * scale, (by1 - y1) * scale, (bx2 - x1) * scale, (by2 - y1) * scale)
    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    dark = hsv[:, :, 2] < dark_v
    fill = cv2.inRange(hsv, *WHITE)
    fill[cv2.dilate(dark.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = 0
    fill = cv2.morphologyEx(fill, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(fill)
    ring_kernel = np.ones((2 * scale + 3, 2 * scale + 3), np.uint8)
    comps, rejected = [], []
    for i in range(1, n):
        if stats[i][cv2.CC_STAT_AREA] < 20 * scale:
            continue
        cx, cy, cw, ch = stats[i][:4]
        if cx + cw <= inner[0] or cx >= inner[2] or not inner[1] <= cy + ch / 2 <= inner[3]:
            continue  # 与原框没有重叠，或垂直方向不在框内：属于相邻的其他数字
        mask = (labels == i).astype(np.uint8)
        ring = (cv2.dilate(mask, ring_kernel) > 0) & (mask == 0)
        if ring.any() and dark[ring].mean() >= RING_DARK_MIN:
            comps.append(i)
        else:
            rejected.append(i)
    if not comps:
        return "", False, None
    boxes = [(i, *stats[i][:4]) for i in sorted(comps, key=lambda i: stats[i][cv2.CC_STAT_LEFT])]
    if align:  # 只用于 HUD 回合 / 体力 / 元气：排名分数的数字会和玩家卡白边连成一块，按对齐过滤会把它静默丢掉
        boxes = _aligned(boxes, labels, (by2 - by1) * scale) or boxes
    tallest = max(b[4] for b in boxes)
    boxes = [b for b in boxes if b[4] >= tallest * MIN_HEIGHT_RATIO]
    # 最左一个整高白块的左缘（无字体时裁 OCR 框的左边用）：不分组，描边被闪光盖住 / 压在角标上的白字（rejected）也算
    left = (
        x1
        + min(
            stats[i][cv2.CC_STAT_LEFT]
            for i in comps + rejected
            if stats[i][cv2.CC_STAT_HEIGHT] >= tallest * MIN_HEIGHT_RATIO
        )
        // scale
    )
    # 扩边会带进相邻数字：按水平间距分组（同一个数内字距远小于字高），取与原框重叠最多的一组
    groups, last_right = [], None
    for b in boxes:
        if last_right is None or b[1] - last_right > tallest * GROUP_GAP:
            groups.append([])
        groups[-1].append(b)
        last_right = b[1] + b[3]

    def overlap(group) -> int:
        return sum(max(0, min(b[1] + b[3], inner[2]) - max(b[1], inner[0])) for b in group)

    out, uncertain = [], False
    group = max(groups, key=overlap)
    gx1, gy1 = min(b[1] for b in group), min(b[2] for b in group)
    gx2, gy2 = max(b[1] + b[3] for b in group), max(b[2] + b[4] for b in group)
    info = {
        "span": (x1 + gx1 // scale, y1 + gy1 // scale, x1 + -(-gx2 // scale), y1 + -(-gy2 // scale)),
        "glyphs": len(group),
        "occluded": False,
        "left": left,
    }
    info["parts"] = []
    for i, x, y, w, h in group:
        glyph = _scale(labels[y : y + h, x : x + w] == i)
        score, ch = _best_char(glyph)
        info["parts"].append((ch, round(score, 2), w, h))
        if score >= MIN_SCORE:
            out.append(ch)
        else:
            uncertain = True  # 字符被特效（闪光等）遮挡变形，整串不可信
    info["occluded"] = _occluded_neighbor(stats, rejected, group)
    if out and not uncertain:
        uncertain = info["occluded"]
    return "".join(out), uncertain, info


MAX_GLYPH_BOX = 1.2  # 字高不会超过框高的这个倍数；更高的是背景亮块（浅色地面）
ALIGN_TOP = 0.25  # 同一行字的顶边差不超过字高的这个比例
ALIGN_H = (0.75, 1.25)


def _aligned(boxes: list[tuple], labels: np.ndarray, box_h: int) -> list[tuple]:
    """以同一行认得出的字最多的那组为准，只留顶边对齐、高度相近的连通域（read_text(align=True)）。
    浅色地面、元气角标底边等背景块也带深色描边，会混进数字串让整串判为不确定；它们与数字不在同一行。
    被特效遮住的字仍与数字对齐，照样留下并判为不确定。没有任何字认得出时返回空列表（交回原逻辑）。"""
    if not _templates():
        return []  # 没有字体：认不出任何字，交回原逻辑
    sized = [b for b in boxes if b[4] <= box_h * MAX_GLYPH_BOX]

    def row(ref: tuple) -> list[tuple]:
        _, _, ry, _, rh = ref
        return [b for b in sized if abs(b[2] - ry) <= rh * ALIGN_TOP and ALIGN_H[0] * rh <= b[4] <= ALIGN_H[1] * rh]

    # 基准取「同一行里认得出的字最多」的那个，而不是单个分数最高的：闪光特效里偶尔有像「0」的亮块
    readable = {b[0] for b in sized
                if max(_iou(_scale(labels[b[2] : b[2] + b[4], b[1] : b[1] + b[3]] == b[0]), t) for t in _templates().values()) >= MIN_SCORE}  # fmt: skip
    best, out = 0, []
    for b in sized:
        if b[0] in readable:
            members = row(b)
            good = sum(1 for m in members if m[0] in readable)
            if good > best:
                best, out = good, members
    return out


def _occluded_neighbor(stats, rejected: list[int], group) -> bool:
    """被闪光等特效盖住的字符，描边断开，会在外圈检查中被当作背景排除。若被排除的连通域
    大小像一个字符（高度接近、宽度不超过两个字宽）且紧挨着这串数字，说明可能漏了字。"""
    heights = sorted(b[4] for b in group)
    widths = sorted(b[3] for b in group)
    h, w = heights[len(heights) // 2], widths[len(widths) // 2]
    left, right = group[0][1] - w, group[-1][1] + group[-1][3] + w
    for i in rejected:
        x, _, cw, ch = stats[i][:4]
        if 0.75 * h <= ch <= 1.35 * h and cw <= 2 * w and x < right and x + cw > left:
            return True
    return False


GLYPH_H_RANGE = (12, 20)  # 状态栏数字的原始高度约 16 像素
PERCENT_RING_MAX = 0.65  # 「%」的两个小圈高度约为数字的 0.46；按「0」比对能认出


def read_leading_int(img: MatLike, box: tuple[int, int, int, int]) -> tuple[int | None, str | None]:
    """读「数字 + 单位」（20%、4ターン、3回）开头的数字，返回 (数值, 后缀)。后缀：None 无、"%"、"text" 其他文字；
    数值为 None 时后缀 "empty" 表示框里没有任何字，None 表示没把握（调用方应退回其他读法）。

    read_text 会把单位里的「%」斜线、「タ」当成没认出的字，整串判为不确定；这里只取左起连续的数字，
    数字之后紧跟的第一个连通域若是「%」的小圈记为 "%"，否则记为 "text"（由调用方按上下文决定是否可信）。
    数字左侧有疑似被遮挡的字，或一个数字都没认出时返回 (None, None)。"""
    if not _templates():
        return None, None  # 没有字体：没把握，调用方退回 ddddocr
    bx1, by1, bx2, by2 = box
    x1, y1 = max(0, bx1 - MARGIN), max(0, by1 - MARGIN // 2)
    x2, y2 = min(img.shape[1], bx2 + MARGIN), min(img.shape[0], by2 + MARGIN // 2)
    scale = 3
    inner_x1 = (bx1 - x1) * scale
    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    dark = hsv[:, :, 2] < DARK_V
    fill = cv2.inRange(hsv, *WHITE)
    fill[cv2.dilate(dark.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = 0
    fill = cv2.morphologyEx(fill, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(fill)
    ring_kernel = np.ones((2 * scale + 3, 2 * scale + 3), np.uint8)
    inner_y = ((by1 - y1) * scale, (by2 - y1) * scale)
    comps, rejected = [], []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area < 20 * scale or not inner_y[0] <= cy + ch / 2 <= inner_y[1] or cx + cw <= inner_x1:
            continue
        mask = (labels == i).astype(np.uint8)
        ring = (cv2.dilate(mask, ring_kernel) > 0) & (mask == 0)
        (comps if ring.any() and dark[ring].mean() >= RING_DARK_MIN else rejected).append(i)
    comps.sort(key=lambda i: stats[i][cv2.CC_STAT_LEFT])

    def best(i: int) -> tuple[float, str]:
        x, y, w, h = stats[i][:4]
        return max((_iou(_scale(labels[y : y + h, x : x + w] == i), t), c) for c, t in _templates().items())

    lo, hi = GLYPH_H_RANGE[0] * scale, GLYPH_H_RANGE[1] * scale
    tall = [i for i in comps if lo <= stats[i][3] <= hi]
    if not tall:
        glyph_like = [i for i in rejected if lo <= stats[i][3] <= hi and stats[i][2] <= hi]
        return None, ("empty" if not glyph_like else None)  # empty：常驻效果不显示数值，框里只有背景亮块
    height = max(stats[i][3] for i in tall)
    tall = [i for i in tall if stats[i][3] >= height * MIN_HEIGHT_RATIO]  # 矮块：数字间的高光、「%」的小圈
    digits, first_x, last, k = [], None, None, len(tall)
    for n_, i in enumerate(tall):
        x, _, w, _ = stats[i][:4]
        score, ch = best(i)
        if score < MIN_SCORE or not ch.isdigit() or (last is not None and x - last > height * GROUP_GAP):
            if not digits and x < inner_x1:
                continue  # 数字框左侧伸进来的图标边框碎块
            k = n_
            break
        digits.append(ch)
        first_x = x if first_x is None else first_x
        last = x + w
    if not digits:
        return None, None
    if first_x > inner_x1 + height:
        return None, None  # 数字左对齐：首位离框左端太远，说明首位可能被遮住
    for i in rejected:  # 数字两侧紧挨着被排除的、字大小的连通域：可能有一位被特效遮住（框左端以外是图标边框，不算）
        x, _, cw, ch = stats[i][:4]
        near_left = x >= inner_x1 and first_x - height <= x + cw <= first_x + 2
        if 0.75 * height <= ch <= 1.35 * height and (near_left or last - 2 <= x <= last + height):
            return None, None
    value = int("".join(digits))
    rings = [
        i for i in comps if stats[i][3] <= height * PERCENT_RING_MAX and last - 2 <= stats[i][0] <= last + height * 0.6
    ]
    if any(best(i)[1] == "0" and best(i)[0] >= MIN_SCORE for i in rings):
        return value, "%"
    return value, ("text" if tall[k:] else None)


def read_int(img: MatLike, box: tuple[int, int, int, int], *, align: bool = False) -> int | None:
    """只用字体比对；读不出或有字符没认出时返回 None。"""
    text, uncertain = read_text(img, box, align=align)
    m = re.search(r"\d+", text)
    return int(m.group()) if m and not uncertain else None


WHITE_DIGIT_H = 0.7  # read_white_digits：连通块高度至少为最高字的这么多倍
WHITE_DIGIT_TOP = 0.3  # 顶边与最高字相差不超过字高的这么多倍


def read_white_digits(img: MatLike, box: tuple[int, int, int, int]) -> int | None:
    """白色填充 + 深色描边的数字（考试 HUD 元気角标）：只留白色像素，再只留和最高那块等高、顶边对齐的连通块，交 ddddocr。
    框里的其他东西（体力条没满时的深灰圆头、角标左下的浅色高光、背景亮线）不是白色或比数字矮，读之前就抹掉
    （DMM mainverify2 11-17：整框交 ddddocr 时元気 0 → 10、100 → 2100、700 → 5700）。框里没有白色块返回 None。"""
    x1, y1, x2, y2 = box
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    mask = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), *WHITE)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    tallest = max(range(1, n), key=lambda i: stats[i][cv2.CC_STAT_HEIGHT])
    th, ty = stats[tallest][cv2.CC_STAT_HEIGHT], stats[tallest][cv2.CC_STAT_TOP]
    keep = [
        i
        for i in range(1, n)
        if stats[i][cv2.CC_STAT_HEIGHT] >= th * WHITE_DIGIT_H
        and abs(stats[i][cv2.CC_STAT_TOP] - ty) <= th * WHITE_DIGIT_TOP
    ]
    out = np.zeros_like(crop)
    out[np.isin(labels, keep)] = 255
    text = (
        _dddd().classification(cv2.imencode(".png", cv2.resize(out, None, fx=2, fy=2))[1].tobytes()) or ""
    ).translate(DDDD_FIX)
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


DDDD_FIX = str.maketrans({"o": "0", "O": "0", "l": "1", "I": "1"})


@cache
def _dddd():
    import ddddocr

    return ddddocr.DdddOcr(show_ad=False)


def dddd_digits(crop: MatLike) -> list[int]:
    """ddddocr 读一块里的数字（灰度、放大 2 倍、Otsu 二值化），返回读到的各个整数（同 KAA _digits_from_image）。"""
    if crop is None or crop.size == 0:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ok, buf = cv2.imencode(".png", bw)
    return [int(n) for n in re.findall(r"\d+", _dddd().classification(buf.tobytes()) or "")] if ok else []


def read_dddd(img: MatLike, box: tuple[int, int, int, int]) -> int | None:
    """通用 OCR（ddddocr）读数字。"""
    x1, y1, x2, y2 = box
    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=2, fy=2)
    m = re.search(r"\d+", (_dddd().classification(cv2.imencode(".png", crop)[1].tobytes()) or "").translate(DDDD_FIX))
    return int(m.group()) if m else None


def read_number(img: MatLike, box: tuple[int, int, int, int], *, align: bool = False) -> int | None:
    """先按游戏字体比对；字体比对没把握（字被特效遮挡）时退回 ddddocr。
    回归集上字体比对从不读错，只会说不确定，所以合起来既快又避免 23→25 这类静默误读。"""
    value = read_int(img, box, align=align)
    if value is not None:
        return value
    if not _templates():
        return _number_without_font(img, box, align=align)
    return read_dddd(img, box)


CIRCLED = str.maketrans("⓪①②③④⑤⑥⑦⑧⑨", "0123456789")


def _number_without_font(img: MatLike, box: tuple[int, int, int, int], *, align: bool) -> int | None:
    """没有数字字体时的 read_number：整框和「左边收到最左一个白字」各让 ddddocr 读一次。
    元気框为了不切掉首位左扩过 20，整框会把体力条右端的深色圆头读成「1」（元気 0→10）；收左边又会在白点花纹背景上丢掉首位（1283→283）。
    两次一致就用；不一致时用 Apple Vision（整框放大 6 倍，⓪ 等圈字按数字算）判哪个对，都不对给 None。
    58 帧考试画面（回合 / 元気 / 体力 174 个读数）与有字体时的结果全部一致。"""
    whole = read_dddd(img, box)
    _, _, info = _read_glyphs(img, box, align=align)
    if info is None:
        return whole
    trimmed_box = (max(box[0], info["left"] - SPAN_PAD), *box[1:])
    trimmed = read_dddd(img, trimmed_box) if trimmed_box != box else whole
    if whole == trimmed:
        return whole
    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = box
    text = re.sub(r"\D", "", "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=6.0)).translate(CIRCLED))
    vision = int(text) if text else None
    return vision if vision is not None and vision in (whole, trimmed) else None


SPAN_PAD = 4  # ddddocr 兜底时字形组外多取的像素


def _subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(c in it for c in short)


def read_score(img: MatLike, box: tuple[int, int, int, int]) -> tuple[int | None, dict]:
    """读分数这类长数字，返回 (值, {method, confidence, note})。method：digits 字体比对 / dddd OCR 兜底 / None 读不出。

    旧做法（read_number）在字体比对「不确定」时按 box 裁图交给 ddddocr；玩家分数 5 位时会超出玩家卡白框，
    按白框裁会切掉首尾一位（37811→3781、14031→1403，复盘 2026-10-31）。现在：
    - 字体比对确定 → 直接用（confidence 1.0）。
    - 不确定（多半是玩家卡白框边、背景亮块混进字形组，或疑似有字被遮住）→ 按字形组的实际范围裁图 OCR，
      与字体认出的那串（只含分数 ≥ MIN_SCORE 的字形）核对：
      一致 → 用（0.9；组里混有可疑块时 0.8）；
      OCR 多出几位、字体那串是其子序列、多出的位数不超过可疑块数（疑似遮挡再加 1）→ 用 OCR（0.6）；
      其他（OCR 位数更少、对不上）→ None，不交出少一位的错值。
    """
    text, uncertain, info = _read_glyphs(img, box)
    m = re.fullmatch(r"\d+", text)
    if m and not uncertain:
        return int(text), {"method": "digits", "confidence": 1.0, "note": None}
    if info is None:
        return None, {"method": None, "confidence": None, "note": "没有找到数字字形"}
    x1, y1, x2, y2 = info["span"]
    span = (
        max(0, x1 - SPAN_PAD),
        max(0, y1 - SPAN_PAD),
        min(img.shape[1], x2 + SPAN_PAD),
        min(img.shape[0], y2 + SPAN_PAD),
    )
    if not _templates():
        return _score_without_font(img, span, info)
    value = read_dddd(img, span)
    font = re.sub(r"\D", "", text)
    noise = sum(1 for _, score, *_ in info["parts"] if score < MIN_SCORE)
    if value is None:
        return None, {
            "method": None,
            "confidence": None,
            "note": f"字体比对不确定（认出 {font or '无'}），OCR 也读不出",
        }
    ocr_text = str(value)
    if ocr_text == font:
        return value, {"method": "dddd", "confidence": 0.8 if noise else 0.9, "note": "字体比对与 OCR 一致"}
    extra = len(ocr_text) - len(font)
    if 0 < extra <= noise + (1 if info["occluded"] else 0) and _subsequence(font, ocr_text):
        return value, {"method": "dddd", "confidence": 0.6, "note": f"字体只认出 {font or '无'}，OCR 补出 {ocr_text}"}
    return None, {
        "method": None,
        "confidence": None,
        "note": f"字体认出 {font or '无'}、OCR 读到 {ocr_text}，对不上，丢弃",
    }


def _full_glyphs(parts: list[tuple]) -> int:
    """字形组里「整高、正常宽」的字形个数，近似数字位数：背景亮块、白框碎片要么矮、要么宽得多。"""
    if not parts:
        return 0
    hs = sorted(h for *_, w, h in parts)
    ws = sorted(w for *_, w, h in parts)
    mh, mw = hs[len(hs) // 2], ws[len(ws) // 2]
    return sum(1 for *_, w, h in parts if h >= 0.85 * mh and w <= 1.6 * mw)


def _score_without_font(img: MatLike, span: tuple[int, int, int, int], info: dict) -> tuple[int | None, dict]:
    """没有数字字体（开源默认素材）时的分数读法：字形组的范围裁图，ddddocr 与 Apple Vision（放大 9 倍，同 hif._ocr_int）各读一次互相核对。
    排名栏 11 帧回归：两者一致 10 帧；不一致的 1 帧（Vision 22511 / ddddocr 22311）ddddocr 位数与整高字形数相同，取 ddddocr。
    - 一致 → 0.8
    - 一个是另一个掉了首 / 末位 → 取长的，0.6
    - 不一致 → ddddocr 位数等于整高字形数时取 ddddocr，0.6；否则 None（不交出可能掉位的值）"""
    from hiro.vision.ocr import ocr

    dddd = read_dddd(img, span)
    x1, y1, x2, y2 = span
    m = re.fullmatch(r"\d+", "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=9.0)).replace(",", "").replace(" ", ""))
    vision = int(m.group()) if m else None
    full = _full_glyphs(info["parts"])
    note = f"无数字字体：ddddocr {dddd}、Vision {vision}、整高字形 {full} 个"
    if dddd is not None and dddd == vision:
        return dddd, {"method": "dddd", "confidence": 0.8, "note": "无数字字体：ddddocr 与 Vision 一致"}
    if dddd is not None and vision is not None:
        a, b = sorted((str(dddd), str(vision)), key=len)
        if len(a) < len(b) and (b.startswith(a) or b.endswith(a)):
            return int(b), {"method": "dddd", "confidence": 0.6, "note": note}
    if dddd is not None and len(str(dddd)) == full:
        return dddd, {"method": "dddd", "confidence": 0.6, "note": note}
    return None, {"method": None, "confidence": None, "note": note + "，对不上，丢弃"}
