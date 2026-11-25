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

from hiro.vision.digits import read_number
from hiro.vision.ocr import engine as ocr_engine
from hiro.vision.ocr import ocr, ocr_boxes, ocr_line

import cv2
import numpy as np
from cv2.typing import MatLike

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
    # 「ッ」也会读成大写「ツ」（11-21 DMM：「Vi公開レツスン」，那一格公开课整个丢了），「公開」只出现在这几个按钮上，认到「公開レ」就算
    (re.compile(r"(Vo|Da|Vi)?\.?公開レ"), "open_lesson"),
    (re.compile(r"授業"), "class"),
    (re.compile(r"休む"), "rest"),
    (re.compile(r"相談"), "consult"),
    (re.compile(r"差し入れ"), "present"),
    (re.compile(r"特別指導"), "special_training"),
    (re.compile(r"おでかけ"), "outing"),
    (re.compile(r"^営業$"), "sales"),  # N.I.A 営業（2次まで 6 週 等只有这一个按钮，11-17 原来读不出、决策 Wait 停）
    (
        re.compile(r"活動支給"),
        "allowance",
    ),  # 初 普通周的「活動支給」（领饮料 / 卡），3940a9 的 hajime_nia 决策认 allowance
    # N.I.A「Vo.自主レッスン」：属性前缀和「レッスン」之间夹着「自主」，要先于下面的 lesson（11-17 N.I.A 采样：attr 读成 None）
    (re.compile(r"(Vo|Da|Vi)?\.?自主レ[ッツ][スズ]ン"), "self_lesson"),
    (re.compile(r"(Vo|Da|Vi)?\.?レ[ッツ][スズ]ン"), "lesson"),
]
# 课程按钮左上角的 SP 角标（InProduce.IconSp）相对按钮文字中心的位置：N.I.A 实机 (−67, −146)
SP_DX, SP_DY = (-110, -20), (-190, -100)
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
    sp: bool | None = None  # 课程（lesson / self_lesson）左上角有 SP 角标；其余 None


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
    exam_next: str | None = (
        None  # 初 / N.I.A：页头「中間 / 1次 / 2次 / 最終まで」→ mid1 / mid2 / final（3940a9 按它查下一场的审查基准）
    )
    weeks_left: int | None = (
        None  # 初 / N.I.A：页头「N週」（离下一场考试还有几周；换算成 step 见 hiro.state.modes.step_from_header）
    )

    @property
    def complete(self) -> bool:
        """头部数值齐全、有可选行动且课程类按钮都认出了属性。按钮滑入动画中的帧不完整，应等下一帧。
        初的「授業」只有一个按钮、不分属性（3940a9 11-17），不要求属性。"""
        need_attr = ("open_lesson",) if self.exam_next else ("class", "open_lesson")
        lessons_ok = all(o.attr for o in self.options if o.kind in need_attr)
        return (
            None not in (self.days_left, self.stamina, self.p_points)
            and None not in self.params.values()
            and bool(self.options)
            and lessons_ok
        )

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
    mask = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 120)).astype(
        np.uint8
    )
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    hearts = [
        st[i]
        for i in range(1, n)
        if COST_HEART_AREA[0] <= st[i][4] <= COST_HEART_AREA[1] and 12 <= st[i][2] <= 45 and 15 <= st[i][3] <= 45
    ]
    if not hearts:
        return None
    if read is not None:
        return read
    hx, hy, hw, hh, _ = hearts[0]
    box = (x1 + hx + hw, y1 + hy - 4, x1 + hx + hw + 60, y1 + hy + hh + 4)
    m = re.search(r"(\d{1,2})", "".join(t for t, _, _ in ocr_boxes(img[box[1] : box[3], box[0] : box[2]], scale=3)))
    return int(m.group(1)) if m else None


# 「的」可省：macOS 27 的 Vision 常把「ボーカルを重点的に」读成「ボーカルを重点に」（11-25 账号 3，三条只认出一条、决策 Wait 停机）
OIKOMI = re.compile(r"(ボーカル|ダンス|ビジュアル)を重点")
OIKOMI_ATTR = {"ボーカル": "vo", "ダンス": "da", "ビジュアル": "vi"}
OIKOMI_BAND = (600, 1000)  # 追い込み三选一「〜を重点的に」的 y 范围（初 Mac 11-17：约 y 700 / 800 / 902）
OIKOMI_ICON_X = (55, 115)  # 选项条左边的属性图标


def _oikomi_options(img: MatLike) -> list[ActionOption]:
    """初「追い込みレッスン」：选行动页上盖着三条「ボーカル / ダンス / ビジュアルを重点的に」。属性按左边图标颜色认（用户 11-17：图标优先），
    认不出再看文字。没有这三条返回空。"""
    y1, y2 = OIKOMI_BAND
    out = []
    for text, _, b in ocr_boxes(img[y1:y2], scale=2):
        m = OIKOMI.search(text.replace(" ", ""))
        if not m:
            continue
        cy = (b[1] + b[3]) // 2 + y1
        hsv = cv2.cvtColor(img[cy - 22 : cy + 22, OIKOMI_ICON_X[0] : OIKOMI_ICON_X[1]], cv2.COLOR_BGR2HSV)
        vivid = (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 120)
        hues = hsv[:, :, 0][vivid]
        counts = {a: int(((hues >= lo) & (hues <= hi)).sum()) for a, (lo, hi) in ATTR_HUE.items()}
        best = max(counts, key=counts.get)
        attr = best if counts[best] >= 40 else OIKOMI_ATTR[m.group(1)]
        out.append(ActionOption("oikomi", text.replace(" ", ""), attr, ((b[0] + b[2]) // 2, cy)))
    return sorted(out, key=lambda o: o.center[1]) if len(out) == 3 else []


OIKOMI_STAT_BAND = (
    425,
    485,
)  # 追い込み页的三维数值换成大号排版（约 y 455，x 150 / 380 / 610），普通页的 STAT_ROW 读不到


def _oikomi_params(img: MatLike) -> dict:
    y1, y2 = OIKOMI_STAT_BAND
    # 左边的属性图标常被读成一个字（「込260」）：取末尾的数字
    nums = sorted(
        ((b[0] + b[2]) / 2, int(m.group(1)))
        for t, _, b in ocr_boxes(img[y1:y2], scale=2)
        if (m := re.search(r"(\d{2,4})$", t.strip()))
    )
    return dict(zip(ATTRS, (v for _, v in nums))) if len(nums) == 3 else dict.fromkeys(ATTRS)


def _options(img: MatLike, *, hajime: bool = False) -> list[ActionOption]:
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
            if kind == "self_lesson":
                # N.I.A 自主レッスン：按钮右下的属性小图标颜色优先，文字前缀兜底（用户 11-17：图标优先）
                attr = _attr_by_badge(img, cx, ty - 20) or (m.group(1).lower() if m.group(1) else None)
            elif m.groups() and m.group(1):
                attr = m.group(1).lower()
            elif kind == "class" and not hajime:  # 初的「授業」不分属性（右下小图标是别的东西，读出来是幻影 Da）
                attr = _attr_by_badge(img, cx, ty - 20)
            if attr is None and kind in ("open_lesson", "lesson"):
                attr = _attr_by_band(img, cx, ty)
            option = ActionOption(kind, compact, attr, center)
            if kind in ("lesson", "self_lesson"):
                option.sp = any(
                    SP_DX[0] < x - cx < SP_DX[1] and SP_DY[0] < y - ty < SP_DY[1] for x, y in _sp_badges(img)
                )
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
    out += _missing_open_lessons(img, out)
    return sorted(out, key=lambda o: (o.center[1] > 950, o.center[0]))


OPEN_LESSON_X = (172, 359, 544)  # H.I.F 公开课三张卡的文字中心 x（720x1280）


def _missing_open_lessons(img: MatLike, found: list) -> list:
    """公开课三张卡并排（Vo / Da / Vi）。文字认到了一两张、另一格的字没认出来时（11-21 DMM：主属性 Da 那格连续几天没认出，
    LLM 只能在 Vo / Vi 里选），按那一格文字条的底色补：底色是一种还没出现的属性才补，其余不补（不凭空造选项）。"""
    opened = [o for o in found if o.kind == "open_lesson"]
    if not opened or len(opened) >= 3:
        return []
    ty = opened[0].center[1] + 70  # 文字中心（center 是按钮图标处，文字上方 70）
    have = {o.attr for o in opened}
    add = []
    for x in OPEN_LESSON_X:
        if any(abs(o.center[0] - x) < 60 and abs(o.center[1] - opened[0].center[1]) < 60 for o in found):
            continue
        attr = _attr_by_band(img, x, ty)
        if attr is None or attr in have:
            continue
        option = ActionOption("open_lesson", "（文字没认出，按色条补）", attr, (x, ty - 70))
        option.hp_cost = _cost_by_heart(img, x, ty, None) if ocr_engine() != "vision" else None
        have.add(attr)
        add.append(option)
    return add


def _sp_badges(img) -> list[tuple[int, int]]:
    from hiro.vision.ui_templates import tpl

    return [(m.rect.center.x, m.rect.center.y) for m in tpl("InProduce.IconSp").find_all(img)]


STAMINA_SLASH_MISREAD = "10"  # 「/」被 Apple Vision 认成的字符（画面稍亮时「33/33」→「33133」「33033」）


def parse_stamina(text: str) -> tuple[int, int] | None:
    """「33/33」→ (33, 33)。斜杠被认成 1 / 0 或漏掉时：上限按两位数取末两位，前面的分隔符去掉，剩下的是当前体力（须 ≤ 上限）。"""
    text = re.sub(r"[lI|／\\]", "/", text.replace(" ", ""))
    if m := re.fullmatch(r"\D*(\d{1,3})/(\d{2,3})\D*", text):
        cur, cap = int(m.group(1)), int(m.group(2))
        return (cur, cap) if cap > 0 and cur <= cap else None
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
        text = ocr_line(crop, scale=scale, languages=("en-US",))
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


# 初 / N.I.A 页头「中間まで / 1次まで / 2次まで / 最終まで」（H.I.F 是「選抜試験1まで」「H.I.F本戦まで」）。初 Mac 11-17、N.I.A Mac 11-17 帧核过
HAJIME_HEADER = {"中間": "mid1", "1次": "mid1", "2次": "mid2", "最終": "final"}
WEEKS_MAX = 12


def _weeks(header: str, img: MatLike) -> int | None:
    """页头「N週」：OCR 常把「3」读成「ろ」（初 / N.I.A 帧「中間まで ろ週」「2次まで ろ週」）；读不出数字再用 ddddocr 读大号数字（同 _days_dddd）。"""
    m = re.search(r"([\dろ]{1,2})\s*週", header)
    if m:
        v = int(m.group(1).replace("ろ", "3"))
        if 1 <= v <= WEEKS_MAX:
            return v
    v = _days_dddd(img)
    return v if v is not None and 1 <= v <= WEEKS_MAX else None


def read_action_select(img: MatLike) -> ActionSelectState:
    header = " ".join(t for t, _ in ocr(img[:HEADER_BAND, :280], scale=2))
    exam = re.search(r"(\S+?)まで", header)
    # 选行动页剩余天数 ≥ 1：RapidOCR 会在左上装饰上读出幻影「0日」排在真数字前（DMM qualifydmm1-00390「0日 … ふ日」），跳过它交给 ddddocr
    days = next((int(d) for d in re.findall(r"(\d+)\s*日", header) if 1 <= int(d) <= 30), None)
    stamina = read_stamina(img)
    points = re.search(
        r"\d+", ocr_line(img[slice(*POINTS_BOX[1::2]), slice(*POINTS_BOX[::2])], scale=4, languages=("en-US",))
    )

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
    exam_next = next((v for k, v in HAJIME_HEADER.items() if exam and k in exam.group(1)), None)
    weeks_left = _weeks(header, img) if exam_next else None
    options = _oikomi_options(img) if exam_next else []
    return ActionSelectState(
        exam=normalize_hif(exam.group(1)) if exam else None,
        days_left=days if days is not None else _days_dddd(img),
        stamina=stamina[0] if stamina else None,
        max_stamina=stamina[1] if stamina else None,
        p_points=int(points.group()) if points else None,
        params=_oikomi_params(img) if options else {a: read_number(img, box) for a, box in stat_boxes(img).items()},
        param_caps=caps,
        growth_pct=growth,
        star=read_number(img, STAR_BOX),
        advice=advice,
        options=options or _options(img, hajime=exam_next is not None),
        exam_next=exam_next,
        weeks_left=weeks_left,
    )


def _icon_span(hsv: np.ndarray, attr: str) -> tuple[int, int] | None:
    """属性图标在 STAT_ROW 行里的 x 范围（色相列占比 ≥ 0.3 的连续段，间隔 < 12 的合并：Vo 图标中间有白色话筒）。"""
    lo, hi = STAT_ICON_HUE[attr]
    x1, x2 = STAT_ICON_SEARCH[attr]
    h, sat, v = hsv[:, x1:x2, 0], hsv[:, x1:x2, 1], hsv[:, x1:x2, 2]
    cols = np.nonzero(((h >= lo) & (h <= hi) & (sat >= 120) & (v >= 150)).mean(axis=0) >= 0.3)[0]
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
