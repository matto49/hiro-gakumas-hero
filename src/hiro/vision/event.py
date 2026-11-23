"""授業（STUDY）/ おでかけ（OUTING）三选一：选项文字、代价角标、选中项与选中后弹出的效果说明。

两页是同一套界面。三行选项中心 y ≈ 698 / 799 / 901（720x1280），每行右上方的角标是代价：
体力「-5」（心形）、P 点「P -50」或「トラブル追加」。选中一行后该行变成高饱和色并带「>>」，
上方弹出效果说明（「ボーカル上昇+120 / やる気関係のスキルカードを選択して獲得」）。
整块只做一次 Apple Vision，按文字框的 y 分到各行。
选中后其余两行会压暗，文字常丢首字：选项文字以未选状态那一帧为准，选中后主要读 selected 与 effect。
"""

import re
import unicodedata

from hiro.vision.ocr import ocr_boxes

import cv2
import numpy as np
from cv2.typing import MatLike

REGION = (75, 470, 720, 950)  # 左边界避开行首图标（会被读成「2」并吃掉首字）
ROW_Y = (698, 799, 901)
ROW_HALF = 26  # 选项文字行的半高
BADGE = (-56, -22)  # 角标相对行中心的 y 范围
TEXT_X = (70, 560)  # 选项文字的 x 范围（左边是图标，右边是「>>」）
BADGE_X = 520
EFFECT_Y = (480, 640)  # 选中后的效果说明
SELECTED_SAT = 100  # 选中行饱和度：授業 118–166、外出 129–141；未选 ≤ 70
SELECTED_LEAD = 40


def _badge_kind(img: MatLike, x1: int, y1: int, y2: int) -> str | None:
    """角标数字左侧的小图标：黄 = P 点，绿 = 体力（取自 github-f4 的实现）。"""
    hsv = cv2.cvtColor(img[max(0, y1 - 4) : y2 + 4, max(0, x1 - 34) : max(1, x1 - 2)], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 120)
    if vivid.sum() < 15:
        return None
    hue = np.median(hsv[:, :, 0][vivid])
    if 15 <= hue <= 32:
        return "p"
    if 33 < hue <= 85:
        return "hp"
    return None


def _badge_fallback(img: MatLike, cy: int) -> tuple[int | None, int | None]:
    """整块识别漏掉的角标（选中后其余行压暗，小字常读不出）：单独裁按钮右上角放大再认。→ (体力消耗, P 消耗)。取自 github-f4。"""
    y1, y2 = cy + BADGE[0], cy + BADGE[1]
    # 先 6 倍（Vision 小字要放大）；读不出再 3 倍补一次：RapidOCR 6 倍时把「-5」读成「5」、框也偏，判不出角标种类
    for scale in (6, 3):
        for text, _, b in ocr_boxes(img[y1:y2, 560:700], scale=scale, languages=("en-US",)):
            m = re.search(r"-?\s*(\d{1,3})", text)
            if not m:
                continue
            kind = _badge_kind(img, 560 + b[0], y1 + b[1], y1 + b[3])
            if kind is None and "-" not in text:
                continue
            value = int(m.group(1))
            return (None, value) if kind == "p" or "P" in text else (value, None)
    return None, None


def _row_of(cy: float, offset: tuple[int, int] = (-ROW_HALF, ROW_HALF)) -> int | None:
    for i, y in enumerate(ROW_Y):
        if y + offset[0] <= cy <= y + offset[1]:
            return i
    return None


GREY_V = (125, 190)  # 选不了的选项（P 不够）：整条灰底，亮度约 158；普通未选中约 220、白底 255、选中是彩色
GREY_UNIFORM = 0.9


def _grey_row(hsv: np.ndarray, y: int) -> bool:
    """第 y 行的按钮是灰的（选不了，初外出 P 不够的档，协调 11-17）：按钮右半段（文字后面）几乎全是低饱和、亮度在 GREY_V 里。"""
    r = hsv[y - 12 : y + 12, 330:560]
    if r.size == 0:
        return False
    v = float(np.median(r[..., 2]))
    return (
        GREY_V[0] <= v <= GREY_V[1]
        and float((r[..., 1] < 40).mean()) >= GREY_UNIFORM
        and float(np.median(r[..., 1])) < 20
    )


def grey_rows(img: MatLike) -> list[int]:
    """灰色（选不了）的选项行号（ROW_Y 的下标）。判页用：option_regions 只认亮按钮，灰的不算，外出页只剩 1 个按钮会判成 IDLE。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return [i for i, y in enumerate(ROW_Y) if _grey_row(hsv, y)]


def read_event_choices(img: MatLike) -> dict:
    """→ {"choices": [{index, text, cost_hp, cost_p, trouble, selected, center}], "selected": index | None, "effect": str | None, "complete": bool}。
    文字读不到的行 text 为 None；没有选中时 effect 为 None。"""
    x0, y0, x1, y1 = REGION
    boxes = [
        (t, (bx1 + x0, by1 + y0, bx2 + x0, by2 + y0))
        for t, _, (bx1, by1, bx2, by2) in ocr_boxes(img[y0:y1, x0:x1], scale=2)
    ]
    texts: list[list[tuple[int, str]]] = [[] for _ in ROW_Y]
    badges: list[list[tuple[str, tuple]]] = [[] for _ in ROW_Y]
    effect: list[tuple[int, str]] = []
    for t, (bx1, by1, bx2, by2) in boxes:
        cy = (by1 + by2) / 2
        if EFFECT_Y[0] <= cy <= EFFECT_Y[1] and _row_of(cy, BADGE) is None:
            effect.append((by1, t))
        elif (r := _row_of(cy)) is not None and bx1 < TEXT_X[1]:
            texts[r].append((bx1, t))
        elif (r := _row_of(cy, BADGE)) is not None and bx1 >= BADGE_X:
            badges[r].append((t, (bx1, by1, bx2, by2)))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sats = [float(hsv[y - 12 : y + 12, 120:520, 1].mean()) for y in ROW_Y]
    top = int(np.argmax(sats))
    selected = (
        top
        if sats[top] >= SELECTED_SAT and all(sats[top] - s >= SELECTED_LEAD for i, s in enumerate(sats) if i != top)
        else None
    )
    choices = []
    for i, y in enumerate(ROW_Y):
        text = "".join(t for _, t in sorted(texts[i])).strip()
        text = re.sub(
            r"^[\d\s>＞]+(?!\d*\s*(?:パーセント|%))", "", text
        )  # 行首图标常被读成「2」（初外出「50パーセント」的数字保留）
        text = re.sub(r"^\S\s+", "", text)  # 或读成单个符号 / 字加空格（「& 」「日 」）；选项文字本身不含空格
        text = re.sub(r"[>＞\s]+$", "", text) or None  # 选中行末尾的「>>」
        cost_hp = cost_p = None
        for t, (bx1, by1, _, by2) in badges[i]:
            m = re.search(r"-\s*(\d{1,3})", t) or re.fullmatch(r"\s*(\d{1,3})\s*", t)
            if not m:
                continue
            kind = _badge_kind(img, bx1, by1, by2)
            if "-" not in t and kind is None:
                continue  # 不带减号的数字只有左侧确有爱心 / P 图标时才算角标
            if kind == "p" or "P" in t or "Ｐ" in t:
                cost_p = int(m.group(1))
            else:
                cost_hp = int(m.group(1))
        trouble = any("トラブル" in t for t, _ in badges[i])
        if cost_hp is None and cost_p is None and not trouble:
            cost_hp, cost_p = _badge_fallback(img, y)
        choices.append({"index": i, "text": text, "cost_hp": cost_hp, "cost_p": cost_p, "trouble": trouble,
                        "selected": selected == i if selected is not None else False, "center": [360, y],
                        "disabled": _grey_row(hsv, y)})  # fmt: skip
    # 效果说明做 NFKC 规范化（全角「％」「＋」→ 半角），决策器按效果名匹配
    eff = (
        unicodedata.normalize("NFKC", "\n".join(t for _, t in sorted(effect)))
        if selected is not None and effect
        else None
    )
    # complete：三行选项文字都读到了（按钮滑入动画中的帧不完整，主循环会等下一帧）
    return {"choices": choices, "selected": selected, "effect": eff, "complete": all(c["text"] for c in choices)}


DIALOGUE_DARK_MIN = (
    0.04  # 纯台词按钮：白底深色字，按钮里深色像素约 8%（初开场二选一）；授業 / 外出的选项字是彩色或灰色，≤ 2%
)
DIALOGUE_COLOR_MAX = 0.02  # 彩色像素（属性图标、选中的彩色底）超过这个就不是纯台词
BUTTON_MATCH_DY = 30


def _plain_dialogue(hsv: np.ndarray, b) -> bool:
    r = hsv[b.y1 + 10 : b.y2 - 10, b.x1 + 40 : b.x1 + 500]
    if r.size == 0:
        return False
    dark = float((r[..., 2] < 90).mean())
    color = float(((r[..., 1] > 100) & (r[..., 2] > 120)).mean())
    return dark >= DIALOGUE_DARK_MIN and color <= DIALOGUE_COLOR_MAX


def read_event_choice_page(img: MatLike) -> dict:
    """事件选项页（判页 INITIAL_CHOICE）：read_event_choices 按授業的三行固定位置读，二选一时第 0 行是空的幻影（初开场
    「お祝いをしないといけませんね / 次の試練が待っていますよ」，协调 11-17：EVENT_CHOICE 读成 3 项、其中 1 项没字）。
    这里去掉既没有按钮（option_regions）也没读到字的行，按从上到下重新编号（二选一时和 HifCore._event_commit 的按钮序号一致）。
    dialogue_only：每个按钮都是白底深色字、没有图标、没有代价角标 / トラブル（纯台词，选哪个都没有效果）。"""
    from hiro.vision.classify import option_regions

    raw = read_event_choices(img)
    buttons = sorted(option_regions(img), key=lambda b: b.center.y)
    keep = []
    for c in raw["choices"]:
        b = next((b for b in buttons if abs(c["center"][1] - b.center.y) <= BUTTON_MATCH_DY), None)
        if b is not None or c["text"]:  # 没字也没按钮的行是幻影；选中态的按钮 option_regions 可能认不出，有字就留
            keep.append((c, b))
    choices, selected = [], None
    for i, (c, b) in enumerate(keep):
        if c.get("selected"):
            selected = i
        choices.append(
            {**c, "index": i, "center": [int(b.center.x), int(b.center.y)] if b is not None else c["center"]}
        )
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    dialogue = (len(choices) >= 2 and all(b is not None and _plain_dialogue(hsv, b) for _, b in keep)
                and not any(c["cost_hp"] or c["cost_p"] or c["trouble"] for c in choices))  # fmt: skip
    return {"choices": choices, "selected": selected, "effect": raw["effect"] if selected is not None else None,
            "complete": bool(choices) and all(c["text"] for c in choices), "dialogue_only": dialogue}  # fmt: skip
