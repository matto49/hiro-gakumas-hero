# 来源：digital-hiro src/hiro/vision/cards.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""技能卡身份补全。

KAA 用卡面插图比对出卡牌 id，但插图与强化次数无关，返回的总是未强化版本。
强化卡的图标右上角有红色「+」，按红色像素数判断是否强化，再按 (id, upgradeCount) 查数据库得到正确的名称和效果。
"""

import json
import re
from functools import lru_cache

import cv2
import numpy as np
from cv2.typing import MatLike
from kaa.db.skill_card import PRODUCE_CARD_SELECT, SkillCard, _load_exam_effects_from_rows
from kaa.db.sqlite import select
from kotonebot.primitives import Rect

UPGRADE_RED_MIN = 20  # 实测：强化 55–62，未强化 0–1
# 红色占满检测框的比例上限：「+」只占一小块（选卡框 52–97 / 3300 px）；卡面本身偏红（DMM dmm1m 本战 ノートの端の決意 的肤色手，
# 921 px、28%）时红色是插图不是「+」，改看高饱和红（「+」S≥150 有 40–54 px，肤色 0），至少 UPGRADE_RED_MIN 个才算强化
UPGRADE_RED_MAX_FRAC = 0.25
UPGRADE_PLUS_SAT = 150


def red_mask(img: MatLike) -> np.ndarray:
    """「+」标记的红色。饱和度/亮度下限放宽到 60/90，压暗的不可用卡上的「+」也能检出
    （实测：亮卡 strict 117–124 / loose 146–154，灰卡 strict 0 / loose 149，未强化 0 / 0）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (0, 60, 90), (10, 255, 255)) | cv2.inRange(hsv, (170, 60, 90), (180, 255, 255))


def dialog_card_upgraded(img: MatLike, letter: Rect) -> tuple[bool, int]:
    """选卡弹窗中的卡：letter 为卡图标底部的类型字母框（KAA fetch_cards 返回的 rect）。"""
    cx, top = letter.x1 + letter.w // 2, letter.y1
    return _upgraded(img, (slice(top - 110, top - 50), slice(cx + 2, cx + 57)))


def hand_card_upgraded(img: MatLike, letter: Rect) -> tuple[bool, int]:
    """出牌画面手牌：红色「+」位于费用心形上方，相对卡名首字母框 x+65..95、y-55..-28（720x1280）。"""
    cx, top = letter.x1 + letter.w // 2, letter.y1
    return _upgraded(img, (slice(top - 55, top - 28), slice(cx + 65, cx + 95)))


def _upgraded(img: MatLike, box: tuple[slice, slice]) -> tuple[bool, int]:
    """检测框里的红色「+」：红色像素够数、又没把框占满（占满 = 卡面插图偏红，改数高饱和红）。返回 (是否强化, 红色像素数)。"""
    roi = red_mask(img[box])
    pixels = int(np.count_nonzero(roi))
    if pixels < UPGRADE_RED_MIN:
        return False, pixels
    if pixels <= UPGRADE_RED_MAX_FRAC * roi.size:
        return True, pixels
    sat = cv2.cvtColor(img[box], cv2.COLOR_BGR2HSV)[..., 1]
    return int(np.count_nonzero((roi > 0) & (sat >= UPGRADE_PLUS_SAT))) >= UPGRADE_RED_MIN, pixels


@lru_cache(maxsize=512)
def card_version(card_id: str, upgrade_count: int) -> SkillCard | None:
    row = select(f"{PRODUCE_CARD_SELECT} WHERE id = ? AND upgradeCount = ?;", card_id, upgrade_count)
    if row is None:
        return None
    return SkillCard._from_row(row, _load_exam_effects_from_rows([row]))


def full_text(card: SkillCard) -> str:
    """卡牌完整描述（含使用条件、开局入手、レッスン中1回等），与游戏内卡牌详情一致。

    KAA 的 effect_display_text 只拼接各出牌效果的说明，会丢掉卡牌级的条件与属性。
    这里按 produce_descriptions 片段的来源效果 id 分行；只带触发 id 的片段（条件）并入其后的效果行。
    """
    try:
        parts = json.loads(card.produce_descriptions or "[]")
    except json.JSONDecodeError:
        return card.effect_display_text
    lines, buf, buf_effect, buf_plain = [], "", None, False

    def flush():
        nonlocal buf, buf_effect, buf_plain
        if buf:
            lines.append(buf)
        buf, buf_effect, buf_plain = "", None, False

    for item in parts:
        text = re.sub(r"</?nobr>", "", item.get("text") or "")
        effect = item.get("originProduceExamEffectId")
        trigger = item.get("originProduceExamTriggerId")
        if item.get("produceDescriptionType") == "ProduceDescriptionType_ProduceDescription" and not effect and not trigger:
            flush()  # 独立标签：重複不可、レッスン中1回
            lines.append(text)
            continue
        if effect and ((buf_effect and effect != buf_effect) or buf_plain):
            flush()  # 新效果开始；或前面是不挂效果的前缀（消耗、开局入手）
        if effect:
            buf_effect = effect
        elif not trigger and not buf_effect and text.strip():
            buf_plain = True
        if trigger and buf_plain:
            flush()  # 消耗前缀之后的条件另起一行
        if trigger:
            buf_plain = False  # 条件并入其后的效果行
        buf += text
        if trigger and buf.endswith("使用可"):
            flush()  # 出牌条件（〜の場合、使用可）单独一行
    if buf:
        lines.append(buf)
    return "\n".join(line.strip() for line in lines if line.strip()) or card.effect_display_text


def card_summary(card: SkillCard | None, *, upgraded: bool | None = None) -> dict | None:
    """卡牌字段；upgraded=True 时换成 +1 版本（选卡/手牌里看不出 ++ 以上，按 +1 处理）。"""
    if card is None:
        return None
    if upgraded:
        card = card_version(card._id, 1) or card
    return {
        "name": card.name,
        "card_id": card._id,
        "upgrade": card.upgrade_count or 0,
        "plan": card.plan_type,
        "category": card.category,
        "rarity": card.rarity,
        "genki_cost": card.stamina or None,
        "hp_cost": card.force_stamina or None,
        "extra_cost": {"type": card.cost_type, "value": card.cost_value} if card.cost_value else None,
        "effect": full_text(card),
    }
