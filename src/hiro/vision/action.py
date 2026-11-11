# 来源：digital-hiro src/hiro/vision/action.py（develop c3fc27f）
"""培育「行动选择」页：读出决策需要的状态与可选行动。

页面结构（720x1280，三种变体共用上半部分）：
- 头部：距考试天数（「選抜試験1まで」「6日」）、体力「33/33」、P 点数、老师建议
- 中部：三维数值（白字深描边，游戏字体）、上限「/3000」、成长率「41.5%」，左侧星标数值
- 底部行动按钮：主页（相談 / 差し入れ / 特別指導）、公开课（Vo./Da./Vi.公開レッスン，带体力消耗）、
  授業（三个按钮文字相同，按右下角小图标颜色区分属性）与「休む」

客户端 data.unity3d 里没有这个页面的布局（在按需下载的资源包里），坐标为截图实测。
文字（按钮、天数、建议）用 Apple Vision 整屏识别一次；数字按区域单独读。
"""

import re
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np
from cv2.typing import MatLike

from hiro.vision.digits import read_number
from hiro.vision.ocr import engine as ocr_engine
from hiro.vision.ocr import ocr, ocr_boxes

ATTRS = ("vo", "da", "vi")
STAT_BOXES = {"vo": (200, 684, 275, 719), "da": (345, 682, 420, 720), "vi": (478, 684, 558, 718)}  # 避开左侧属性小图标
# 属性数字框按左侧图标定位（stat_boxes）：「图标 + 数字」一组随位数左右移（三位数时 Vi 图标右移 12px），
# 固定框会切掉四位数的首位（1741→741、2296 读成 3296）或把图标右缘读成 9（998→9998）。图标色相：Vo 粉 / Da 蓝 / Vi 橙。
STAT_ICON_HUE = {"vo": (155, 178), "da": (98, 112), "vi": (10, 28)}
STAT_ICON_SEARCH = {"vo": (120, 300), "da": (260, 440), "vi": (400, 590)}
STAT_ROW = (686, 718)
STAT_DIGITS_W = 112  # 四位数约 100px
STAT_RIGHT_LIMIT = 590  # Vi 右边是「P手帳」
CAP_BOXES = {"vo": (178, 718, 265, 748), "da": (320, 718, 406, 748), "vi": (462, 718, 548, 748)}
GROWTH_BOXES = {"vo": (176, 750, 262, 780), "da": (312, 750, 404, 780), "vi": (456, 750, 548, 780)}
STAR_BOX = (64, 686, 122, 718)
STAMINA_BOX = (322, 50, 436, 94)
POINTS_BOX = (330, 94, 440, 136)
HEADER_BAND = 140  # 头部文字（考试名、天数）
ADVICE_BAND = (160, 240)
BUTTON_BAND = (840, 1120)  # 行动按钮及其文字、体力消耗

# 按钮文字 → 行动类型
ACTIONS = [
    # 「ス」容许读成「ズ」、属性前缀容许读不出（RapidOCR：「Vo.公開レッスン」→「V0公開レッズン」），属性再按文字条颜色补
    (re.compile(r"(Vo|Da|Vi)?\.?公開レッ[スズ]ン"), "open_lesson"),
    (re.compile(r"授業"), "class"),
    (re.compile(r"休む"), "rest"),
    (re.compile(r"相談"), "consult"),
    (re.compile(r"差し入れ"), "present"),
    (re.compile(r"特別指導"), "special_training"),
    (re.compile(r"おでかけ"), "outing"),
    (re.compile(r"(Vo|Da|Vi)?\.?レッ[スズ]ン"), "lesson"),
]
# 授業按钮右下角属性小图标的色相（OpenCV 0–180）：粉=Vo、蓝=Da、黄=Vi
ATTR_HUE = {"vo": (160, 180), "da": (95, 115), "vi": (15, 35)}


@dataclass
class ActionOption:
    kind: str
    label: str
    attr: str | None
    center: tuple[int, int]
    hp_cost: int | None = None  # 课程卡右上角「-8」
    hp_gain: int | None = None  # 「休む」左上角的回复量


@dataclass
class ActionSelectState:
    exam: str | None
    days_left: int | None
    stamina: int | None
    max_stamina: int | None
    p_points: int | None
    params: dict
    param_caps: dict
    growth_pct: dict
    star: int | None
    advice: str | None
    options: list[ActionOption] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """头部数值齐全、有可选行动且课程类按钮都认出了属性。按钮滑入动画中的帧不完整，应等下一帧。"""
        lessons_ok = all(o.attr for o in self.options if o.kind in ("class", "open_lesson"))
        return (None not in (self.days_left, self.stamina, self.p_points) and None not in self.params.values()
                and bool(self.options) and lessons_ok)

    def to_dict(self) -> dict:
        return {**asdict(self), "complete": self.complete}


def _attr_by_badge(img: MatLike, cx: int, cy: int) -> str | None:
    """授業按钮：按钮右下角属性小图标的主色相。"""
    hsv = cv2.cvtColor(img[cy - 50 : cy + 10, cx + 10 : cx + 60], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)
    if vivid.sum() < 40:
        return None
    hues = hsv[:, :, 0][vivid]
    counts = {a: int(((hues >= lo) & (hues <= hi)).sum()) for a, (lo, hi) in ATTR_HUE.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 30 else None


def _attr_by_band(img: MatLike, cx: int, ty: int) -> str | None:
    """レッスン / 公開レッスン 按钮：文字条的底色（粉=Vo、蓝=Da、橙=Vi）。文字认不出属性时的退路
    （RapidOCR 会把「Vo.」读成「V0」、「Vi.」读成「W」，2026-11-06 Windows 实测）。"""
    h, w = img.shape[:2]
    band = img[max(ty - 14, 0) : min(ty + 14, h), max(cx - 75, 0) : min(cx + 75, w)]
    if band.size == 0:
        return None
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)
    if vivid.sum() < 200:
        return None
    hues = hsv[:, :, 0][vivid]
    counts = {a: int(((hues >= lo) & (hues <= hi)).sum()) for a, (lo, hi) in ATTR_HUE.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 0.5 * vivid.sum() else None


COST_HEART_AREA = (150, 1200)  # 课程卡右上角绿色爱心（体力消耗徽章）的像素数：整颗约 640，只绿一半约 290


def _cost_by_heart(img: MatLike, cx: int, ty: int, read: int | None) -> int | None:
    """非 Vision 后端核对课程卡的体力消耗：消耗数字总在卡片右上角的绿色爱心右边。
    没有爱心 → 没有消耗（RapidOCR 在「授業」卡上读出幻影「18」「1」，A5b / Windows 第六局共 11 次）；
    有爱心但整条 OCR 没读到数（「-6」漏掉，Windows 第六局 #83）→ 单独读爱心右边。"""
    x1, y1 = max(0, cx - 90), max(0, ty - 210)
    band = img[y1 : ty - 30, x1 : cx + 90]
    if band.size == 0:
        return read
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 120)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    hearts = [st[i] for i in range(1, n) if COST_HEART_AREA[0] <= st[i][4] <= COST_HEART_AREA[1] and 12 <= st[i][2] <= 45 and 15 <= st[i][3] <= 45]
    if not hearts:
        return None
    if read is not None:
        return read
    hx, hy, hw, hh, _ = hearts[0]
    box = (x1 + hx + hw, y1 + hy - 4, x1 + hx + hw + 60, y1 + hy + hh + 4)
    m = re.search(r"(\d{1,2})", "".join(t for t, _, _ in ocr_boxes(img[box[1] : box[3], box[0] : box[2]], scale=3)))
    return int(m.group(1)) if m else None


def _options(img: MatLike) -> list[ActionOption]:
    y1, y2 = BUTTON_BAND
    tokens = [(t, (b[0], b[1] + y1, b[2], b[3] + y1)) for t, _, b in ocr_boxes(img[y1:y2], scale=2)]
    costs = []
    for text, b in tokens:
        # 淡色的卡上减号常被漏认（「-6」读成「6」）；位置限定在卡片右上角，所以不带减号的一两位数也算
        m = re.fullmatch(r"\s*-?\s*(\d{1,2})\s*", text)
        if m:
            costs.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, int(m.group(1))))
    out = []
    for text, b in tokens:
        compact = text.replace(" ", "")
        for pattern, kind in ACTIONS:
            m = pattern.search(compact)
            if not m:
                continue
            cx, ty = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
            # 按钮图标在文字上方；「休む」是右侧的小按钮，文字在图标下方紧挨着
            center = (cx, ty - 22) if kind == "rest" else (cx, ty - 70)
            attr = None
            if m.groups() and m.group(1):
                attr = m.group(1).lower()
            elif kind == "class":
                attr = _attr_by_badge(img, cx, ty - 20)
            if attr is None and kind in ("open_lesson", "lesson"):
                attr = _attr_by_band(img, cx, ty)
            option = ActionOption(kind, compact, attr, center)
            if kind == "rest":
                # 回复量在按钮左上角的爱心徽章里（白字深描边），位于文字左上方
                option.hp_gain = read_number(img, (cx - 58, ty - 96, cx - 14, ty - 54))
            else:
                # 消耗在同一张卡的右上角：水平在卡宽内、竖直在文字上方 40–200 像素
                option.hp_cost = next((c for x, y, c in costs if abs(x - cx) < 80 and ty - 200 < y < ty - 40), None)
                if ocr_engine() != "vision":
                    option.hp_cost = _cost_by_heart(img, cx, ty, option.hp_cost)
            out.append(option)
            break
    return sorted(out, key=lambda o: (o.center[1] > 950, o.center[0]))


STAMINA_SLASH_MISREAD = "10"  # 「/」被 Apple Vision 认成的字符（画面稍亮时「33/33」→「33133」「33033」）


def parse_stamina(text: str) -> tuple[int, int] | None:
    """「33/33」→ (33, 33)。斜杠被认成 1 / 0 或漏掉时：上限按两位数取末两位，前面的分隔符去掉，剩下的是当前体力（须 ≤ 上限）。"""
    text = re.sub(r"[lI|／\\]", "/", text.replace(" ", ""))
    if m := re.fullmatch(r"\D*(\d{1,3})/(\d{2,3})\D*", text):
        cur, cap = int(m.group(1)), int(m.group(2))
        return (cur, cap) if 0 < cap and cur <= cap else None
    if "/" in text:
        return None  # 看得到斜杠却对不上（如 12/9），不猜
    digits = re.sub(r"\D", "", text)
    if not 4 <= len(digits) <= 5:
        return None
    cap = int(digits[-2:])
    for head in ((digits[:-3],) if digits[-3] in STAMINA_SLASH_MISREAD else ()) + (digits[:-2],):
        if 1 <= len(head) <= 2 and int(head) <= cap:
            return int(head), cap
    return None


def read_stamina(img: MatLike) -> tuple[int, int] | None:
    """体力「当前/上限」。q7 续跑第 1 天连续 5 帧给 None（画面淡入、偏亮时斜杠读成 1），离线同一帧又读得出：
    先严格读（放大 4 倍、2 倍各一次），都不成再按斜杠误读规则拆。"""
    x1, y1, x2, y2 = STAMINA_BOX
    crop = img[y1:y2, x1:x2]
    texts = []
    for scale in (4, 2):
        text = "".join(t for t, _ in ocr(crop, scale=scale, languages=("en-US",)))
        if m := re.search(r"(\d+)\s*/\s*(\d+)", text):
            cur, cap = int(m.group(1)), int(m.group(2))
            if cur <= cap:
                return cur, cap
        texts.append(text)
    return next((r for t in texts if (r := parse_stamina(t))), None)


DAYS_DIGIT_BOX = (78, 82, 118, 132)  # 左上「N日」的大号数字（不含「日」）


def _days_dddd(img: MatLike) -> int | None:
    """OCR 没读出「N日」时（RapidOCR 只读到「日」），用 ddddocr 读固定位置的大号数字：8 帧回归全对。"""
    from hiro.vision.digits import read_dddd

    v = read_dddd(img, DAYS_DIGIT_BOX)
    return v if v is not None and 1 <= v <= 30 else None


def normalize_hif(text: str) -> str:
    """「H.I.F」的点和 I 常被 RapidOCR 读走样：「HI.F本戦」「H.「.F本戦」「H.、F本戦」（DMM maindmm1m-00058）。H、F 之间只剩标点 / 竖线类字符时统一回「H.I.F」。"""
    return re.sub(r"H[\s.．。、,，「」|｜lI1Ｉ]{0,4}F", "H.I.F", text)


def read_action_select(img: MatLike) -> ActionSelectState:
    header = " ".join(t for t, _ in ocr(img[:HEADER_BAND, :280], scale=2))
    exam = re.search(r"(\S+?)まで", header)
    # 选行动页剩余天数 ≥ 1：RapidOCR 会在左上装饰上读出幻影「0日」排在真数字前（DMM qualifydmm1-00390「0日 … ふ日」），跳过它交给 ddddocr
    days = next((int(d) for d in re.findall(r"(\d+)\s*日", header) if 1 <= int(d) <= 30), None)
    stamina = read_stamina(img)
    points = re.search(r"\d+", "".join(t for t, _ in ocr(img[slice(*POINTS_BOX[1::2]), slice(*POINTS_BOX[::2])], scale=4, languages=("en-US",))))

    def small_text(box) -> str:
        x1, y1, x2, y2 = box
        return "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=4, languages=("en-US",)))

    caps, growth = {}, {}
    for a in ATTRS:
        # 「/3000」的斜杠常被认成 1（「13000」）；上限固定四位数，取最后四位
        digits = re.sub(r"\D", "", small_text(CAP_BOXES[a]).replace("O", "0"))
        caps[a] = int(digits[-4:]) if len(digits) >= 4 else None
        g = re.search(r"(\d+(?:\.\d)?)\s*%", small_text(GROWTH_BOXES[a]))
        growth[a] = float(g.group(1)) if g else None
    advice = "".join(t for t, _ in ocr(img[slice(*ADVICE_BAND)], scale=2)) or None
    return ActionSelectState(
        exam=normalize_hif(exam.group(1)) if exam else None,
        days_left=days if days is not None else _days_dddd(img),
        stamina=stamina[0] if stamina else None,
        max_stamina=stamina[1] if stamina else None,
        p_points=int(points.group()) if points else None,
        params={a: read_number(img, box) for a, box in stat_boxes(img).items()},
        param_caps=caps,
        growth_pct=growth,
        star=read_number(img, STAR_BOX),
        advice=advice,
        options=_options(img),
    )


def _icon_span(hsv: np.ndarray, attr: str) -> tuple[int, int] | None:
    """属性图标在 STAT_ROW 行里的 x 范围（色相列占比 ≥ 0.3 的连续段，间隔 < 12 的合并：Vo 图标中间有白色话筒）。"""
    lo, hi = STAT_ICON_HUE[attr]
    x1, x2 = STAT_ICON_SEARCH[attr]
    h, sat, v = hsv[:, x1:x2, 0], hsv[:, x1:x2, 1], hsv[:, x1:x2, 2]
    cols = np.nonzero((((h >= lo) & (h <= hi) & (sat >= 120) & (v >= 150)).mean(axis=0) >= 0.3))[0]
    if len(cols) == 0:
        return None
    start = end = int(cols[0])
    for c in cols[1:]:
        if c - end >= 12:
            break
        end = int(c)
    return (x1 + start, x1 + end + 1) if end - start >= 20 else None


def stat_boxes(img: MatLike) -> dict[str, tuple[int, int, int, int]]:
    """三个属性数字框：从图标右缘开始、到下一个图标（Vi 到「P手帳」）之前；图标找不到时用固定框 STAT_BOXES。"""
    y1, y2 = STAT_ROW
    hsv = cv2.cvtColor(img[y1:y2], cv2.COLOR_BGR2HSV)
    spans = {a: _icon_span(hsv, a) for a in ATTRS}
    out = {}
    for i, a in enumerate(ATTRS):
        if spans[a] is None:
            out[a] = STAT_BOXES[a]
            continue
        nxt = spans[ATTRS[i + 1]] if i + 1 < len(ATTRS) else None
        limit = nxt[0] - 2 if nxt is not None else STAT_RIGHT_LIMIT
        left = spans[a][1] + 2
        out[a] = (left, 684, min(left + STAT_DIGITS_W, limit), 719)
    return out
