"""初 / N.I.A 按偶像流派选卡、删卡用的知识（协调 11-19 多偶像：第一张是 藤田ことね「世界一可愛い私」，ロジック・好印象）。

流派来自偶像卡的 examEffectType（hiro.state.memory_priority.profile_for_idol：yaruki / review_smile / sense_spotlight / anomaly_tsuyoki）。
社区依据：
- 循环件：game8「NIAマスター攻略」的「効果別のループパーツ例」[g8-NIAM]（原文摘录在 .temp/plans-20261112/hajime-nia-sources/game8_NIA_master.txt）。
- 卡的优先度：memory_priority.PROFILES 各流派的优先表（いおん的回忆攻略，原本给回忆编成用）。初 / N.I.A 里借来当「社区评级」。
- 冲突：同一种打法（プラン）里另一种 buff 的卡——卡面的主要得分 / 使用条件挂在那种 buff 上。アノマリー的指针 / 热意没有可靠的卡面写法，暂无。
Campus 広（やる気）走 deciders 原来的路径（YARUKI_CARDS / SMALL_DECK_CORE），这里的表不改广的行为。
"""

from __future__ import annotations

import re
from functools import cache, lru_cache  # noqa: F401

# 偶像拿手 buff → 同一种プラン里会冲突的 buff（主打它的卡对这个偶像没用）
OTHER_BUFFS = {"やる気": ("好印象",), "好印象": ("やる気",), "好調": ("集中",), "集中": ("好調",)}

# 循环件 [g8-NIAM]「効果別のループパーツ例」：examEffectType → 卡 id
LOOP_PARTS = {
    "ProduceExamEffectType_ExamCardPlayAggressive": (
        "p_card-02-men-2_057",
        "p_card-02-act-2_062",
    ),  # ゆめみごこち、きらきら紙吹雪
    "ProduceExamEffectType_ExamReview": (
        "p_card-02-act-2_049",
        "p_card-02-men-2_060",
        "p_card-02-act-2_046",
    ),  # みんな大好き、オトメゴコロ、ラブリーウインク
    "ProduceExamEffectType_ExamParameterBuff": (
        "p_card-01-men-2_037",
        "p_card-01-act-2_001",
        "p_card-01-act-3_185",
    ),  # 存在感、シュプレヒコール、脚光
    "ProduceExamEffectType_ExamLessonBuff": ("p_card-01-men-2_037", "p_card-01-act-2_001", "p_card-01-act-3_185"),
    "ProduceExamEffectType_ExamConcentration": (
        "p_card-03-men-1_050",
        "p_card-03-act-2_102",
        "p_card-03-act-3_188",
    ),  # 巻き返し、オーバードライブ、エンターテイナー
    "ProduceExamEffectType_ExamFullPower": ("p_card-03-act-3_053", "p_card-03-act-3_189"),  # 輝け！、羽ばたけ！
}
STRONG_WEIGHT = 80  # 流派优先表里权重 ≥ 它的算「社区强卡」


@cache
def exam_effect(idol_card_id: str | None) -> str | None:
    if not idol_card_id:
        return None
    try:
        from hiro.state.context import catalog

        return catalog().one("IdolCard", idol_card_id.replace("i_card-skin-", "i_card-")).get("examEffectType")
    except Exception:  # noqa: BLE001
        return None


@cache
def flow(idol_card_id: str | None) -> str | None:
    """偶像 → 流派键（memory_priority.PROFILES）；查不到 None。"""
    if not idol_card_id:
        return None
    try:
        from hiro.state.memory_priority import profile_for_idol

        return profile_for_idol(idol_card_id)[0]
    except Exception:  # noqa: BLE001
        return None


def flow_name(idol_card_id: str | None) -> str | None:
    from hiro.state.memory_priority import PROFILES

    f = flow(idol_card_id)
    return PROFILES[f]["name"] if f in PROFILES else None


def priority(idol_card_id: str | None, card_id: str) -> dict | None:
    """流派优先表里这张卡的条目（weight / role / sources）；没有 None。"""
    from hiro.state.memory_priority import PROFILES

    f = flow(idol_card_id)
    return PROFILES[f]["by_id"].get(card_id) if f in PROFILES else None


def loop_parts(idol_card_id: str | None) -> tuple[str, ...]:
    return LOOP_PARTS.get(exam_effect(idol_card_id) or "", ())


_MAIN = (r"{w}の\d+%分パラメータ上昇", r"{w}が\d+以上の場合、使用可", r"以降.*{w}\+", r"{w}増加量増加")


@cache
def buff_main(card_id: str, word: str) -> bool:
    """卡的主要得分 / 使用条件挂在 word（好印象 / やる気 / 好調 / 集中）上：「wのN%分パラメータ上昇」「wがN以上の場合、使用可」
    「以降…w+」「w増加量増加」。只消費 w、顺带加 w 的不算。"""
    from hiro.brain.cards import description_text
    from hiro.state.context import catalog

    try:
        text = description_text(catalog().card(card_id, 0)["produceDescriptions"])
    except Exception:  # noqa: BLE001
        return False
    return any(re.search(p.format(w=re.escape(word)), text) for p in _MAIN)


def conflicts(card_id: str, buff: str | None) -> str | None:
    """对拿手 buff 是 buff 的偶像，这张卡冲突的原因（主打同プラン另一种 buff）；不冲突 / 不知道时 None。"""
    for other in OTHER_BUFFS.get(buff or "", ()):
        if buff_main(card_id, other):
            return f"主打{other}"
    return None
