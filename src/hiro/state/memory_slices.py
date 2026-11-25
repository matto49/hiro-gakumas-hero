"""回忆分片（资料库扫描设计 5.1）：一片 = 一个流派（技能卡的プラン）× 一个获得模式（プロデュース），共 12 片。
游戏回忆选择页的「ソート・フィルタ」能按这两组筛选；扫描一次只扫一片，资料库按片记录。"""

from __future__ import annotations

PLANS = ("sense", "logic", "anomaly", "common")
PRODUCES = ("hajime", "nia", "hif")
SLICES = tuple(f"{p}:{m}" for p in PLANS for m in PRODUCES)


def slice_key(plan: str, produce: str) -> str:
    if plan not in PLANS or produce not in PRODUCES:
        raise ValueError(f"没有这一片：{plan} × {produce}")
    return f"{plan}:{produce}"


def slices_for_plan(plan: str | None) -> list[str]:
    """一局要用的片：偶像卡流派的 3 片 + 共通的 3 片；流派不知道（None）时 12 片全要。"""
    if plan is None:
        return list(SLICES)
    return [slice_key(plan, m) for m in PRODUCES] + [slice_key("common", m) for m in PRODUCES]
