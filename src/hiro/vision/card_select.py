"""三选一选卡弹窗（差し入れ / 授業奖励 / 活動支給 等）的候选：位置、卡、「おすすめ」（去 KAA 第 5/6 步，替换 KAA CardSelectContext）。

位置照 KAA fetch_cards 移植：卡区（box InProduce.SkillCardSelect.CardsBox）里找类别字母 A / M（阈值 0.65），按左上角排序，
中心 x 相差 30 以内算同一张。「おすすめ」照 KAA fetch_recommend_card：徽章挂在卡的正下方（badge.match 的 mb），取第一张。
认卡（_dialog_card）用卡面图库 hiro.vision.card_db（和 KAA match_card_region 同一做法，相談货架 / 牌组卡格同一套图库）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hiro.vision.ocr import Box
from hiro.vision.ui_templates import box, normalize, tpl

import numpy as np

LETTER_THRESHOLD = 0.65
SAME_CARD_DX = 30


@dataclass(frozen=True)
class CardOption:
    rect: Box  # 类别字母框（点这里选卡，同 KAA）
    card: object | None  # 认出的卡（hiro.master.Card）；认不出 None


def card_letters(img: np.ndarray) -> list[Box]:
    """卡区里的类别字母框，按左上角排序、去掉同一张卡的重复命中。"""
    img = normalize(img)
    region = box("InProduce.SkillCardSelect.CardsBox").xywh
    hits = [
        m.rect
        for name in ("InProduce.A", "InProduce.M")
        for m in tpl(name).find_all(img, threshold=LETTER_THRESHOLD, region=region)
    ]
    out: list[Box] = []
    for r in sorted(hits, key=lambda r: (r.x1, r.y1)):
        if all(abs(r.center.x - o.center.x) >= SAME_CARD_DX for o in out):
            out.append(r)
    return out


def _dialog_card(crop: np.ndarray):
    from hiro import master
    from hiro.vision import card_db

    asset = card_db.match(crop)
    return master.card_by_asset(asset) if asset else None


def select_card_options(img: np.ndarray) -> list[CardOption]:
    """每个字母框一张：往上取卡面（字母中心 ±60、上 105）认卡；卡面出了画面给 None。"""
    img = normalize(img)
    out = []
    for r in card_letters(img):
        x1, y1, x2, y2 = r.center.x - 60, r.y1 - 105, r.center.x + 60, r.y2
        card = None if x1 < 0 or y1 < 0 else _dialog_card(img[y1:y2, x1:x2])
        out.append(CardOption(r, card))
    return out


def badge_match(objects: list[Box], badges: list[Box]) -> list[Box | None]:
    """每个对象正下方（水平落在对象范围内、垂直在中心以下）最近的徽章，一个徽章只配一次（同 KAA badge.match 的 mb）。"""
    left = list(badges)
    out: list[Box | None] = []
    for o in objects:
        cx, cy = o.x1 + o.w // 2, o.y1 + o.h // 2
        best, best_d = None, float("inf")
        for i, b in enumerate(left):
            bx, by = b.x1 + b.w // 2, b.y1 + b.h // 2
            if not (o.x1 <= bx <= o.x2) or by <= cy:
                continue
            d = ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        out.append(left.pop(best) if best is not None else None)
    return out


def recommended_flags(img: np.ndarray, options: list[CardOption]) -> list[bool]:
    """每张候选下面有没有「おすすめ」（可以同时挂在多张下）。"""
    if not options:
        return []
    badges = [m.rect for m in tpl("InProduce.TextRecommend").find_all(normalize(img))]
    return [b is not None for b in badge_match([o.rect for o in options], badges)] if badges else [False] * len(options)


def recommended_index(img: np.ndarray, options: list[CardOption]) -> int | None:
    """第一张带「おすすめ」的候选（同 KAA fetch_recommend_card）。"""
    return next((i for i, f in enumerate(recommended_flags(img, options)) if f), None)
